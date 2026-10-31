"""Phase 5.6b Done-when: deleting a thread or a dataset leaves 0 rows, files and memory points in
every store (GDPR Art. 17), in memory and on Postgres. A real delivered run (the recorded e2e-01
overview, $0) fills every store first; each store is counted before (> 0, so the test can't pass
on an empty store) and after (0). Purge also survives a crash after each of its steps, a memory
store that is down, orphans left by a race, and retention."""
import os
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text

from api.config import settings
from api.rate_limit import limiter
from connectors.csv_connector import user_upload_dir
from memory import retention, session_data
from memory.retention import NOT_USER_DATA, PURGE_TABLES, sweep
from orchestrator.runner import in_memory_deps, open_deps
from tests.test_durable_runs import FIXTURE, _close, _sandbox, replay  # noqa: F401
from tests.test_workspace_api import _ask, _client, _upload
from tests.test_workspace_reports import _deliver

PG = os.environ.get("TEST_PG_URL")
CHART = {"id": "c1", "chart_type": "BarChart", "title": "t", "data": [], "x_key": "a",
         "y_keys": ["b"], "source_finding": "f"}


class FakeMemory:
    """The Qdrant store's contract, in a dict: one point per thread."""

    def __init__(self):
        self.points: dict[str, dict] = {}
        self.down = False

    def _up(self):
        if self.down:
            raise ConnectionError("qdrant down")

    async def store_insight(self, session_id, user_id, report, task_summary, dataset=""):
        self.points[session_id] = {"user_id": user_id, "dataset_key": dataset}

    async def retrieve_similar(self, *args, **kwargs):
        return []

    async def delete_session(self, session_id):
        self._up()
        self.points.pop(session_id, None)

    async def delete_dataset(self, dataset):
        self._up()
        for sid in [s for s, p in self.points.items() if p["dataset_key"] == dataset]:
            self.points.pop(sid)

    async def session_ids(self):
        self._up()
        return set(self.points)


@pytest.fixture(params=["memory", "postgres"])
async def env(request, replay, monkeypatch):  # noqa: F811
    monkeypatch.setattr(settings, "encryption_key", Fernet.generate_key().decode())
    path, _ = replay
    if request.param == "memory":
        deps = in_memory_deps()
    else:
        if not PG:
            pytest.skip("TEST_PG_URL not set")
        deps = await open_deps("production", PG, vector=False)
        async with deps.run_store._engine.begin() as conn:
            await conn.execute(text(f"TRUNCATE {', '.join(PURGE_TABLES)}"))
    deps.vector_store = FakeMemory()
    limiter.reset()
    try:
        async with _client(deps) as client:
            yield client, deps, path.read_bytes()
    finally:
        if request.param == "postgres":
            await _close(deps)


async def _thread(client, deps, version_id: str, recorded_llm=None, monkeypatch=None):
    """A delivered overview on this dataset version, with a pinned chart: (thread, run)."""
    if recorded_llm is not None:   # a second run in one test: the recording again
        from agents import analysis_agent
        from llm.client import LLMResult
        recorded_llm.responses[:] = [LLMResult(**r) for r in FIXTURE["replies"]]
        monkeypatch.setattr(analysis_agent, "run_code", _sandbox())
    run_id, sid, _ = await _deliver(client, deps, dataset_version_id=version_id)
    chart = {**CHART, "id": f"chart-{sid}"}   # a chart id is unique: one pin per thread here
    assert (await client.post("/dashboard/pin", json={"session_id": sid, "chart": chart})
            ).status_code == 200
    return sid, run_id


async def _checkpoints(deps, run_ids: list[str]) -> int:
    if hasattr(deps.run_store, "_engine"):
        n = 0
        async with deps.run_store._engine.connect() as conn:
            for t in ("checkpoints", "checkpoint_blobs", "checkpoint_writes"):
                n += (await conn.execute(text(
                    f"SELECT count(*) FROM {t} WHERE thread_id = ANY(:ids)"), {"ids": run_ids})
                ).scalar_one()
        return n
    saver = deps.graph.checkpointer   # InMemorySaver: checkpoints, writes and blobs by thread
    return sum(1 for r in run_ids if r in saver.storage) + sum(
        1 for store in (saver.writes, saver.blobs) for k in store if k[0] in run_ids)


async def inventory(deps, *, sid: str, run_ids: list[str], dataset_id: str | None = None,
                    files: bool = False) -> dict[str, int]:
    """Everything stored for this thread (and dataset), store by store."""
    out = {**await deps.session_store.count_rows(session_id=sid, dataset_id=dataset_id),
           **await deps.run_store.count_rows(run_ids, sid),
           "checkpoints": await _checkpoints(deps, run_ids),
           "session_data": int((session_data._DATA_DIR / sid).exists()),
           "memory_points": sum(1 for s, p in deps.vector_store.points.items()
                                if s == sid or dataset_id and p["dataset_key"] == dataset_id)}
    if files:
        d = user_upload_dir("u1")
        out["upload_files"] = len(list(d.iterdir())) if d.exists() else 0
    return out


async def test_deleting_a_thread_leaves_nothing(env):
    client, deps, csv = env
    up = (await _upload(client, csv)).json()
    sid, run_id = await _thread(client, deps, up["dataset_version_id"])
    before = await inventory(deps, sid=sid, run_ids=[run_id])
    for store in ("analysis_sessions", "dashboard_charts", "runs", "run_events", "checkpoints",
                  "session_data", "memory_points"):
        assert before[store] > 0, f"{store} was never filled: the test would prove nothing"

    assert (await client.delete(f"/api/sessions/{sid}")).status_code == 202
    assert deps.vector_store.points == {}   # memory dropped at delete, not at purge
    # The purge alone (not the orphan sweep behind it) removes every store.
    assert await retention.purge_thread(deps, sid, "u1", now=datetime.now(timezone.utc),
                                        grace_s=0)
    after = await inventory(deps, sid=sid, run_ids=[run_id])
    assert after == dict.fromkeys(after, 0), after
    # The dataset is not the thread's: it stays, file and all.
    [ds] = (await client.get("/api/datasets")).json()
    assert ds["id"] == up["dataset_id"] and len(list(user_upload_dir("u1").iterdir())) == 1


async def test_deleting_a_dataset_leaves_nothing(env, recorded_llm, monkeypatch):
    client, deps, csv = env
    up = (await _upload(client, csv)).json()
    s1, r1 = await _thread(client, deps, up["dataset_version_id"])
    s2, r2 = await _thread(client, deps, up["dataset_version_id"], recorded_llm, monkeypatch)
    did = up["dataset_id"]
    before = [await inventory(deps, sid=s, run_ids=[r], dataset_id=did, files=True)
              for s, r in ((s1, r1), (s2, r2))]
    assert all(v > 0 for b in before for v in b.values()), before

    r = await client.delete(f"/api/datasets/{did}")
    assert (r.status_code, r.json()["threads"]) == (202, 2)
    await sweep(deps, grace_s=0)
    for s, r in ((s1, r1), (s2, r2)):
        after = await inventory(deps, sid=s, run_ids=[r], dataset_id=did, files=True)
        assert after == dict.fromkeys(after, 0), after
    assert (await client.get("/api/datasets")).json() == []


async def test_every_table_with_user_data_is_purged():
    """A new table that holds per-thread or per-dataset data must be added to the purge."""
    if not PG:
        pytest.skip("TEST_PG_URL not set")
    from memory.session_store import SessionStore
    store = SessionStore(PG)
    try:
        async with store._engine.connect() as conn:
            rows = (await conn.execute(text(
                "SELECT DISTINCT table_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND column_name IN "
                "('session_id', 'run_id', 'thread_id', 'dataset_id', 'user_id')"))).scalars()
            tables = set(rows)
    finally:
        await store._engine.dispose()
    assert tables, "no tables found: run the other Postgres tests first"
    assert tables - set(PURGE_TABLES) - NOT_USER_DATA == set()


async def test_purge_waits_until_the_run_has_stopped(env):
    client, deps, csv = env
    up = (await _upload(client, csv)).json()
    run = (await _ask(client, dataset_version_id=up["dataset_version_id"])).json()
    claimed = await deps.run_store.claim()   # a worker has it
    assert claimed.id == run["run_id"]
    assert (await client.delete(f"/api/sessions/{run['session_id']}")).status_code == 202
    await sweep(deps, grace_s=0)
    left = await inventory(deps, sid=run["session_id"], run_ids=[run["run_id"]])
    assert left["analysis_sessions"] == 1 and left["runs"] == 1   # tombstone kept: still running
    # The worker notices the cancel at its heartbeat and stops; the purge then waits a lease
    # (a stalled worker can't write after it), and goes.
    from memory.run_store import CANCELLED, cancel_events
    assert await deps.run_store.finish(claimed.id, "cancelled", attempt=claimed.attempt,
                                       error=CANCELLED, events=cancel_events())
    await sweep(deps)
    assert (await inventory(deps, sid=run["session_id"], run_ids=[run["run_id"]]))["runs"] == 1
    # Its own check, apart from the delete's: a run that stopped a moment ago holds the purge.
    assert not await retention.purge_thread(deps, run["session_id"], "u1",
                                            now=datetime.now(timezone.utc))
    await sweep(deps, now=datetime.now(timezone.utc) + timedelta(seconds=retention.LEASE_S + 1))
    after = await inventory(deps, sid=run["session_id"], run_ids=[run["run_id"]])
    assert after == dict.fromkeys(after, 0), after


@pytest.mark.parametrize("step", ["vector_store.delete_session", "checkpointer.adelete_thread",
                                  "run_store.purge_runs", "session_store.purge_session"])
async def test_a_crash_after_any_purge_step_is_finished_by_the_next_sweep(env, step, monkeypatch):
    client, deps, csv = env
    up = (await _upload(client, csv)).json()
    sid, run_id = await _thread(client, deps, up["dataset_version_id"])
    owner, name = step.split(".")
    target = deps.graph.checkpointer if owner == "checkpointer" else getattr(deps, owner)
    real = getattr(target, name)

    async def done_then_crash(*args, **kwargs):
        await real(*args, **kwargs)
        raise RuntimeError(f"crashed after {step}")
    monkeypatch.setattr(target, name, done_then_crash)
    await client.delete(f"/api/sessions/{sid}")
    await sweep(deps, grace_s=0)   # logs the crash, keeps going
    monkeypatch.setattr(target, name, real)
    await sweep(deps, grace_s=0)
    after = await inventory(deps, sid=sid, run_ids=[run_id])
    assert after == dict.fromkeys(after, 0), (step, after)


async def test_memory_down_keeps_the_tombstone_until_it_is_back(env):
    client, deps, csv = env
    up = (await _upload(client, csv)).json()
    sid, run_id = await _thread(client, deps, up["dataset_version_id"])
    deps.vector_store.down = True
    assert (await client.delete(f"/api/sessions/{sid}")).status_code == 202   # still deleted
    assert (await client.get("/api/sessions")).json() == []
    await sweep(deps, grace_s=0)
    left = await inventory(deps, sid=sid, run_ids=[run_id])
    assert left["analysis_sessions"] == 1 and left["memory_points"] == 1
    deps.vector_store.down = False
    await sweep(deps, grace_s=0)
    after = await inventory(deps, sid=sid, run_ids=[run_id])
    assert after == dict.fromkeys(after, 0), after


async def test_orphans_of_a_race_are_swept_and_live_threads_kept(env):
    client, deps, csv = env
    up = (await _upload(client, csv)).json()
    sid, run_id = await _thread(client, deps, up["dataset_version_id"])
    # What a write racing a purge leaves: a run, its events, a cache dir and a memory point,
    # all for a thread that has no row.
    ghost, _ = await deps.run_store.create(session_id="ghost", user_id="u1",
                                           input={"message": "q"},
                                           first_events=[{"type": "session_id"}])
    session_data.write_cleaned_parquet("ghost", b"x")
    deps.vector_store.points["ghost"] = {"user_id": "u1", "dataset_key": ""}
    await sweep(deps, grace_s=0)
    gone = await inventory(deps, sid="ghost", run_ids=[ghost.id])
    assert gone == dict.fromkeys(gone, 0), gone
    kept = await inventory(deps, sid=sid, run_ids=[run_id])
    assert all(v for k, v in kept.items() if not k.startswith("dataset")), kept


async def test_retention(env):
    client, deps, csv = env
    up = (await _upload(client, csv)).json()
    sid, run_id = await _thread(client, deps, up["dataset_version_id"])
    await sweep(deps, grace_s=0, retention_days=0)   # the default: keep forever
    await sweep(deps, grace_s=0, retention_days=30)  # not idle long enough
    assert (await client.get("/api/sessions")).json()
    later = datetime.now(timezone.utc) + timedelta(days=31)
    await sweep(deps, now=later, retention_days=30)   # tombstoned
    await sweep(deps, now=later, retention_days=30)   # purged, then the unused dataset
    await sweep(deps, now=later, retention_days=30)
    after = await inventory(deps, sid=sid, run_ids=[run_id], dataset_id=up["dataset_id"],
                            files=True)
    assert after == dict.fromkeys(after, 0), after
