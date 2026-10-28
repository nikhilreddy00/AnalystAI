"""
SQL connector — PostgreSQL (evaluated), MySQL and SQLite via SQLAlchemy.
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

from agents.errors import GuardrailViolation
from connectors.base import DataConnector
from guardrails import check_sql_readonly

MAX_ROWS = 10_000
TIMEOUT_S = 15
_DIALECTS = {"postgresql": "postgres", "mysql": "mysql", "sqlite": "sqlite"}


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
        return pd.DataFrame(self.rows, columns=self.columns)


def run_readonly(engine, sql: str, max_rows: int = MAX_ROWS,
                 timeout_s: int = TIMEOUT_S) -> QueryResult:
    """Execute one query in a read-only transaction that is always rolled back. Sync: call it
    through asyncio.to_thread. Does not run the parse guard — callers do (layer 1)."""
    name = engine.dialect.name
    with engine.connect() as conn:
        if name == "postgresql":
            conn.exec_driver_sql("SET TRANSACTION READ ONLY")
            conn.exec_driver_sql(f"SET LOCAL statement_timeout = {int(timeout_s * 1000)}")
        elif name == "mysql":
            conn.exec_driver_sql("SET TRANSACTION READ ONLY")
            conn.exec_driver_sql(f"SET SESSION max_execution_time = {int(timeout_s * 1000)}")
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
    def _engine(self):
        conn_str = self.config.connection_string
        if not conn_str:
            raise ValueError("connection_string is required for SQL connector")
        # Sync engine for pandas; Postgres goes through psycopg 3 (the only sync PG driver installed)
        url = conn_str.replace("+asyncpg", "+psycopg").replace("+aiosqlite", "")
        if url.startswith(("postgresql://", "postgres://")):
            url = "postgresql+psycopg://" + url.split("://", 1)[1]
        return create_engine(url)

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

    @staticmethod
    def _read_sql(engine, statement) -> pd.DataFrame:
        """Run a SQL statement and return the result; ensures the connection is closed."""
        with engine.connect() as conn:
            return pd.read_sql(statement, conn)

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
        return await asyncio.to_thread(
            self._read_sql, engine, text(f'SELECT * FROM "{table}" LIMIT 50000')
        )

    async def test_connection(self) -> tuple[bool, str]:
        try:
            engine = self._engine()

            def _probe() -> None:
                with engine.connect() as conn:
                    conn.execute(text("SELECT 1"))

            await asyncio.to_thread(_probe)
            return True, "Connection successful"
        except Exception as e:
            return False, str(e)
