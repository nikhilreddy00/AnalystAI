"""Data sources: caps are disclosed, every SQL read is read-only and timed, SQLite stays out of
production, Sheets survives pandas 3, common API shapes load, and the E2E harness can feed any
file format or a database through the same graph path. Offline: no network, no keys."""
from __future__ import annotations

import json

import httpx
import pandas as pd
import pytest
from hypothesis import given
from hypothesis import strategies as st
from sqlalchemy import create_engine

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

async def test_the_table_load_runs_through_run_readonly(tmp_path, monkeypatch):
    import connectors.sql_connector as sc
    seen = []
    real = sc.run_readonly
    monkeypatch.setattr(sc, "run_readonly", lambda e, sql, *a, **k: seen.append(sql) or real(
        e, sql, *a, **k))
    conn = _sql(_sqlite(tmp_path, pd.DataFrame({"a": [1]})))
    await conn.load()
    assert seen and seen[0] == 'SELECT * FROM "t" LIMIT 50001'   # no key: no ORDER BY


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

def _db_urls(tmp_path) -> dict[str, str]:
    """A scratch database per dialect: SQLite always, Postgres and MySQL where CI (or a local
    run) names one. A dialect without one here is skipped, never passed."""
    import os
    urls = {"sqlite": f"sqlite:///{tmp_path / 'ro.sqlite'}"}
    for name, env in (("postgresql", "TEST_PG_URL"), ("mysql", "TEST_MYSQL_URL")):
        if os.environ.get(env):
            urls[name] = os.environ[env]
    return urls


def _each_dialect(tmp_path, name):
    from connectors.sql_connector import DIALECTS
    assert name in DIALECTS, f"no dialect row: {name}"
    url = _db_urls(tmp_path).get(name)
    if not url:
        pytest.skip(f"no {name} database here (CI sets one)")
    return url


DIALECT_NAMES = ["sqlite", "postgresql", "mysql"]
# The database's own read-only refusal, per dialect: a write must fail for THIS reason. Any error
# passed before (audit pass 2, B3): a no-row INSERT fails on fetchmany and the rollback undoes it,
# so 5 of 6 writes passed with SQLite's read-only row emptied.
_READ_ONLY_ERROR = {"sqlite": "readonly", "postgresql": "read-only transaction",
                    "mysql": "READ ONLY transaction"}


def _engine_for(url):
    from connectors.sql_connector import SQLConnector
    return SQLConnector(ConnectorConfig(type=ConnectorType.SQL, connection_string=url))._engine()


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
    with engine.begin() as c:
        c.execute(text("DROP TABLE IF EXISTS ro_t"))
        c.execute(text("DROP TABLE IF EXISTS ro_new"))
        c.execute(text("CREATE TABLE ro_t (a INTEGER)"))
        c.execute(text("INSERT INTO ro_t VALUES (1)"))
    with pytest.raises(SQLAlchemyError, match=_READ_ONLY_ERROR[name]):
        run_readonly(engine, write)
    with engine.connect() as c:
        assert c.execute(text("SELECT a FROM ro_t")).fetchall() == [(1,)]
    assert "ro_new" not in inspect(engine).get_table_names()


@pytest.mark.parametrize("name,slow", [("postgresql", "SELECT pg_sleep(20)"),
                                       ("mysql", "SELECT SLEEP(20)")])
def test_a_slow_query_stops_at_the_timeout(tmp_path, name, slow):
    """Layer 3 on the real database: Postgres cancels the statement; MySQL's SLEEP cut by
    max_execution_time returns 1 instead of 0 (no error). Any other error fails the test."""
    import time

    from sqlalchemy.exc import OperationalError

    from connectors.sql_connector import run_readonly
    engine = _engine_for(_each_dialect(tmp_path, name))
    start = time.monotonic()
    if name == "postgresql":
        with pytest.raises(OperationalError, match="statement timeout"):
            run_readonly(engine, slow, timeout_s=1)
    else:
        assert run_readonly(engine, slow, timeout_s=1).rows == [(1,)]
    assert time.monotonic() - start < 5


@pytest.mark.parametrize("url", ["mssql+pymssql://u:p@h/db", "oracle+oracledb://u:p@h/db",
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
    engine = type("Engine", (), {"dialect": type("D", (), {"name": "mssql"})(),
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


@pytest.mark.parametrize("scheme", ["postgresql", "mysql+pymysql"])
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
    pinned = {**engine.url.query, "host": engine.url.host}
    assert "93.184.216.34" in (pinned.get("hostaddr"), pinned["host"])


def test_a_question_result_takes_the_same_load_rule_as_a_table_load():
    """ADR-0010 "values are normalised once": a question's rows (QueryResult.frame, the SQL
    agent's path) get the rule a table load does: text dates parse, '' is a blank. A TINYINT(1)
    that holds a 2 is a number, not a yes/no with the 2 lost."""
    from connectors.sql_connector import QueryResult
    df = QueryResult(["day", "note", "flag", "level"],
                     [("2026-01-05", "", 1, 2), ("2026-02-01", "x", 0, 0)], False,
                     frozenset({2, 3})).frame()
    assert str(df["day"].dtype).startswith("datetime64") and df["note"].isna().tolist() == [True, False]
    assert df["flag"].tolist() == [True, False] and df["level"].tolist() == [2, 0]


async def test_a_named_table_load_asks_the_database_for_at_most_the_cap(tmp_path, monkeypatch):
    """Drivers buffer a whole result before fetchmany: the load's SQL bounds the rows itself."""
    import connectors.sql_connector as sc
    seen = []
    real = sc.run_readonly
    monkeypatch.setattr(sc, "run_readonly", lambda e, sql, *a, **k: seen.append(sql) or real(
        e, sql, *a, **k))
    await _sql(_sqlite(tmp_path, pd.DataFrame({"a": [1]}))).load()
    assert seen[0].endswith(f"LIMIT {sc.LOAD_MAX_ROWS + 1}")


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
    """Serve `pages` (url → JSON) through httpx.MockTransport; the SSRF guard still runs and sees
    a public address for every host."""
    import socket

    import connectors.api_connector as ac
    pages: dict[str, object] = {}
    headers: list[dict] = []
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, *a, **k: [(0, 0, 0, "", ("93.184.216.34", 0))])

    def handler(request: httpx.Request) -> httpx.Response:
        headers.append(dict(request.headers))
        return httpx.Response(200, json=pages[str(request.url)])
    real = httpx.AsyncClient
    monkeypatch.setattr(ac.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
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


async def test_the_ssrf_guard_still_blocks_private_hosts(monkeypatch):
    import socket
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(0, 0, 0, "", ("10.0.0.1", 0))])
    with pytest.raises(ValueError, match="non-public"):
        await _api_load()


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
     "'mssql+pymssql' isn't a supported database. Use postgresql:// or mysql:// (SQLite for "
     "local development)."),
    ("mysql://u:p@h/db?init_command=x", _DEV, "connection string options",
     "These connection string options aren't allowed: init_command. Allowed: charset."),
    ("mysql://u:p@h/db?local_infile=1&init_command=x", _DEV, "connection string options",
     "These connection string options aren't allowed: init_command, local_infile. Allowed: charset."),
    ("postgresql://u:p@h/db?passfile=/x", _DEV, "connection string options",
     "These connection string options aren't allowed: passfile. Allowed: application_name, "
     "channel_binding, connect_timeout, sslmode, target_session_attrs."),
    ("sqlite:///x.db?mode=ro", _DEV, "connection string options",
     "These connection string options aren't allowed: mode. Allowed: none."),
    ("sqlite:///x.db", _PROD, "sqlite in production",
     "SQLite is for development only. Connect a PostgreSQL or MySQL database instead."),
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
    engine = type("Engine", (), {"dialect": type("D", (), {"name": "mssql"})()})()
    with pytest.raises(IngestionError) as err:
        dialect_of(engine)
    assert (str(err.value), err.value.user_message) == (
        "ingestion: no dialect mssql", "mssql databases aren't supported (PostgreSQL and MySQL are).")


@pytest.mark.parametrize("conn,auth,sa,message,want", [
    # the whole string, then its user-info, then the password (split at the first ':' only)
    ("mysql://reader:p:a:ss@h/db", None, None, "bad mysql://reader:p:a:ss@h/db p:a:ss reader:p:a:ss",
     "bad *** *** ***"),
    ("postgresql://reader:s3cr%40t@h/db", None, None, "pw s3cr@t and s3cr%40t", "pw *** and ***"),
    ("postgresql://onlyuser@h/db", None, None, "user onlyuser XXXX", "user *** XXXX"),
    ("postgresql://a:zzz@h/db", None, None, "a:zzz", "***"),     # longest first, not abc order
    ("postgresql://u:abc@h/db", None, None, "abc", "***"),       # three characters: a secret
    ("postgresql://u:ab@h/db", None, None, "ab cab", "ab cab"),  # two: too short to cut safely
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
    df = QueryResult(list(names), rows, False, frozenset({0}), frozenset({len(names) - 1})).frame()
    assert list(df.columns) == names and len(df) == len(rows)
    assert list(normalize_frame(df).columns) == names


@pytest.mark.parametrize("name", DIALECT_NAMES)
def test_a_join_returning_two_id_columns_reads_on_every_dialect(tmp_path, name):
    from sqlalchemy import text

    from connectors.sql_connector import run_readonly
    engine = _engine_for(_each_dialect(tmp_path, name))
    with engine.begin() as c:
        for t in ("ja", "jb"):
            c.execute(text(f"DROP TABLE IF EXISTS {t}"))
            c.execute(text(f"CREATE TABLE {t} (id INTEGER)"))
            c.execute(text(f"INSERT INTO {t} VALUES (1)"))
    df = run_readonly(engine, "SELECT ja.id, jb.id FROM ja JOIN jb ON ja.id = jb.id").frame()
    assert list(df.columns) == ["id", "id"] and df.shape == (1, 2)


def _connector_with_mine(tmp_path, name):
    from sqlalchemy import text
    url = _each_dialect(tmp_path, name)
    with _engine_for(url).begin() as c:
        c.execute(text("DROP TABLE IF EXISTS mine"))
        c.execute(text("CREATE TABLE mine (a INTEGER)"))
        c.execute(text("INSERT INTO mine VALUES (1)"))
        if name == "mysql":
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
        "the database's own tables are not readable" in said or "function not allowed" in said


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
                "WITH RECURSIVE r AS (SELECT 1 AS n UNION ALL SELECT n + 1 FROM r WHERE n < 1) "
                "SELECT a FROM mine JOIN r ON r.n = mine.a"]:
        assert (await conn.query(sql)).rows == [(1,)], sql


async def test_a_function_read_as_a_table_is_named_in_the_refusal(tmp_path):
    from agents.errors import GuardrailViolation
    conn = _connector_with_mine(tmp_path, "postgresql")
    with pytest.raises(GuardrailViolation) as err:
        await conn.query("SELECT * FROM some_srf(1) AS s, other_srf(2) AS o")   # read as tables
    assert err.value.user_message == ("Blocked unsafe SQL: only the connected database's tables "
                                      "can be read: SOME_SRF(), OTHER_SRF().")


async def test_a_postgres_set_returning_function_is_not_a_table(tmp_path):
    conn = _connector_with_mine(tmp_path, "postgresql")
    assert len((await conn.query("SELECT * FROM generate_series(1, 3) AS g")).rows) == 3


def test_a_mysql_string_must_name_its_database():
    """B7: without one, every database on the server was listed to the model."""
    with pytest.raises(IngestionError) as err:
        _sql("mysql+pymysql://u:p@h:3306/")._engine()
    assert str(err.value) == "ingestion: no database"
    assert err.value.user_message == ("The connection string must name the database: "
                                      "mysql://user:password@host:port/<database>.")


def test_a_database_that_cant_be_reached_is_a_session_error_not_a_crash(tmp_path):
    """B5: run_readonly's setup failure is an IngestionError naming the session (no connection
    here: nothing to close)."""
    from connectors.sql_connector import run_readonly
    engine = _engine_for(f"sqlite:///{tmp_path / 'no_dir' / 'x.sqlite'}")
    with pytest.raises(IngestionError) as err:
        run_readonly(engine, "SELECT 1")
    assert str(err.value) == "ingestion: session setup"
    assert err.value.user_message.startswith("Couldn't open a read-only session on the database: ")


@pytest.mark.parametrize("name", ["postgresql", "mysql"])
async def test_a_capped_table_load_reads_the_first_rows_by_key(tmp_path, name, monkeypatch):
    """B8: LIMIT without ORDER BY read an arbitrary subset past the cap (two runs, two datasets;
    a resumed run: 'the data changed'). A keyed table loads in key order."""
    from sqlalchemy import text

    import connectors.sql_connector as sc
    url = _each_dialect(tmp_path, name)
    with _engine_for(url).begin() as c:
        c.execute(text("DROP TABLE IF EXISTS keyed"))
        c.execute(text("CREATE TABLE keyed (k INTEGER PRIMARY KEY, v INTEGER)"))
        c.execute(text("INSERT INTO keyed VALUES (5, 50), (3, 30), (9, 90), (1, 10)"))
        c.execute(text("UPDATE keyed SET v = v + 1 WHERE k = 1"))   # PG: k=1 moves to the end
    monkeypatch.setattr(sc, "LOAD_MAX_ROWS", 2)
    assert list((await _sql(url, "keyed").load())["k"]) == [1, 3]


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


def test_the_mysql_session_mode_reads_sql_as_the_guard_does(tmp_path):
    """B11a: with ANSI_QUOTES or NO_BACKSLASH_ESCAPES on the server, MySQL would read a string
    differently from the parse guard. The connection's init_command takes both off."""
    import pymysql
    from sqlalchemy.engine import make_url

    from connectors.sql_connector import DIALECTS
    u = make_url(_each_dialect(tmp_path, "mysql"))
    c = pymysql.connect(host=u.host, port=u.port, user=u.username, password=u.password,
                        database=u.database)
    try:
        cur = c.cursor()
        cur.execute("SET SESSION sql_mode = 'ANSI_QUOTES,NO_BACKSLASH_ESCAPES,STRICT_ALL_TABLES'")
        cur.execute(DIALECTS["mysql"].connect_args["init_command"])
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
    """Audit pass 2 (B3, B10): expecting any Exception, or swallowing one, let a safety test
    pass on any error. The source and guard tests name the error they expect."""
    import re
    from pathlib import Path
    here = Path(__file__).parent
    for f in ("test_connectors_sources.py", "test_sql_guard.py"):
        source = (here / f).read_text()
        assert not re.search(r"raises\(Exception\)|except Exception:\s*\n\s*pass", source), f
