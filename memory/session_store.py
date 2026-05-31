"""
PostgreSQL-backed session store.
Persists AnalysisSession as JSONB so multi-turn conversations survive restarts.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, String, Text, select, delete, text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from orchestrator.state import AnalysisSession


class Base(DeclarativeBase):
    pass


class SessionRecord(Base):
    __tablename__ = "analysis_sessions"

    session_id = Column(String, primary_key=True)
    user_id = Column(String, nullable=False, index=True)
    data = Column(Text, nullable=False)          # full AnalysisSession as JSON
    dataset_name = Column(String, nullable=True)
    file_hash = Column(String, nullable=True, index=True)
    status = Column(String, nullable=False, default="in_progress")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class DashboardChart(Base):
    __tablename__ = "dashboard_charts"

    id = Column(String, primary_key=True)
    user_id = Column(String, nullable=False, index=True)
    session_id = Column(String, nullable=False)
    chart_data = Column(Text, nullable=False)    # ChartConfig as JSON
    pinned_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class InMemorySessionStore:
    """Fallback store for local dev when PostgreSQL is unavailable."""

    def __init__(self) -> None:
        # session_id -> {"data": json_str, "meta": {dataset_name, file_hash, status, user_id, created_at, updated_at}}
        self._sessions: dict[str, dict] = {}
        self._charts: dict[str, dict] = {}

    async def init(self) -> None:
        pass

    async def save(self, session: AnalysisSession) -> None:
        existing = self._sessions.get(session.session_id)
        now = datetime.now(timezone.utc)
        created = existing["meta"]["created_at"] if existing else now
        self._sessions[session.session_id] = {
            "data": session.model_dump_json(),
            "meta": {
                "user_id": session.user_id,
                "dataset_name": session.dataset_name,
                "file_hash": session.file_hash,
                "status": session.status,
                "created_at": created,
                "updated_at": now,
            },
        }

    async def load(self, session_id: str) -> AnalysisSession | None:
        entry = self._sessions.get(session_id)
        if not entry:
            return None
        return AnalysisSession.model_validate_json(entry["data"])

    async def delete(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    async def pin_chart(self, user_id: str, session_id: str, chart_data: dict) -> None:
        from orchestrator.state import ChartConfig
        chart = ChartConfig.model_validate(chart_data)
        self._charts[chart.id] = {
            "user_id": user_id,
            "session_id": session_id,
            "chart": chart.model_dump(),
        }

    async def get_dashboard(self, user_id: str) -> list[dict]:
        return [v["chart"] for v in self._charts.values() if v["user_id"] == user_id]

    async def unpin_chart(self, chart_id: str, user_id: str) -> None:
        # Mirror PG store: only the owner may unpin. A bare pop() would let
        # any caller delete any user's chart by id.
        existing = self._charts.get(chart_id)
        if existing and existing["user_id"] == user_id:
            self._charts.pop(chart_id, None)

    async def get_latest_analysis(self, user_id: str) -> AnalysisSession | None:
        candidates: list[AnalysisSession] = []
        for entry in self._sessions.values():
            if entry["meta"]["user_id"] != user_id:
                continue
            s = AnalysisSession.model_validate_json(entry["data"])
            if s.insight_report is not None:
                candidates.append(s)
        if not candidates:
            return None
        return max(candidates, key=lambda s: s.updated_at)


class SessionStore:
    def __init__(self, database_url: str) -> None:
        self._engine = create_async_engine(database_url, echo=False)
        self._session_factory = async_sessionmaker(self._engine, expire_on_commit=False)

    async def init(self) -> None:
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            # Backfill columns on pre-existing PG tables. SQLite picks these up
            # at table-create time so we only run on the postgresql dialect.
            if self._engine.dialect.name == "postgresql":
                await conn.execute(text(
                    "ALTER TABLE analysis_sessions ADD COLUMN IF NOT EXISTS dataset_name VARCHAR"
                ))
                await conn.execute(text(
                    "ALTER TABLE analysis_sessions ADD COLUMN IF NOT EXISTS file_hash VARCHAR"
                ))
                await conn.execute(text(
                    "ALTER TABLE analysis_sessions ADD COLUMN IF NOT EXISTS status VARCHAR NOT NULL DEFAULT 'in_progress'"
                ))
                await conn.execute(text(
                    "CREATE INDEX IF NOT EXISTS ix_analysis_sessions_file_hash ON analysis_sessions(file_hash)"
                ))

    async def save(self, session: AnalysisSession) -> None:
        async with self._session_factory() as db:
            existing = await db.get(SessionRecord, session.session_id)
            payload = session.model_dump_json()
            if existing:
                existing.data = payload
                existing.dataset_name = session.dataset_name
                existing.file_hash = session.file_hash
                existing.status = session.status
                existing.updated_at = datetime.now(timezone.utc)
            else:
                db.add(SessionRecord(
                    session_id=session.session_id,
                    user_id=session.user_id,
                    data=payload,
                    dataset_name=session.dataset_name,
                    file_hash=session.file_hash,
                    status=session.status,
                ))
            await db.commit()

    async def load(self, session_id: str) -> AnalysisSession | None:
        async with self._session_factory() as db:
            record = await db.get(SessionRecord, session_id)
            if not record:
                return None
            return AnalysisSession.model_validate_json(record.data)

    async def delete(self, session_id: str) -> None:
        async with self._session_factory() as db:
            await db.execute(delete(SessionRecord).where(SessionRecord.session_id == session_id))
            await db.commit()

    # ── Dashboard chart pinning ────────────────────────────────────────────────

    async def pin_chart(self, user_id: str, session_id: str, chart_data: dict) -> None:
        from orchestrator.state import ChartConfig
        chart = ChartConfig.model_validate(chart_data)
        async with self._session_factory() as db:
            # BUG-28: Upsert to avoid duplicate primary key errors when re-pinning
            existing = await db.get(DashboardChart, chart.id)
            if existing:
                existing.chart_data = chart.model_dump_json()
                existing.user_id = user_id
                existing.session_id = session_id
                existing.pinned_at = datetime.now(timezone.utc)
            else:
                db.add(DashboardChart(
                    id=chart.id,
                    user_id=user_id,
                    session_id=session_id,
                    chart_data=chart.model_dump_json(),
                ))
            await db.commit()

    async def get_dashboard(self, user_id: str) -> list[dict]:
        from orchestrator.state import ChartConfig
        async with self._session_factory() as db:
            result = await db.execute(
                select(DashboardChart)
                .where(DashboardChart.user_id == user_id)
                .order_by(DashboardChart.pinned_at.desc())
            )
            charts = result.scalars().all()
            return [ChartConfig.model_validate_json(c.chart_data).model_dump() for c in charts]

    async def unpin_chart(self, chart_id: str, user_id: str) -> None:
        async with self._session_factory() as db:
            await db.execute(
                delete(DashboardChart)
                .where(DashboardChart.id == chart_id, DashboardChart.user_id == user_id)
            )
            await db.commit()

    async def get_latest_analysis(self, user_id: str) -> AnalysisSession | None:
        # Walks sessions newest-first and returns the first one with an
        # insight_report. We can't filter on the JSONB content portably across
        # SQLite/Postgres without dialect-specific operators, so we paginate.
        async with self._session_factory() as db:
            result = await db.execute(
                select(SessionRecord)
                .where(SessionRecord.user_id == user_id)
                .order_by(SessionRecord.updated_at.desc())
                .limit(20)
            )
            for record in result.scalars():
                s = AnalysisSession.model_validate_json(record.data)
                if s.insight_report is not None:
                    return s
            return None
