"""
SQL connector — PostgreSQL (evaluated) and SQLite (development only) via SQLAlchemy.
MySQL support is planned (Phase 5.8).
Questions are turned into SQL by agents/sql_agent.py; this module only loads and executes.

Every model-written query passes four layers, each tested on its own:
1. check_sql_readonly (parsed: one read statement, no INTO / locks / side-effect functions);
2. a READ ONLY transaction that is always rolled back;
3. a statement timeout and a row cap (fetchmany, not the query's own LIMIT);
4. the database role — operators should connect with a read-only user (docs/baselines/phase4.md).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pandas as pd
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import NullPool

from agents.errors import GuardrailViolation, IngestionError
from connectors.base import DataConnector, truncation_note
from guardrails import check_sql_readonly

MAX_ROWS = 10_000
LOAD_MAX_ROWS = 50_000   # a named table is loaded whole up to this; past it, the load says so
TIMEOUT_S = 15
_DIALECTS = {"postgresql": "postgres", "sqlite": "sqlite"}


def sql_dialect(engine) -> str | None:
    return _DIALECTS.get(engine.dialect.name)


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

    def frame(self) -> pd.DataFrame:
        # coerce_float: Postgres NUMERIC arrives as Decimal; numbers, as pd.read_sql gives them
        return pd.DataFrame.from_records(self.rows, columns=self.columns, coerce_float=True)


def run_readonly(engine, sql: str, max_rows: int = MAX_ROWS,
                 timeout_s: int = TIMEOUT_S) -> QueryResult:
    """Execute one query in a read-only transaction that is always rolled back. Sync: call it
    through asyncio.to_thread. Does not run the parse guard — callers do (layer 1)."""
    name = engine.dialect.name
    with engine.connect() as conn:
        if name == "postgresql":
            conn.exec_driver_sql("SET TRANSACTION READ ONLY")
            conn.exec_driver_sql(f"SET LOCAL statement_timeout = {int(timeout_s * 1000)}")
        elif name == "sqlite":
            conn.exec_driver_sql("PRAGMA query_only = ON")
        try:
            result = conn.execute(text(sql))
            rows = [tuple(r) for r in result.fetchmany(max_rows + 1)]
            return QueryResult(list(result.keys()), rows[:max_rows], len(rows) > max_rows)
        finally:
            conn.rollback()
            if name == "sqlite":
                conn.exec_driver_sql("PRAGMA query_only = OFF")


class SQLConnector(DataConnector):
    def __init__(self, config) -> None:
        super().__init__(config)
        from api.config import settings
        if settings.app_env == "production" and \
                (config.connection_string or "").lower().startswith("sqlite"):
            raise IngestionError("sqlite in production", "SQLite is for development only. "
                                 "Connect a PostgreSQL database instead.")

    def _engine(self):
        conn_str = self.config.connection_string
        if not conn_str:
            raise ValueError("connection_string is required for SQL connector")
        if conn_str.lower().startswith("mysql"):
            raise IngestionError("mysql", "MySQL support is planned (Phase 5.8). Connect a "
                                 "PostgreSQL database for now.")
        # Sync engine for pandas; Postgres goes through psycopg 3 (the only sync PG driver installed)
        url = conn_str.replace("+asyncpg", "+psycopg").replace("+aiosqlite", "")
        if url.startswith(("postgresql://", "postgres://")):
            url = "postgresql+psycopg://" + url.split("://", 1)[1]
        # NullPool: a connector lives for one request and builds an engine per call, so a pool
        # would leave idle connections open on the user's database (measured: 4 after 5 queries).
        return create_engine(url, poolclass=NullPool)

    def schema_context(self, engine) -> str:
        """Introspected tables and columns, for when no semantic layer is configured."""
        inspector = inspect(engine)
        parts = []
        for schema in [s for s in inspector.get_schema_names()
                       if s not in ("information_schema", "pg_catalog")]:
            for table in inspector.get_table_names(schema=schema)[:20]:   # cap prompt size
                cols = inspector.get_columns(table, schema=schema)
                col_desc = ", ".join(f"{c['name']} ({c['type']})" for c in cols)
                parts.append(f"Table `{schema}.{table}`: {col_desc}")
        return "\n".join(parts)

    async def query(self, sql: str, max_rows: int = MAX_ROWS) -> QueryResult:
        """Guard then execute read-only. Raises GuardrailViolation for anything but one read."""
        engine = self._engine()
        sql = _assert_single_select(sql, sql_dialect(engine))
        return await asyncio.to_thread(run_readonly, engine, sql, max_rows)

    async def load(self) -> pd.DataFrame:
        engine = self._engine()
        table = self.config.table_name

        if not table:
            raise ValueError("table_name is required for SQL connector direct load")

        # BUG-02: Validate table name against DB inspector to prevent SQL injection
        valid_tables = await asyncio.to_thread(lambda: inspect(engine).get_table_names())
        if table not in valid_tables:
            raise ValueError(
                f"Table '{table}' does not exist. Available tables: {valid_tables[:20]}"
            )
        # The same read-only, timed path as a question (the name was checked just above).
        res = await asyncio.to_thread(run_readonly, engine, f'SELECT * FROM "{table}"',
                                      LOAD_MAX_ROWS)
        if res.truncated:
            try:
                count = await asyncio.to_thread(run_readonly, engine,
                                                f'SELECT COUNT(*) FROM "{table}"', 1)
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
            return False, getattr(e, "user_message", str(e))
