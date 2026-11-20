"""
SQL connector — PostgreSQL, MySQL and SQLite (development only) via SQLAlchemy (ADR-0010).
Questions are turned into SQL by agents/sql_agent.py; this module only loads and executes.

Every model-written query passes four layers, each tested on its own:
1. check_sql_readonly (parsed: one read statement, no INTO / locks / side-effect functions), then
   the connection's own rule: it reads only tables `_tables` lists (the load's rule too);
2. a READ ONLY transaction that is always rolled back;
3. a statement timeout and a row cap (fetchmany; a named table's load also asks for at most it);
4. the database role — operators should connect with a read-only user (docs/baselines/phase4.md).
An engine without a row in DIALECTS gets none of this, so it is refused (fail closed).
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field

import pandas as pd
import sqlglot
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import NullPool
from sqlglot import exp
from sqlglot.optimizer.scope import traverse_scope

from agents.errors import AgentError, GuardrailViolation, IngestionError
from connectors.base import DataConnector, normalize_frame, resolved, truncation_note
from guardrails import check_sql_readonly

logger = logging.getLogger(__name__)

MAX_ROWS = 10_000
LOAD_MAX_ROWS = 50_000   # a named table is loaded whole up to this; past it, the load says so
TIMEOUT_S = 15
CONNECT_TIMEOUT_S = 10
_TYPE_CODE, _INTERNAL_SIZE, _MYSQL_TINY = 1, 3, 1   # cursor.description fields; FIELD_TYPE.TINY
_MYSQL_DATES = frozenset({7, 10, 12, 14})   # FIELD_TYPE TIMESTAMP, DATE, DATETIME, NEWDATE
# MySQL's zero date, as PyMySQL returns what it can't convert: DATE, DATETIME, DATETIME(fsp)
_ZERO_DATE = re.compile(r"0000-00-00( 00:00:00(\.0+)?)?")


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
    dates: Callable[[tuple], bool] | None = None    # one it types as a date (may hold zero dates)
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
        # UTC; and the parse guard reads strings and quotes as MySQL's default mode does, so a
        # server's ANSI_QUOTES / NO_BACKSLASH_ESCAPES can't make the two read a query differently
        connect_args={"connect_timeout": CONNECT_TIMEOUT_S, "read_timeout": TIMEOUT_S + 15,
                      "local_infile": False, "init_command":
                      "SET time_zone = '+00:00', sql_mode = REPLACE(REPLACE(@@sql_mode, "
                      "'NO_BACKSLASH_ESCAPES', ''), 'ANSI_QUOTES', '')"},
        one_database=True,
        # a DB-API description is (name, type_code, display_size, internal_size, …): PyMySQL's
        # TINY type (1) of width 1 is BOOL / TINYINT(1)
        yes_no=lambda col: col[_TYPE_CODE] == _MYSQL_TINY and col[_INTERNAL_SIZE] == 1,
        dates=lambda col: col[_TYPE_CODE] in _MYSQL_DATES),
    "sqlite": Dialect(   # development only (refused in production): no statement timeout
        sqlglot="sqlite", driver="", drivers=frozenset({""}), query_keys=frozenset(),
        begin=("PRAGMA query_only = ON",), end=("PRAGMA query_only = OFF",)),
}
_ALIASES = {"postgres": "postgresql"}   # Heroku's and Supabase's copied strings
SYSTEM_SCHEMAS = frozenset().union(*(d.system_schemas for d in DIALECTS.values()))
_UNREADABLE = ("The connection string couldn't be read. Use the form "
               "dialect://user:password@host:port/database (a password with @ or : must be "
               "URL-encoded, e.g. %40 for @).")


def dialect_of(engine) -> Dialect:
    """The engine's row, or IngestionError: nothing runs on an engine without one."""
    if (d := DIALECTS.get(engine.dialect.name)) is None:
        raise IngestionError(f"no dialect {engine.dialect.name}", f"{engine.dialect.name} "
                             "databases aren't supported (PostgreSQL and MySQL are).")
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
        raise IngestionError("unreadable connection string", _UNREADABLE) from None
    engine, _, driver = url.drivername.partition("+")
    engine = _ALIASES.get(engine, engine)
    d = DIALECTS.get(engine)
    if d is None or driver not in d.drivers:
        raise IngestionError(f"no dialect {url.drivername}",
                      f"'{url.drivername}' isn't a supported database. Use postgresql:// or "
                      "mysql:// (SQLite for local development).")
    if extra := sorted(set(url.query) - d.query_keys):
        raise IngestionError("connection string options", "These connection string options aren't "
                      f"allowed: {', '.join(extra)}. Allowed: {', '.join(sorted(d.query_keys)) or 'none'}.")
    if d.one_database and not url.database:   # else every database on the server is listed
        raise IngestionError("no database", "The connection string must name the database: "
                             f"{engine}://user:password@host:port/<database>.")
    from api.config import settings
    url = url.set(drivername=f"{engine}+{d.driver}" if d.driver else engine)
    if settings.app_env == "production":
        if engine == "sqlite":
            raise IngestionError("sqlite in production", "SQLite is for development only. Connect a "
                          "PostgreSQL or MySQL database instead.")
        url = d.pin(url, _checked_address(url))
    return url, d


def _checked_address(url: URL) -> str:
    """Production: the address the database is reached at, checked. Never the app's own
    machine (loopback), a link-local or cloud metadata address, or the app's own store's
    address (any port). A LAN address is allowed (self-hosted). The caller connects to this
    address, not the name again (DNS rebinding). The store's other routes (the host's LAN address
    of a published port) are the deployment's: compose binds its port to loopback."""
    from api.config import settings
    if not url.host:
        raise IngestionError("no host", "The connection string has no host.")
    try:
        ips = resolved(url.host)
    except ValueError:
        ips = []
    if not ips:
        raise IngestionError("host not found", f"The database host '{url.host}' wasn't found.")
    own = make_url(settings.database_url)
    # fail closed: without the app store's addresses the check can't tell the user's host from it
    # (the message says it's this server's problem, not the user's host)
    try:
        own_ips = set(resolved(own.host)) if own.host else set()
    except ValueError:
        raise IngestionError("own store unresolved", "This server can't check database hosts "
                             "right now (its own database address didn't resolve). Try again "
                             "shortly, or ask the administrator.") from None
    for ip in ips:
        if ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_multicast or \
                ip in own_ips:
            raise IngestionError("host refused", f"The database host '{url.host}' isn't allowed "
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


def _assert_single_select(sql: str, dialect: str) -> str:
    """Reject anything that isn't a single read query (parsed, not prefix-matched)."""
    sql = sql.strip().rstrip(";").strip()
    reason = check_sql_readonly(sql, dialect=dialect)
    if reason:
        raise GuardrailViolation("sql", reason, f"Blocked unsafe SQL: {reason}.")
    return sql


def _unlisted_tables(sql: str, d: Dialect, listed) -> list[str]:
    """The tables `sql` reads that the connection doesn't list (`_tables`: what may be read, one
    rule for a load and a question). A name is a CTE only where sqlglot's scopes resolve it to one
    (`WITH pg_shadow AS (SELECT * FROM pg_shadow)` reads the real one inside); the only function
    read as a table is generate_series (pg_stat_get_activity(...) reads the server's sessions)."""
    tree = sqlglot.parse_one(sql, read=d.sqlglot)
    try:
        real = {id(s) for scope in traverse_scope(tree) for s in scope.sources.values()
                if isinstance(s, exp.Table)}
    except sqlglot.errors.SqlglotError:   # fail closed: tables we can't resolve aren't read
        return ["(a query whose tables couldn't be resolved)"]
    ctes = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    known = {name.lower() for name in listed}
    out = []
    for t in tree.find_all(exp.Table):
        if isinstance(t.this, exp.ExplodingGenerateSeries):
            continue
        if not isinstance(t.this, exp.Identifier):
            out.append(f"{t.this.sql(d.sqlglot).split('(')[0]}()")
            continue
        name = ".".join(p for p in (t.catalog, t.db, t.name) if p)
        cte_ref = id(t) not in real and name == t.name and t.name.lower() in ctes
        if not cte_ref and name.lower() not in known:
            out.append(name)
    return out


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[tuple]
    truncated: bool          # more than max_rows existed; only the first max_rows are here
    yes_no: frozenset[int] = frozenset()   # positions the database types as a yes/no (TINYINT(1))
    dates: frozenset[int] = frozenset()    # positions it types as a date

    def frame(self) -> pd.DataFrame:
        """The one place a database result becomes a frame (ADR-0010), a table load and a
        question's result alike: what a CSV of the same table gives. NUMERIC/DECIMAL → float
        (coerce_float), a yes/no type holding only 0/1 → True/False (a TINYINT(1) may hold 2: then
        it stays a number), a date column's zero date (legacy MySQL, any precision) → blank; then
        the load rule every source ends in (normalize_frame: DATE objects become datetimes
        there). Columns by position: a join can return two `id` columns."""
        df = pd.DataFrame.from_records(self.rows, columns=self.columns, coerce_float=True)
        for i in range(df.shape[1]):
            s = df.iloc[:, i]
            if i in self.yes_no and s.dropna().isin((0, 1)).all():
                df.isetitem(i, s.map({1: True, 0: False}))
            elif i in self.dates:
                df.isetitem(i, s.mask(s.map(lambda v: isinstance(v, str)
                                            and _ZERO_DATE.fullmatch(v) is not None)))
        return normalize_frame(df)


def run_readonly(engine, sql: str, max_rows: int = MAX_ROWS,
                 timeout_s: int = TIMEOUT_S) -> QueryResult:
    """Execute one query in a read-only transaction that is always rolled back, with the
    dialect's statement timeout. Sync: call it through asyncio.to_thread. Does not run the parse
    guard — callers do (layer 1). An engine without a dialect row is refused; a session that
    can't be opened read-only and timed (no connection, a statement the server lacks) is an
    IngestionError, never the query's fault."""
    d = dialect_of(engine)
    conn = None
    try:
        conn = engine.connect()
        for statement in d.begin:
            conn.exec_driver_sql(statement.format(ms=int(timeout_s * 1000)))
    except SQLAlchemyError as exc:
        if conn is not None:
            conn.close()
        said = str(getattr(exc, "orig", None) or exc).strip().splitlines()[0][:300]
        if engine.url.password:
            said = said.replace(engine.url.password, "***")
        raise IngestionError("session setup", "Couldn't open a read-only session on the "
                             f"database: {said}") from exc
    with conn:
        try:
            result = conn.execute(text(sql))
            described = list(enumerate(result.cursor.description or ()))
            yes_no = frozenset(i for i, c in described if d.yes_no and d.yes_no(c))
            dates = frozenset(i for i, c in described if d.dates and d.dates(c))
            rows = [tuple(r) for r in result.fetchmany(max_rows + 1)]
            return QueryResult(list(result.keys()), rows[:max_rows], len(rows) > max_rows,
                               yes_no, dates)
        finally:
            conn.rollback()
            for statement in d.end:
                conn.exec_driver_sql(statement)


class SQLConnector(DataConnector):
    def _engine(self):
        """Sync (production resolves the host): async callers use `engine()`."""
        conn_str = self.config.connection_string
        if not conn_str:
            raise ValueError("connection_string is required for SQL connector")
        url, d = safe_url(conn_str)
        # NullPool: a connector lives for one request and builds an engine per call, so a pool
        # would leave idle connections open on the user's database (measured: 4 after 5 queries).
        return create_engine(url, poolclass=NullPool, connect_args=d.connect_args)

    async def engine(self):
        """The engine, built off the event loop (the production host check is a DNS lookup)."""
        return await asyncio.to_thread(self._engine)

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
        """Guard then execute read-only. Raises GuardrailViolation for anything but one read of
        the tables this connection lists."""
        engine = await self.engine()
        d = dialect_of(engine)
        sql = _assert_single_select(sql, d.sqlglot)
        if unlisted := _unlisted_tables(sql, d, await asyncio.to_thread(_tables, engine)):
            reason = f"only the connected database's tables can be read: {', '.join(unlisted)}"
            raise GuardrailViolation("sql", reason, f"Blocked unsafe SQL: {reason}.")
        return await asyncio.to_thread(run_readonly, engine, sql, max_rows)

    async def _read(self) -> pd.DataFrame:
        engine = await self.engine()
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
        # Past the cap, the first rows by key: the same rows each run (a resumed run re-reads).
        # ponytail: a table without a key reads the database's order; ordering by every column
        # can pass the timeout on a big table
        key = (await asyncio.to_thread(inspect(engine).get_pk_constraint, name, schema))
        order = (" ORDER BY " + ", ".join(map(quote, key["constrained_columns"]))
                 if key.get("constrained_columns") else "")
        # The same read-only, timed path as a question (the name was checked just above).
        # LIMIT: a driver buffers the whole result before fetchmany (ponytail: LIMIT syntax;
        # SQL Server's TOP comes with its dialect row in 5.8b)
        res = await asyncio.to_thread(run_readonly, engine,
                                      # noqa below: `qualified` is a listed table, quoted
                                      f"SELECT * FROM {qualified}{order} LIMIT {LOAD_MAX_ROWS + 1}",  # noqa: S608
                                      LOAD_MAX_ROWS)
        if res.truncated:
            try:
                count = await asyncio.to_thread(run_readonly, engine,
                                                f"SELECT COUNT(*) FROM {qualified}", 1)  # noqa: S608
                total = int(count.rows[0][0])
            except SQLAlchemyError:   # a count past the timeout: "the source has more" still holds
                total = None
            self.read_info = {"notes": [truncation_note(
                len(res.rows), total, f"a named table loads at most {LOAD_MAX_ROWS:,} rows; "
                "connect without a table to have questions answered in the database")]}
        return res.frame()

    async def test_connection(self) -> tuple[bool, str]:
        try:
            engine = await self.engine()

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
        except (AgentError, SQLAlchemyError, ValueError) as e:   # the route scrubs (one owner)
            return False, getattr(e, "user_message", str(e))
        except Exception:   # a bug here, not the user's connection: logged, said as ours
            logger.exception("connection test failed unexpectedly")
            return False, "The connection test hit an unexpected error on this server (logged)."
