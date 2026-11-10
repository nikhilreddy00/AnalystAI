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


def _hypothesis_profiles() -> None:
    """Property tests (tests/test_properties.py) run derandomized everywhere, so a local run
    finds what CI finds: `ci` extends Hypothesis' own (no database, no deadline; it loads
    itself when CI is set), `deep` is the nightly job, `mutation` the cheap one each mutant
    runs (scripts/mutation.py). HYPOTHESIS_PROFILE picks one."""
    from hypothesis import HealthCheck, settings
    common = {"print_blob": True, "deadline": None,
              # the autouse fixtures only set constants (today, a key), never per-example state
              "suppress_health_check": [HealthCheck.function_scoped_fixture, HealthCheck.too_slow]}
    settings.register_profile("ci", settings.get_profile("ci"), max_examples=25, **common)
    settings.register_profile("dev", max_examples=25, derandomize=True, database=None, **common)
    settings.register_profile("deep", max_examples=500, derandomize=False, database=None, **common)
    settings.register_profile("mutation", max_examples=5, derandomize=True, database=None, **common)
    default = "ci" if os.environ.get("CI") else "dev"
    settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE") or default)


_hypothesis_profiles()


def pytest_collection_modifyitems(config, items):
    """TEST_ORDER_SEED=<n>: the tests in a shuffled order (CI's nightly job): a test that passes
    only after another one created its state fails here first (retro 2026-11-06: the purge-table
    test). The same seed in every xdist worker, so they agree on the order.
    CI_SHARD=i/n (CI's backend matrix): this job runs every n-th test from the i-th. By test,
    not by file: the slow cross-domain tests are spread over many files."""
    if seed := os.environ.get("TEST_ORDER_SEED"):
        import random
        random.Random(seed).shuffle(items)
        print(f"test order shuffled, TEST_ORDER_SEED={seed}")
    if not (shard := os.environ.get("CI_SHARD")):
        return
    i, n = map(int, shard.split("/"))
    config.hook.pytest_deselected(items=[t for k, t in enumerate(items) if k % n != i])
    items[:] = items[i::n]


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
def mutant_caches():
    """Under mutmut (scripts/mutation.py) each mutant runs in a process forked after the clean
    tests: a result cached then (packs, memoised spell dates, built datasets) was computed by
    the unmutated code and would hide the mutant. Clear every cache before each test there."""
    if os.environ.get("MUTANT_UNDER_TEST"):
        import sys
        import domain_pack
        domain_pack._MEMO.clear()
        for name, mod in list(sys.modules.items()):
            if name == "domain_pack" or name.startswith(("domain_pack.", "tests.")):
                for v in list(vars(mod).values()):
                    if callable(getattr(v, "cache_clear", None)):
                        v.cache_clear()


@pytest.fixture(autouse=True)
def today(monkeypatch):
    """Today is the generators' extract date (agents.utils.today): an HR exit dated after it
    is not yet one, so a test's answer never changes with the calendar."""
    monkeypatch.setenv("ANALYSTAI_TODAY", "2026-11-01")
