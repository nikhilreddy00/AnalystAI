"""Data sources: caps are disclosed, every SQL read is read-only and timed, SQLite stays out of
production, Sheets survives pandas 3, common API shapes load, and the E2E harness can feed any
file format or a database through the same graph path. Offline: no network, no keys."""
from __future__ import annotations

import json

import httpx
import pandas as pd
import pytest
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
    assert seen and seen[0].startswith('SELECT * FROM "t"')


# ── 3. SQLite is a development database ────────────────────────────────────────────────────

def test_sqlite_is_refused_in_production(monkeypatch):
    from api.config import settings
    monkeypatch.setattr(settings, "app_env", "production")
    with pytest.raises(IngestionError) as err:
        _sql("sqlite:///x.db")
    assert "SQLite" in err.value.user_message
    monkeypatch.setattr(settings, "app_env", "development")
    assert _sql("sqlite:///x.db") is not None


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


@pytest.mark.parametrize("name", DIALECT_NAMES)
@pytest.mark.parametrize("write", ["INSERT INTO ro_t VALUES (2)", "DROP TABLE ro_t",
                                   "DELETE FROM ro_t", "UPDATE ro_t SET a = 3"])
def test_a_write_through_run_readonly_fails_on_every_dialect(tmp_path, name, write):
    """Layer 2 alone (the parse guard is skipped: run_readonly is called directly), on the real
    database: a write fails and the table is untouched."""
    from sqlalchemy import text

    from connectors.sql_connector import SQLConnector, run_readonly
    url = _each_dialect(tmp_path, name)
    engine = SQLConnector(ConnectorConfig(type=ConnectorType.SQL, connection_string=url))._engine()
    with engine.begin() as c:
        c.execute(text("DROP TABLE IF EXISTS ro_t"))
        c.execute(text("CREATE TABLE ro_t (a INTEGER)"))
        c.execute(text("INSERT INTO ro_t VALUES (1)"))
    with pytest.raises(Exception):
        run_readonly(engine, write)
    with engine.connect() as c:
        assert c.execute(text("SELECT a FROM ro_t")).fetchall() == [(1,)]


@pytest.mark.parametrize("name,slow", [("postgresql", "SELECT pg_sleep(20)"),
                                       ("mysql", "SELECT SLEEP(20)")])
def test_a_slow_query_stops_at_the_timeout(tmp_path, name, slow):
    """Layer 3 on the real database. MySQL's SLEEP cut by max_execution_time returns 1 without
    raising, so the check is the time it took."""
    import time

    from connectors.sql_connector import SQLConnector, run_readonly
    url = _each_dialect(tmp_path, name)
    engine = SQLConnector(ConnectorConfig(type=ConnectorType.SQL, connection_string=url))._engine()
    start = time.monotonic()
    try:
        run_readonly(engine, slow, timeout_s=1)
    except Exception:
        pass
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
    connector's nor the route's message holds the password."""
    from api.routes.connectors import test_connection
    config = ConnectorConfig(type=ConnectorType.SQL, connection_string=url)
    ok, message = await get_connector(config).test_connection()
    route = await test_connection(config, None)
    for text in (message, route.message):
        assert not ok and "w0rd" not in text and "s3cr" not in text and "ss:w0rd" not in text


@pytest.mark.parametrize("host,refused", [("127.0.0.1", True), ("169.254.169.254", True),
                                          ("appdb.internal", True), ("10.0.0.5", False),
                                          ("93.184.216.34", False)])
def test_in_production_a_database_host_is_never_the_app_itself_or_the_cloud_metadata(
        monkeypatch, host, refused):
    """A user's database may sit on the company's LAN (self-hosted: 10.x is the usual case), but
    never on the app's own machine, the cloud metadata address, or the app's own store (Superset
    CVE-2023-39265 read its own metadata database). Development and CI use localhost."""
    import socket

    from api.config import settings
    ips = {"appdb.internal": "10.1.2.3"}
    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda h, *a, **k: [(0, 0, 0, "", (ips.get(h, h), 0))])
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "database_url", "postgresql+asyncpg://a:b@appdb.internal:5432/x")
    url = f"postgresql://u:p@{host}:5432/db"
    if refused:
        with pytest.raises(IngestionError):
            _sql(url)._engine()
    else:
        assert _sql(url)._engine() is not None


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
