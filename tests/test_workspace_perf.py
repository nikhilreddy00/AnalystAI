"""Phase 5.6b Done-when: the sidebar lists 200 threads with p95 < 300 ms, on Postgres, with each
thread's stored session ~1 MB (listing must never read or parse it)."""
import json
import os
import statistics
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text

from api.config import settings
from api.rate_limit import limiter
from orchestrator.runner import open_deps
from tests.test_durable_runs import _close
from tests.test_workspace_api import _client

PG = os.environ.get("TEST_PG_URL")
THREADS, CALLS, BUDGET_MS = 200, 50, 300


async def test_the_sidebar_lists_200_threads_fast(monkeypatch):
    if not PG:
        pytest.skip("TEST_PG_URL not set")
    monkeypatch.setattr(settings, "encryption_key", Fernet.generate_key().decode())
    monkeypatch.setattr(limiter, "enabled", False)
    deps = await open_deps("production", PG, vector=False)
    try:
        async with deps.run_store._engine.begin() as conn:
            await conn.execute(text("TRUNCATE analysis_sessions, runs, run_events"))
            blob = json.dumps({"session_id": "x", "pad": "x" * 1_000_000})
            now = datetime.now(timezone.utc)
            for i in range(THREADS):
                sid = str(uuid.uuid4())
                at = (now - timedelta(hours=i)).replace(tzinfo=None)
                await conn.execute(text(
                    "INSERT INTO analysis_sessions (session_id, user_id, data, dataset_name, "
                    "status, created_at, updated_at, title, questions, pinned, archived) VALUES "
                    "(:sid, 'u1', :data, 'orders', 'completed', :at, :at, :title, :q, false, "
                    "false)"), {"sid": sid, "data": blob, "at": at, "title": f"orders · q{i}",
                                "q": f"question {i}\nfollow-up {i}"})
                for k in range(2):   # two runs each: the dot shows the latest
                    await conn.execute(text(
                        "INSERT INTO runs (id, session_id, user_id, status, input, attempt, "
                        "crashes, cancel_requested, created_at, updated_at) VALUES (:id, :sid, "
                        "'u1', :s, '{}', 1, 0, false, :at, :at)"),
                        {"id": str(uuid.uuid4()), "sid": sid, "at": now + timedelta(seconds=k),
                         "s": "completed" if k else "failed"})
        async with _client(deps) as client:
            first = (await client.get("/api/sessions")).json()   # warm
            assert len(first) == THREADS and {t["run_status"] for t in first} == {"completed"}
            took = []
            for _ in range(CALLS):
                t0 = time.perf_counter()
                r = await client.get("/api/sessions")
                took.append((time.perf_counter() - t0) * 1000)
                assert r.status_code == 200
        p95 = statistics.quantiles(took, n=20)[-1]
        print(f"\nsidebar list, {THREADS} threads x ~1 MB: p50 {statistics.median(took):.1f} ms, "
              f"p95 {p95:.1f} ms")
        assert p95 < BUDGET_MS
    finally:
        async with deps.run_store._engine.begin() as conn:
            await conn.execute(text("TRUNCATE analysis_sessions, runs, run_events"))
        await _close(deps)
