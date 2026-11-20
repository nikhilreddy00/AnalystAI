"""
SQL connector — PostgreSQL, MySQL and SQLite (development only) via SQLAlchemy (ADR-0010).
Questions are turned into SQL by agents/sql_agent.py; this module only loads and executes.

Every model-written query passes four layers, each tested on its own:
1. check_sql_readonly (parsed: one read statement, no INTO / locks / side-effect functions);
2. a READ ONLY transaction that is always rolled back;
3. a statement timeout and a row cap (fetchmany; a named table's load also asks for at most it);
4. the database role — operators should connect with a read-only user (docs/baselines/phase4.md).
An engine without a row in DIALECTS gets none of this, so it is refused (fail closed).
"""

from __future__ import annotations

import asyncio
import datetime
from collections.abc import Callable
from dataclasses import dataclass, field

import pandas as pd
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import NullPool

from agents.errors import GuardrailViolation, IngestionError
from connectors.base import DataConnector, resolved, truncation_note, without_secrets
from guardrails import check_sql_readonly

MAX_ROWS = 10_000
LOAD_MAX_ROWS = 50_000   # a named table is loaded whole up to this; past it, the load says so
TIMEOUT_S = 15
CONNECT_TIMEOUT_S = 10


@dataclass(frozen=True)
class Dialect:
    """What the SQL source knows about one database engine. Every reader of the engine's kind
    reads this row; an engine without one is refused everywhere."""
    sqlglot: str                     # the parse guard's and the SQL agent's dialect name
    driver: str                      # the one driver the engine is opened with ("" = the default)
    drivers: frozenset[str]          # spellings accepted in a user's string, opened as `driver`
    query_keys: frozenset[str]       # URL keys passed to the driver; any other key is refused
    begin: tuple[str, ...]           # run first on each connection: read only, then the timeout
    end: tuple[str, ...] = ()        # run after the rollback
    system_schemas: frozenset[str] = frozenset()   # the engine's own catalogs, never a user table
    connect_args: dict = field(default_factory=dict)
    one_database: bool = False       # a "schema" is a database: list only the URL's own
    yes_no: Callable[[tuple], bool] | None = None   # a cursor column the engine types as a yes/no
    # production: the URL connecting to the checked address (the name stays for TLS where the
    # driver can keep both; ponytail: MySQL has no TLS option yet, so its host becomes the IP)
    pin: Callable[[URL, str], URL] = lambda url, ip: url.set(host=ip)


DIALECTS: dict[str, Dialect] = {
    "postgresql": Dialect(
        sqlglot="postgres", driver="psycopg", drivers=frozenset({"", "psycopg", "asyncpg"}),
        query_keys=frozenset({"sslmode", "channel_binding", "connect_timeout", "application_name",
                              "target_session_attrs"}),
        begin=("SET TRANSACTION READ ONLY", "SET LOCAL statement_timeout = {ms}"),
        system_schemas=frozenset({"information_schema", "pg_catalog", "pg_toast"}),
        connect_args={"connect_timeout": CONNECT_TIMEOUT_S},
        # libpq connects to hostaddr and verifies TLS against host
        pin=lambda url, ip: url.update_query_dict({"hostaddr": ip})),
    "mysql": Dialect(
        sqlglot="mysql", driver="pymysql", drivers=frozenset({"", "pymysql"}),
        query_keys=frozenset({"charset"}),
        # SESSION read only too: a DDL statement commits the open transaction implicitly and
        # would run outside it; max_execution_time covers SELECT, the only statement let through
        begin=("SET SESSION TRANSACTION READ ONLY", "SET SESSION max_execution_time = {ms}",
               "START TRANSACTION READ ONLY"),
        system_schemas=frozenset({"information_schema", "mysql", "sys", "performance_schema"}),
        connect_args={"connect_timeout": CONNECT_TIMEOUT_S, "read_timeout": TIMEOUT_S + 15,
                      "local_infile": False, "init_command": "SET time_zone = '+00:00'"},
        one_database=True,
        yes_no=lambda c: c[1] == 1 and c[3] == 1),   # TINY of display width 1: BOOL / TINYINT(1)
    "sqlite": Dialect(   # development only (refused in production): no statement timeout
        sqlglot="sqlite", driver="", drivers=frozenset({""}), query_keys=frozenset(),
        begin=("PRAGMA query_only = ON",), end=("PRAGMA query_only = OFF",)),
}
_ALIASES = {"postgres": "postgresql"}   # Heroku's and Supabase's copied strings
SYSTEM_SCHEMAS = frozenset().union(*(d.system_schemas for d in DIALECTS.values()))
_UNREADABLE = ("The connection string couldn't be read. Use the form "
               "dialect://user:password@host:port/database (a password with @ or : must be "
               "URL-encoded, e.g. %40 for @).")


def _refuse(reason: str, message: str) -> IngestionError:
    return IngestionError(reason, message)


def dialect_of(engine) -> Dialect:
    """The engine's row, or IngestionError: nothing runs on an engine without one."""
    if (d := DIALECTS.get(engine.dialect.name)) is None:
        raise _refuse(f"no dialect {engine.dialect.name}",
                      f"{engine.dialect.name} databases aren't supported (PostgreSQL and MySQL are).")
    return d


def safe_url(conn_str: str) -> tuple[URL, Dialect]:
    """The user's connection string as the URL it is opened with, after the checks every
    database connection passes (ADR-0010): it parses; its engine and driver have a row; every
    query key is allowlisted (PyMySQL runs `init_command` on connect, Postgres `host=` reaches a
    socket); in production its host is never the app's own machine, the cloud metadata address
    or the app's own database. No message carries the string or its password."""
    try:
        url = make_url(conn_str)
        url.port   # a raw '@' or ':' in the password lands in the port; reading it raises
    except Exception:   # noqa: BLE001 - the parser's message quotes the string
        raise _refuse("unreadable connection string", _UNREADABLE) from None
    engine, _, driver = url.drivername.partition("+")
    engine = _ALIASES.get(engine, engine)
    d = DIALECTS.get(engine)
    if d is None or driver not in d.drivers:
        raise _refuse(f"no dialect {url.drivername}",
                      f"'{url.drivername}' isn't a supported database. Use postgresql:// or "
                      "mysql:// (SQLite for local development).")
    if extra := sorted(set(url.query) - d.query_keys):
        raise _refuse("connection string options", "These connection string options aren't "
                      f"allowed: {', '.join(extra)}. Allowed: {', '.join(sorted(d.query_keys)) or 'none'}.")
    from api.config import settings
    url = url.set(drivername=f"{engine}+{d.driver}" if d.driver else engine)
    if settings.app_env == "production":
        if engine == "sqlite":
            raise _refuse("sqlite in production", "SQLite is for development only. Connect a "
                          "PostgreSQL or MySQL database instead.")
        url = d.pin(url, _checked_address(url))
    return url, d


def _plain(ip):
    """An IPv4-mapped IPv6 address (::ffff:127.0.0.1) as the IPv4 address it is."""
    return getattr(ip, "ipv4_mapped", None) or ip


def _checked_address(url: URL) -> str:
    """Production: the address the database is reached at, checked. Never the app's own
    machine (loopback), a link-local or cloud metadata address, or the app's own store's host
    (any port). A LAN address is allowed (self-hosted). The caller connects to this address,
    not the name again (DNS rebinding)."""
    from api.config import settings
    if not url.host:
        raise _refuse("no host", "The connection string has no host.")
    try:
        ips = [_plain(ip) for ip in resolved(url.host)]
        own = make_url(settings.database_url)
        own_ips = {_plain(ip) for ip in resolved(own.host)} if own.host else set()
    except ValueError:
        raise _refuse("host not found", f"The database host '{url.host}' wasn't found.") from None
    if not ips:
        raise _refuse("host not found", f"The database host '{url.host}' wasn't found.")
    for ip in ips:
        if ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_multicast or \
                ip in own_ips:
            raise _refuse("host refused", f"The database host '{url.host}' isn't allowed "
                          "(it is this server itself, or a reserved address).")
    return str(ips[0])


def _tables(engine) -> dict[str, tuple[str | None, str]]:
    """THE tables a connection offers: {name: (schema, table)}. The default schema's tables by
    their bare name, every other user schema's as `schema.table` (Domain 5 minor: `hr_live.roster`
    was refused). schema_context and load both read it; a name must be in it to be queried.
    MySQL: a schema is a database, so only the URL's own is listed."""
    d = dialect_of(engine)
    inspector = inspect(engine)
    default = inspector.default_schema_name
    schemas = [default] if d.one_database and default else inspector.get_schema_names()
    out: dict[str, tuple[str | None, str]] = {}
    for schema in [s for s in schemas if s not in d.system_schemas
                   and not s.startswith("pg_temp") and not s.startswith("pg_toast")]:
        for table in inspector.get_table_names(schema=schema):
            out[f"{schema}.{table}"] = (schema, table)
            if schema == default:
                out[table] = (None, table)
    return out


def sql_dialect(engine) -> str:
    """The engine's sqlglot dialect name (the parse guard's and the SQL agent's); refuses an
    engine without a row."""
    return dialect_of(engine).sqlglot


def _assert_single_select(sql: str, dialect: str | None = None) -> str:
    """Reject anything that isn't a single read query (parsed, not prefix-matched)."""
    sql = sql.strip().rstrip(";").strip()
    reason = check_sql_readonly(sql, dialect=dialect)
    if reason:
        raise GuardrailViolation("sql", reason, f"Blocked unsafe SQL: {reason}.")
    return sql


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[tuple]
    truncated: bool          # more than max_rows existed; only the first max_rows are here
    yes_no: frozenset[str] = frozenset()   # columns the database types as a yes/no (TINYINT(1))

    def frame(self) -> pd.DataFrame:
        """The one place a database result becomes a frame (ADR-0010): what a CSV of the same
        table gives. NUMERIC/DECIMAL → float (coerce_float), a yes/no type → True/False, a
        zero date ('0000-00-00', legacy MySQL) → blank, DATE objects → datetimes."""
        df = pd.DataFrame.from_records(self.rows, columns=self.columns, coerce_float=True)
        for col in df.columns:
            if col in self.yes_no:
                df[col] = df[col].map({1: True, 0: False})
                continue
            if df[col].dtype != object:
                continue
            values = df[col].dropna()
            values = values[~values.isin(_ZERO_DATES)]
            if len(values) and values.map(lambda v: isinstance(v, datetime.date)).all():
                df[col] = pd.to_datetime(df[col].where(~df[col].isin(_ZERO_DATES)))
        return df


_ZERO_DATES = ("0000-00-00", "0000-00-00 00:00:00")


def run_readonly(engine, sql: str, max_rows: int = MAX_ROWS,
                 timeout_s: int = TIMEOUT_S) -> QueryResult:
    """Execute one query in a read-only transaction that is always rolled back, with the
    dialect's statement timeout. Sync: call it through asyncio.to_thread. Does not run the parse
    guard — callers do (layer 1). An engine without a dialect row is refused."""
    d = dialect_of(engine)
    with engine.connect() as conn:
        for statement in d.begin:
            conn.exec_driver_sql(statement.format(ms=int(timeout_s * 1000)))
        try:
            result = conn.execute(text(sql))
            yes_no = frozenset(c[0] for c in result.cursor.description or ()
                               if d.yes_no and d.yes_no(c))
            rows = [tuple(r) for r in result.fetchmany(max_rows + 1)]
            return QueryResult(list(result.keys()), rows[:max_rows], len(rows) > max_rows, yes_no)
        finally:
            conn.rollback()
            for statement in d.end:
                conn.exec_driver_sql(statement)


class SQLConnector(DataConnector):
    def __init__(self, config) -> None:
        super().__init__(config)
        from api.config import settings
        if settings.app_env == "production" and \
                (config.connection_string or "").lower().startswith("sqlite"):
            raise IngestionError("sqlite in production", "SQLite is for development only. "
                                 "Connect a PostgreSQL or MySQL database instead.")

    def _engine(self):
        conn_str = self.config.connection_string
        if not conn_str:
            raise ValueError("connection_string is required for SQL connector")
        url, d = safe_url(conn_str)
        # NullPool: a connector lives for one request and builds an engine per call, so a pool
        # would leave idle connections open on the user's database (measured: 4 after 5 queries).
        return create_engine(url, poolclass=NullPool, connect_args=d.connect_args)

    def schema_context(self, engine) -> str:
        """Introspected tables and columns, for when no semantic layer is configured."""
        inspector = inspect(engine)
        default, parts, per_schema = inspector.default_schema_name, [], {}
        for schema, table in dict.fromkeys((s or default, t) for s, t in _tables(engine).values()):
            if per_schema.setdefault(schema, 0) >= 20:   # cap prompt size
                continue
            per_schema[schema] += 1
            cols = inspector.get_columns(table, schema=schema)
            col_desc = ", ".join(f"{c['name']} ({c['type']})" for c in cols)
            parts.append(f"Table `{schema}.{table}`: {col_desc}")
        return "\n".join(parts)

    async def query(self, sql: str, max_rows: int = MAX_ROWS) -> QueryResult:
        """Guard then execute read-only. Raises GuardrailViolation for anything but one read."""
        engine = self._engine()
        sql = _assert_single_select(sql, sql_dialect(engine))
        return await asyncio.to_thread(run_readonly, engine, sql, max_rows)

    async def _read(self) -> pd.DataFrame:
        engine = self._engine()
        table = self.config.table_name

        if not table:
            raise ValueError("table_name is required for SQL connector direct load")

        # BUG-02: Validate table name against DB inspector to prevent SQL injection
        valid_tables = await asyncio.to_thread(_tables, engine)
        if table not in valid_tables:
            raise ValueError(
                f"Table '{table}' does not exist. Available tables: {list(valid_tables)[:20]}"
            )
        schema, name = valid_tables[table]
        quote = engine.dialect.identifier_preparer.quote_identifier
        qualified = f"{quote(schema)}.{quote(name)}" if schema else quote(name)
        # The same read-only, timed path as a question (the name was checked just above).
        # LIMIT: a driver buffers the whole result before fetchmany (ponytail: LIMIT syntax;
        # SQL Server's TOP comes with its dialect row in 5.8b)
        res = await asyncio.to_thread(run_readonly, engine,
                                      f"SELECT * FROM {qualified} LIMIT {LOAD_MAX_ROWS + 1}",
                                      LOAD_MAX_ROWS)
        if res.truncated:
            try:
                count = await asyncio.to_thread(run_readonly, engine,
                                                f"SELECT COUNT(*) FROM {qualified}", 1)
                total = int(count.rows[0][0])
            except SQLAlchemyError:   # a count past the timeout: "the source has more" still holds
                total = None
            self.read_info = {"notes": [truncation_note(
                len(res.rows), total, f"a named table loads at most {LOAD_MAX_ROWS:,} rows; "
                "connect without a table to have questions answered in the database")]}
        return res.frame()

    async def test_connection(self) -> tuple[bool, str]:
        try:
            engine = self._engine()

            def _probe() -> None:
                with engine.connect() as conn:
                    conn.execute(text("SELECT 1"))

            await asyncio.to_thread(_probe)
            from semantic_layer import check_against_db, configured_layer
            layer = configured_layer()
            stale = await asyncio.to_thread(check_against_db, layer, engine) if layer else []
            if stale:   # connected, but questions would be answered against a stale layer
                return True, ("Connection successful, but the semantic layer does not match this "
                              "database: " + "; ".join(stale[:5]))
            return True, "Connection successful"
        except Exception as e:
            return False, without_secrets(getattr(e, "user_message", str(e)), self.config)
