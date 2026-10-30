"""Phase 5.6a run queue: the in-memory store and Postgres (when TEST_PG_URL is set) keep the same
contract: one run per double submit, one claimer per run, fenced stale workers, loud reaping."""
import asyncio
import os

import pytest
from sqlalchemy import text

from memory.run_store import MAX_ATTEMPTS, InMemoryRunStore, RunStore

PG = os.environ.get("TEST_PG_URL")


@pytest.fixture(params=["memory", "postgres"])
async def store(request):
    if request.param == "memory":
        yield InMemoryRunStore()
        return
    if not PG:
        pytest.skip("TEST_PG_URL not set")
    s = RunStore(PG)
    await s.init()
    async with s._engine.begin() as conn:
        await conn.execute(text("TRUNCATE runs, run_events"))
    yield s
    await s._engine.dispose()


async def _new(store, crid=None, sid="s1"):
    return await store.create(session_id=sid, user_id="u1", input={"message": "q"},
                              client_request_id=crid, first_events=[{"type": "session_id"}])


async def test_a_double_submit_starts_one_run(store):
    (a, made_a), (b, made_b) = await _new(store, "req-1"), await _new(store, "req-1")
    assert made_a and not made_b and a.id == b.id
    assert (await store.by_request("u1", "req-1")).id == a.id
    assert [s for s, _ in await store.events_after(a.id, 0)] == [1]


async def test_concurrent_claimers_never_share_a_run(store):
    ids = {(await _new(store))[0].id for _ in range(3)}
    got = await asyncio.gather(*(store.claim() for _ in range(5)))
    claimed = [r.id for r in got if r]
    assert sorted(claimed) == sorted(ids) and all(r.attempt == 1 for r in got if r)


async def test_a_worker_that_lost_its_lease_is_fenced(store):
    run, _ = await _new(store)
    old = await store.claim(lease_s=0)
    await asyncio.sleep(0.05)
    assert await store.reap() == [(run.id, "queued")]
    new = await store.claim()
    assert new.attempt == 2
    assert await store.heartbeat(run.id, old.attempt) == (False, False)
    assert not await store.append(run.id, [{"type": "agent_start"}], attempt=old.attempt)
    assert not await store.finish(run.id, "completed", attempt=old.attempt)
    assert await store.append(run.id, [{"type": "agent_start"}], attempt=new.attempt)
    types = [e["type"] for _, e in await store.events_after(run.id, 0)]
    assert types == ["session_id", "run_resumed", "agent_start"]


async def test_the_reaper_fails_a_run_that_keeps_dying_with_a_reason(store):
    run, _ = await _new(store)
    for _ in range(MAX_ATTEMPTS):
        await store.claim(lease_s=0)
        await asyncio.sleep(0.05)
        await store.reap()
    got = await store.get(run.id)
    assert got.status == "failed" and "stopped responding 3 times" in got.error
    events = [e for _, e in await store.events_after(run.id, 0)]
    assert events[-2]["type"] == "error" and events[-1] == {"type": "done"}


async def test_events_replay_after_any_seq_exactly_once(store):
    run, _ = await _new(store)
    claimed = await store.claim()
    for i in range(5):
        await store.append(run.id, [{"type": "agent_start", "i": i}], attempt=claimed.attempt)
    seqs = [s for s, _ in await store.events_after(run.id, 0)]
    assert seqs == [1, 2, 3, 4, 5, 6]
    assert [e["i"] for _, e in await store.events_after(run.id, 3)] == [2, 3, 4]


async def test_cancel_and_resume(store):
    queued, _ = await _new(store, sid="a")
    assert await store.request_cancel(queued.id, [{"type": "done"}]) == "cancelled"
    running, _ = await _new(store, sid="b")
    claimed = await store.claim()
    assert await store.request_cancel(running.id, []) == "running"
    assert await store.heartbeat(running.id, claimed.attempt) == (True, True)
    await store.finish(running.id, "waiting_approval", attempt=claimed.attempt)
    assert (await store.active_for_session("b")).status == "waiting_approval"
    assert await store.resume(running.id, {"answer": "Continue"})
    again = await store.claim()
    assert again.resume == {"answer": "Continue"} and not again.cancel_requested
    await store.finish(running.id, "failed", attempt=again.attempt, error="boom")
    assert (await store.get(running.id)).resume is None   # an answer is used by one run only
    assert await store.resume(running.id, {"retry": True}, from_status=("failed",))
    assert (await store.claim()).attempt == 1             # a retry gets a fresh crash budget


async def test_shutdown_hands_a_run_back_without_spending_an_attempt(store):
    run, _ = await _new(store)
    claimed = await store.claim()
    await store.requeue(run.id, claimed.attempt)
    assert (await store.claim()).attempt == 1
