"""Phase 5.6b independent review #1: each finding, as a test that failed before its fix (memory and
Postgres, where both apply)."""
import asyncio
import json
import os
import time
from datetime import datetime, timedelta, timezone

import pytest

from connectors.csv_connector import user_upload_dir
from memory import retention
from memory.retention import sweep
from memory.run_store import report_events, report_hash
from memory.session_store import DatasetGone, InMemorySessionStore
from orchestrator.state import AnalysisSession
from tests.test_deletion import FakeMemory, _thread, env, inventory  # noqa: F401
from tests.test_durable_runs import replay  # noqa: F401
from tests.test_workspace_api import _ask, _upload, _uploads, ws  # noqa: F401


async def test_r1_a_chat_racing_a_dataset_delete_creates_nothing(env):  # noqa: F811
    client, deps, csv = env
    up = (await _upload(client, csv)).json()
    store, real = deps.session_store, deps.session_store.get_version

    async def looked_up_then_deleted(vid, uid):
        got = await real(vid, uid)
        await store.tombstone_dataset(up["dataset_id"], uid)   # the DELETE lands here
        return got
    store.get_version = looked_up_then_deleted
    r = await _ask(client, dataset_version_id=up["dataset_version_id"])
    store.get_version = real
    assert r.status_code == 404
    assert (await client.get("/api/sessions")).json() == []
    await sweep(deps, grace_s=0)
    left = await store.count_rows(dataset_id=up["dataset_id"])
    assert left == dict.fromkeys(left, 0) and not _uploads(deps), left


async def test_r1_a_thread_that_slipped_past_a_delete_is_deleted_with_it(env):  # noqa: F811
    """Belt and braces: whatever made it, a live thread on a deleted dataset can't block the
    dataset's purge forever."""
    client, deps, csv = env
    up = (await _upload(client, csv)).json()
    run = (await _ask(client, dataset_version_id=up["dataset_version_id"])).json()
    await client.post(f"/runs/{run['run_id']}/cancel")
    store = deps.session_store
    await store.tombstone_dataset(up["dataset_id"], "u1")
    if isinstance(store, InMemorySessionStore):   # undo the cascade for it: the slipped case
        store._sessions[run["session_id"]]["meta"]["deleted_at"] = None
    else:
        from sqlalchemy import text
        async with store._engine.begin() as conn:
            await conn.execute(text("UPDATE analysis_sessions SET deleted_at = NULL"))
    await sweep(deps, grace_s=0)   # tombstones it
    await sweep(deps, grace_s=0)   # purges it, then the dataset
    left = await store.count_rows(session_id=run["session_id"], dataset_id=up["dataset_id"])
    assert left == dict.fromkeys(left, 0), left


async def test_r1_r10_a_thread_on_a_deleted_or_moved_version_cant_be_created(env):  # noqa: F811
    client, deps, csv = env
    store = deps.session_store
    v1 = (await _upload(client, csv)).json()
    lines = csv.decode().splitlines()
    v2 = (await _upload(client, ("\n".join(lines + [lines[-1]]) + "\n").encode())).json()
    moved = await store.split_version(v2["dataset_version_id"], "u1")
    with pytest.raises(DatasetGone):   # the version now belongs to `moved`, not v1's dataset
        await store.create(AnalysisSession(session_id="x", user_id="u1",
                                           dataset_id=v1["dataset_id"],
                                           dataset_version_id=v2["dataset_version_id"]))
    await store.create(AnalysisSession(session_id="y", user_id="u1", dataset_id=moved.id,
                                       dataset_version_id=v2["dataset_version_id"]))
    await store.tombstone_dataset(v1["dataset_id"], "u1")
    with pytest.raises(DatasetGone):
        await store.create(AnalysisSession(session_id="z", user_id="u1",
                                           dataset_id=v1["dataset_id"],
                                           dataset_version_id=v1["dataset_version_id"]))


async def test_r2_deleting_a_legacy_thread_keeps_a_file_another_thread_uses(env):  # noqa: F811
    client, deps, csv = env
    d = user_upload_dir("u1")
    d.mkdir(parents=True, exist_ok=True)
    p = d / "legacy.csv"
    p.write_bytes(csv)
    cfg = {"type": "csv", "file_path": str(p)}
    a = (await _ask(client, connector_config=cfg)).json()
    b = (await _ask(client, connector_config=cfg)).json()
    for r in (a, b):
        await client.post(f"/runs/{r['run_id']}/cancel")
    await client.delete(f"/api/sessions/{a['session_id']}")
    await sweep(deps, grace_s=0)
    assert p.exists() and await deps.session_store.load(b["session_id"])
    await client.delete(f"/api/sessions/{b['session_id']}")
    await sweep(deps, grace_s=0)
    assert not p.exists()   # the last thread on it takes it


async def test_r3_the_sweep_never_loads_checkpoints(env, monkeypatch):  # noqa: F811
    client, deps, csv = env
    up = (await _upload(client, csv)).json()
    await _thread(client, deps, up["dataset_version_id"])

    def no(*a, **k):
        raise AssertionError("the sweep listed (and decrypted) every checkpoint")
    monkeypatch.setattr(deps.graph.checkpointer, "alist", no)
    await sweep(deps, grace_s=0)
    assert len(await retention._checkpoint_threads(deps)) == 1


async def test_r3_the_worker_claims_while_a_sweep_is_slow(monkeypatch):
    from orchestrator.runner import in_memory_deps, work
    deps = in_memory_deps()
    gate = asyncio.Event()

    async def slow(_deps):
        await gate.wait()
    monkeypatch.setattr(retention, "sweep_logged", slow)
    run, _ = await deps.run_store.create(session_id="s", user_id="u", input={"message": "q"})
    stop = asyncio.Event()
    task = asyncio.create_task(work(deps, stop, poll_s=0.01, heartbeat_s=0.05))
    for _ in range(100):
        if (await deps.run_store.get(run.id)).status != "queued":
            break
        await asyncio.sleep(0.01)
    assert (await deps.run_store.get(run.id)).status != "queued"
    gate.set()
    stop.set()
    await task


async def test_r4_markdown_export_with_a_non_ascii_title(ws):  # noqa: F811
    client, _, _, csv = ws
    up = (await _upload(client, csv, name="売上.csv")).json()
    r = (await _ask(client, dataset_version_id=up["dataset_version_id"])).json()
    res = await client.get(f"/api/sessions/{r['session_id']}/export.md")
    assert res.status_code == 200 and res.text.startswith("# 売上")


async def test_r5_rename_pin_archive_keep_the_threads_place(env):  # noqa: F811
    client, deps, csv = env
    up = (await _upload(client, csv)).json()
    run = (await _ask(client, dataset_version_id=up["dataset_version_id"])).json()
    [before] = (await client.get("/api/sessions")).json()
    await asyncio.sleep(1.1)
    await client.patch(f"/api/sessions/{run['session_id']}",
                       json={"title": "x", "pinned": True, "archived": True})
    [after] = (await client.get("/api/sessions")).json()
    assert after["updated_at"] == before["updated_at"]


async def test_r6_thread_times_are_utc_whatever_the_server_zone(env):  # noqa: F811
    client, deps, csv = env
    if hasattr(deps.session_store, "_engine"):
        from sqlalchemy import event
        engine = deps.session_store._engine.sync_engine

        @event.listens_for(engine, "connect")
        def _zone(conn, _rec):   # a server not on UTC (as many are)
            cur = conn.cursor()
            cur.execute("SET TIME ZONE 'America/Los_Angeles'")
            cur.close()
        await deps.session_store._engine.dispose()
    up = (await _upload(client, csv)).json()
    await _ask(client, dataset_version_id=up["dataset_version_id"])
    [t] = (await client.get("/api/sessions")).json()
    for k in ("created_at", "updated_at"):
        at = datetime.fromisoformat(t[k].replace("Z", "+00:00"))
        assert abs((datetime.now(timezone.utc) - at).total_seconds()) < 60, (k, t[k])


async def test_r7_upload_new_version_stays_in_its_dataset(env):  # noqa: F811
    client, _, csv = env
    a = (await _upload(client, csv, name="a.csv")).json()
    b = (await _upload(client, b"x,y\n1,2\n3,4\n", name="b.csv")).json()
    r = (await _upload(client, csv, name="b2.csv", dataset_id=b["dataset_id"])).json()
    assert (r["status"], r["dataset_id"], r["version"]) == ("new_version", b["dataset_id"], 2)
    again = (await _upload(client, csv, name="b3.csv", dataset_id=b["dataset_id"])).json()
    assert again["status"] == "existing" and again["dataset_id"] == b["dataset_id"]
    assert a["dataset_id"] != b["dataset_id"]


async def test_r8_one_store_down_doesnt_stop_the_other_orphans(env):  # noqa: F811
    client, deps, csv = env
    from memory import session_data
    session_data.write_cleaned_parquet("ghost", b"x")
    deps.vector_store.down = True
    await sweep(deps, grace_s=0)
    assert not (session_data._DATA_DIR / "ghost").exists()


async def test_r9_upload_files_nothing_points_at_are_swept_after_an_hour(env):  # noqa: F811
    client, deps, csv = env
    (await _upload(client, csv)).json()
    d = user_upload_dir("u1")
    stray, fresh = d / "stray.csv", d / "fresh.csv"
    stray.write_text("x")
    fresh.write_text("x")
    old = time.time() - retention.UPLOAD_GRACE_S - 10
    os.utime(stray, (old, old))
    [kept] = [f for f in _uploads(deps) if f.name not in ("stray.csv", "fresh.csv")]
    os.utime(kept, (old, old))   # an old file a version points at: kept
    evals = user_upload_dir("u1").parent / "evals"
    evals.mkdir(exist_ok=True)
    (evals / "e2e.csv").write_text("x")
    os.utime(evals / "e2e.csv", (old, old))
    await sweep(deps, grace_s=0)
    assert not stray.exists() and fresh.exists() and kept.exists()
    assert (evals / "e2e.csv").exists()   # the eval harness's own folder


async def test_r11_retention_counts_a_datasets_newest_version(env):  # noqa: F811
    client, deps, csv = env
    store = deps.session_store
    up = (await _upload(client, csv)).json()
    later = datetime.now(timezone.utc) + timedelta(days=31)
    assert await store.idle_datasets(later - timedelta(days=30))   # untouched for 31 days: idle
    lines = csv.decode().splitlines()
    if isinstance(store, InMemorySessionStore):   # the dataset is old, its new version is today
        store._datasets[up["dataset_id"]]["created_at"] -= timedelta(days=60)
    else:
        from sqlalchemy import text
        async with store._engine.begin() as conn:
            await conn.execute(text("UPDATE datasets SET created_at = now() - interval '60 days'"))
            await conn.execute(text(
                "UPDATE dataset_versions SET created_at = now() - interval '60 days'"))
    await _upload(client, ("\n".join(lines + [lines[-1]]) + "\n").encode())
    assert await store.idle_datasets(datetime.now(timezone.utc) - timedelta(days=30)) == []


async def test_r12_another_users_thread_is_a_404_on_chat(ws):  # noqa: F811
    client, other, _, csv = ws
    up = (await _upload(client, csv)).json()
    sid = (await _ask(client, dataset_version_id=up["dataset_version_id"])).json()["session_id"]
    assert (await _ask(other, session_id=sid)).status_code == 404


def test_r13_m4_the_report_is_what_the_ui_last_built():
    """Reviews #1 and #2: the report the reducer shows. A new answer starts at a gate's answer;
    a retry or a crash resume carries on, later events replacing earlier ones of their type."""
    ins = lambda n: {"type": "insight", "data": {"n": n}}  # noqa: E731
    ev, charts = {"type": "evidence", "data": [1]}, {"type": "charts", "data": [2]}
    review = {"type": "expert_review", "data": {}}
    # Insight failed after charts, then "retry from where it stopped" re-ran insight only.
    retry = [ev, charts, {"type": "error", "message": "x"}, {"type": "done"},
             {"type": "run_resumed", "message": "r"}, ins(2), review, {"type": "done"}]
    assert report_events(retry) == [ev, charts, ins(2), review]
    # Killed after quality_check's events, before its checkpoint: it re-runs and emits again.
    crash = [ev, charts, ins(1), review, {"type": "done"}, {"type": "run_resumed", "message": "r"},
             review, {"type": "done"}]
    assert report_events(crash) == [ev, charts, ins(1), review, review]
    # Killed after the last checkpoint: resumed, nothing new.
    late = [ev, ins(1), {"type": "done"}, {"type": "run_resumed", "message": "r"}]
    assert report_events(late) == [ev, ins(1)]
    # A gate's answer starts the answer that follows it.
    gate = [ev, {"type": "approval_required"}, {"type": "approval_answered"}, ins(1),
            {"type": "done"}]
    assert report_events(gate) == [ins(1)]
    assert report_hash(retry) == report_hash([ev, charts, ins(2), review])
    assert json.dumps(report_events([])) == "[]" and report_hash([]) is None


async def test_r14_in_memory_the_sweep_leaves_memory_points_and_files_alone(replay):  # noqa: F811
    from memory import session_data
    from orchestrator.runner import in_memory_deps
    deps = in_memory_deps()
    deps.vector_store = FakeMemory()
    deps.vector_store.points["from-before-a-restart"] = {"user_id": "u1", "dataset_key": "d"}
    session_data.write_cleaned_parquet("from-before-a-restart", b"x")
    await sweep(deps, grace_s=0)
    assert deps.vector_store.points
    assert (session_data._DATA_DIR / "from-before-a-restart").exists()
