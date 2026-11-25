"""Data sources: caps are disclosed, every SQL read is read-only and timed, SQLite stays out of
production, Sheets survives pandas 3, common API shapes load, and the E2E harness can feed any
file format or a database through the same graph path. Offline: no network, no keys."""
from __future__ import annotations

import json
import re
import ssl

import httpx
import pandas as pd
import pytest
from hypothesis import given
from hypothesis import strategies as st
from sqlalchemy import create_engine

from tests.conftest import MSSQL_CAN_WRITE

from agents.errors import IngestionError
from connectors.base import TRUNCATED, get_connector
from orchestrator.state import AnalysisSession, CleaningAudit, ConnectorConfig, ConnectorType


def _sqlite(tmp_path, df: pd.DataFrame, table: str = "t") -> str:
    url = f"sqlite:///{tmp_path / 'db.sqlite'}"
    df.to_sql(table, create_engine(url), index=False)
    return url


def _sql(url: str, table: str | None = "t"):
    return get_connector(ConnectorConfig(type=ConnectorType.SQL, connection_string=url,
                                         table_name=table))


# ── 1. a capped load says so, all the way to the report ────────────────────────────────────

async def test_a_capped_table_load_is_disclosed_end_to_end(tmp_path, monkeypatch):
    import connectors.sql_connector as sc
    from agents.ingestion_agent import build_contract
    from agents.insight_agent import _alerts, _analyst_view
    from orchestrator.state import AnalysisFindings
    monkeypatch.setattr(sc, "LOAD_MAX_ROWS", 3)
    conn = _sql(_sqlite(tmp_path, pd.DataFrame({"a": [1, 2, 3, 4], "b": list("wxyz")})))
    df = await conn.load()
    assert len(df) == 3
    note = next(n for n in conn.read_info["notes"] if n.startswith(TRUNCATED))
    assert "3 of 4 rows" in note
    contract = build_contract(df, ConnectorType.SQL, conn.read_info)
    assert note in contract.source_evidence.assumptions
    audit = CleaningAudit(decisions=[], rows_before=3, rows_after=3, columns_coerced=[],
                          duplicates_removed=0, outliers_flagged=[], high_null_columns=[],
                          summary="")
    s = AnalysisSession(session_id="s", data_contract=contract, cleaning_audit=audit,
                        analysis_findings=AnalysisFindings(
                            eda_summary={}, correlations={}, trends=[], segments=[], anomalies=[],
                            statistical_tests=[], code_executed="",
                            evidence=[contract.source_evidence]))
    alerts, _ = _alerts(s)
    assert any(a.description == note for a in alerts)
    assert note in _analyst_view(s, []).caveats


async def test_an_uncapped_table_load_has_no_truncation_note(tmp_path):
    conn = _sql(_sqlite(tmp_path, pd.DataFrame({"a": [1, 2]})))
    assert len(await conn.load()) == 2
    assert not any(n.startswith(TRUNCATED) for n in conn.read_info.get("notes", []))


# ── 2. the table load goes through the read-only, timed path ───────────────────────────────

# ── 3. SQLite is a development database ────────────────────────────────────────────────────

def test_sqlite_is_refused_in_production(monkeypatch):
    from api.config import settings
    monkeypatch.setattr(settings, "app_env", "production")
    with pytest.raises(IngestionError) as err:
        _sql("sqlite:///x.db")._engine()
    with pytest.raises(IngestionError):
        _sql("sqlite+pysqlite:///x.db")._engine()   # any spelling (Superset CVE-2023-39265)
    assert "SQLite" in err.value.user_message
    monkeypatch.setattr(settings, "app_env", "development")
    assert _sql("sqlite:///x.db")._engine() is not None


async def test_connectors_test_route_says_why_sqlite_is_refused(monkeypatch):
    from api.config import settings
    from api.routes.connectors import test_connection
    monkeypatch.setattr(settings, "app_env", "production")
    r = await test_connection(ConnectorConfig(type=ConnectorType.SQL,
                                              connection_string="sqlite:///x.db"), None)
    assert not r.success and "SQLite" in r.message and "ingestion:" not in r.message


# ── 3b. every dialect (ADR-0010): read-only and timed on its real database, its connection
# string allowlisted, its password never said. One test per property over every dialect row. ──

# The connector's URL per dialect (SQL Server's: the read-only login the can-write check lets
# through) and, where it differs, the admin URL that writes the fixtures (5.8b red-team B5).
_DB_ENV = {"postgresql": "TEST_PG_URL", "mysql": "TEST_MYSQL_URL", "mariadb": "TEST_MARIADB_URL",
           "mssql": "TEST_MSSQL_READER_URL"}
_ADMIN_ENV = {"mssql": "TEST_MSSQL_URL"}


def _db_urls(tmp_path) -> dict[str, str]:
    """A scratch database per dialect: SQLite always, the others where CI (or a local run,
    scripts/test_dbs.sh) names one. A dialect without one here is skipped, never passed."""
    import os
    urls = {"sqlite": f"sqlite:///{tmp_path / 'ro.sqlite'}"}
    urls |= {name: os.environ[env] for name, env in _DB_ENV.items() if os.environ.get(env)}
    return urls


def _each_dialect(tmp_path, name):
    from connectors.sql_connector import DIALECTS
    assert name in DIALECTS, f"no dialect row: {name}"
    url = _db_urls(tmp_path).get(name)
    if not url:
        pytest.skip(f"no {name} database here (CI sets one)")
    return url


DIALECT_NAMES = ["sqlite", "postgresql", "mysql", "mariadb", "mssql"]
# The database's own read-only refusal, per dialect: a write must fail for THIS reason. Any error
# passed before (audit pass 2, B3): a no-row INSERT fails on fetchmany and the rollback undoes it,
# so 5 of 6 writes passed with SQLite's read-only row emptied. SQL Server has no read-only
# transaction: its reason is the read-only login's ("…permission was denied", "…do not have
# permission").
_READ_ONLY_ERROR = {"sqlite": "readonly", "postgresql": "read-only transaction",
                    "mysql": "READ ONLY transaction", "mariadb": "READ ONLY transaction",
                    "mssql": "permission"}


def _engine_for(url):
    from connectors.sql_connector import SQLConnector
    return SQLConnector(ConnectorConfig(type=ConnectorType.SQL, connection_string=url))._engine()


def _admin(tmp_path, name):
    """The engine that writes a test's fixtures: the connector's own, except SQL Server's
    read-only login, which can't (its admin URL, as the database's owner would)."""
    import os

    from sqlalchemy import create_engine
    url = _each_dialect(tmp_path, name)
    return create_engine(os.environ[_ADMIN_ENV[name]]) if name in _ADMIN_ENV else _engine_for(url)


@pytest.mark.parametrize("name", DIALECT_NAMES)
@pytest.mark.parametrize("write", ["INSERT INTO ro_t VALUES (2)", "DROP TABLE ro_t",
                                   "DELETE FROM ro_t", "UPDATE ro_t SET a = 3",
                                   "TRUNCATE TABLE ro_t", "CREATE TABLE ro_new (a INTEGER)"])
def test_a_write_through_run_readonly_fails_on_every_dialect(tmp_path, name, write):
    """Layer 2 alone (the parse guard is skipped: run_readonly is called directly), on the real
    database: a write is refused as a read-only write, and nothing changed."""
    from sqlalchemy import inspect, text
    from sqlalchemy.exc import SQLAlchemyError

    from connectors.sql_connector import run_readonly
    if name == "sqlite" and write.startswith("TRUNCATE"):
        pytest.skip("SQLite has no TRUNCATE (it doesn't parse: no read-only proof)")
    engine = _engine_for(_each_dialect(tmp_path, name))
    with _admin(tmp_path, name).begin() as c:
        c.execute(text("DROP TABLE IF EXISTS ro_t"))
        c.execute(text("DROP TABLE IF EXISTS ro_new"))
        c.execute(text("CREATE TABLE ro_t (a INTEGER)"))
        c.execute(text("INSERT INTO ro_t VALUES (1)"))
    # Postgres reads a question through a server-side cursor (5.8b hardening: streamed), and a
    # cursor holds only a query: the write is refused there first, by name, before the read-only
    # transaction; that layer is proven alone by the FOR UPDATE case below
    reason = rf"CURSOR FOR {write.split()[0]}" if name == "postgresql" else _READ_ONLY_ERROR[name]
    with pytest.raises(SQLAlchemyError, match=reason):
        run_readonly(engine, write)
    with _admin(tmp_path, name).connect() as c:
        assert c.execute(text("SELECT a FROM ro_t")).fetchall() == [(1,)]
    assert "ro_new" not in inspect(engine).get_table_names()


def test_postgres_read_only_transaction_refuses_a_query_that_locks(tmp_path):
    """A query a cursor takes that still writes (row locks): the read-only transaction's own
    refusal, the layer the cursor now stands in front of."""
    from sqlalchemy import text
    from sqlalchemy.exc import SQLAlchemyError

    from connectors.sql_connector import run_readonly
    engine = _engine_for(_each_dialect(tmp_path, "postgresql"))
    with _admin(tmp_path, "postgresql").begin() as c:
        c.execute(text("DROP TABLE IF EXISTS ro_t"))
        c.execute(text("CREATE TABLE ro_t (a INTEGER)"))
    with pytest.raises(SQLAlchemyError, match="read-only transaction"):
        run_readonly(engine, "SELECT a FROM ro_t FOR UPDATE")


# what makes each row read-only, removed by the plant below: the read-only transaction (SQLite's
# query_only); SQL Server's can-write check (its read-only login is then swapped for one that can)
_READ_ONLY_STATEMENT = re.compile(r"READ ONLY|query_only|CAN_WRITE", re.I)


@pytest.mark.parametrize("name", DIALECT_NAMES)
def test_without_its_read_only_statements_no_write_is_refused_as_read_only(tmp_path, name,
                                                                          monkeypatch):
    """The plant, kept (retro 5.8a): mutation testing can't mutate a data row, so the write test
    above was never shown to depend on `begin`. Each dialect row's read-only statements removed,
    the database no longer refuses the write as read-only (SQL Server: a login that can write,
    no longer refused by the session's check, runs its write)."""
    import dataclasses
    import os

    from sqlalchemy import text
    from sqlalchemy.exc import SQLAlchemyError

    import connectors.sql_connector as sc
    url = _each_dialect(tmp_path, name)
    if name == "mssql":
        url = os.environ["TEST_MSSQL_WRITER_URL"]
    engine = _engine_for(url)
    with _admin(tmp_path, name).begin() as c:
        c.execute(text("DROP TABLE IF EXISTS ro_t"))
        c.execute(text("CREATE TABLE ro_t (a INTEGER)"))
    row = sc.DIALECTS[name]
    kept = tuple(s for s in row.begin if not _READ_ONLY_STATEMENT.search(s))
    assert kept != row.begin   # the row has read-only statements to remove
    monkeypatch.setitem(sc.DIALECTS, name, dataclasses.replace(row, begin=kept, end=()))
    # the write runs; reading its (absent) rows then fails: anything but the read-only refusal
    with pytest.raises((SQLAlchemyError, AttributeError)) as err:
        sc.run_readonly(engine, "DELETE FROM ro_t")
    assert _READ_ONLY_ERROR[name] not in str(err.value)


@pytest.mark.parametrize("name,slow", [("postgresql", "SELECT pg_sleep(20)"),
                                       ("mysql", "SELECT SLEEP(20)"),
                                       ("mariadb", "SELECT SLEEP(20)"),
                                       ("mssql", "WAITFOR DELAY '00:00:20'")])
def test_a_slow_query_stops_at_the_timeout(tmp_path, name, slow):
    """Layer 3 on the real database: Postgres cancels the statement, MariaDB interrupts it
    (1969), SQL Server's driver gives up at its query timeout; MySQL's SLEEP cut by
    max_execution_time returns 1 instead of 0 (no error). Any other error fails the test."""
    import time

    from sqlalchemy.exc import OperationalError

    from connectors.sql_connector import run_readonly
    engine = _engine_for(_each_dialect(tmp_path, name))
    start = time.monotonic()
    if name == "mysql":
        assert run_readonly(engine, slow, timeout_s=1).rows == [(1,)]
    else:
        reason = {"postgresql": "statement timeout", "mariadb": "max_statement_time exceeded",
                  "mssql": "timeout expired"}[name]
        with pytest.raises(OperationalError, match=f"(?i){reason}"):
            run_readonly(engine, slow, timeout_s=1)
    assert time.monotonic() - start < 5


def test_a_mysql_string_pointed_at_a_mariadb_server_reads_with_the_mariadb_row(tmp_path):
    """5.8b: the row follows the server, not the URL's word. MariaDB has no max_execution_time:
    MySQL's row there failed every session (1193)."""
    from connectors.sql_connector import run_readonly
    url = _each_dialect(tmp_path, "mariadb").replace("mariadb+pymysql://", "mysql+pymysql://")
    assert run_readonly(_engine_for(url), "SELECT 1").rows == [(1,)]


@pytest.mark.parametrize("url", ["mssql+pymssql://u:p@h/db", "mssql+pytds://u:p@h/db",
                                 "mariadb+mariadbconnector://u:p@h/db", "oracle+oracledb://u:p@h/db",
                                 "mysql+mysqldb://u:p@h/db", "sqlite+pysqlite:///x.db",
                                 "h2:./x", "postgresql+pg8000://u:p@h/db", "not a url"])
def test_an_engine_or_driver_without_a_dialect_row_is_refused(url):
    """Fail closed: an engine (or a driver of a known engine) the dialect table doesn't name is
    refused before any connection (Superset CVE-2023-39265 passed `sqlite+pysqlite://` by a
    `sqlite://` blocklist)."""
    with pytest.raises(IngestionError):
        _sql(url)._engine()


def test_run_readonly_refuses_an_engine_it_has_no_row_for():
    """Before any connection: an engine of a kind without a row (its driver need not exist)."""
    from connectors.sql_connector import run_readonly
    engine = type("Engine", (), {"dialect": type("D", (), {"name": "oracle"})(),
                                 "connect": lambda self: pytest.fail("connected")})()
    with pytest.raises(IngestionError):
        run_readonly(engine, "SELECT 1")


@pytest.mark.parametrize("url", [
    "mysql+pymysql://u:p@h/db?init_command=DROP%20TABLE%20t", "mysql+pymysql://u:p@h/db?local_infile=1",
    "mysql+pymysql://u:p@h/db?client_flag=65536", "mysql+pymysql://u:p@h/db?unix_socket=/tmp/x",
    "mysql+pymysql://u:p@h/db?read_default_file=/etc/my.cnf", "mysql+pymysql://u:p@h/db?ssl_key=/k",
    "postgresql://u:p@h/db?host=/var/run/postgresql", "postgresql://u:p@h/db?passfile=/x",
    "postgresql://u:p@h/db?service=x", "postgresql://u:p@h/db?sslkey=/k",
    "postgresql://u:p@h/db?options=-c%20default_transaction_read_only%3Doff"])
def test_a_connection_string_key_outside_the_allowlist_is_refused(url):
    """Driver options in the URL reach the driver (PyMySQL runs init_command on connect)."""
    with pytest.raises(IngestionError) as err:
        _sql(url)._engine()
    assert "p@h" not in err.value.user_message


@pytest.mark.parametrize("url", [
    "postgresql://u:p@ep-x.us-east-2.aws.neon.tech/db?sslmode=require&channel_binding=require",
    "postgres://u:p@db.abc.supabase.co:5432/postgres", "postgresql+asyncpg://u:p@h/db",
    "postgresql+psycopg://u:p@h/db?sslmode=verify-full&connect_timeout=5",
    "mysql://u:p@h/db", "mysql+pymysql://u:p@h:3306/db?charset=utf8mb4"])
def test_the_connection_strings_users_copy_are_accepted(url):
    """Neon's and Supabase's copied strings, stored live configs (`postgres://`, `+asyncpg`)."""
    assert _sql(url)._engine() is not None


@pytest.mark.parametrize("url", ["mysql+pymysql://analyst:p@ss:w0rd@db.example.com/x",
                                 "postgresql://analyst:p@ss:w0rd@db.example.com/x",
                                 "postgresql://analyst:s3cr%40t-pw@127.0.0.1:1/x"])
async def test_a_password_is_never_in_a_message(url):
    """A malformed string (a raw '@' / ':' in the password) or a failed connect: neither the
    route's message (the one place a connection test reaches the user) holds the password."""
    from api.routes.connectors import test_connection
    config = ConnectorConfig(type=ConnectorType.SQL, connection_string=url)
    route = await test_connection(config, None)
    assert not route.success
    assert "w0rd" not in route.message and "s3cr" not in route.message


@pytest.mark.parametrize("host,refused", [("127.0.0.1", True), ("169.254.169.254", True),
                                          ("appdb.internal", True), ("10.0.0.5", False),
                                          ("93.184.216.34", False), ("mapped.example", True),
                                          ("appdb-other-port.example", True)])
def test_in_production_a_database_host_is_never_the_app_itself_or_the_cloud_metadata(
        monkeypatch, host, refused):
    """A user's database may sit on the company's LAN (self-hosted: 10.x is the usual case), but
    never on the app's own machine, the cloud metadata address, or the app's own store (Superset
    CVE-2023-39265 read its own metadata database). Development and CI use localhost."""
    import socket

    from api.config import settings
    ips = {"appdb.internal": "10.1.2.3", "appdb-other-port.example": "10.1.2.3",
           "mapped.example": "::ffff:127.0.0.1"}   # IPv4-mapped IPv6 loopback
    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda h, *a, **k: [(0, 0, 0, "", (ips.get(h, h), 0))])
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "database_url", "postgresql+asyncpg://a:b@appdb.internal:5432/x")
    port = 6543 if host == "appdb-other-port.example" else 5432   # the app's store host, any port
    url = f"postgresql://u:p@{host}:{port}/db"
    if refused:
        with pytest.raises(IngestionError):
            _sql(url)._engine()
    else:
        assert _sql(url)._engine() is not None


def test_in_production_an_unresolvable_app_store_refuses_rather_than_skips_the_check(monkeypatch):
    """Fail closed (security review): if the app's own store host doesn't resolve, the check
    can't tell the user's host from it, so the connection is refused, and the message says it
    is this server's problem, not the user's host."""
    import socket

    from api.config import settings
    monkeypatch.setattr(socket, "getaddrinfo", lambda h, *a, **k: (_ for _ in ()).throw(
        socket.gaierror("down")) if h == "appdb.internal" else [(0, 0, 0, "", ("10.0.0.5", 0))])
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "database_url", "postgresql+asyncpg://a:b@appdb.internal:5432/x")
    with pytest.raises(IngestionError) as err:
        _sql("postgresql://u:p@db.lan:5432/db")._engine()
    assert "db.lan" not in err.value.user_message and "server" in err.value.user_message


@pytest.mark.parametrize(("scheme", "pinned"), [
    # libpq connects to hostaddr and verifies TLS against host
    ("postgresql", lambda u: (u.query["hostaddr"], u.host)),
    # PyMySQL: do_connect dials the address, the name stays for TLS
    ("mysql+pymysql", lambda u: (u.query["_analystai_checked_address"], u.host)),
    ("mariadb", lambda u: (u.query["_analystai_checked_address"], u.host)),
    # ODBC: Server is the address, the certificate is checked against the name
    ("mssql", lambda u: (u.host, u.query["HostNameInCertificate"])),
])
def test_each_pin_keeps_the_name_for_tls(monkeypatch, scheme, pinned):
    """5.8b audit (test analyst): the pin test only found the address somewhere in the URL; a
    pin that replaced the name by the address would verify TLS against the address."""
    import socket

    from api.config import settings
    monkeypatch.setattr(socket, "getaddrinfo", lambda h, *a, **k: [(0, 0, 0, "", (
        "93.184.216.34" if h == "db.example.com" else "10.9.9.9", 0))])
    monkeypatch.setattr(settings, "app_env", "production")
    url = _sql(f"{scheme}://u:p@db.example.com:5432/db")._engine().url
    assert pinned(url) == ("93.184.216.34", "db.example.com")


@pytest.mark.parametrize("scheme", ["postgresql", "mysql+pymysql", "mariadb", "mssql"])
def test_in_production_the_connection_goes_to_the_address_that_was_checked(monkeypatch, scheme):
    """DNS rebinding: a name that resolves to a public address when checked and to loopback
    when the driver resolves it again. The driver is handed the checked address."""
    import socket

    from api.config import settings
    answers = iter(["93.184.216.34"] + ["127.0.0.1"] * 10)
    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda h, *a, **k: [(0, 0, 0, "", (next(answers) if h == "rebind.example"
                                                           else "10.9.9.9", 0))])
    monkeypatch.setattr(settings, "app_env", "production")
    engine = _sql(f"{scheme}://u:p@rebind.example:5432/db")._engine()
    # libpq's hostaddr, MySQL's do_connect address (the name kept for TLS), SQL Server's host
    assert "93.184.216.34" in (*engine.url.query.values(), engine.url.host)


def test_a_question_result_takes_the_same_load_rule_as_a_table_load():
    """ADR-0010 "values are normalised once": a question's rows (QueryResult.frame, the SQL
    agent's path) get the rule a table load does: text dates parse, '' is a blank. A TINYINT(1)
    that holds a 2 is a number, not a yes/no with the 2 lost."""
    from connectors.sql_connector import QueryResult
    df = QueryResult(["day", "note", "flag", "level"],
                     [("2026-01-05", "", 1, 2), ("2026-02-01", "x", 0, 0)], False,
                     (None, None, "bool", "bool")).frame()
    assert str(df["day"].dtype).startswith("datetime64") and df["note"].isna().tolist() == [True, False]
    assert df["flag"].tolist() == [True, False] and df["level"].tolist() == [2, 0]


@pytest.mark.parametrize("name", DIALECT_NAMES)
async def test_a_named_table_load_asks_the_database_for_at_most_the_cap(tmp_path, monkeypatch,
                                                                         name):
    """Drivers buffer a whole result before fetchmany: the load's SQL bounds the rows itself, in
    the engine's own words (SQL Server has no LIMIT: TOP), through run_readonly, on the real
    database."""
    from sqlalchemy import text

    import connectors.sql_connector as sc
    url = _each_dialect(tmp_path, name)
    with _admin(tmp_path, name).begin() as c:
        c.execute(text("DROP TABLE IF EXISTS capped"))
        c.execute(text("CREATE TABLE capped (a INTEGER)"))
        c.execute(text("INSERT INTO capped VALUES (1)"))
    seen = []
    real = sc.run_readonly
    monkeypatch.setattr(sc, "run_readonly", lambda e, sql, *a, **k: seen.append(sql) or real(
        e, sql, *a, **k))
    df = await _sql(url, "capped").load()
    cap = f"TOP {sc.LOAD_MAX_ROWS + 1}" if name == "mssql" else f"LIMIT {sc.LOAD_MAX_ROWS + 1}"
    assert df["a"].tolist() == [1] and cap in seen[0] and "ORDER BY" not in seen[0]   # no key
    # SQL Server: a serial scan, so a keyless table reads in its stored order under load too
    assert ("MAXDOP 1" in seen[0]) == (name == "mssql")


# ── 4. Sheets on pandas 3 ──────────────────────────────────────────────────────────────────

async def test_sheets_load_converts_numbers_without_crashing(monkeypatch):
    from connectors.sheets_connector import SheetsConnector

    class Sheet:
        sheet1 = property(lambda self: self)

        def get_all_values(self):
            return [["region", "sales", "units", "zip"], ["N", "1,200", "3", "02134"],
                    ["S", "800", "4", "02139"]]

    class Client:
        def open_by_key(self, key):
            return Sheet()

    conn = SheetsConnector(ConnectorConfig(type=ConnectorType.SHEETS, spreadsheet_id="x"))
    monkeypatch.setattr(conn, "_client", lambda: Client())
    df = await conn.load()
    assert pd.api.types.is_numeric_dtype(df["units"])
    assert df["region"].tolist() == ["N", "S"] and df["zip"].tolist() == ["02134", "02139"]


# ── 5. API shapes, header name, cap ────────────────────────────────────────────────────────

@pytest.fixture
def api(monkeypatch):
    """Serve `pages` (url → JSON) under the production guarded client (tests/fake_network.py):
    every host resolves to one public address."""
    from tests.fake_network import serve
    pages: dict[str, object] = {}
    headers: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        headers.append(dict(request.headers))
        return httpx.Response(200, json=pages[str(request.url)])
    serve(monkeypatch, handler)
    return pages, headers


async def _api_load(url="https://api.example.com/x", **cfg):
    conn = get_connector(ConnectorConfig(type=ConnectorType.API, url=url, **cfg))
    return await conn.load(), conn


@pytest.mark.parametrize("body", [
    {"orders": [{"id": 1}, {"id": 2}], "count": 2},
    {"data": {"items": [{"id": 1}, {"id": 2}], "total": 2}},
    [{"id": 1}, {"id": 2}],
])
async def test_common_api_shapes_load(api, body):
    api[0]["https://api.example.com/x"] = body
    df, _ = await _api_load()
    assert df["id"].tolist() == [1, 2]


async def test_api_key_header_name_is_configurable(api):
    api[0]["https://api.example.com/x"] = [{"id": 1}]
    await _api_load(auth_type="api_key", auth_value="k")
    await _api_load(auth_type="api_key", auth_value="k", api_key_header="Authorization-Token")
    assert api[1][0]["x-api-key"] == "k" and api[1][1]["authorization-token"] == "k"


async def test_api_page_cap_is_disclosed(api, monkeypatch):
    import connectors.api_connector as ac
    monkeypatch.setattr(ac, "MAX_PAGES", 2)
    for i in range(3):
        api[0][f"https://api.example.com/{i}"] = {
            "data": [{"id": i}], "next": f"https://api.example.com/{i + 1}"}
    df, conn = await _api_load(url="https://api.example.com/0")
    assert len(df) == 2
    assert any(n.startswith(TRUNCATED) for n in conn.read_info["notes"])


# ── 6. upload types ────────────────────────────────────────────────────────────────────────

def test_jsonl_is_read_and_xls_is_refused_clearly():
    from connectors.csv_connector import _read_bytes
    df, _ = _read_bytes(b'{"a": 1}\n{"a": 2}\n', ".jsonl")
    assert df["a"].tolist() == [1, 2]
    with pytest.raises(IngestionError, match="xls"):
        _read_bytes(b"x", ".xls")


def test_upload_allowlist_takes_jsonl_not_xls():
    from api.routes.connectors import UPLOAD_SUFFIXES
    assert ".jsonl" in UPLOAD_SUFFIXES and ".xls" not in UPLOAD_SUFFIXES


# ── 7. a patient database's earlier names stay guarded after a switch ─────────────────────

def test_a_phi_sql_switch_keeps_guarding_the_old_patient_names(tmp_path):
    from agents.ingestion_agent import build_contract, prepare_frame
    from api.routes.chat import _last_turn_pii
    from evals import datagen
    df, _ = datagen.make("encounters", 1)
    url = _sqlite(tmp_path, df, "encounters")
    _, c = prepare_frame(df, build_contract(df, ConnectorType.SQL, {}), ConnectorType.SQL)
    assert c.phi
    s = AnalysisSession(session_id="s", cleaned_data_path="x", data_contract=c,
                        connector_config=ConnectorConfig(type=ConnectorType.SQL,
                                                         connection_string=url,
                                                         table_name="encounters"))
    assert df["patient_name"].iloc[0] in _last_turn_pii(s)


# ── 8. the E2E harness feeds any source through the same path ─────────────────────────────

@pytest.mark.parametrize("source", ["csv", "xlsx", "json", "parquet", "sqlite"])
async def test_e2e_harness_writes_each_source(source, tmp_path, monkeypatch):
    import connectors.csv_connector as csvc
    from evals import run
    monkeypatch.setattr(run, "UPLOADS", tmp_path)
    monkeypatch.setattr(csvc, "_UPLOADS_DIR", tmp_path.resolve())
    case = {"dataset": {"name": "sales", "seed": 1}, "source": source}
    df, _, csv = run.dataset(case)
    cfg = run.source_config(case, df, csv)
    assert cfg.type == (ConnectorType.SQL if source == "sqlite" else ConnectorType.CSV)
    loaded = await get_connector(cfg).load()
    want = (await get_connector(run.source_config({**case, "source": "csv"}, df, csv)).load())

    def text(f):
        return f.astype(str).replace({"NaT": "nan", "None": "nan", "<NA>": "nan"})
    assert list(loaded.columns) == list(want.columns) and len(loaded) == len(want)
    for c in want.columns:
        a, b = loaded[c], want[c]
        if pd.api.types.is_datetime64_any_dtype(b) or pd.api.types.is_datetime64_any_dtype(a):
            a, b = pd.to_datetime(a), pd.to_datetime(b)
            assert (a.dt.tz_localize(None) if a.dt.tz else a).equals(
                b.dt.tz_localize(None) if b.dt.tz else b), c
        elif pd.api.types.is_numeric_dtype(b):
            assert pd.to_numeric(a).astype(float).round(6).equals(b.astype(float).round(6)), c
        else:
            assert text(a).tolist() == text(b).tolist(), c
    json.dumps(cfg.model_dump(mode="json"))


# ── S4 (Domain 6): a table in another schema loads by its qualified name ───────────────────

async def test_a_table_in_another_schema_loads_by_its_qualified_name():
    """HR minor: the load checked names against the default schema only, so `hr_live.roster`
    was refused. The names offered are the inspector's own (no injection: a name must be listed),
    each part quoted; an unqualified name keeps meaning the default schema."""
    import os

    from sqlalchemy import text
    url = os.environ.get("TEST_PG_URL")
    if not url:
        pytest.skip("TEST_PG_URL not set")
    eng = create_engine(url.replace("+asyncpg", "+psycopg"))
    with eng.begin() as c:
        c.execute(text('DROP SCHEMA IF EXISTS "s4 live" CASCADE'))
        c.execute(text('CREATE SCHEMA "s4 live"'))
        c.execute(text('CREATE TABLE "s4 live".mrr (customer_id text, mrr float)'))
        c.execute(text('''INSERT INTO "s4 live".mrr VALUES ('C1', 10.5), ('C2', 20)'''))
        c.execute(text('DROP TABLE IF EXISTS public.mrr'))
        c.execute(text('CREATE TABLE public.mrr (only_public int)'))
    try:
        df = await _sql(url, "s4 live.mrr").load()
        assert list(df.columns) == ["customer_id", "mrr"] and len(df) == 2
        assert list((await _sql(url, "mrr").load()).columns) == ["only_public"]
        with pytest.raises(ValueError, match="does not exist"):
            await _sql(url, 's4 live".mrr; --').load()
    finally:
        with eng.begin() as c:
            c.execute(text('DROP SCHEMA "s4 live" CASCADE'))
            c.execute(text("DROP TABLE public.mrr"))


# ── 3c. what each refusal says, exactly (the mutation gate: a refusal's words are the product) ──

_DEV, _PROD = "development", "production"
_ALLOWED_HINT = ("The connection string couldn't be read. Use the form "
                 "dialect://user:password@host:port/database (a password with @ or : must be "
                 "URL-encoded, e.g. %40 for @).")


@pytest.mark.parametrize("url,env,reason,message", [
    ("not a url", _DEV, "unreadable connection string", _ALLOWED_HINT),
    ("mssql+pymssql://u:p@h/db", _DEV, "no dialect mssql+pymssql",
     "'mssql+pymssql' isn't a supported database. Use PostgreSQL (postgresql://), MySQL "
     "(mysql://), MariaDB (mariadb://), SQL Server (mssql://) (SQLite for local development)."),
    ("mssql://u:p@h/db?odbc_connect=DRIVER%3Dx&Driver=y&TrustServerCertificate=yes", _DEV,
     "connection string options", "These connection string options aren't allowed: Driver, "
     "odbc_connect. Allowed: encrypt, trustservercertificate."),
    ("mysql://u:p@h/db?init_command=x", _DEV, "connection string options",
     "These connection string options aren't allowed: init_command. Allowed: charset, ssl_mode."),
    ("mysql://u:p@h/db?local_infile=1&init_command=x", _DEV, "connection string options",
     "These connection string options aren't allowed: init_command, local_infile. Allowed: "
     "charset, ssl_mode."),
    ("postgresql://u:p@h/db?passfile=/x", _DEV, "connection string options",
     "These connection string options aren't allowed: passfile. Allowed: application_name, "
     "channel_binding, connect_timeout, sslmode, target_session_attrs."),
    ("sqlite:///x.db?mode=ro", _DEV, "connection string options",
     "These connection string options aren't allowed: mode. Allowed: none."),
    ("sqlite:///x.db", _PROD, "sqlite in production",
     "SQLite is for development only. Connect one of PostgreSQL (postgresql://), MySQL "
     "(mysql://), MariaDB (mariadb://), SQL Server (mssql://) instead."),
    ("postgresql://u:p@/db", _PROD, "no host", "The connection string has no host."),
    ("postgresql://u:p@nowhere.invalid/db", _PROD, "host not found",
     "The database host 'nowhere.invalid' wasn't found."),
    ("postgresql://u:p@0.0.0.0/db", _PROD, "host refused",
     "The database host '0.0.0.0' isn't allowed (it is this server itself, or a reserved address)."),
    ("postgresql://u:p@224.0.0.1/db", _PROD, "host refused",
     "The database host '224.0.0.1' isn't allowed (it is this server itself, or a reserved address)."),
    ("postgresql://u:p@mapped-own.example/db", _PROD, "host refused",   # ::ffff: of the app store
     "The database host 'mapped-own.example' isn't allowed (it is this server itself, or a "
     "reserved address)."),
    ("postgresql://u:p@db.lan/db", "production-own-down", "own store unresolved",
     "This server can't check database hosts right now (its own database address didn't "
     "resolve). Try again shortly, or ask the administrator."),
])
def test_every_connection_refusal_says_exactly_why(monkeypatch, url, env, reason, message):
    import socket

    from api.config import settings
    ips = {"appdb.internal": "10.1.2.3", "mapped-own.example": "::ffff:10.1.2.3", "db.lan": "10.0.0.5"}

    def lookup(host, *a, **k):
        if host == "nowhere.invalid" or (env == "production-own-down" and host == "appdb.internal"):
            raise socket.gaierror("not found")
        return [(0, 0, 0, "", (ips.get(host, host), 0))]
    monkeypatch.setattr(socket, "getaddrinfo", lookup)
    monkeypatch.setattr(settings, "app_env", "development" if env == _DEV else "production")
    monkeypatch.setattr(settings, "database_url", "postgresql+asyncpg://a:b@appdb.internal:5432/x")
    with pytest.raises(IngestionError) as err:
        _sql(url)._engine()
    assert (str(err.value), err.value.user_message) == (f"ingestion: {reason}", message)


def test_in_production_an_app_store_without_a_host_checks_only_the_users():
    """A store named by a path (no host) has no address to compare: the user's LAN host passes."""
    import socket

    from api.config import settings
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(socket, "getaddrinfo", lambda h, *a, **k: [(0, 0, 0, "", ("10.0.0.5", 0))])
        mp.setattr(settings, "app_env", "production")
        mp.setattr(settings, "database_url", "sqlite:///store.db")
        assert _sql("postgresql://u:p@db.lan/db")._engine() is not None


def test_an_engine_without_a_row_is_refused_in_so_many_words():
    from connectors.sql_connector import dialect_of
    engine = type("Engine", (), {"dialect": type("D", (), {"name": "oracle"})()})()
    with pytest.raises(IngestionError) as err:
        dialect_of(engine)
    assert (str(err.value), err.value.user_message) == (
        "ingestion: no dialect oracle", "oracle databases aren't supported (these are: "
        "PostgreSQL (postgresql://), MySQL (mysql://), MariaDB (mariadb://), SQL Server (mssql://)).")


@pytest.mark.parametrize("conn,auth,sa,message,want", [
    # the whole string, then its user-info, then the password (split at the first ':' only)
    ("mysql://reader:p:a:ss@h/db", None, None, "bad mysql://reader:p:a:ss@h/db p:a:ss reader:p:a:ss",
     "bad *** *** ***"),
    ("postgresql://reader:s3cr%40t@h/db", None, None, "pw s3cr@t and s3cr%40t", "pw *** and ***"),
    ("postgresql://onlyuser@h/db", None, None, "user onlyuser XXXX", "user *** XXXX"),
    ("postgresql://a:zzz@h/db", None, None, "a:zzz", "***"),     # longest first, not abc order
    ("postgresql://u:abc@h/db", None, None, "abc", "***"),       # three characters: a secret
    ("postgresql://u:ab@h/db", None, None, "ab cab", "ab cab"),  # two: too short to cut safely
    # a short bare value only as a whole word (5.8b hardening: 'postgres' cut 'postgresql')
    ("postgresql://postgres:postgres@h/db", None, None, "postgresql said: postgres refused",
     "postgresql said: *** refused"),
    (None, "tok-123", None, "key tok-123", "key ***"),
    (None, None, '{"k": 1}', 'sa {"k": 1}', "sa ***"),
    (None, None, None, "XXXX nothing to hide", "XXXX nothing to hide"),
])
def test_without_secrets_cuts_every_credential_and_nothing_else(conn, auth, sa, message, want):
    from connectors.base import without_secrets
    config = ConnectorConfig(type=ConnectorType.SQL, connection_string=conn, auth_value=auth,
                             service_account_json=sa)
    assert without_secrets(message, config) == want


def test_resolved_skips_what_isnt_an_address_and_names_a_host_it_cant_find(monkeypatch):
    import socket

    from connectors.base import resolved
    assert {str(ip) for ip in resolved("127.0.0.1")} == {"127.0.0.1"}   # the real lookup (one per socket type)
    monkeypatch.setattr(socket, "getaddrinfo", lambda h, *a, **k: (_ for _ in ()).throw(
        socket.gaierror("nope")) if h == "gone.invalid" else
        [(0, 0, 0, "", ("not-an-address", 0)), (0, 0, 0, "", ("1.2.3.4", 0))])
    assert [str(ip) for ip in resolved("x.example")] == ["1.2.3.4"]
    with pytest.raises(ValueError, match="gone.invalid"):
        resolved("gone.invalid")


@pytest.mark.parametrize("name", ["sqlite", "postgresql", "mysql"])
def test_a_connection_lists_only_its_users_tables(tmp_path, name):
    """No catalog table (information_schema, pg_catalog, mysql.*) and, on MySQL, no other
    database the account can see: a schema there is a database."""
    from sqlalchemy import text

    from connectors.sql_connector import SQLConnector, _tables
    url = _each_dialect(tmp_path, name)
    engine = SQLConnector(ConnectorConfig(type=ConnectorType.SQL, connection_string=url))._engine()
    with engine.begin() as c:
        c.execute(text("DROP TABLE IF EXISTS mine"))
        c.execute(text("CREATE TABLE mine (a INTEGER)"))
        if name == "mysql":
            c.execute(text("CREATE DATABASE IF NOT EXISTS other_5_8a"))
            c.execute(text("CREATE TABLE IF NOT EXISTS other_5_8a.theirs (a INTEGER)"))
    listed = _tables(engine)
    assert "mine" in listed
    assert not [t for t in listed if t.split(".")[0] in {"information_schema", "pg_catalog",
                                                         "pg_toast", "mysql", "sys",
                                                         "performance_schema", "other_5_8a"}]


def test_a_result_of_exactly_the_cap_is_not_truncated(tmp_path):
    from connectors.sql_connector import SQLConnector, run_readonly
    url = _sqlite(tmp_path, pd.DataFrame({"a": [1, 2]}))
    engine = SQLConnector(ConnectorConfig(type=ConnectorType.SQL, connection_string=url))._engine()
    assert not run_readonly(engine, "SELECT a FROM t", max_rows=2).truncated
    assert run_readonly(engine, "SELECT a FROM t", max_rows=1).truncated


def test_the_engine_keeps_no_idle_connections_on_the_users_database():
    from sqlalchemy.pool import NullPool
    assert isinstance(_sql("postgresql://u:p@h/db")._engine().pool, NullPool)


def test_every_sources_datetimes_read_at_one_resolution():
    """A DATE read as [s] or a parquet [ns] column is [us], as the CSV reader gives it."""
    from connectors.base import normalize_frame
    df = pd.DataFrame({"d": pd.to_datetime(["2026-01-05"]).as_unit("s"),
                       "n": pd.to_datetime(["2026-01-05 10:00"]).as_unit("ns")})
    assert list(normalize_frame(df).dtypes.astype(str)) == ["datetime64[us]", "datetime64[us]"]


def test_a_blank_text_column_from_a_database_stays_text():
    from connectors.sql_connector import QueryResult
    df = QueryResult(["note"], [(None,), (None,)], False).frame()
    assert not str(df["note"].dtype).startswith("datetime64")


# ── audit pass 2 (5.8a): the bugs four fresh reviewers found, each pinned here ─────────────────

_FRAME_CELLS = st.one_of(st.none(), st.just(""), st.integers(-3, 3), st.floats(allow_nan=False,
                         allow_infinity=False, width=32), st.sampled_from(
                         ["a", "2026-01-05", "0000-00-00", "0000-00-00 00:00:00.000000"]))


@given(names=st.lists(st.sampled_from(["id", "x", "y"]), min_size=1, max_size=4),
       data=st.data())
def test_any_result_shape_becomes_a_frame_without_losing_a_column(names, data):
    """B1: a join's two `id` columns crashed the result → frame step ('truth value of a Series is
    ambiguous') on every dialect: column steps ran before names are made unique. Any names
    (duplicates too), any cells: the frame keeps every column, in order."""
    from connectors.base import normalize_frame
    from connectors.sql_connector import QueryResult
    rows = [tuple(data.draw(_FRAME_CELLS) for _ in names)
            for _ in range(data.draw(st.integers(0, 3)))]
    kinds = tuple("bool" if i == 0 else "date" if i == len(names) - 1 else None
                  for i in range(len(names)))
    df = QueryResult(list(names), rows, False, kinds).frame()
    assert list(df.columns) == names and len(df) == len(rows)
    assert list(normalize_frame(df).columns) == names


def test_a_column_with_no_value_reads_as_a_csvs_empty_column():
    """5.8c's first import oracle run: Stripe's empty Due Date read None from the import (an
    object column) and NaN from Stripe's CSV export of the same invoices (a float column). Every
    source's empty column now reads as a CSV's; one with a value keeps its own type."""
    from connectors.base import normalize_frame
    from connectors.csv_connector import _read_bytes
    from connectors.sql_connector import QueryResult
    csv, _ = _read_bytes(b"id,due\n1,\n2,\n", ".csv")
    got = QueryResult(["id", "due"], [(1, None), (2, None)], False).frame()
    assert str(csv["due"].dtype) == str(got["due"].dtype) == "float64"
    kept = normalize_frame(pd.DataFrame({"a": [None, "x"]}))
    none = normalize_frame(pd.DataFrame({"b": pd.Series([], dtype=object)}))
    assert kept["a"].isna().tolist() == [True, False] and kept["a"].dtype != "float64"
    assert str(none["b"].dtype) == "object"


@pytest.mark.parametrize("name", DIALECT_NAMES)
def test_a_join_returning_two_id_columns_reads_on_every_dialect(tmp_path, name):
    from sqlalchemy import text

    from connectors.sql_connector import run_readonly
    engine = _engine_for(_each_dialect(tmp_path, name))
    with _admin(tmp_path, name).begin() as c:
        for t in ("ja", "jb"):
            c.execute(text(f"DROP TABLE IF EXISTS {t}"))
            c.execute(text(f"CREATE TABLE {t} (id INTEGER)"))
            c.execute(text(f"INSERT INTO {t} VALUES (1)"))
    df = run_readonly(engine, "SELECT ja.id, jb.id FROM ja JOIN jb ON ja.id = jb.id").frame()
    assert list(df.columns) == ["id", "id"] and df.shape == (1, 2)


def _connector_with_mine(tmp_path, name):
    from sqlalchemy import text
    url = _each_dialect(tmp_path, name)
    with _admin(tmp_path, name).begin() as c:
        c.execute(text("DROP TABLE IF EXISTS mine"))
        c.execute(text("CREATE TABLE mine (a INTEGER)"))
        c.execute(text("INSERT INTO mine VALUES (1)"))
        if name in ("mysql", "mariadb"):
            c.execute(text("CREATE DATABASE IF NOT EXISTS other_5_8a"))
            c.execute(text("CREATE TABLE IF NOT EXISTS other_5_8a.theirs (a INTEGER)"))
    return _sql(url, None)


_NOT_LISTED = {
    "sqlite": ["SELECT * FROM sqlite_master"],
    "postgresql": ["SELECT * FROM pg_shadow", "SELECT query FROM pg_stat_activity",
                   "SELECT * FROM PG_AUTHID", "SELECT * FROM information_schema.tables",
                   "SELECT * FROM mine m JOIN pg_roles r ON true",
                   # security review of the fix: a CTE named after a table reads the real one
                   # inside its own body; a function read as a table, or a table read by name
                   "WITH pg_shadow AS (SELECT * FROM pg_shadow) SELECT * FROM pg_shadow",
                   "SELECT * FROM pg_stat_get_activity(NULL) AS s",
                   "SELECT table_to_xml('pg_authid', true, false, '')",
                   "SELECT * FROM mine m, LATERAL (SELECT * FROM pg_authid) z",
                   "SELECT * FROM generate_series(1, 3) AS g, pg_shadow"],
    "mysql": ["SELECT * FROM mysql.user", "SELECT * FROM other_5_8a.theirs",
              "SELECT * FROM information_schema.processlist",
              "SELECT * FROM `other_5_8a`.`theirs`",
              "SELECT a FROM mine WHERE a IN (SELECT a FROM other_5_8a.theirs)"],
}


@pytest.mark.parametrize("name,sql", [(n, s) for n, qs in _NOT_LISTED.items() for s in qs])
async def test_a_question_reads_only_the_tables_the_connection_lists(tmp_path, name, sql):
    """B2: what a question may read is THE connection's table list (_tables), the rule the table
    load already had. The parse guard's system-schema list caught `pg_catalog.pg_shadow` but not
    `pg_shadow`, and nothing stopped a MySQL query reading another database on the server."""
    from agents.errors import GuardrailViolation
    conn = _connector_with_mine(tmp_path, name)
    with pytest.raises(GuardrailViolation) as err:
        await conn.query(sql)
    said = err.value.user_message   # the parse guard refuses a qualified system table first
    assert err.value.agent == "sql" and said.startswith("Blocked unsafe SQL: ")
    assert "only the connected database's tables can be read" in said or \
        "the database's own tables are not readable" in said or "function not allowed" in said or \
        "a lock, admin, session or random function is not allowed" in said


@pytest.mark.parametrize("name", DIALECT_NAMES)
async def test_a_question_reads_its_own_tables_by_any_spelling(tmp_path, name):
    from sqlalchemy import inspect
    conn = _connector_with_mine(tmp_path, name)
    engine = await conn.engine()
    quote = engine.dialect.identifier_preparer.quote_identifier
    schema = inspect(engine).default_schema_name
    for sql in ["SELECT a FROM mine", f"SELECT a FROM {quote('mine')}", f"SELECT a FROM {schema}.mine",
                "WITH x AS (SELECT a FROM mine) SELECT a FROM x",
                "SELECT m.a FROM mine AS m WHERE m.a IN (SELECT a FROM mine)",
                "SELECT COUNT(*) FROM (SELECT a FROM mine) AS sub",
                # T-SQL's recursive CTE has no RECURSIVE keyword
                f"WITH {'' if name == 'mssql' else 'RECURSIVE '}r AS (SELECT 1 AS n UNION ALL "
                "SELECT n + 1 FROM r WHERE n < 1) SELECT a FROM mine JOIN r ON r.n = mine.a"]:
        assert (await conn.query(sql)).rows == [(1,)], sql


async def test_a_function_read_as_a_table_is_named_in_the_refusal(tmp_path):
    from agents.errors import GuardrailViolation
    conn = _connector_with_mine(tmp_path, "postgresql")
    with pytest.raises(GuardrailViolation) as err:
        await conn.query("SELECT * FROM some_srf(1) AS s, other_srf(2) AS o")   # read as tables
    assert err.value.user_message == "Blocked unsafe SQL: function not allowed: some_srf."
    assert str(err.value) == "sql: function not allowed: some_srf"   # the trace's reason


@pytest.mark.parametrize(("sql", "dialect", "unlisted"), [
    ("SELECT * FROM some_srf(1) AS s, other_srf(2) AS o", "postgres", ["SOME_SRF()", "OTHER_SRF()"]),
    ("SELECT * FROM mine CROSS APPLY sys.dm_exec_sql_text(mine.a) q", "tsql",
     ["APPLY sys.dm_exec_sql_text"]),
    ("SELECT * FROM mine OUTER APPLY f(mine.a) q", "tsql", ["APPLY F"]),
    # named as the user's dialect writes it (mutation gate: the default dialect's quotes passed)
    ("SELECT * FROM mine CROSS APPLY [dbo].[My Fn](mine.a) x", "tsql", ["APPLY [dbo].[My Fn]"]),
    ("SELECT * FROM #mine", "tsql", ["#mine"]), ("SELECT * FROM ##mine", "tsql", ["#mine"]),
    # every unlisted name is said, past a temp table too (mutation gate: `break` there passed)
    ("SELECT * FROM #mine, theirs", "tsql", ["#mine", "theirs"]),
    ("SELECT * FROM OPENDATASOURCE('x', 'y').db.dbo.mine", "tsql", ["dbo.mine()"]),
    ("SELECT * FROM master..mine", "tsql", ["master..mine"]),   # another database, as written
    ("SELECT * FROM other.dbo.mine", "tsql", ["other.dbo.mine"]),
    ("SELECT * FROM mine CROSS APPLY (SELECT TOP 1 a FROM mine) q", "tsql", []),
    ("SELECT * FROM [dbo].[mine]", "tsql", []),
    # T-SQL's CTEs are recursive without the keyword; Postgres's aren't: there the inner name is
    # the real table (5.8a), here the CTE itself
    ("WITH r AS (SELECT 1 AS n UNION ALL SELECT n + 1 FROM r WHERE n < 3) SELECT n FROM r",
     "tsql", []),
    ("WITH pg_shadow AS (SELECT * FROM pg_shadow) SELECT * FROM pg_shadow", "postgres",
     ["pg_shadow"]),
    # a regression the 5.8b review found: LATERAL generate_series makes its own rows
    ("SELECT g FROM mine CROSS JOIN LATERAL generate_series(mine.a, mine.a + 2) g", "postgres", []),
])
def test_the_table_rule_names_whatever_isnt_a_listed_table(sql, dialect, unlisted):
    """Layer 1b on its own (the parse guard refuses most of these first; this rule is the one
    owner of "what may be read", so it holds without it): a function's rows, an APPLY over a
    function, a temp table and a table in another database are never the connection's tables
    (5.8b red-team B3: APPLY and #temp got past it)."""
    from connectors.sql_connector import DIALECTS, _unlisted_tables
    d = next(r for r in DIALECTS.values() if r.sqlglot == dialect)
    assert _unlisted_tables(sql, d, {"mine": None, "dbo.mine": None}) == unlisted


async def test_a_postgres_set_returning_function_is_not_a_table(tmp_path):
    conn = _connector_with_mine(tmp_path, "postgresql")
    assert len((await conn.query("SELECT * FROM generate_series(1, 3) AS g")).rows) == 3


def test_a_mysql_string_must_name_its_database():
    """B7: without one, every database on the server was listed to the model."""
    with pytest.raises(IngestionError) as err:
        _sql("mysql+pymysql://u:p@h:3306/")._engine()
    assert str(err.value) == "ingestion: no database"
    assert err.value.user_message == ("The connection string must name the user and the database: "
                                      "mysql://user:password@host:port/<database>.")


def test_in_production_a_pinned_sql_server_url_opens_a_real_session(tmp_path, monkeypatch):
    """Audit pass 2 (test analyst): the pin's shape was tested, never that ODBC takes the IP as
    the server with HostNameInCertificate beside it."""
    from sqlalchemy.engine import make_url

    import connectors.sql_connector as sc
    from api.config import settings
    url = make_url(_each_dialect(tmp_path, "mssql")).set(host="localhost")
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(sc, "_checked_address", lambda u: "127.0.0.1")
    engine = _engine_for(url.render_as_string(hide_password=False))
    assert engine.url.host == "127.0.0.1"
    assert sc.run_readonly(engine, "SELECT 1").rows == [(1,)]


def test_a_repeated_option_is_said_not_a_crash():
    with pytest.raises(IngestionError) as err:
        _sql("mysql://u:p@h/x?ssl_mode=REQUIRED&ssl_mode=VERIFY_CA&charset=a&charset=b")._engine()
    assert str(err.value) == "ingestion: connection string options"
    assert err.value.user_message == \
        "Each connection string option may appear once: charset, ssl_mode."


def test_a_session_error_says_the_drivers_first_300_characters():
    from sqlalchemy import create_engine

    from connectors.sql_connector import _session_error
    err = _session_error(Exception("x" * 400 + "\nsecond line"), create_engine("sqlite://"))
    assert err.user_message == "Couldn't open a read-only session on the database: " + "x" * 300
    # the driver's own words, not SQLAlchemy's wrapper around them
    from sqlalchemy.exc import OperationalError
    wrapped = OperationalError("SELECT 1", {}, Exception("Access denied for user 'u'"))
    assert _session_error(wrapped, create_engine("sqlite://")).user_message == \
        "Couldn't open a read-only session on the database: Access denied for user 'u'"
    # the password cut out, said as ***
    echoed = OperationalError("x", {}, Exception("bad login s3cr3t-pw for u"))
    engine = create_engine("mysql+pymysql://u:s3cr3t-pw@h/x")
    assert _session_error(echoed, engine).user_message == \
        "Couldn't open a read-only session on the database: bad login *** for u"


@pytest.mark.parametrize("cs", ["mssql://u:p@db.example:1433", "mssql://db.example:1433/acme"])
def test_a_sql_server_string_names_its_user_and_database(cs):
    """Audit pass 2 (security): SQLAlchemy's ODBC builder reads no database as `dsn=<host>` and
    no user as Trusted_Connection=Yes (this server's own Windows credentials)."""
    with pytest.raises(IngestionError, match="no database"):
        _sql(cs)._engine()


def test_a_database_that_cant_be_reached_is_a_session_error_not_a_crash(tmp_path):
    """B5: run_readonly's setup failure is an IngestionError naming the session (no connection
    here: nothing to close)."""
    from connectors.sql_connector import run_readonly
    engine = _engine_for(f"sqlite:///{tmp_path / 'no_dir' / 'x.sqlite'}")
    with pytest.raises(IngestionError) as err:
        run_readonly(engine, "SELECT 1")
    assert str(err.value) == "ingestion: session setup"
    assert err.value.user_message.startswith("Couldn't open a read-only session on the database: ")


async def test_a_capped_load_stands_when_its_count_cant_open_a_session(tmp_path, monkeypatch):
    """5.8b audit: the count after a capped load caught SQLAlchemyError only, and a session that
    couldn't open is an IngestionError: a load that had worked was thrown away."""
    import connectors.sql_connector as sc
    monkeypatch.setattr(sc, "LOAD_MAX_ROWS", 2)
    real = sc.run_readonly

    def count_fails(engine, sql, *a, **k):
        if "count(" in sql.lower():
            raise IngestionError("session setup", "Couldn't open a read-only session")
        return real(engine, sql, *a, **k)
    monkeypatch.setattr(sc, "run_readonly", count_fails)
    conn = _sql(_sqlite(tmp_path, pd.DataFrame({"a": [1, 2, 3]})))
    assert len(await conn.load()) == 2
    assert any("the source has more" in n for n in conn.read_info["notes"])


@pytest.mark.parametrize("name", DIALECT_NAMES)
async def test_a_capped_table_load_reads_the_first_rows_by_key(tmp_path, name, monkeypatch):
    """B8: LIMIT without ORDER BY read an arbitrary subset past the cap (two runs, two datasets;
    a resumed run: 'the data changed'). A keyed table loads in key order (SQL Server: TOP), and
    the note says how many rows the table has, counted in the engine's own SQL (a name that
    needs quoting: a generic count would quote it as MySQL reads a string)."""
    from sqlalchemy import Column, Integer, MetaData, Table, update

    import connectors.sql_connector as sc
    url = _each_dialect(tmp_path, name)
    keyed = Table("Keyed Rows", MetaData(), Column("k", Integer, primary_key=True,
                                                   autoincrement=False), Column("v", Integer))
    admin = _admin(tmp_path, name)
    keyed.drop(admin, checkfirst=True)
    keyed.create(admin)
    with admin.begin() as c:
        c.execute(keyed.insert(), [{"k": k, "v": 10 * k} for k in (5, 3, 9, 1)])
        c.execute(update(keyed).where(keyed.c.k == 1).values(v=11))   # PG: k=1 moves to the end
    monkeypatch.setattr(sc, "LOAD_MAX_ROWS", 2)
    conn = _sql(url, "Keyed Rows")
    assert list((await conn.load())["k"]) == [1, 3]
    assert any("2 of 4 rows" in n for n in conn.read_info["notes"])


async def test_a_capped_load_outside_the_default_schema_reads_its_own_key(tmp_path, monkeypatch):
    import connectors.sql_connector as sc
    from sqlalchemy import text
    url = _each_dialect(tmp_path, "postgresql")
    with _engine_for(url).begin() as c:
        c.execute(text("CREATE SCHEMA IF NOT EXISTS s58"))
        c.execute(text("DROP TABLE IF EXISTS s58.keyed2"))
        c.execute(text("CREATE TABLE s58.keyed2 (k INTEGER PRIMARY KEY, v INTEGER)"))
        c.execute(text("INSERT INTO s58.keyed2 VALUES (5, 50), (3, 30), (1, 10)"))
        c.execute(text("UPDATE s58.keyed2 SET v = v + 1 WHERE k = 1"))
    monkeypatch.setattr(sc, "LOAD_MAX_ROWS", 2)
    assert list((await _sql(url, "s58.keyed2").load())["k"]) == [1, 3]


async def test_zero_dates_are_blanked_only_where_the_database_types_a_date(tmp_path):
    """B9: '0000-00-00' was blanked in any text column of any dialect; a VARCHAR holding it is
    text the user wrote (the CSV of it keeps it)."""
    conn = _connector_with_mine(tmp_path, "postgresql")
    df = (await conn.query("SELECT CAST('0000-00-00' AS varchar) AS v FROM mine")).frame()
    assert df["v"].tolist() == ["0000-00-00"]


@pytest.mark.parametrize("name", ["mysql", "mariadb"])
def test_the_mysql_session_mode_reads_sql_as_the_guard_does(tmp_path, name):
    """B11a: with ANSI_QUOTES or NO_BACKSLASH_ESCAPES on the server, MySQL (and MariaDB) would
    read a string differently from the parse guard. The connection's init_command takes both
    off."""
    import pymysql
    from sqlalchemy.engine import make_url

    from connectors.sql_connector import DIALECTS
    u = make_url(_each_dialect(tmp_path, name))
    c = pymysql.connect(host=u.host, port=u.port, user=u.username, password=u.password,
                        database=u.database)
    try:
        cur = c.cursor()
        cur.execute("SET SESSION sql_mode = 'ANSI_QUOTES,NO_BACKSLASH_ESCAPES,STRICT_ALL_TABLES'")
        cur.execute(DIALECTS[name].connect_args["init_command"])
        cur.execute("SELECT @@SESSION.sql_mode, @@SESSION.time_zone")
        mode, tz = cur.fetchone()
    finally:
        c.close()
    assert set(mode.split(",")) == {"STRICT_ALL_TABLES"} and tz == "+00:00"


async def test_a_connection_test_bug_is_logged_not_shown_as_a_connection_problem(
        tmp_path, monkeypatch, caplog):
    """B11b: a programming error in the connection test read as the user's connection failing."""
    import semantic_layer
    monkeypatch.setattr(semantic_layer, "configured_layer", lambda: {}["missing"])
    ok, message = await _sql(_sqlite(tmp_path, pd.DataFrame({"a": [1]})), None).test_connection()
    assert (ok, message) == (False, "The connection test hit an unexpected error on this server "
                                    "(logged).")
    assert [r.getMessage() for r in caplog.records if r.exc_info] == \
        ["connection test failed unexpectedly"]


def test_a_mapped_ipv6_address_resolves_as_its_ipv4_address(monkeypatch):
    """B11d: one normalisation for every host check (the API's and the database's)."""
    import ipaddress
    import socket

    from connectors.base import resolved
    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda *a, **k: [(0, 0, 0, "", ("::ffff:127.0.0.1", 0, 0, 0))])
    assert resolved("x") == [ipaddress.ip_address("127.0.0.1")]


@pytest.mark.parametrize(("record", "inside"), [
    ("::ffff:127.0.0.1", ["127.0.0.1"]),                     # IPv4-mapped
    ("::ffff:0:7f00:1", ["127.0.0.1"]),                      # IPv4-translated (SIIT)
    ("::127.0.0.1", ["127.0.0.1"]),                          # IPv4-compatible (deprecated)
    ("64:ff9b::a9fe:a9fe", ["169.254.169.254"]),             # NAT64 well-known prefix
    ("2002:7f00:1::", ["127.0.0.1"]),                        # 6to4
    ("2001:0:4136:e378:8000:63bf:80ff:fffe", ["65.54.227.120", "127.0.0.1"]),   # Teredo, both ends
    ("64:ff9b:1::a9fe:a9fe", ["0.0.0.0"]),   # local-use NAT64: embedding unknown → refused everywhere
    ("2606:4700::1111", ["2606:4700::1111"]),                # a plain public IPv6 stays itself
    ("::1", ["::1"]), ("::", ["::"]),   # loopback and unspecified stay themselves
    ("::2", ["0.0.0.2"]),                # the first IPv4-compatible address unwraps
])
def test_resolved_unwraps_every_ipv4_embedding(monkeypatch, record, inside):
    """5.8b (recurrence of P58-07: the 5.8a fix unwrapped only IPv4-mapped addresses): a DNS
    record holding an IPv4 address inside an IPv6 one (NAT64 to the cloud metadata address reads
    `is_global` on Python 3.11) is checked as the IPv4 address it reaches. One lookup behind every
    host check: the database's, the API's and the share-link download's."""
    import ipaddress
    import socket

    from connectors.base import resolved
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(0, 0, 0, "", (record, 0, 0, 0))])
    assert resolved("x") == [ipaddress.ip_address(a) for a in inside]


def test_the_app_store_is_not_published_on_the_network():
    """B4: the store's port on every interface (with default credentials) let a connection to
    the host's LAN address reach it: the own-store check knows its address, not every route to
    it. Compose binds it to loopback."""
    from pathlib import Path

    import yaml
    compose = yaml.safe_load((Path(__file__).parent.parent / "docker-compose.yml").read_text())
    ports = compose["services"]["postgres"].get("ports", [])
    assert all(str(p).startswith("127.0.0.1:") for p in ports), ports


async def test_every_table_reaches_the_model_by_name(tmp_path):
    """Audit pass 2's paid run (e2e-99): a database of 40 tables showed the model 20, silently;
    the one the question needed was the 30th, and the model said no table held the data. Every
    table is named; past a size budget, by name only, and the prompt says so."""
    from sqlalchemy import text
    url = f"sqlite:///{tmp_path / 'many.sqlite'}"
    with create_engine(url).begin() as c:
        for i in range(60):
            cols = ", ".join(f"c{j} INTEGER" for j in range(40))
            c.execute(text(f"CREATE TABLE t{i:02d} ({cols})"))
    conn = _sql(url, None)
    context = conn.schema_context(await conn.engine())
    assert all(f"t{i:02d}" in context for i in range(60))
    assert "columns not shown" in context and len(context) < 20_000
    assert "`main.t58`, `main.t59`" in context


async def test_sql_server_tables_reach_the_model_by_schema_and_no_catalog_does(tmp_path):
    """P58-17 on SQL Server: the default schema `dbo` is named like Postgres's `public`, another
    user schema too, and none of its catalog or role schemas (sys, INFORMATION_SCHEMA, db_*)."""
    from sqlalchemy import text

    from connectors.sql_connector import _tables
    url = _each_dialect(tmp_path, "mssql")
    with _admin(tmp_path, "mssql").begin() as c:
        c.execute(text("DROP TABLE IF EXISTS mine"))
        c.execute(text("CREATE TABLE mine (a INTEGER)"))
        c.execute(text("IF SCHEMA_ID('sales') IS NULL EXEC('CREATE SCHEMA sales')"))
        c.execute(text("DROP TABLE IF EXISTS sales.orders"))
        c.execute(text("CREATE TABLE sales.orders (b INTEGER)"))
    conn = _sql(url, None)
    engine = await conn.engine()
    listed = _tables(engine)
    assert {"mine", "dbo.mine", "sales.orders"} <= set(listed)
    assert not [t for t in listed if t.split(".")[0].lower() in
                {"sys", "information_schema", "guest", "db_owner", "db_datareader"}]
    context = conn.schema_context(engine)
    assert "`dbo.mine`" in context and "`sales.orders`" in context


async def test_a_sql_server_login_that_can_write_is_refused_before_any_query(tmp_path):
    """Decision 2 (ADR-0010): SQL Server has no read-only session, so a login that can change
    data is refused when the session opens: an IngestionError that says what to do (no paid
    retry, P58-14), at "Test connection" too (red-team M6)."""
    import os

    from connectors.sql_connector import run_readonly
    _each_dialect(tmp_path, "mssql")
    conn = _sql(os.environ["TEST_MSSQL_WRITER_URL"], None)
    with pytest.raises(IngestionError, match="session setup") as err:
        run_readonly(await conn.engine(), "SELECT 1")
    assert "connect with a read-only login" in err.value.user_message
    ok, message = await conn.test_connection()
    assert not ok and "read-only login" in message


@pytest.mark.parametrize("name", ["mysql", "mariadb"])
def test_ssl_mode_required_encrypts_the_session(tmp_path, name):
    """Hosted MySQL (PlanetScale, RDS) takes TLS: `ssl_mode`, in the MySQL client's own words.
    REQUIRED encrypts (the CI servers' certificates are self-signed)."""
    from connectors.sql_connector import run_readonly
    url = _each_dialect(tmp_path, name) + "?ssl_mode=REQUIRED"
    rows = run_readonly(_engine_for(url), "SHOW SESSION STATUS LIKE 'Ssl_cipher'").rows
    assert rows[0][1].startswith("TLS")


@pytest.mark.parametrize("name", ["mysql", "mariadb"])
def test_ssl_mode_verify_identity_refuses_a_certificate_no_known_ca_signed(tmp_path, name):
    """VERIFY_IDENTITY checks the chain (certifi + AWS RDS's CAs, never a file a user names)
    and the name: a self-signed server is refused, as a session the app couldn't open."""
    from connectors.sql_connector import run_readonly
    url = _each_dialect(tmp_path, name) + "?ssl_mode=verify_identity"
    with pytest.raises(IngestionError) as err:
        run_readonly(_engine_for(url), "SELECT 1")
    assert "CERTIFICATE_VERIFY_FAILED" in err.value.user_message


def test_an_upper_case_ssl_mode_is_the_same_option(tmp_path):
    """5.8b audit: the allowlist read keys without case, the TLS reader by exact case, so
    SSL_MODE passed the one and reached PyMySQL as an unknown argument."""
    from connectors.sql_connector import run_readonly
    url = _each_dialect(tmp_path, "mysql") + "?SSL_MODE=REQUIRED"
    rows = run_readonly(_engine_for(url), "SHOW SESSION STATUS LIKE 'Ssl_cipher'").rows
    assert rows[0][1].startswith("TLS")


@pytest.mark.parametrize("mode", ["DISABLED", "PREFERRED", "x", "required%20"])
def test_an_ssl_mode_that_doesnt_encrypt_is_refused(mode):
    with pytest.raises(IngestionError) as err:
        _sql(f"mysql://u:p@h/db?ssl_mode={mode}")._engine()
    assert (str(err.value), err.value.user_message) == (
        "ingestion: ssl_mode",
        "ssl_mode must be REQUIRED, VERIFY_CA or VERIFY_IDENTITY (leave it out for no TLS).")


@pytest.mark.parametrize(("mode", "verify", "name"), [
    ("REQUIRED", ssl.CERT_NONE, False), ("verify_ca", ssl.CERT_REQUIRED, False),
    ("Verify_Identity", ssl.CERT_REQUIRED, True)])
def test_each_ssl_mode_checks_what_its_name_says(monkeypatch, mode, verify, name):
    """REQUIRED encrypts; VERIFY_CA checks the chain; VERIFY_IDENTITY the name too. Against
    certifi's CAs plus the operator's bundle (the Docker image's is AWS RDS's)."""
    import certifi

    from api.config import settings
    from connectors.sql_connector import _tls
    ctx = _tls(mode)
    assert (ctx.verify_mode, ctx.check_hostname) == (verify, name)
    certifi_cas = ssl.create_default_context(cafile=certifi.where()).cert_store_stats()["x509_ca"]
    assert ctx.cert_store_stats()["x509_ca"] == certifi_cas
    monkeypatch.setattr(settings, "extra_ca_bundle", certifi.where())   # the operator's is read
    assert _tls(mode).cert_store_stats()["x509_ca"] == certifi_cas
    monkeypatch.setattr(settings, "extra_ca_bundle", "/nonexistent/bundle.pem")
    with pytest.raises(IngestionError, match="ca bundle") as err:   # this server's, said as such
        _tls(mode)
    assert "EXTRA_CA_BUNDLE" in err.value.user_message


def test_in_production_a_mysql_server_that_doesnt_answer_is_a_session_error(monkeypatch):
    """5.8b audit pass 2: the pinned socket's OSError wasn't the driver's error, so SQLAlchemy
    passed it raw: the SQL agent took it for the query's fault and paid a retry."""
    import connectors.sql_connector as sc
    from api.config import settings
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(sc, "_checked_address", lambda url: "127.0.0.1")
    engine = _engine_for("mysql://u:p@db.example:1/x")   # nothing listens on port 1
    with pytest.raises(IngestionError, match="session setup") as err:
        sc.run_readonly(engine, "SELECT 1")
    assert "Can't connect to the database server" in err.value.user_message


def test_in_production_a_tls_session_goes_to_the_checked_address_and_verifies_the_name(
        tmp_path, monkeypatch):
    """5.8a's ponytail ("MySQL has no TLS option yet, so its host becomes the IP") is gone: the
    socket goes to the checked address and TLS still names the host (VERIFY_IDENTITY would
    check the certificate against it)."""
    import socket

    import connectors.sql_connector as sc
    from api.config import settings
    from sqlalchemy.engine import make_url
    u = make_url(_each_dialect(tmp_path, "mysql"))
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(sc, "_checked_address", lambda url: "127.0.0.1")
    dialed = []
    real = socket.create_connection
    monkeypatch.setattr(socket, "create_connection", lambda addr, *a, **k: dialed.append(
        (addr, k.get("timeout"))) or real(addr, *a, **k))
    url = u.set(host="localhost").render_as_string(hide_password=False) + "?ssl_mode=REQUIRED"
    engine = _engine_for(url)
    with engine.connect() as c:
        assert c.connection.dbapi_connection.host == "localhost"   # the name TLS is given
    assert dialed == [(("127.0.0.1", u.port), sc.CONNECT_TIMEOUT_S)]


def test_in_production_a_pinned_mysql_without_a_port_dials_3306(monkeypatch):
    import socket

    import connectors.sql_connector as sc
    from api.config import settings
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(sc, "_checked_address", lambda url: "10.0.0.5")
    dialed = []

    def dial(addr, *a, **k):
        dialed.append(addr)
        raise OSError("no route")
    monkeypatch.setattr(socket, "create_connection", dial)
    from sqlalchemy.exc import OperationalError
    with pytest.raises(OperationalError, match="Can't connect"), \
            _engine_for("mysql://u:p@db.example/x").connect():
        pass
    assert dialed == [("10.0.0.5", 3306)]


@pytest.mark.parametrize(("grant", "revoke"), MSSQL_CAN_WRITE)
async def test_every_way_a_sql_server_login_can_write_is_refused(tmp_path, grant, revoke):
    """5.8b audit (security review, test analyst): the can-write check was proven for
    db_datawriter only. A grant on a table or a schema, a role holding one, db_owner, owning a
    schema (ownership is CONTROL, with no permission row), impersonating a user who can write,
    and EXECUTE (a procedure that writes) each make a login able to change data."""
    from sqlalchemy import text

    from connectors.sql_connector import run_readonly
    url = _each_dialect(tmp_path, "mssql")
    with _admin(tmp_path, "mssql").begin() as c:
        c.execute(text("DROP TABLE IF EXISTS cw_t"))
        c.execute(text("CREATE TABLE cw_t (a INTEGER)"))
    engine = _engine_for(url)
    assert run_readonly(engine, "SELECT 1").rows == [(1,)]   # the reader as it is: allowed
    with _admin(tmp_path, "mssql").begin() as c:
        c.execute(text(grant))
    try:
        with pytest.raises(IngestionError, match="session setup") as err:
            run_readonly(engine, "SELECT 1")
        assert "connect with a read-only login" in err.value.user_message
    finally:
        with _admin(tmp_path, "mssql").begin() as c:
            c.execute(text(revoke))


def test_a_grant_a_killed_run_left_on_the_reader_is_undone_at_the_next_start(tmp_path):
    """The mutation gate's timeout killed a run between a grant and its revoke: the reader owned
    a schema, and every later run on that database failed the can-write check (P58-35)."""
    import os

    from sqlalchemy import text
    from sqlalchemy.engine import make_url

    from connectors.sql_connector import run_readonly
    from tests.conftest import _mssql_logins
    url = _each_dialect(tmp_path, "mssql")
    with _admin(tmp_path, "mssql").begin() as c:
        c.execute(text("EXEC('CREATE SCHEMA cw_owned AUTHORIZATION aai_reader')"))
    with pytest.raises(IngestionError, match="session setup"):
        run_readonly(_engine_for(url), "SELECT 1")
    admin = create_engine(make_url(os.environ["TEST_MSSQL_URL"]).set(database="master"),
                          isolation_level="AUTOCOMMIT")
    with admin.connect() as c:   # what the next start does
        _mssql_logins(c, text, make_url(os.environ["TEST_MSSQL_URL"]).database)
    admin.dispose()
    assert run_readonly(_engine_for(url), "SELECT 1").rows == [(1,)]


def test_a_sql_server_query_waits_for_a_lock_no_longer_than_the_timeout(tmp_path):
    """A table another session holds is given up on at the query timeout (the test analyst asked
    for LOCK_TIMEOUT's proof; it raced the driver's timeout at the same limit and was deleted)."""
    import time

    from sqlalchemy import text
    from sqlalchemy.exc import SQLAlchemyError

    from connectors.sql_connector import run_readonly
    url = _each_dialect(tmp_path, "mssql")
    admin = _admin(tmp_path, "mssql")
    with admin.begin() as c:
        c.execute(text("DROP TABLE IF EXISTS locked"))
        c.execute(text("CREATE TABLE locked (a INTEGER)"))
    holder = admin.connect()
    try:
        holder.execute(text("BEGIN TRANSACTION; SELECT * FROM locked WITH (TABLOCKX, HOLDLOCK)"))
        start = time.monotonic()
        with pytest.raises(SQLAlchemyError, match="Query timeout expired"):
            run_readonly(_engine_for(url), "SELECT * FROM locked", timeout_s=1)
        assert time.monotonic() - start < 5
    finally:
        holder.rollback()
        holder.close()


async def test_sql_server_apply_and_temp_tables_through_a_real_question(tmp_path):
    """The table rule on the real listing (test analyst): APPLY over a query of the user's own
    tables reads; a #temp table is never one of them."""
    from agents.errors import GuardrailViolation
    conn = _connector_with_mine(tmp_path, "mssql")
    rows = (await conn.query("SELECT m.a, x.b FROM mine m CROSS APPLY (SELECT TOP 1 a AS b "
                             "FROM mine) x")).rows
    assert rows == [(1, 1)]
    with pytest.raises(GuardrailViolation, match="#mine"):
        await conn.query("SELECT * FROM #mine")


def test_the_sql_server_admin_login_is_refused(tmp_path):
    import os

    from sqlalchemy.engine import make_url

    from connectors.sql_connector import run_readonly
    _each_dialect(tmp_path, "mssql")
    sa = make_url(os.environ["TEST_MSSQL_URL"])
    url = sa.set(drivername="mssql").difference_update_query(["driver"]).render_as_string(
        hide_password=False)
    with pytest.raises(IngestionError, match="session setup"):
        run_readonly(_engine_for(url), "SELECT 1")


def test_postgres_reads_backslashes_in_strings_as_the_guard_does(tmp_path):
    """P58-16 on Postgres (5.8b security review): a database with standard_conforming_strings
    off reads 'x\\' as an unterminated string, so text sqlglot reads as a string ran as SQL."""
    from sqlalchemy import text
    from sqlalchemy.engine import make_url

    from connectors.sql_connector import run_readonly
    url = _each_dialect(tmp_path, "postgresql")
    name = make_url(url).database
    with _admin(tmp_path, "postgresql").connect().execution_options(
            isolation_level="AUTOCOMMIT") as c:
        c.execute(text(f'ALTER DATABASE "{name}" SET standard_conforming_strings = off'))
    try:
        rows = run_readonly(_engine_for(url), "SELECT 'a\\' AS s, current_setting("
                            "'standard_conforming_strings') AS scs").rows
        assert rows == [("a\\", "on")]
    finally:
        with _admin(tmp_path, "postgresql").connect().execution_options(
                isolation_level="AUTOCOMMIT") as c:
            c.execute(text(f'ALTER DATABASE "{name}" RESET standard_conforming_strings'))


def test_sql_server_reads_double_quotes_as_names_as_the_guard_does(tmp_path):
    """P58-16 on SQL Server: QUOTED_IDENTIFIER is pinned on, so "a" is a column as sqlglot's
    T-SQL reads it, never the string 'a'."""
    from sqlalchemy import text

    from connectors.sql_connector import run_readonly
    url = _each_dialect(tmp_path, "mssql")
    with _admin(tmp_path, "mssql").begin() as c:
        c.execute(text("DROP TABLE IF EXISTS quoted"))
        c.execute(text("CREATE TABLE quoted (a INTEGER)"))
        c.execute(text("INSERT INTO quoted VALUES (7)"))
    assert run_readonly(_engine_for(url), 'SELECT "a" FROM quoted').rows == [(7,)]


@pytest.mark.parametrize("extra,second_described", [(0, False), (1, True)])
async def test_the_schema_budget_is_exact(tmp_path, monkeypatch, extra, second_described):
    """A table is described while the descriptions so far are under the budget."""
    import connectors.sql_connector as sc
    url = _sqlite(tmp_path, pd.DataFrame({"a": [1]}), "t1")
    pd.DataFrame({"b": [1]}).to_sql("t2", create_engine(url), index=False)
    conn = _sql(url, None)
    engine = await conn.engine()
    first = conn.schema_context(engine).splitlines()[0]
    monkeypatch.setattr(sc, "_SCHEMA_CHARS", len(first) + extra)
    assert ("Table `main.t2`" in conn.schema_context(engine)) is second_described


def test_a_safety_test_names_the_reason_it_expects():
    """Audit pass 2 (B10): the timeout test swallowed any error and passed on a connection
    failure. A safety test never swallows one (a broad `pytest.raises` is ruff PT011/B017's,
    repo-wide; a swallowed error is legitimate in a test reading only a prompt, so not there)."""
    import re
    from pathlib import Path
    here = Path(__file__).parent
    for f in ("test_connectors_sources.py", "test_sql_guard.py"):
        source = (here / f).read_text()
        assert not re.search(r"except Exception:\s*\n\s*pass", source), f


# ── 5.8b hardening: each engine's own types, read as the truth (a catalog, not our datasets) ──
# The 5.8b gates passed while a NUMERIC(19,0) id read as a float (two ids became one) and MySQL's
# BIT(1) as bytes: the oracle's tables are written from CSVs, so they never held these types.
# Two rows per table: edge values, then NULLs (a NULL turns an int column to float in pandas).
_ID, _BIG = 123456789012345678, "12345678901234567890123"
_P53 = 2**53 + 1   # the first whole number a float can't hold: the plain-column fast path's edge
_CATALOG = {
    "postgresql": (
        "CREATE TABLE tcat (k int, n0 numeric(19,0), n38 numeric(38,0), n2 numeric(12,2), "
        "r real, b boolean, tm time, u uuid, j jsonb, by bytea, ts timestamp(6), bi bigint, "
        "tz timestamptz)",
        f"INSERT INTO tcat VALUES (1, {_ID}, {_BIG}, 10.50, 0.1, true, '13:45:00', "
        "'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', '{\"k\": 1}', '\\x0102', '2024-03-10 23:30:00', "
        f"{_P53}, '2024-03-10 23:30:00+05'), "
        f"(2, {_ID + 1}, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL), "
        "(3, NULL, 1, 1.25, 2.5, false, '00:00:01', NULL, NULL, NULL, NULL, 1, NULL)",
        # an instant, in UTC: as the CSV Postgres exports of it reads ('2024-03-10 18:30:00+00')
        {"u": ["a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11", None, None], "j": ['{"k": 1}', None, None],
         "tz": [pd.Timestamp("2024-03-10 18:30:00", tz="UTC"), None, None]}),
    "mysql": (
        "CREATE TABLE tcat (k int, n0 decimal(19,0), n38 decimal(38,0), n2 decimal(12,2), "
        "r float, b bit(1), tm time, u char(36), j json, by_ blob, ts datetime(6), dt date, "
        "bi bigint, bt bit(8))",
        f"INSERT INTO tcat VALUES (1, {_ID}, {_BIG}, 10.50, 0.1, b'1', '13:45:00', "
        "'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', '{\"k\": 1}', x'0102', '2024-03-10 23:30:00', "
        f"'2024-03-10', {_P53}, b'101'), "
        f"(2, {_ID + 1}, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, '0000-00-00', "
        "NULL, NULL), "
        "(3, NULL, 1, 1.25, 2.5, b'0', '-25:00:01', NULL, NULL, NULL, NULL, '2024-03-00', 1, b'0')",
        {"u": ["a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11", None, None], "j": ['{"k": 1}', None, None],
         "tm": ["13:45:00", None, "-25:00:01"], "bt": [5, None, 0],   # BIT(8) is a number
         # a zero date and a partial one are blanks, and the column stays a date column
         "dt": [pd.Timestamp("2024-03-10"), None, None]}),
    "mssql": (
        "CREATE TABLE tcat (k int, n0 numeric(19,0), n38 numeric(38,0), n2 decimal(12,2), "
        "r real, b bit, tm time(0), u uniqueidentifier, j nvarchar(40), by_ varbinary(4), "
        "ts datetime2(6), bi bigint)",
        f"INSERT INTO tcat VALUES (1, {_ID}, {_BIG}, 10.50, 0.1, 1, '13:45:00', "
        "'A0EEBC99-9C0B-4EF8-BB6D-6BB9BD380A11', N'{\"k\": 1}', 0x0102, '2024-03-10 23:30:00', "
        f"{_P53}), "
        f"(2, {_ID + 1}, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL), "
        "(3, NULL, 1, 1.25, 2.5, 0, '00:00:01', NULL, NULL, NULL, NULL, 1)",
        {"u": ["A0EEBC99-9C0B-4EF8-BB6D-6BB9BD380A11", None, None], "j": ['{"k": 1}', None, None]}),
}
_CATALOG["mariadb"] = (_CATALOG["mysql"][0].replace("j json", "j longtext"), *_CATALOG["mysql"][1:])
# what every engine's table must read as: exact ids (one NULL: still exact), a 23-digit number as
# its digits, money as a float, REAL's 0.1 as 0.1 (pyodbc widens float32), a yes/no as one,
# TIME as the text a CSV holds, bytes as hex, and the timestamp as the CSV of it reads
_EXPECTED = {"n0": [_ID, _ID + 1, None], "n38": [_BIG, None, "1"], "n2": [10.5, None, 1.25],
             "r": [0.1, None, 2.5], "b": [True, None, False], "tm": ["13:45:00", None, "00:00:01"],
             "by": ["0x0102", None, None], "ts": [pd.Timestamp("2024-03-10 23:30:00"), None, None],
             "bi": [_P53, None, 1]}


def _cells(s: pd.Series) -> list:
    return [None if pd.isna(v) else v for v in s.tolist()]


@pytest.mark.parametrize("name", ["postgresql", "mysql", "mariadb", "mssql"])
async def test_each_engines_own_types_read_as_their_values(tmp_path, name):
    """Load and question alike (one rule, `QueryResult.frame`), against the values themselves."""
    from sqlalchemy import text
    ddl, rows, own = _CATALOG[name]
    url = _each_dialect(tmp_path, name)
    with _admin(tmp_path, name).begin() as c:
        if name in ("mysql", "mariadb"):
            c.execute(text("SET SESSION sql_mode = ''"))   # zero dates, as old schemas hold them
        c.execute(text("DROP TABLE IF EXISTS tcat"))
        c.execute(text(ddl))
        c.execute(text(rows))
    try:
        conn = _sql(url, "tcat")
        loaded = (await conn.load()).sort_values("k", ignore_index=True)
        if name in ("mysql", "mariadb"):   # the zero and the partial date: blanked, and said
            notes = conn.read_info.get("notes", [])
            assert any("2 values" in n and "zero or partial date" in n for n in notes), notes
        result = await conn.query("SELECT * FROM tcat ORDER BY k")
        asked = result.frame()
        if name in ("mysql", "mariadb"):   # a question's blanks said too (audit pass 2)
            assert any("2 values" in n for n in result.notes()), result.notes()
        for frame in (loaded, asked):
            frame.columns = [c.rstrip("_") for c in frame.columns]
            for col, want in {**_EXPECTED, **own}.items():
                if col in frame.columns:
                    assert _cells(frame[col]) == want, (name, col, frame[col].tolist())
            assert frame["n0"].nunique() == 2   # two ids stay two
            # money is a float column (a Decimal == float compare hid object dtype: test analyst)
            assert frame["n2"].dtype == "float64" and frame["bi"].dtype == "Int64", frame.dtypes
    finally:
        with _admin(tmp_path, name).begin() as c:
            c.execute(text("DROP TABLE tcat"))


async def test_the_database_runs_what_the_guard_read_never_the_raw_text(tmp_path, monkeypatch):
    """Canonicalize, then execute (5.8b hardening): every bypass so far was text the guard and
    the server read differently. The server gets sqlglot's writing of what the guard parsed: no
    comment survives, and a name the guard read as a name reaches the server quoted as one."""
    import connectors.sql_connector as sc
    sent = []
    real = sc.run_readonly
    monkeypatch.setattr(sc, "run_readonly", lambda e, sql, *a, **k: sent.append(sql) or real(e, sql, *a, **k))
    conn = _sql(_sqlite(tmp_path, pd.DataFrame({"a": [1, 2]})), None)
    res = await conn.query("SELECT a /* keep? */ FROM t -- a trailing note")
    assert res.rows == [(1,), (2,)] and sent == ["SELECT a FROM t"]


async def test_without_its_pre_scans_the_guard_still_never_lets_a_second_statement_run(
        tmp_path, monkeypatch):
    """The U+200B batch (5.8b audit pass 2, proven on SQL Server: the WAITFOR ran) with the
    pre-scans that refuse it switched off: the server gets sqlglot's writing of what the guard
    read, an alias, so nothing waits. This is the class closed by construction, not one more scan."""
    import time

    import guardrails
    from sqlalchemy import text
    monkeypatch.setattr(guardrails, "_invisible_outside_quotes", lambda sql, d: False)
    monkeypatch.setattr(guardrails, "_tsql_batch_word", lambda sql: None)
    url = _each_dialect(tmp_path, "mssql")
    with _admin(tmp_path, "mssql").begin() as c:
        c.execute(text("DROP TABLE IF EXISTS keyed"))
        c.execute(text("CREATE TABLE keyed (a INT)"))
        c.execute(text("INSERT INTO keyed VALUES (1)"))
    import connectors.sql_connector as sc
    sent = []
    real = sc.run_readonly
    monkeypatch.setattr(sc, "run_readonly", lambda e, sql, *a, **k: sent.append(sql) or real(e, sql, *a, **k))
    for attack in ("SELECT TOP 1 a FROM keyed x​EXEC('WAITFOR DELAY ''0:0:4''')",
                   "SELECT TOP 1 a FROM keyed x​EXEC(sp_who)"):
        started = time.monotonic()
        try:
            await _sql(url, None).query(attack)
        except Exception:   # noqa: BLE001 - refused or a syntax error: either way nothing ran
            pass
        assert time.monotonic() - started < 3, "the second statement ran"
    # what reached the server: the alias as one bracketed name, never a word the server splits
    assert sent and all("[x​EXEC]" in s for s in sent), sent


async def test_the_canonical_query_answers_exactly_as_the_models_own_text(tmp_path):
    """Hardening audit (two reviewers, on SQL Server): the first canonical form was sqlglot's
    regenerated tree, whose writer CASTs DATEDIFF's DATETIMEOFFSET arguments to DATETIME2
    (-3 hours became 2) and turns SYSDATETIME into GETDATE. Tokens only: the same answer."""
    from sqlalchemy import create_engine, text
    url = _each_dialect(tmp_path, "mssql")
    q = ("SELECT DATEDIFF(hour, CAST('2024-01-01T23:00:00-05:00' AS datetimeoffset), "
         "CAST('2024-01-02T01:00:00+00:00' AS datetimeoffset)) AS h, "
         "DATEDIFF(day, CAST('2024-01-01T23:00:00-05:00' AS datetimeoffset), "
         "CAST('2024-01-02T01:00:00+00:00' AS datetimeoffset)) AS d")
    import os
    with create_engine(os.environ["TEST_MSSQL_URL"]).connect() as c:
        raw = [tuple(r) for r in c.execute(text(q))]
    assert raw == [(-3, 0)]   # the database's own reading of the model's text
    assert (await _sql(url, None).query(q)).rows == raw


async def test_a_sql_server_question_never_reads_another_database_through_db_dot_dot(tmp_path):
    """Hardening security review, proven: `xdb..t` (database xdb, default schema) was joined to
    `xdb.t`, a listed schema of this database, and the other database's row came back."""
    import os

    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    from agents.errors import GuardrailViolation
    url = _each_dialect(tmp_path, "mssql")
    reader = make_url(os.environ["TEST_MSSQL_READER_URL"]).username
    admin = create_engine(os.environ["TEST_MSSQL_URL"], isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text("IF DB_ID('xdb_5_8b') IS NULL CREATE DATABASE xdb_5_8b"))
        c.execute(text(f"USE xdb_5_8b; IF OBJECT_ID('dbo.t') IS NULL BEGIN CREATE TABLE dbo.t "
                       f"(s NVARCHAR(20)); INSERT INTO dbo.t VALUES ('OTHER_DB_SECRET') END; "
                       f"IF USER_ID('{reader}') IS NULL CREATE USER [{reader}] FOR LOGIN [{reader}]; "
                       f"ALTER ROLE db_datareader ADD MEMBER [{reader}]; USE master"))
    with _admin(tmp_path, "mssql").begin() as c:   # a schema of the same name in this database
        c.execute(text("IF SCHEMA_ID('xdb_5_8b') IS NULL EXEC('CREATE SCHEMA xdb_5_8b')"))
        c.execute(text("IF OBJECT_ID('xdb_5_8b.t') IS NULL CREATE TABLE xdb_5_8b.t (s NVARCHAR(20))"))
    conn = _sql(url, None)
    with pytest.raises(GuardrailViolation, match="only the connected database's tables"):
        await conn.query("SELECT * FROM xdb_5_8b..t")
    assert (await conn.query("SELECT * FROM xdb_5_8b.t")).rows == []   # its own schema: allowed


def test_a_sql_server_named_instance_is_said_not_a_host_not_found():
    with pytest.raises(IngestionError, match="named instance") as err:
        _sql("mssql://u:p@db.example\\SQLEXPRESS/acme")._engine()
    assert "host:port" in err.value.user_message


def test_in_production_a_dual_stack_host_is_reached_at_its_ipv4_address(monkeypatch):
    """Senior audit: the pin took the first address, often IPv6, where many databases listen on
    IPv4 only: it failed in production and worked in development (the driver tries each)."""
    import socket

    import connectors.sql_connector as sc
    from api.config import settings
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(socket, "getaddrinfo", lambda h, *a, **k: [
        (0, 0, 0, "", ("2606:4700::6810:84e5", 0, 0, 0)), (0, 0, 0, "", ("104.16.132.229", 0))]
        if h == "db.example" else [(0, 0, 0, "", ("10.9.9.9", 0))])
    assert sc._checked_address(sc.make_url("postgresql://u:p@db.example/x")) == "104.16.132.229"
    monkeypatch.setattr(socket, "getaddrinfo", lambda h, *a, **k: [
        (0, 0, 0, "", ("2606:4700::6810:84e5", 0, 0, 0))]
        if h == "db.example" else [(0, 0, 0, "", ("10.9.9.9", 0))])
    assert sc._checked_address(sc.make_url("postgresql://u:p@db.example/x")) == "2606:4700::6810:84e5"


def test_the_ca_file_is_rebuilt_when_it_goes_and_a_bad_bundle_is_said(monkeypatch, tmp_path):
    """Silent-failure hunter, test analyst: the cached temp file, once a cleaner took it, failed
    every verified session until a restart; a bundle that isn't PEM was libpq's opaque word."""
    import os

    from api.config import settings
    from connectors.sql_connector import _ca_file, _ca_file_for
    monkeypatch.setattr(settings, "extra_ca_bundle", None)
    _ca_file_for.cache_clear()
    os.unlink(_ca_file())
    assert os.path.isfile(_ca_file())
    junk = tmp_path / "junk.pem"
    junk.write_text("not a certificate")
    monkeypatch.setattr(settings, "extra_ca_bundle", str(junk))
    with pytest.raises(IngestionError) as err:
        _ca_file()
    assert "holds no PEM certificate" in err.value.user_message
    monkeypatch.setattr(settings, "extra_ca_bundle", str(tmp_path / "missing.pem"))
    with pytest.raises(IngestionError) as err:
        _ca_file()
    assert "EXTRA_CA_BUNDLE can't be read" in err.value.user_message


@pytest.mark.parametrize("mode", ["verify-ca", "verify-full"])
def test_a_verified_postgres_session_trusts_the_image_cas(mode, monkeypatch):
    """Senior audit: libpq takes a CA file and the user can't name one (allowlist), so verify-full
    against RDS failed: the code names certifi's and the operator's EXTRA_CA_BUNDLE."""
    import certifi

    from api.config import settings
    from connectors.sql_connector import _ca_file_for
    monkeypatch.setattr(settings, "extra_ca_bundle", certifi.where())
    url = _sql(f"postgresql://u:p@db.example/x?sslmode={mode}")._engine().url
    pem = open(url.query["sslrootcert"], "rb").read()
    assert pem.count(b"BEGIN CERTIFICATE") >= 2 * open(certifi.where(), "rb").read().count(
        b"BEGIN CERTIFICATE") and _ca_file_for.cache_info().currsize >= 1
    assert "sslrootcert" not in _sql("postgresql://u:p@db.example/x?sslmode=require")._engine().url.query
    with pytest.raises(IngestionError, match="connection string options"):   # never the user's
        _sql("postgresql://u:p@db.example/x?sslmode=verify-full&sslrootcert=/etc/passwd")._engine()


@pytest.mark.parametrize("name", ["postgresql", "mysql", "mssql"])
async def test_numbers_codes_and_intervals_read_as_the_database_wrote_them(tmp_path, name):
    """Hardening audit: NUMERIC 'NaN' crashed the frame; a VARCHAR of long digits (an ICCID, its
    leading zero) became an Int64; every interval became text (Postgres age(), ts2 - ts1)."""
    from sqlalchemy import text
    conn = _sql(_each_dialect(tmp_path, name), None)
    code = (await conn.query("SELECT '00123456789012345678' AS iccid")).frame()
    assert code["iccid"].tolist() == ["00123456789012345678"]
    if name == "postgresql":
        df = (await conn.query("SELECT x::numeric AS n FROM (VALUES ('10'), ('NaN'), "
                               "('Infinity')) AS v(x)")).frame()
        assert df["n"].tolist()[0] == 10.0 and pd.isna(df["n"].tolist()[1])
        assert df["n"].tolist()[2] == float("inf")
        span = (await conn.query("SELECT '1 day 2 hours'::interval AS i")).frame()
        assert span["i"].tolist() == [pd.Timedelta(hours=26)]
    if name == "mysql":
        with _admin(tmp_path, name).begin() as c:
            c.execute(text("DROP TABLE IF EXISTS tm6"))
            c.execute(text("CREATE TABLE tm6 (t TIME(6))"))
            c.execute(text("INSERT INTO tm6 VALUES ('13:45:00.123456')"))
        assert (await conn.query("SELECT t FROM tm6")).frame()["t"].tolist() == ["13:45:00.123456"]


_BIG_RESULT = {
    "postgresql": "SELECT g, repeat('x', 100) AS s FROM generate_series(1, 3000000) g",
    "mysql": "WITH RECURSIVE r(g) AS (SELECT 1 UNION ALL SELECT g+1 FROM r WHERE g < 1000) "
             # 10^9 rows, no LIMIT: a drained result waits out the timeout, never 3 s (test analyst)
             "SELECT a.g, REPEAT('x', 100) AS s FROM r a CROSS JOIN r b CROSS JOIN r c",
    "mssql": "SELECT TOP 3000000 ROW_NUMBER() OVER (ORDER BY (SELECT 1)) AS g, REPLICATE('x', 100) "
             "AS s FROM sys.all_objects a CROSS JOIN sys.all_objects b CROSS JOIN sys.all_objects c",
}
_BIG_RESULT["mariadb"] = _BIG_RESULT["mysql"]


@pytest.mark.parametrize("name", ["postgresql", "mysql", "mariadb", "mssql"])
def test_a_question_holds_its_row_cap_never_the_whole_result(tmp_path, name):
    """psycopg and PyMySQL buffered every row before fetchmany capped them (security review:
    587 MB on Postgres, 659 MB on MySQL to return 10 of 3M rows). Measured as the process."""
    import subprocess
    import sys
    from pathlib import Path
    from tests.conftest import PEAK_MB
    url = _each_dialect(tmp_path, name)
    code = PEAK_MB + f"""
from connectors.sql_connector import SQLConnector, run_readonly
from orchestrator.state import ConnectorConfig, ConnectorType
engine = SQLConnector(ConnectorConfig(type=ConnectorType.SQL, connection_string={url!r}))._engine()
import time
before = peak_mb()
started = time.monotonic()
r = run_readonly(engine, {_BIG_RESULT[name]!r}, 10)
took = time.monotonic() - started
print(len(r.rows), r.truncated, round(peak_mb() - before), took)
"""
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=300,
                         cwd=str(Path(__file__).resolve().parents[1]))
    rows, truncated, grew, took = out.stdout.split() or (out.stderr[-500:], "", "", "")
    grew_mb = int(grew)
    # and at once: PyMySQL's close drained every remaining row, a capped preview waited out the
    # statement timeout (audit pass 2); nothing in stderr (its drain of a dropped socket)
    assert (rows, truncated) == ("10", "True") and grew_mb < 100 and float(took) < 5, \
        (name, rows, grew_mb, took)
    assert "Exception ignored" not in out.stderr, out.stderr[-800:]


def test_a_plain_result_skips_the_per_cell_work(monkeypatch):
    """A 50k-row load was 12 times slower: plain columns go straight to pandas. Pinned by what
    runs, never by a clock (test analyst: without the fast path it still met the time bound)."""
    import connectors.sql_connector as sc
    monkeypatch.setattr(sc, "exact_whole_numbers", lambda *_: pytest.fail("per-cell work ran"))
    rows = [(i, f"name {i}", i * 1.5, i % 2 == 0, None) for i in range(1000)] + \
        [(None, None, None, None, None)]
    df = sc.QueryResult(["a", "b", "c", "d", "e"], rows, False).frame()
    assert df["a"].iloc[3] == 3 and df["b"].iloc[3] == "name 3"


def test_a_leading_zero_is_never_a_whole_number():
    """An ICCID with its leading zero: the shared rule's own guard (test analyst: no test
    reached it, the query's text column takes the fast path)."""
    from connectors.base import exact_whole_numbers
    assert exact_whole_numbers(pd.Series(["0123456789012345678", None], dtype=object)) is None
    assert exact_whole_numbers(pd.Series(["123456789012345678", None], dtype=object)).tolist()[0] \
        == 123456789012345678
    # a float holds every whole number up to 2**53 itself, and not the one after it
    assert exact_whole_numbers(pd.Series([str(2**53), "-1"], dtype=object)) is None
    assert exact_whole_numbers(pd.Series([str(2**53 + 1)], dtype=object)).dtype == "Int64"
    assert exact_whole_numbers(pd.Series([str(2**63 - 1)], dtype=object)).dtype == "Int64"
    assert exact_whole_numbers(pd.Series([str(2**63)], dtype=object)).tolist() == [str(2**63)]
    assert exact_whole_numbers(pd.Series([str(-2**63)], dtype=object)).dtype == "Int64"
    assert exact_whole_numbers(pd.Series([str(-2**63 - 1)], dtype=object)).tolist() == [str(-2**63 - 1)]
    kept = exact_whole_numbers(pd.Series([str(2**60), None], index=[7, 3], dtype=object))
    assert kept.index.tolist() == [7, 3] and kept[7] == 2**60   # each value stays on its row
    past = exact_whole_numbers(pd.Series([str(2**63), None], dtype=object)).tolist()
    assert past[0] == str(2**63) and past[1] is None   # a blank stays None, never NaN in text


def test_an_offset_reads_as_minutes_east_of_utc():
    from connectors.base import _minutes
    assert [_minutes(o) for o in ("+05:30", "-0530", "+05", "-05", "Z")] == [330, -330, 300, -300, 0]


def test_a_sql_server_host_that_doesnt_answer_is_said_with_its_port():
    from sqlalchemy import create_engine

    from connectors.sql_connector import _session_error
    said = Exception("[HYT00] [Microsoft][ODBC Driver 18 for SQL Server]Login timeout expired")
    for url, where in (("mssql+pyodbc://u:p@db.example/x", "db.example:1433"),
                       ("mssql+pyodbc://u:p@db.example:14330/x", "db.example:14330")):
        assert f"can't reach {where} " in _session_error(said, create_engine(url)).user_message


def test_only_a_one_wide_tinyint_or_bit_is_yes_no():
    from pymysql.constants import FIELD_TYPE

    from connectors.sql_connector import _mysql_kind
    col = lambda code, size: ("c", code, None, size, None, None, True)   # noqa: E731
    assert _mysql_kind(col(FIELD_TYPE.TINY, 1)) == "bool"
    assert _mysql_kind(col(FIELD_TYPE.BIT, 1)) == "bool"
    assert _mysql_kind(col(FIELD_TYPE.TINY, 4)) is None
    assert _mysql_kind(col(FIELD_TYPE.VAR_STRING, 1)) is None   # a VARCHAR(1) of latin1


def test_a_blanked_date_is_said_in_its_number():
    from connectors.sql_connector import QueryResult
    one = QueryResult(["d"], [("0000-00-00",), (None,)], False, ("date",))
    two = QueryResult(["d"], [("0000-00-00",), ("2024-03-00",)], False, ("date",))
    assert one.notes()[0].startswith("1 value in `d` is a zero or partial date")
    assert one.notes()[0].endswith("has no calendar day: read as blank")
    assert two.notes()[0].startswith("2 values in `d` are a zero or partial date")


def test_a_query_that_cant_run_as_written_is_the_sql_guards_refusal():
    from agents.errors import GuardrailViolation
    from connectors.sql_connector import _cant_run_as_written
    with pytest.raises(GuardrailViolation) as err:
        _cant_run_as_written()
    assert err.value.agent == "sql"
    assert str(err.value) == "sql: the query has characters that can't be run exactly as written"


def test_a_postgres_day_is_utcs_whatever_the_servers_zone(tmp_path):
    """The session's `SET LOCAL TIME ZONE 'UTC'`: a timestamptz's day computed in the database
    is UTC's (test analyst: removing it failed nothing; instants compare equal in any zone)."""
    from sqlalchemy import create_engine

    from connectors.sql_connector import run_readonly
    url = _each_dialect(tmp_path, "postgresql")
    tokyo = create_engine(url, connect_args={"options": "-c timezone=Asia/Tokyo"})
    r = run_readonly(tokyo, "SELECT CAST(TIMESTAMPTZ '2024-03-10 20:00:00+00' AS date) AS d")
    assert str(r.rows[0][0]) == "2024-03-10"


@pytest.mark.parametrize("values", [["2024-03-10 18:30:00+00", "2024-03-11 01:00:00+00"],
                                    ["2024-03-10 23:30:00+05", "2024-03-11 01:00:00+00"]])
def test_a_postgres_export_of_instants_reads_as_instants(values):
    """Postgres writes a timestamptz with an hours-only offset: its CSV export stayed text while
    the same column read from the database was an instant (5.8b hardening, the type catalog)."""
    from connectors.base import normalize_frame
    from connectors.csv_connector import _read_bytes
    csv = "k,tz\n1," + values[0] + "\n2,\n3," + values[1] + "\n"
    df = normalize_frame(_read_bytes(csv.encode(), ".csv")[0])
    assert _cells(df["tz"]) == [pd.Timestamp("2024-03-10 18:30:00", tz="UTC"), None,
                                pd.Timestamp("2024-03-11 01:00:00", tz="UTC")]


def test_an_hours_offset_counts_only_after_a_clock_time():
    """Audit pass 2 (two reviewers): '2024-03-10 12-05' (a file stamp) became 12:00 at UTC-05,
    and in a column mixing offsets the '-04' of '2025-01-04' stripped the day."""
    from connectors.base import normalize_frame, parse_text_dates
    from connectors.csv_connector import _read_bytes
    df = normalize_frame(_read_bytes(b"shot,n\n2024-03-10 12-05,1\n2024-03-11 14-07,2\n", ".csv")[0])
    assert df["shot"].tolist() == ["2024-03-10 12-05", "2024-03-11 14-07"]
    mixed = parse_text_dates(pd.Series(["2025-01-05 10:00:00-05", "2025-01-04",
                                        "2025-01-06 09:00:00-05"]), 0.0)
    assert [t.day for t in mixed] == [5, 4, 6]
    dst = parse_text_dates(pd.Series(["2024-11-03 01:30:00-0400", "2024-11-03 01:30:00.5-0500"]), 0.0)
    assert dst.tolist() == [pd.Timestamp("2024-11-03 01:30:00"), pd.Timestamp("2024-11-03 01:30:00.5")]


def test_a_file_keeps_long_ids_exact_with_a_blank():
    """The same bug in the file reader: an id column with one blank read as float64."""
    from connectors.csv_connector import _read_bytes
    df = _read_bytes(f"id,x\n{_ID},1\n,2\n{_ID + 1},3\n{_BIG},4\n".encode(), ".csv")[0]
    assert _cells(df["id"]) == [str(_ID), None, str(_ID + 1), _BIG]   # past int64: its digits
    df = _read_bytes(f"id,x\n{_ID},1\n,2\n{_ID + 1},3\n".encode(), ".csv")[0]
    assert _cells(df["id"]) == [_ID, None, _ID + 1] and df["id"].nunique() == 2
