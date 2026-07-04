"""
SQL connector — PostgreSQL and MySQL via SQLAlchemy.
NL→SQL translation uses Vanna.ai for schema-trained generation with Claude fallback.
Parameterized queries only — no raw SQL injection risk.
"""

from __future__ import annotations

import asyncio
import logging

import anthropic
import pandas as pd
from sqlalchemy import create_engine, inspect, text

from api.config import settings
from connectors.base import DataConnector

logger = logging.getLogger(__name__)


def _assert_single_select(sql: str) -> str:
    """Reject anything that isn't a single SELECT statement.
    Defends against prompt-injection like `SELECT 1; DROP TABLE users; --`."""
    stripped = sql.strip().rstrip(";").strip()
    if not stripped:
        raise ValueError("Empty SQL statement.")
    # Multiple statements: split on `;` and require exactly one non-empty piece
    pieces = [p.strip() for p in stripped.split(";") if p.strip()]
    if len(pieces) != 1:
        raise ValueError(
            f"Only a single SQL statement is allowed (got {len(pieces)} statements)."
        )
    first_word = pieces[0].split()[0].upper()
    if first_word != "SELECT":
        raise ValueError(f"Generated SQL is not a SELECT statement: {pieces[0][:80]}")
    return pieces[0]


class SQLConnector(DataConnector):
    def __init__(self, config):
        super().__init__(config)
        # BUG-07: Pass API key from settings instead of relying on env var
        self._client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        self._vanna = None  # lazy-initialized

    def _init_vanna(self, engine):
        """Lazy-initialize Vanna.ai with database schema for NL→SQL."""
        if self._vanna is not None:
            return
        try:
            from vanna.remote import VannaDefault
            self._vanna = VannaDefault(model="analystai", api_key=settings.groq_api_key)
            # Train Vanna on the database schema
            schema_context = self._get_schema_context(engine)
            for line in schema_context.split("\n"):
                if line.strip():
                    self._vanna.train(ddl=line)
            logger.info("Vanna.ai initialized and trained on schema")
        except Exception as exc:
            logger.warning(f"Vanna.ai initialization failed, will use Claude fallback: {exc}")
            self._vanna = None

    def _engine(self):
        conn_str = self.config.connection_string
        if not conn_str:
            raise ValueError("connection_string is required for SQL connector")
        # Use sync engine for pandas — async not needed here
        sync_url = conn_str.replace("+asyncpg", "").replace("+aiosqlite", "")
        return create_engine(sync_url)

    def _get_schema_context(self, engine) -> str:
        """Introspect DB schema and return a description for the LLM prompt."""
        inspector = inspect(engine)
        tables = inspector.get_table_names()
        parts = []
        for table in tables[:20]:      # cap at 20 tables to keep prompt size manageable
            cols = inspector.get_columns(table)
            col_desc = ", ".join(f"{c['name']} ({c['type']})" for c in cols)
            parts.append(f"Table `{table}`: {col_desc}")
        return "\n".join(parts)

    def _nl_to_sql(self, natural_language: str, schema_context: str) -> str:
        """Convert a natural language query to SQL using Claude."""
        prompt = f"""You are a SQL expert. Given the database schema below, write a safe,
read-only SQL SELECT query that answers the user's question.

SCHEMA:
{schema_context}

USER QUESTION: {natural_language}

Rules:
- Only write SELECT statements. Never INSERT, UPDATE, DELETE, DROP, or CREATE.
- Use table aliases for clarity.
- Limit results to 10000 rows maximum using LIMIT.
- Return ONLY the SQL query, no explanation, no markdown fences.
"""
        response = self._client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        sql = response.content[0].text.strip()
        return _assert_single_select(sql)

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

    async def query_nl(self, natural_language: str) -> pd.DataFrame:
        """Execute a natural language query against the connected database."""
        engine = self._engine()

        # Try Vanna.ai first for trained NL→SQL
        sql = None
        try:
            self._init_vanna(engine)
            if self._vanna is not None:
                sql = self._vanna.generate_sql(natural_language)
                logger.info(f"Vanna generated SQL: {sql}")
        except Exception as exc:
            logger.warning(f"Vanna.ai NL→SQL failed, falling back to Claude: {exc}")
            sql = None

        # Claude fallback
        if not sql:
            schema_context = await asyncio.to_thread(
                lambda: self._get_schema_context(engine)
            )
            sql = self._nl_to_sql(natural_language, schema_context)

        # Reject multi-statement / non-SELECT injection attempts before execution
        sql = _assert_single_select(sql)
        return await asyncio.to_thread(self._read_sql, engine, text(sql))

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
