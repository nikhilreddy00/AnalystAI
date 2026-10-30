import pytest

from llm.client import RecordedClient, set_client


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
