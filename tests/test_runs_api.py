"""Phase 5.6a API: POST /chat stores a run, a worker executes it, GET /runs/{id}/events replays it.
The worker runs in-process on the recorded e2e-01 overview (tests/fixtures/runs), at $0."""
import asyncio
import json
import shutil

import httpx
import pytest
from fastapi import FastAPI

from api.auth import UserProfile, get_current_user
from api.rate_limit import limiter
from api.routes import chat, runs
from connectors.csv_connector import user_upload_dir
from orchestrator.runner import in_memory_deps, work
from tests.test_durable_runs import replay  # noqa: F401  (the recorded-run fixture)


def _app(deps, uid="u1") -> FastAPI:
    app = FastAPI()
    app.state.limiter = limiter
    app.state.deps, app.state.run_store = deps, deps.run_store
    app.state.session_store, app.state.vector_store = deps.session_store, None
    app.include_router(chat.router, prefix="/chat")
    app.include_router(runs.router, prefix="/runs")
    app.dependency_overrides[get_current_user] = lambda: UserProfile(uid=uid)
    return app


@pytest.fixture
async def api(replay):  # noqa: F811
    path, _ = replay
    mine = user_upload_dir("u1") / "sales.csv"
    mine.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(path, mine)
    deps = in_memory_deps()
    limiter.reset()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_app(deps)),
                                 base_url="http://t") as client:
        yield client, deps, {"type": "csv", "file_path": str(mine)}


async def _events(client, run_id, last=None) -> list[tuple[int, dict]]:
    headers = {"Last-Event-ID": str(last)} if last is not None else {}
    out, seq = [], None
    async with client.stream("GET", f"/runs/{run_id}/events", headers=headers) as r:
        assert r.status_code == 200
        async for line in r.aiter_lines():
            if line.startswith("id: "):
                seq = int(line[4:])
            elif line.startswith("data: "):
                out.append((seq, json.loads(line[6:])))
    return out


async def _worker(deps):
    stop = asyncio.Event()
    task = asyncio.create_task(work(deps, stop, poll_s=0.01, heartbeat_s=0.2))
    return stop, task


async def test_a_run_survives_the_request_and_replays_from_any_event(api):
    client, deps, cfg = api
    body = {"message": "Give me an overview of this dataset.", "connector_config": cfg,
            "client_request_id": "req-1"}
    first = (await client.post("/chat", json=body)).json()
    again = (await client.post("/chat", json=body)).json()   # a double submit
    assert first["status"] == "queued" and again["run_id"] == first["run_id"]
    run_id, sid = first["run_id"], first["session_id"]

    stop, task = await _worker(deps)
    try:
        part1 = await _events(client, run_id)       # ends when the run stops at the gate
        assert part1[0][1] == {"type": "session_id", "value": sid, "message": body["message"],
                               "is_approval": False}
        assert part1[-1][1]["type"] == "approval_required"
        assert (await client.get(f"/runs/{run_id}")).json()["status"] == "waiting_approval"

        answer = {"message": "Continue with imputation", "session_id": sid, "is_approval": True,
                  "approval_context": "high_null"}
        assert (await client.post("/chat", json=answer)).json()["run_id"] == run_id
        part2 = await _events(client, run_id, last=part1[-1][0])
        assert part2[0][1]["type"] == "approval_answered" and part2[-1][1] == {"type": "done"}
        assert (await client.get(f"/runs/{run_id}")).json()["status"] == "completed"

        everything = await _events(client, run_id)  # a refreshed tab replays it all, once
        assert everything == part1 + part2
        assert [s for s, _ in everything] == list(range(1, len(everything) + 1))
        middle = everything[len(everything) // 2][0]
        assert await _events(client, run_id, last=middle) == everything[middle:]
    finally:
        stop.set()
        await task


async def test_one_run_at_a_time_cancel_and_retry(api):
    client, deps, cfg = api
    first = (await client.post("/chat", json={"message": "q", "connector_config": cfg})).json()
    busy = await client.post("/chat", json={"message": "q2", "session_id": first["session_id"]})
    assert busy.status_code == 409
    late = await client.post("/chat", json={"message": "Continue", "is_approval": True,
                                            "approval_context": "high_null",
                                            "session_id": first["session_id"]})
    assert late.status_code == 200 and late.json()["run_id"] == first["run_id"]   # no-op

    cancelled = (await client.post(f"/runs/{first['run_id']}/cancel")).json()
    assert cancelled["status"] == "cancelled"
    events = [e for _, e in await _events(client, first["run_id"])]
    assert events[-2]["message"] == "Cancelled at your request." and events[-1]["type"] == "done"

    nothing = await client.post("/chat", json={"message": "Continue", "is_approval": True,
                                               "approval_context": "high_null",
                                               "session_id": first["session_id"]})
    assert nothing.status_code == 409   # no gate is waiting any more

    retried = (await client.post(f"/runs/{first['run_id']}/retry")).json()
    assert retried["status"] == "queued"
    assert (await client.post(f"/runs/{first['run_id']}/retry")).status_code == 409


async def test_runs_are_private(api, replay):  # noqa: F811
    client, deps, cfg = api
    run = (await client.post("/chat", json={"message": "q", "connector_config": cfg})).json()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_app(deps, uid="u2")),
                                 base_url="http://t") as other:
        for r in (await other.get(f"/runs/{run['run_id']}"),
                  await other.get(f"/runs/{run['run_id']}/events"),
                  await other.post(f"/runs/{run['run_id']}/cancel"),
                  await other.post(f"/runs/{run['run_id']}/retry")):
            assert r.status_code == 404
