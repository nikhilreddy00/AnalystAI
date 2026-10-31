"""Phase 5.6a durable runs, offline at $0: a real overview run (e2e-01, recorded by
scripts/record_run_fixture.py) replayed through the runner, killed at every node boundary."""
import json
import re
from pathlib import Path

import os

import pytest
from cryptography.fernet import Fernet
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import text

from agents import analysis_agent
from connectors import csv_connector
from evals.run import dataset, e2e_pii_literals, load_cases
from llm.client import LLMResult, pii_values
from memory import session_data
from api.lib.report import provenance
from memory.run_store import InMemoryRunStore, report_hash
from memory.session_store import InMemorySessionStore
from orchestrator.graph import build_graph
from api.config import settings
from orchestrator.runner import PLAIN, Deps, execute, open_deps
from orchestrator.state import AnalysisSession, ConnectorConfig, ConnectorType
from sandbox.executor import ExecutionResult

FIXTURE = json.loads((Path(__file__).parent / "fixtures/runs/e2e-01.json").read_text())
CASE = load_cases("e2e", "e2e-01")[0]
WANT_COST = round(sum(r["cost_usd"] for r in FIXTURE["replies"]), 6)
ANSWER = "Continue with imputation"


class WorkerKilled(BaseException):
    """A kill: not an Exception, so no node or runner handler can catch it."""


class KillingSaver(InMemorySaver):
    """Dies right after its k-th checkpoint is written: a crash at a node boundary."""

    def __init__(self, kill_at: int | None = None):
        super().__init__(serde=PLAIN)
        self.kill_at, self.puts = kill_at, 0

    async def aput(self, *args, **kwargs):
        out = await super().aput(*args, **kwargs)
        self.puts += 1
        if self.puts == self.kill_at:
            self.kill_at = None
            raise WorkerKilled
        return out


@pytest.fixture
def replay(recorded_llm, monkeypatch, tmp_path):
    """Load the recorded replies + sandbox outputs; the cleaned-data cache goes to tmp."""
    monkeypatch.setattr(session_data, "_DATA_DIR", tmp_path / "session_data")
    monkeypatch.setattr(csv_connector, "_UPLOADS_DIR", tmp_path.resolve())
    outputs = [ExecutionResult(**o) for o in FIXTURE["sandbox"]]

    async def fake_run_code(code, df_parquet=None):
        return outputs.pop(0)

    monkeypatch.setattr(analysis_agent, "run_code", fake_run_code)
    recorded_llm.responses.extend(LLMResult(**r) for r in FIXTURE["replies"])
    df, _, csv = dataset(CASE)
    path = tmp_path.resolve() / "sales-1.csv"
    path.write_text(csv)
    return path, e2e_pii_literals(df)


async def _start(deps: Deps, path: Path, sid: str = "s-1") -> str:
    session = AnalysisSession(session_id=sid, user_id="u1",
                              connector_config=ConnectorConfig(type=ConnectorType.CSV,
                                                               file_path=str(path)))
    await deps.session_store.create(session)
    run, _ = await deps.run_store.create(session_id=sid, user_id="u1",
                                         input={"message": CASE["question"]})
    return run.id


async def _expire(store, run_id: str) -> None:
    if isinstance(store, InMemoryRunStore):
        store._runs[run_id].lease_until = 0
        return
    async with store._engine.begin() as conn:
        await conn.execute(text("UPDATE runs SET lease_until = now() - interval '1 second' "
                                "WHERE id = :id"), {"id": run_id})


async def _drive(deps: Deps, run_id: str, *, restart=None, at_gate=False) -> tuple[str, int]:
    """Play the worker (and the user at the gate) until the run is over. A kill is followed by
    a fresh worker: the dead one's lease expires, the reaper re-queues, another claims."""
    store, kills = deps.run_store, 0
    for _ in range(20):
        run = await store.get(run_id)
        if run.status in ("completed", "failed", "cancelled"):
            return run.status, kills
        if run.status == "waiting_approval":
            if restart and at_gate:
                deps = await restart(deps)   # everything in-process is gone; the stores remain
                store = deps.run_store
            await store.resume(run_id, {"answer": ANSWER},
                               events=[{"type": "approval_answered", "answer": ANSWER}])
            continue
        if run.status == "running":   # a dead worker's run: let its lease expire
            await _expire(store, run_id)
            await store.reap()
            if restart:
                deps = await restart(deps)
                store = deps.run_store
            continue
        try:
            await execute(await store.claim(), deps)
        except WorkerKilled:
            kills += 1
            pii_values.set(set())   # the new worker process starts with an empty guard
    raise AssertionError("run never finished")


def _result(deps: Deps, run_id: str):
    return deps.graph.get_state({"configurable": {"thread_id": run_id}}).values["session"]


_VOLATILE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
                       r"|\d{4}-\d\d-\d\d[T ]\d\d:\d\d:\d\d\.\d+(Z|[+-]\d\d:\d\d)?")


def _stable(obj) -> str:
    """Chart ids (uuid4) and generated_at differ between any two runs, killed or not."""
    return _VOLATILE.sub("<v>", json.dumps(obj, default=lambda o: o.model_dump(mode="json")
                                           if hasattr(o, "model_dump") else str(o), sort_keys=True))


def _report(s: AnalysisSession) -> dict:
    return {k: getattr(s, k) for k in ("insight_report", "analysis_findings", "chart_configs",
                                       "expert_review", "cleaning_audit", "data_contract",
                                       "task_plan", "status", "high_null_answer")}


def _deps(saver) -> Deps:
    return Deps(graph=build_graph(saver), run_store=InMemoryRunStore(),
                session_store=InMemorySessionStore())


async def _baseline(replay_fixture, recorded_llm):
    path, _ = replay_fixture
    deps = _deps(KillingSaver())
    run_id = await _start(deps, path)
    status, _ = await _drive(deps, run_id)
    return deps, run_id, status


async def test_uninterrupted_replay_completes(replay, recorded_llm):
    deps, run_id, status = await _baseline(replay, recorded_llm)
    assert status == "completed" and not recorded_llm.responses   # every recorded reply used
    s = _result(deps, run_id)
    assert s.insight_report and s.expert_review and s.chart_configs
    events = [e for _, e in await deps.run_store.events_after(run_id, 0)]
    types = [e["type"] for e in events]
    assert types.count("approval_required") == 1 and types.count("done") == 1
    assert types[-1] == "done"
    # Phase 5.6b: the report is sealed, and the run knows what it ran under and what it cost.
    run = await deps.run_store.get(run_id)
    assert run.report_sha256 and run.report_sha256 == report_hash(events)
    assert types[0] == "provenance" and types.count("provenance") == 1
    prov = provenance(run, events)
    assert prov["llm_calls"] == len(FIXTURE["replies"])
    assert prov["cost_usd"] == WANT_COST > 0
    assert {p["agent"] for p in prov["prompts"]} >= {"intake", "analysis", "insight"}
    assert prov["cards"]["analysis"] and prov["model"]


async def test_kill_at_every_node_boundary_resumes_to_the_same_report(
        replay, recorded_llm, monkeypatch):
    deps, base_id, _ = await _baseline(replay, recorded_llm)
    want = _report(_result(deps, base_id))
    want_events = [e for _, e in await deps.run_store.events_after(base_id, 0)]
    boundaries = deps.graph.checkpointer.puts
    assert boundaries > 10

    path, pii = replay
    for k in range(1, boundaries + 1):   # + the final checkpoint: dead before the run row ends
        recorded_llm.responses[:] = [LLMResult(**r) for r in FIXTURE["replies"]]
        recorded_llm.calls.clear()
        monkeypatch.setattr(analysis_agent, "run_code", _sandbox())
        deps = _deps(KillingSaver(kill_at=k))
        run_id = await _start(deps, path)
        status, kills = await _drive(deps, run_id)
        assert (status, kills) == ("completed", 1), k
        assert not recorded_llm.responses, f"kill at {k}: a model call was repeated or skipped"
        assert _stable(_report(_result(deps, run_id))) == _stable(want), f"kill at {k}: report"
        events = [e for _, e in await deps.run_store.events_after(run_id, 0)]
        assert _stable([e for e in events if e["type"] != "run_resumed"]) == _stable(want_events), k
        run = await deps.run_store.get(run_id)   # one seal, over what this run delivered
        assert run.report_sha256 == report_hash(events) and run.report_sha256, k
        assert provenance(run, events)["cost_usd"] == WANT_COST, f"kill at {k}: cost"
        sent = json.dumps([c["messages"] for c in recorded_llm.calls])
        assert not [v for v in pii if v in sent], f"kill at {k}: PII reached the model"


def _sandbox():
    outputs = [ExecutionResult(**o) for o in FIXTURE["sandbox"]]

    async def fake_run_code(code, df_parquet=None):
        return outputs.pop(0)
    return fake_run_code


async def test_an_approval_answered_after_a_restart_continues(replay, recorded_llm):
    path, _ = replay
    saver = KillingSaver()
    deps = _deps(saver)
    run_id = await _start(deps, path)
    restarted = []

    async def restart(old: Deps) -> Deps:
        # A new process: a new graph and run env; only the database (saver + stores) survives.
        restarted.append(True)
        pii_values.set(set())
        return Deps(graph=build_graph(saver), run_store=old.run_store,
                    session_store=old.session_store)

    status, _ = await _drive(deps, run_id, restart=restart, at_gate=True)
    assert restarted and status == "completed"
    s = _result(deps, run_id)
    assert s.high_null_answer == "continue" and s.insight_report


async def test_the_dataframe_is_never_checkpointed(replay, recorded_llm):
    deps, run_id, _ = await _baseline(replay, recorded_llm)
    saver = deps.graph.checkpointer
    blobs = [v for v in saver.blobs.values()] + [w for ws in saver.writes.values()
                                                 for w in ws.values()]
    raw = b"".join(b if isinstance(b, bytes) else json.dumps(b, default=str).encode()
                   for blob in blobs for b in (blob if isinstance(blob, tuple) else (blob,)))
    assert raw and b"PAR1" not in raw   # parquet magic: no frame in any checkpoint


async def test_a_changed_source_fails_the_resume_loudly(replay, recorded_llm):
    path, _ = replay
    deps = _deps(KillingSaver(kill_at=3))   # dies right after ingestion's checkpoint
    run_id = await _start(deps, path)
    try:
        await execute(await deps.run_store.claim(), deps)
    except WorkerKilled:
        pass
    path.write_text(path.read_text().replace("North", "Nort", 1))
    status, _ = await _drive(deps, run_id)
    run = await deps.run_store.get(run_id)
    assert status == "failed" and "different rows" in run.error


PG = os.environ.get("TEST_PG_URL")


@pytest.mark.skipif(not PG, reason="TEST_PG_URL not set")
async def test_postgres_kill_at_every_boundary_with_a_new_process_each_time(
        replay, recorded_llm, monkeypatch):
    """The real stack: Postgres runs + sessions, AsyncPostgresSaver with encrypted checkpoints,
    and after every kill a new pool, graph and run env (a new worker process)."""
    monkeypatch.setattr(settings, "encryption_key", Fernet.generate_key().decode())
    path, pii = replay
    opened: list[Deps] = []

    async def fresh(_old=None) -> Deps:
        while opened:   # a killed process's connections die with it
            await _close(opened.pop())
        pii_values.set(set())
        deps = await open_deps("production", PG, vector=False)
        opened.append(deps)
        return deps

    try:
        deps = await fresh()
        async with deps.run_store._engine.begin() as conn:
            await conn.execute(text("TRUNCATE runs, run_events, checkpoints, checkpoint_blobs, "
                                    "checkpoint_writes, analysis_sessions"))
        run_id = await _start(deps, path)
        assert await _drive(deps, run_id) == ("completed", 0)
        want = _stable(_report(await _pg_session(deps, run_id)))
        want_events = _stable([e for _, e in await deps.run_store.events_after(run_id, 0)])
        boundaries = await _pg_count(deps, run_id)

        for k in range(1, boundaries + 1):   # + the final checkpoint: dead before the run row ends
            recorded_llm.responses[:] = [LLMResult(**r) for r in FIXTURE["replies"]]
            recorded_llm.calls.clear()
            monkeypatch.setattr(analysis_agent, "run_code", _sandbox())
            deps = await fresh()
            _arm(deps.graph.checkpointer, k)
            run_id = await _start(deps, path, sid=f"s-kill-{k}")
            status, kills = await _drive(deps, run_id, restart=fresh)
            deps = opened[-1]   # the worker that finished it
            assert (status, kills) == ("completed", 1), k
            assert not recorded_llm.responses, k
            assert _stable(_report(await _pg_session(deps, run_id))) == want, k
            events = [e for _, e in await deps.run_store.events_after(run_id, 0)]
            assert _stable([e for e in events if e["type"] != "run_resumed"]) == want_events, k
            run = await deps.run_store.get(run_id)
            assert run.report_sha256 == report_hash(events) and run.report_sha256, k
            assert provenance(run, events)["cost_usd"] == WANT_COST, k
            assert not [v for v in pii if v in json.dumps([c["messages"]
                                                           for c in recorded_llm.calls])], k

        async with deps.run_store._engine.connect() as conn:   # what is at rest
            blobs = [bytes(b) for (b,) in (await conn.execute(text(
                "SELECT blob FROM checkpoint_blobs WHERE blob IS NOT NULL"))).all()]
        assert blobs and not any(b"sales-1.csv" in b for b in blobs)   # ciphertext only
        plain = [Fernet(settings.encryption_key.encode()).decrypt(b) for b in blobs]
        assert any(b"sales-1.csv" in p for p in plain) and not any(b"PAR1" in p for p in plain)
    finally:
        for d in opened:
            await _close(d)


async def _close(d: Deps) -> None:
    for close in d.closers:
        await close()
    await d.run_store._engine.dispose()
    await d.session_store._engine.dispose()


def _arm(saver, k: int) -> None:
    """Kill the process right after the k-th checkpoint write."""
    put, n = saver.aput, [0]

    async def aput(*args, **kwargs):
        out = await put(*args, **kwargs)
        n[0] += 1
        if n[0] == k:
            raise WorkerKilled
        return out
    saver.aput = aput


async def _pg_session(deps: Deps, run_id: str) -> AnalysisSession:
    return (await deps.graph.aget_state({"configurable": {"thread_id": run_id}})).values["session"]


async def _pg_count(deps: Deps, run_id: str) -> int:
    async with deps.run_store._engine.connect() as conn:
        return (await conn.execute(text("SELECT count(*) FROM checkpoints WHERE thread_id = :t"),
                                   {"t": run_id})).scalar_one()


async def test_a_database_blip_does_not_kill_the_worker(replay, recorded_llm):
    """Audit: claim()/reap() raising used to end work() (the API's embedded worker silently)."""
    import asyncio

    from orchestrator.runner import work
    path, _ = replay
    deps = _deps(KillingSaver())
    run_id = await _start(deps, path)
    claim, calls = deps.run_store.claim, []

    async def flaky_claim(*a, **kw):
        calls.append(1)
        if len(calls) == 1:
            raise ConnectionError("db restarting")
        return await claim(*a, **kw)

    deps.run_store.claim = flaky_claim
    stop = asyncio.Event()
    task = asyncio.create_task(work(deps, stop, poll_s=0.01, heartbeat_s=0.2))
    for _ in range(500):   # the run reaches its high-null gate
        if (await deps.run_store.get(run_id)).status == "waiting_approval":
            break
        await asyncio.sleep(0.02)
    stop.set()
    await task
    assert len(calls) > 1 and (await deps.run_store.get(run_id)).status == "waiting_approval"
