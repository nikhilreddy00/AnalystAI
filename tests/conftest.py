import os

import pytest

from llm.client import RecordedClient, set_client


_MSSQL_ROLES = {"TEST_MSSQL_READER_URL": "db_datareader", "TEST_MSSQL_WRITER_URL": "db_datawriter"}
# Each way a login can write, and its undo (test_every_way_a_sql_server_login_can_write_is_refused).
# The reader's are undone here, at every start: a run killed between a grant and its revoke (the
# mutation gate's timeout) left the reader owning a schema, and every later run failed (5.8b hardening)
MSSQL_CAN_WRITE = [
    ("GRANT INSERT ON dbo.cw_t TO aai_reader", "REVOKE INSERT ON dbo.cw_t FROM aai_reader"),
    ("GRANT UPDATE ON SCHEMA::dbo TO aai_reader", "REVOKE UPDATE ON SCHEMA::dbo FROM aai_reader"),
    ("CREATE ROLE cw_role; GRANT DELETE ON dbo.cw_t TO cw_role; ALTER ROLE cw_role ADD MEMBER aai_reader",
     "ALTER ROLE cw_role DROP MEMBER aai_reader; DROP ROLE cw_role"),
    ("ALTER ROLE db_owner ADD MEMBER aai_reader", "ALTER ROLE db_owner DROP MEMBER aai_reader"),
    ("EXEC('CREATE SCHEMA cw_owned AUTHORIZATION aai_reader')", "DROP SCHEMA cw_owned"),
    ("GRANT IMPERSONATE ON USER::aai_writer TO aai_reader",
     "REVOKE IMPERSONATE ON USER::aai_writer FROM aai_reader"),
    ("GRANT EXECUTE TO aai_reader", "REVOKE EXECUTE FROM aai_reader"),
    # audit pass 2 (security, proven: it granted itself INSERT and wrote; test analyst): the
    # deny list missed these, the allow list refuses anything not a read
    ("ALTER ROLE db_securityadmin ADD MEMBER aai_reader",
     "ALTER ROLE db_securityadmin DROP MEMBER aai_reader"),
    ("ALTER ROLE db_ddladmin ADD MEMBER aai_reader", "ALTER ROLE db_ddladmin DROP MEMBER aai_reader"),
    ("GRANT ALTER ANY ROLE TO aai_reader", "REVOKE ALTER ANY ROLE FROM aai_reader"),
    ("GRANT CREATE SCHEMA TO aai_reader", "REVOKE CREATE SCHEMA FROM aai_reader"),
    ("ALTER AUTHORIZATION ON OBJECT::dbo.cw_t TO aai_reader",
     "ALTER AUTHORIZATION ON OBJECT::dbo.cw_t TO SCHEMA OWNER"),
]


# The tests that measure a read "as the process" run it in a child and print `peak_mb()`, the
# child's own peak. Never ru_maxrss on Linux: it is kept across exec, so a child of a large xdist
# worker starts at the worker's peak (CI: a 0.3 GB read said as 1.6 GB, and a before/after
# delta could read 0 for a real 1 GB; post-merge audit, 5.8b hardening). VmHWM is the new
# process's own; macOS has no /proc and keeps no peak across exec.
PEAK_MB = """
def peak_mb():
    try:
        with open('/proc/self/status') as f:
            return next(int(x.split()[1]) for x in f if x.startswith('VmHWM:')) / 1024
    except OSError:
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2**20
"""


def _mssql_logins(c, text, name: str) -> None:
    """SQL Server (5.8b): the admin URL writes the fixtures; the connector reads with a login that
    can't write (the can-write check refuses any other), and the refusal test needs one that can.
    Both logins are created here from their URLs, so CI and scripts/test_dbs.sh only set URLs."""
    from sqlalchemy.engine import make_url
    for var, role in _MSSQL_ROLES.items():
        if not (url := os.environ.get(var)):
            continue
        login = make_url(url)
        user, pw = login.username, (login.password or "").replace("'", "''")
        # a login is the server's, and every xdist worker of a fresh CI service gets here at once:
        # the one that loses the race to create it carries on (test analyst, audit pass 2)
        c.execute(text(f"BEGIN TRY IF SUSER_ID('{user}') IS NULL CREATE LOGIN [{user}] WITH "
                       f"PASSWORD = '{pw}', CHECK_POLICY = OFF END TRY BEGIN CATCH "
                       "IF ERROR_NUMBER() NOT IN (15025, 15023) THROW; END CATCH"))
        c.execute(text(f"USE [{name}]; IF USER_ID('{user}') IS NULL CREATE USER [{user}] FOR LOGIN "
                       f"[{user}]; ALTER ROLE db_datareader ADD MEMBER [{user}]; "
                       f"ALTER ROLE {role} ADD MEMBER [{user}]; USE master"))
        if var == "TEST_MSSQL_READER_URL":
            from sqlalchemy.exc import DBAPIError
            for _, undo in MSSQL_CAN_WRITE:
                try:
                    c.execute(text(f"USE [{name}]; {undo}; USE master"))
                except DBAPIError:   # nothing to undo
                    c.execute(text("USE master"))
        os.environ[var] = login.set(database=name).render_as_string(hide_password=False)


def _worker_database(var: str) -> None:
    """Each pytest-xdist worker gets its own scratch database (<db>_gw0, <db>_gw1 …), created on
    first use, so the database tests run in parallel without sharing schemas (CI ran the whole
    suite serially because of them: 22 min). Set before any test module reads the URL.
    MySQL's and MariaDB's are created utf8mb4_bin and SQL Server's Latin1_General_100_BIN2_UTF8, so
    text compares as pandas does (a default `_ci` collation would read 'Acme' and 'ACME' as one)."""
    url, worker = os.environ.get(var), os.environ.get("PYTEST_XDIST_WORKER")
    if not url:
        return
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url
    base = make_url(url)
    name = f"{base.database}_{worker}" if worker else base.database
    if base.get_backend_name() in ("mysql", "mariadb"):
        # URL.set(database=None) keeps the database: connect to the server's own one instead
        admin = create_engine(base.set(database="mysql"), isolation_level="AUTOCOMMIT")
        with admin.connect() as c:
            c.execute(text(f"CREATE DATABASE IF NOT EXISTS `{name}` "
                           "CHARACTER SET utf8mb4 COLLATE utf8mb4_bin"))
    elif base.get_backend_name() == "mssql":
        admin = create_engine(base.set(database="master"), isolation_level="AUTOCOMMIT")
        with admin.connect() as c:
            c.execute(text(f"IF DB_ID('{name}') IS NULL CREATE DATABASE [{name}] "
                           "COLLATE Latin1_General_100_BIN2_UTF8"))
            _mssql_logins(c, text, name)
    elif worker:
        admin = create_engine(base.set(drivername="postgresql+psycopg", database="postgres"),
                              isolation_level="AUTOCOMMIT")
        with admin.connect() as c:
            if not c.execute(text("select 1 from pg_database where datname = :n"), {"n": name}).first():
                c.execute(text(f'CREATE DATABASE "{name}"'))
    else:
        return
    admin.dispose()
    os.environ[var] = base.set(database=name).render_as_string(hide_password=False)


_worker_database("TEST_PG_URL")
_worker_database("TEST_MYSQL_URL")
_worker_database("TEST_MARIADB_URL")
_worker_database("TEST_MSSQL_URL")   # also points the reader and writer URLs at its database
# CI's backend job (it sets CI_SHARD) has every database: a test needing one skips locally without
# it, never in CI (audit pass 2: ~42 MySQL tests would have skipped unseen had the URL been lost).
CI_DATABASE_URLS = ("TEST_PG_URL", "TEST_MYSQL_URL", "TEST_MARIADB_URL", "TEST_MSSQL_URL",
                    *_MSSQL_ROLES)
if os.environ.get("CI") and "CI_SHARD" in os.environ:
    if missing := [v for v in CI_DATABASE_URLS if not os.environ.get(v)]:
        raise RuntimeError(f"CI's backend job must set {', '.join(missing)}")


def _hypothesis_profiles() -> None:
    """Property tests (tests/test_properties.py) run derandomized everywhere, so a local run
    finds what CI finds: `ci` extends Hypothesis' own (no database, no deadline; it loads
    itself when CI is set), `deep` is the nightly job, `mutation` the cheap one each mutant
    runs (scripts/mutation.py). HYPOTHESIS_PROFILE picks one. `deep` is random, so it keeps
    its failures in .hypothesis/deep and the next deep run replays them first (retro 5.7b-2a:
    a deep failure scrolled away and never came back)."""
    from hypothesis import HealthCheck, settings
    from hypothesis.database import DirectoryBasedExampleDatabase
    common = {"print_blob": True, "deadline": None,
              # the autouse fixtures only set constants (today, a key), never per-example state
              "suppress_health_check": [HealthCheck.function_scoped_fixture, HealthCheck.too_slow]}
    settings.register_profile("ci", settings.get_profile("ci"), max_examples=25, **common)
    settings.register_profile("dev", max_examples=25, derandomize=True, database=None, **common)
    settings.register_profile("deep", max_examples=500, derandomize=False,
                              database=DirectoryBasedExampleDatabase(".hypothesis/deep"), **common)
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
