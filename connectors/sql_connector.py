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
import socket
import ssl
from collections.abc import Callable
from dataclasses import dataclass, field, replace

import certifi
import pandas as pd
import sqlglot
from sqlalchemy import column, create_engine, event, func, inspect, literal_column, select, text
from sqlalchemy import table as sql_table
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
_SCHEMA_CHARS = 12_000   # table descriptions in the SQL agent's prompt; past it, names only
_TYPE_CODE, _INTERNAL_SIZE, _MYSQL_TINY = 1, 3, 1   # cursor.description fields; FIELD_TYPE.TINY
_MYSQL_DATES = frozenset({7, 10, 12, 14})   # FIELD_TYPE TIMESTAMP, DATE, DATETIME, NEWDATE
# MySQL's zero date, as PyMySQL returns what it can't convert: DATE, DATETIME, DATETIME(fsp)
_ZERO_DATE = re.compile(r"0000-00-00( 00:00:00(\.0+)?)?")


@dataclass(frozen=True)
class Dialect:
    """What the SQL source knows about one database engine. Every reader of the engine's kind
    reads this row; an engine without one is refused everywhere."""
    title: str                       # its name in every message and the README's source list
    sqlglot: str                     # the parse guard's and the SQL agent's dialect name
    driver: str                      # the one driver the engine is opened with ("" = the default)
    drivers: frozenset[str]          # spellings accepted in a user's string, opened as `driver`
    query_keys: frozenset[str]       # URL keys passed to the driver (lower case); others refused
    # run first on each connection: read only, then the timeout ({ms} / {s} the timeout)
    begin: tuple[str, ...]
    end: tuple[str, ...] = ()        # run after the rollback
    system_schemas: frozenset[str] = frozenset()   # the engine's own catalogs, never a user table
    connect_args: dict = field(default_factory=dict)
    fixed_query: dict = field(default_factory=dict)   # URL keys set by code, after the allowlist
    one_database: bool = False       # a "schema" is a database: list only the URL's own
    # every CTE may refer to itself (T-SQL has no RECURSIVE keyword); elsewhere, a CTE's own name
    # inside its body is the real table (5.8a: WITH pg_shadow AS (SELECT * FROM pg_shadow))
    recursive_ctes: bool = False
    yes_no: Callable[[tuple], bool] | None = None   # a cursor column the engine types as a yes/no
    dates: Callable[[tuple], bool] | None = None    # one it types as a date (may hold zero dates)
    # the statement timeout where the engine has no statement for it: (DB-API connection, s)
    driver_timeout: Callable[[object, int], None] | None = None
    # production: the URL connecting to the checked address, the name kept for TLS
    pin: Callable[[URL, str], URL] = lambda url, ip: url.set(host=ip)


def _query_timeout(dbapi_conn, seconds: int) -> None:
    dbapi_conn.timeout = seconds   # pyodbc: each statement's timeout (SQL Server has no SET)


# SQL Server has no read-only transaction (ADR-0010): its read-only layer is the login, required.
# Every session first refuses a login that can change data: a server or database owner, a
# writer or DDL role, a database-wide write or EXECUTE permission, an explicit grant to it (or a
# role it is in) on any table, view or schema, an IMPERSONATE (as a user who can write), or
# owning a user schema or object (ownership is CONTROL and leaves no permission row; 5.8b audit).
# Schema ids 5..16383 are users' schemas (1-4 dbo, guest, INFORMATION_SCHEMA, sys; 16384+ the
# fixed roles'). `CAN_WRITE` marks it for the read-only plant test.
# What a read-only login may hold: an allow list of permissions, as the parse guard's functions
# (5.8b audit pass 2: the deny list here missed db_securityadmin, which grants itself INSERT,
# after pass 1 found it missing ownership, IMPERSONATE and EXECUTE). fn_my_permissions is the
# login's effective set: its roles, public and the server roles included.
_MSSQL_READ_DB = ("'CONNECT', 'SELECT', 'REFERENCES', 'VIEW DEFINITION', 'SHOWPLAN', "
                  "'VIEW DATABASE STATE', 'VIEW DATABASE PERFORMANCE STATE', "
                  "'VIEW ANY COLUMN ENCRYPTION KEY DEFINITION', "
                  "'VIEW ANY COLUMN MASTER KEY DEFINITION', 'VIEW CHANGE TRACKING'")
_MSSQL_READ_SERVER = ("'CONNECT', 'CONNECT SQL', 'VIEW ANY DATABASE', 'VIEW SERVER STATE', "
                      "'VIEW SERVER PERFORMANCE STATE', 'VIEW ANY DEFINITION', "
                      "'CONNECT ANY DATABASE'")
_MSSQL_CANT_WRITE = (
    "IF EXISTS (SELECT 1 FROM fn_my_permissions(NULL, 'DATABASE') "
    f"WHERE permission_name NOT IN ({_MSSQL_READ_DB})) "
    "OR EXISTS (SELECT 1 FROM fn_my_permissions(NULL, 'SERVER') "
    f"WHERE permission_name NOT IN ({_MSSQL_READ_SERVER})) "
    # a grant on one table, schema or login isn't in the database's or server's effective set
    "OR EXISTS (SELECT 1 FROM sys.database_permissions p WHERE p.state IN ('G', 'W') "
    f"AND p.permission_name NOT IN ({_MSSQL_READ_DB}) AND (p.grantee_principal_id = "
    "DATABASE_PRINCIPAL_ID() OR IS_MEMBER(USER_NAME(p.grantee_principal_id)) = 1)) "
    "OR EXISTS (SELECT 1 FROM sys.server_permissions p WHERE p.state IN ('G', 'W') "
    f"AND p.permission_name NOT IN ({_MSSQL_READ_SERVER}) "
    "AND (p.grantee_principal_id = SUSER_ID() OR IS_SRVROLEMEMBER(SUSER_NAME(p.grantee_principal_id)) = 1)) "
    "OR EXISTS (SELECT 1 FROM sys.schemas s WHERE s.schema_id BETWEEN 5 AND 16383 "
    "AND (s.principal_id = DATABASE_PRINCIPAL_ID() OR IS_MEMBER(USER_NAME(s.principal_id)) = 1)) "
    "OR EXISTS (SELECT 1 FROM sys.objects o WHERE o.principal_id IS NOT NULL "
    "AND (o.principal_id = DATABASE_PRINCIPAL_ID() OR IS_MEMBER(USER_NAME(o.principal_id)) = 1)) "
    "THROW 50001, 'CAN_WRITE: this login can change data. SQL Server has no read-only "
    "session, so connect with a read-only login (a user in db_datareader only).', 1;")
_SSL_MODES = ("REQUIRED", "VERIFY_CA", "VERIFY_IDENTITY")   # the MySQL client's words for TLS
_PINNED = "_analystai_checked_address"   # production: the address the socket goes to (do_connect)


def _tls(mode: str) -> ssl.SSLContext:
    """MySQL/MariaDB TLS: REQUIRED encrypts, VERIFY_CA also checks the certificate's chain,
    VERIFY_IDENTITY its name too. The CAs: certifi's and the operator's EXTRA_CA_BUNDLE (the
    image ships AWS RDS's), never a file a user's string names (ADR-0010)."""
    from api.config import settings
    if mode.upper() not in _SSL_MODES:
        raise IngestionError("ssl_mode", "ssl_mode must be REQUIRED, VERIFY_CA or VERIFY_IDENTITY "
                             "(leave it out for no TLS).")
    ctx = ssl.create_default_context(cafile=certifi.where())
    if settings.extra_ca_bundle:
        try:
            ctx.load_verify_locations(settings.extra_ca_bundle)
        except (OSError, ssl.SSLError) as exc:   # this server's setting, not the user's database
            raise IngestionError("ca bundle", "This server's EXTRA_CA_BUNDLE can't be read "
                                 f"({type(exc).__name__}); ask the administrator.") from exc
    ctx.check_hostname = mode.upper() == "VERIFY_IDENTITY"
    if mode.upper() == "REQUIRED":
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _connect_pinned(dialect, conn_rec, cargs, cparams):
    """PyMySQL to the checked address with the host's name kept for TLS (server_hostname): the
    socket is opened here, the handshake by the driver (5.8a's pin replaced the name by the IP,
    so a verified TLS name couldn't match)."""
    address = cparams.pop(_PINNED)
    conn = dialect.loaded_dbapi.connect(*cargs, **cparams, defer_connect=True)
    try:
        sock = socket.create_connection((address, cparams.get("port", 3306)),
                                        timeout=CONNECT_TIMEOUT_S)
    except OSError as exc:   # the driver's own error, as PyMySQL raises it when it dials: SQLAlchemy
        # wraps that, and the session is then said as the database's (5.8b audit: a raw OSError
        # took the query's paid retry)
        raise dialect.loaded_dbapi.OperationalError(
            2003, f"Can't connect to the database server ({exc.strerror or type(exc).__name__})"
        ) from exc
    conn.connect(sock=sock)
    return conn


_MYSQL = Dialect(
    title="MySQL",
    sqlglot="mysql", driver="pymysql", drivers=frozenset({"", "pymysql"}),
    # SESSION read only too: a DDL statement commits the open transaction implicitly and
    # would run outside it; max_execution_time covers SELECT, the only statement let through
    begin=("SET SESSION TRANSACTION READ ONLY", "SET SESSION max_execution_time = {ms}",
           "START TRANSACTION READ ONLY"),
    query_keys=frozenset({"charset", "ssl_mode"}),
    pin=lambda url, ip: url.update_query_dict({_PINNED: ip}),
    system_schemas=frozenset({"information_schema", "mysql", "sys", "performance_schema"}),
    # UTC; and the parse guard reads strings and quotes as MySQL's default mode does, so a
    # server's ANSI_QUOTES / NO_BACKSLASH_ESCAPES can't make the two read a query differently
    connect_args={"connect_timeout": CONNECT_TIMEOUT_S, "read_timeout": TIMEOUT_S + 15,
                  "local_infile": False, "init_command":
                  "SET time_zone = '+00:00', sql_mode = REPLACE(REPLACE(@@sql_mode, "
                  "'NO_BACKSLASH_ESCAPES', ''), 'ANSI_QUOTES', '')"},
    one_database=True,
    # a DB-API description is (name, type_code, display_size, internal_size, …): PyMySQL's
    # TINY type (1) of width 1 is BOOL / TINYINT(1); the same on MariaDB (5.8b, checked)
    yes_no=lambda col: col[_TYPE_CODE] == _MYSQL_TINY and col[_INTERNAL_SIZE] == 1,
    dates=lambda col: col[_TYPE_CODE] in _MYSQL_DATES)


DIALECTS: dict[str, Dialect] = {
    "postgresql": Dialect(
        title="PostgreSQL",
        sqlglot="postgres", driver="psycopg", drivers=frozenset({"", "psycopg", "asyncpg"}),
        query_keys=frozenset({"sslmode", "channel_binding", "connect_timeout", "application_name",
                              "target_session_attrs"}),
        # the server reads quotes as the parse guard does: with standard_conforming_strings off a
        # backslash escapes a quote, and a string sqlglot ends reads on (5.8b security review)
        begin=("SET TRANSACTION READ ONLY", "SET LOCAL statement_timeout = {ms}",
               "SET LOCAL standard_conforming_strings = on"),
        system_schemas=frozenset({"information_schema", "pg_catalog", "pg_toast"}),
        connect_args={"connect_timeout": CONNECT_TIMEOUT_S},
        # libpq connects to hostaddr and verifies TLS against host
        pin=lambda url, ip: url.update_query_dict({"hostaddr": ip})),
    "mysql": _MYSQL,
    # MySQL's row but its timeout: max_statement_time is seconds and stops any statement (1969);
    # MariaDB has no max_execution_time (1193). Picked by the server (dialect_of), not the URL.
    "mariadb": replace(
        _MYSQL, title="MariaDB",
        begin=("SET SESSION TRANSACTION READ ONLY", "SET SESSION max_statement_time = {s}",
               "START TRANSACTION READ ONLY")),
    "mssql": Dialect(
        title="SQL Server",
        sqlglot="tsql", driver="pyodbc", drivers=frozenset({"", "pyodbc"}),
        # `driver`, `odbc_connect`, `dsn` and every other ODBC attribute are the user's way to a
        # different driver or connection: refused; the driver is set by code
        query_keys=frozenset({"encrypt", "trustservercertificate"}),
        fixed_query={"driver": "ODBC Driver 18 for SQL Server"},
        # no LOCK_TIMEOUT: the driver's query timeout already ends a lock wait (5.8b audit: the
        # two raced at the same limit)
        begin=(_MSSQL_CANT_WRITE, "SET XACT_ABORT ON", "SET QUOTED_IDENTIFIER ON"),
        driver_timeout=_query_timeout, recursive_ctes=True,
        system_schemas=frozenset({"sys", "information_schema", "guest", "db_owner",
                                  "db_accessadmin", "db_securityadmin", "db_ddladmin",
                                  "db_backupoperator", "db_datareader", "db_datawriter",
                                  "db_denydatareader", "db_denydatawriter"}),
        connect_args={"timeout": CONNECT_TIMEOUT_S},   # pyodbc.connect: the login timeout
        # the driver connects to the checked address and verifies TLS against the name
        pin=lambda url, ip: url.set(host=ip).update_query_dict(
            {"HostNameInCertificate": url.host})),
    "sqlite": Dialect(   # development only (refused in production): no statement timeout
        title="SQLite",
        sqlglot="sqlite", driver="", drivers=frozenset({""}), query_keys=frozenset(),
        begin=("PRAGMA query_only = ON",), end=("PRAGMA query_only = OFF",)),
}
SUPPORTED = ", ".join(f"{d.title} ({key}://)" for key, d in DIALECTS.items() if key != "sqlite")
_ALIASES = {"postgres": "postgresql"}   # Heroku's and Supabase's copied strings
SYSTEM_SCHEMAS = frozenset().union(*(d.system_schemas for d in DIALECTS.values()))
_UNREADABLE = ("The connection string couldn't be read. Use the form "
               "dialect://user:password@host:port/database (a password with @ or : must be "
               "URL-encoded, e.g. %40 for @).")


def dialect_of(engine) -> Dialect:
    """The engine's row, or IngestionError: nothing runs on an engine without one. The server
    decides MySQL vs MariaDB (SQLAlchemy knows it after the first connection): a `mysql://`
    string reaching a MariaDB server takes MariaDB's row."""
    name = "mariadb" if getattr(engine.dialect, "is_mariadb", False) else engine.dialect.name
    if (d := DIALECTS.get(name)) is None:
        raise IngestionError(f"no dialect {name}", f"{name} databases aren't supported "
                             f"(these are: {SUPPORTED}).")
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
                      f"'{url.drivername}' isn't a supported database. Use {SUPPORTED} "
                      "(SQLite for local development).")
    # keys compare as the driver reads them: ODBC's without case (TrustServerCertificate)
    if extra := sorted(k for k in url.query if k.lower() not in d.query_keys):
        raise IngestionError("connection string options", "These connection string options aren't "
                      f"allowed: {', '.join(extra)}. Allowed: {', '.join(sorted(d.query_keys)) or 'none'}.")
    # one_database: else every database on the server is listed. ODBC: SQLAlchemy reads a string
    # with no database as a DSN named by the host, and one with no user as Windows sign-in with
    # this server's own credentials (5.8b audit pass 2)
    if (d.one_database or d.driver == "pyodbc") and not url.database or \
            d.driver == "pyodbc" and not url.username:
        raise IngestionError("no database", "The connection string must name the user and the "
                             f"database: {engine}://user:password@host:port/<database>.")
    from api.config import settings
    url = url.set(drivername=f"{engine}+{d.driver}" if d.driver else engine,
                  # one spelling for every reader after the allowlist (5.8b audit: SSL_MODE passed
                  # it, then reached PyMySQL as an unknown argument)
                  query={k.lower(): v for k, v in url.query.items()})
    url = url.update_query_dict(d.fixed_query)
    if settings.app_env == "production":
        if engine == "sqlite":
            raise IngestionError("sqlite in production", "SQLite is for development only. Connect "
                                 f"one of {SUPPORTED} instead.")
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
    for schema in [s for s in schemas if s.lower() not in d.system_schemas   # INFORMATION_SCHEMA
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
    if d.recursive_ctes:   # read as the engine reads it: each CTE may name itself
        for with_ in tree.find_all(exp.With):
            with_.set("recursive", True)
    # a query sqlglot can't resolve raises here and is never run (fail closed)
    real = {id(s) for scope in traverse_scope(tree) for s in scope.sources.values()
            if isinstance(s, exp.Table)}
    ctes = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    known = {name.lower() for name in listed}
    # T-SQL's APPLY / LATERAL over anything but a query reads a function's rows, not a table's
    # (5.8b red-team: CROSS APPLY sys.dm_exec_sql_text(..) never reached the walk below)
    # (Postgres's LATERAL generate_series makes rows of its own, as it does read as a table)
    out = [f"APPLY {lat.this.sql(d.sqlglot).split('(')[0]}" for lat in tree.find_all(exp.Lateral)
           if not isinstance(lat.this, (exp.Subquery, exp.Unnest, exp.ExplodingGenerateSeries))]
    for t in tree.find_all(exp.Table):
        if isinstance(t.this, exp.ExplodingGenerateSeries):
            continue
        if not isinstance(t.this, exp.Identifier):
            out.append(f"{t.this.sql(d.sqlglot).split('(')[0]}()")
            continue
        # #temp and ##global_temp read as `temp`: never one of the user's tables
        if t.this.args.get("temporary") or t.this.args.get("global_"):
            out.append(f"#{t.name}")
            continue
        name = ".".join(p for p in (t.catalog, t.db, t.name) if p)
        cte_ref = id(t) not in real and name == t.name and t.name.lower() in ctes
        if not cte_ref and name.lower() not in known:
            out.append(name)
    return out


def _sql_of(statement, engine) -> str:
    """A Core statement as the engine's own SQL text, values inlined (run_readonly takes text:
    a bound LIMIT would reach it as a placeholder)."""
    return str(statement.compile(engine, compile_kwargs={"literal_binds": True}))


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


def _session_error(exc: SQLAlchemyError, engine) -> IngestionError:
    """A session that couldn't be opened (no connection, a login refused, a statement the server
    lacks) as the database's problem, never the query's: no paid retry. The driver's first line,
    the password cut out."""
    said = str(getattr(exc, "orig", None) or exc).strip().splitlines()[0][:300]
    if engine.url.password:
        said = said.replace(engine.url.password, "***")
    return IngestionError("session setup", f"Couldn't open a read-only session on the database: "
                          f"{said}")


def run_readonly(engine, sql: str, max_rows: int = MAX_ROWS,
                 timeout_s: int = TIMEOUT_S) -> QueryResult:
    """Execute one query in a read-only transaction that is always rolled back, with the
    dialect's statement timeout. Sync: call it through asyncio.to_thread. Does not run the parse
    guard — callers do (layer 1). An engine without a dialect row is refused; a session that
    can't be opened read-only and timed (no connection, a statement the server lacks) is an
    IngestionError, never the query's fault."""
    dialect_of(engine)   # refused before any connection
    conn = None
    try:
        conn = engine.connect()
        d = dialect_of(engine)   # again: the server is known now (MySQL or MariaDB)
        if d.driver_timeout:
            d.driver_timeout(conn.connection.dbapi_connection, timeout_s)
        for statement in d.begin:
            conn.exec_driver_sql(statement.format(ms=int(timeout_s * 1000), s=timeout_s))
    except SQLAlchemyError as exc:
        if conn is not None:
            conn.close()
        raise _session_error(exc, engine) from exc
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
        args = dict(d.connect_args)
        if (mode := url.query.get("ssl_mode")) is not None:   # MySQL's word; PyMySQL takes a context
            args["ssl"] = _tls(mode)
            url = url.difference_update_query(["ssl_mode"])
        # NullPool: a connector lives for one request and builds an engine per call, so a pool
        # would leave idle connections open on the user's database (measured: 4 after 5 queries).
        engine = create_engine(url, poolclass=NullPool, connect_args=args)
        if _PINNED in url.query:
            event.listen(engine, "do_connect", _connect_pinned)
        return engine

    async def engine(self):
        """The engine, built off the event loop (the production host check is a DNS lookup)."""
        return await asyncio.to_thread(self._engine)

    def schema_context(self, engine) -> str:
        """Introspected tables and columns, for when no semantic layer is configured. Every table
        is named: with columns up to a size budget, past it by name only, and the prompt says so
        (audit pass 2: a 20-table cap hid a 40-table database's 30th table without a word)."""
        inspector = inspect(engine)
        default, parts, names_only, size = inspector.default_schema_name, [], [], 0
        for schema, table in dict.fromkeys((s or default, t) for s, t in _tables(engine).values()):
            if size >= _SCHEMA_CHARS:
                names_only.append(f"`{schema}.{table}`")
                continue
            cols = inspector.get_columns(table, schema=schema)
            col_desc = ", ".join(f"{c['name']} ({c['type']})" for c in cols)
            parts.append(f"Table `{schema}.{table}`: {col_desc}")
            size += len(parts[-1])
        if names_only:
            parts.append(f"{len(names_only)} more tables (columns not shown; run_sql can look one "
                         f"up): {', '.join(names_only)}")
        return "\n".join(parts)

    async def query(self, sql: str, max_rows: int = MAX_ROWS) -> QueryResult:
        """Guard then execute read-only. Raises GuardrailViolation for anything but one read of
        the tables this connection lists."""
        engine = await self.engine()
        d = dialect_of(engine)
        sql = _assert_single_select(sql, d.sqlglot)
        try:
            listed = await asyncio.to_thread(_tables, engine)
        except SQLAlchemyError as exc:   # the first connection a question makes: the database
            raise _session_error(exc, engine) from exc   # down or the login gone, never the query's
        if unlisted := _unlisted_tables(sql, d, listed):
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
        # Past the cap, the first rows by key: the same rows each run (a resumed run re-reads).
        # ponytail: a table without a key reads the database's order; ordering by every column
        # can pass the timeout on a big table
        key = (await asyncio.to_thread(inspect(engine).get_pk_constraint, name, schema))
        listed = sql_table(name, *map(column, key.get("constrained_columns") or ()), schema=schema)
        # The same read-only, timed path as a question (the name was checked just above), written
        # by the engine's own compiler: LIMIT, or SQL Server's TOP (a driver buffers the whole
        # result before fetchmany, so the database is asked for at most the cap)
        # a keyless table has no row order, but the contract's samples (and a statement's
        # same-day balances) read it: SQL Server's parallel scan returned a different order under
        # load (5.8b, recurrence of P58-03), so its scan stays serial, the stored order as on the
        # other engines. ponytail: stable in practice, not guaranteed (no key, no order)
        load = select(literal_column("*")).select_from(listed).order_by(*listed.c) \
            .limit(LOAD_MAX_ROWS + 1).suffix_with("OPTION (MAXDOP 1)", dialect="mssql")
        res = await asyncio.to_thread(run_readonly, engine, _sql_of(load, engine), LOAD_MAX_ROWS)
        if res.truncated:
            try:
                count = await asyncio.to_thread(run_readonly, engine, _sql_of(
                    select(func.count()).select_from(listed), engine), 1)
                total = int(count.rows[0][0])
            # a count past the timeout, or its own session not opening: the rows already loaded
            # stand, and "the source has more" still holds (5.8b audit)
            except (SQLAlchemyError, IngestionError):
                total = None
            self.read_info = {"notes": [truncation_note(
                len(res.rows), total, f"a named table loads at most {LOAD_MAX_ROWS:,} rows; "
                "connect without a table to have questions answered in the database")]}
        return res.frame()

    async def test_connection(self) -> tuple[bool, str]:
        try:
            engine = await self.engine()
            # the session a question gets (5.8b red-team M6): a SQL Server login that can write,
            # or a server missing a statement, fails here, not on the first question
            await asyncio.to_thread(run_readonly, engine, "SELECT 1", 1)
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
