import os

import pytest

from llm.client import RecordedClient, set_client


def _worker_database() -> None:
    """Each pytest-xdist worker gets its own scratch database (<db>_gw0, <db>_gw1 …), created on
    first use, so the Postgres tests run in parallel without sharing schemas (CI ran the whole
    suite serially because of them: 22 min). Set before any test module reads TEST_PG_URL."""
    url, worker = os.environ.get("TEST_PG_URL"), os.environ.get("PYTEST_XDIST_WORKER")
    if not url or not worker:
        return
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url
    base = make_url(url)
    name = f"{base.database}_{worker}"
    admin = create_engine(base.set(drivername="postgresql+psycopg", database="postgres"),
                          isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        if not c.execute(text("select 1 from pg_database where datname = :n"), {"n": name}).first():
            c.execute(text(f'CREATE DATABASE "{name}"'))
    admin.dispose()
    os.environ["TEST_PG_URL"] = base.set(database=name).render_as_string(hide_password=False)


_worker_database()


@pytest.fixture(autouse=True)
def recorded_llm(tmp_path, monkeypatch):
    """Every test runs against an empty RecordedClient — a real model call is impossible.
    Tests that need LLM output append to `recorded_llm.responses`."""
    from api.config import settings
    monkeypatch.setattr(settings, "trace_dir", str(tmp_path / "traces"))
    client = RecordedClient()
    set_client(client)
    yield client
    set_client(None)


@pytest.fixture(autouse=True)
def run_env():
    """Every test gets its own run env (event sink + in-memory frame), as the worker sets one
    per run. Tests read `run_env.events` and put a frame in `run_env.frame`."""
    import asyncio

    from orchestrator.graph import RunEnv, run_env as var
    env = RunEnv(events=asyncio.Queue())
    token = var.set(env)
    yield env
    var.reset(token)


@pytest.fixture(autouse=True)
def encryption_key(monkeypatch):
    """A fixed key, as production always has one (the API and the worker refuse to start
    without). PHI pseudonyms refuse an empty key; no test may pin a pseudonym made with it."""
    from api.config import settings
    if not settings.encryption_key:
        monkeypatch.setattr(settings, "encryption_key", "dGVzdC1maXhlZC1rZXktZm9yLXBzZXVkb255bXMtMTI=")


@pytest.fixture(autouse=True)
def today(monkeypatch):
    """Today is the generators' extract date (agents.utils.today): an HR exit dated after it
    is not yet one, so a test's answer never changes with the calendar."""
    monkeypatch.setenv("ANALYSTAI_TODAY", "2026-11-01")
