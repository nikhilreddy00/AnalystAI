"""
PostgreSQL-backed session store: analysis threads (sessions), pinned charts, and (Phase 5.6b)
datasets with their versions. In-memory twin for development and tests, same contract.

Threads: only `create` inserts a row (POST /chat); `save` updates a live row and is a no-op on a
deleted one, so a late write from a worker can never bring a deleted thread back. Deleting is a
tombstone (`deleted_at`): the thread is hidden at once, and `memory/retention.py` purges every
store once its runs have stopped.

Datasets (ADR-0007): an upload is a version (content hash) of a dataset. Same bytes → the same
version (no new file). Same name and the same columns → a new version of that dataset. Otherwise
a new dataset. Live sources (SQL, Sheets, API) are one dataset per connection, with no versions.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from sqlalchemy import (Boolean, Column, DateTime, Integer, String, Text, UniqueConstraint, delete,
                        select, text, update)
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from orchestrator.state import AnalysisSession, ConnectorConfig, SessionSummary
from memory.session_data import delete_session_files


_SECRET_FIELDS = ("connection_string", "service_account_json", "auth_value")
_ENC_PREFIX = "enc:"


def _fernet():
    from cryptography.fernet import Fernet
    from api.config import settings
    return Fernet(settings.encryption_key.encode())


def _enc_config(cfg: ConnectorConfig) -> ConnectorConfig:
    f = _fernet()
    return cfg.model_copy(update={
        k: _ENC_PREFIX + f.encrypt(v.encode()).decode()
        for k in _SECRET_FIELDS if (v := getattr(cfg, k))
    })


def _dec_config(cfg: ConnectorConfig) -> ConnectorConfig:
    f = _fernet()
    for k in _SECRET_FIELDS:
        v = getattr(cfg, k)
        if v and v.startswith(_ENC_PREFIX):  # rows written before encryption stay readable
            setattr(cfg, k, f.decrypt(v[len(_ENC_PREFIX):].encode()).decode())
    return cfg


def _dump(session: AnalysisSession) -> str:
    """Serialize with connector secrets Fernet-encrypted (never plaintext at rest)."""
    cfg = session.connector_config
    if cfg is None:
        return session.model_dump_json()
    return session.model_copy(update={"connector_config": _enc_config(cfg)}).model_dump_json()


def _load(data: str) -> AnalysisSession:
    s = AnalysisSession.model_validate_json(data)
    if s.connector_config is not None:
        _dec_config(s.connector_config)
    return s


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(dt: datetime) -> datetime:
    """Older columns are stored without a zone (they hold UTC): say so, or a browser reads them
    as local time and puts a thread in the wrong date group."""
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


TITLE_MAX = 120
_QUESTION_IN_TITLE = 60


def make_title(dataset_name: str | None, question: str | None) -> str:
    """A thread's title, by code (no LLM call): dataset name + the first question."""
    q = " ".join((question or "").split())
    if len(q) > _QUESTION_IN_TITLE:
        q = q[:_QUESTION_IN_TITLE - 1].rstrip() + "…"
    name = dataset_name or "Analysis"
    return f"{name} · {q}" if q else name


def conn_key(cfg: ConnectorConfig) -> str:
    """Which live source this is: type + connection + table/sheet/url. Keyed with ENCRYPTION_KEY,
    because the connection string is a secret; two databases with a same-named table differ."""
    from api.config import settings
    where = [cfg.type.value, cfg.connection_string, cfg.table_name, cfg.spreadsheet_id,
             cfg.sheet_name, cfg.url]
    return hmac.new(settings.encryption_key.encode(), "|".join(str(w or "") for w in where).encode(),
                    hashlib.sha256).hexdigest()[:32]


def _same_columns(a: list[str], b: list[str]) -> bool:
    return sorted(c.strip().lower() for c in a) == sorted(c.strip().lower() for c in b)


def _delete_upload(path: str | None, user_id: str) -> None:
    """Remove an upload file, only if it is inside this user's upload folder."""
    from connectors.csv_connector import check_upload_owner
    if path and check_upload_owner(path, user_id) is None:
        Path(path).unlink(missing_ok=True)


@dataclass
class DatasetVersion:
    id: str
    dataset_id: str
    version: int
    content_hash: str
    file_path: str | None
    rows: int | None
    columns: list[str]
    created_at: datetime

    def public(self) -> dict:
        return {"id": self.id, "version": self.version, "content_hash": self.content_hash,
                "rows": self.rows, "columns": self.columns,
                "created_at": _utc(self.created_at).isoformat()}


@dataclass
class Dataset:
    id: str
    user_id: str
    name: str
    kind: str                                  # "upload" | "live"
    created_at: datetime
    versions: list[DatasetVersion] = field(default_factory=list)   # oldest first
    config: ConnectorConfig | None = None      # live sources only, secrets decrypted

    @property
    def latest(self) -> DatasetVersion | None:
        return self.versions[-1] if self.versions else None

    def public(self, analyses: int = 0) -> dict:
        return {"id": self.id, "name": self.name, "kind": self.kind,
                "created_at": _utc(self.created_at).isoformat(), "analyses": analyses,
                "latest": self.latest.public() if self.latest else None,
                "versions": [v.public() for v in self.versions],
                "source": self.config.type.value if self.config else "csv"}


class DatasetConflict(Exception):
    """The version already has analyses (it can no longer be split off)."""


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
    # Phase 5.6b: the workspace sidebar reads only these columns, never `data`.
    dataset_id = Column(String, nullable=True, index=True)
    dataset_version_id = Column(String, nullable=True)
    title = Column(String, nullable=True)
    questions = Column(Text, nullable=False, default="")
    pinned = Column(Boolean, nullable=False, default=False)
    archived = Column(Boolean, nullable=False, default=False)
    deleted_at = Column(DateTime(timezone=True), nullable=True)   # tombstone only


class DashboardChart(Base):
    __tablename__ = "dashboard_charts"

    id = Column(String, primary_key=True)
    user_id = Column(String, nullable=False, index=True)
    session_id = Column(String, nullable=False)
    chart_data = Column(Text, nullable=False)    # ChartConfig as JSON
    pinned_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class DatasetRow(Base):
    __tablename__ = "datasets"

    id = Column(String, primary_key=True)
    user_id = Column(String, nullable=False, index=True)
    name = Column(String, nullable=False)
    kind = Column(String, nullable=False)                 # upload | live
    conn_key = Column(String, nullable=True, index=True)  # live sources: conn_key(cfg)
    config = Column(Text, nullable=True)                  # live: ConnectorConfig, secrets encrypted
    created_at = Column(DateTime(timezone=True), nullable=False)
    deleted_at = Column(DateTime(timezone=True), nullable=True)


class DatasetVersionRow(Base):
    __tablename__ = "dataset_versions"
    __table_args__ = (UniqueConstraint("dataset_id", "content_hash"),
                      UniqueConstraint("dataset_id", "version"))

    id = Column(String, primary_key=True)
    dataset_id = Column(String, nullable=False, index=True)
    user_id = Column(String, nullable=False, index=True)
    version = Column(Integer, nullable=False)
    content_hash = Column(String, nullable=False, index=True)
    file_path = Column(String, nullable=True)
    rows = Column(Integer, nullable=True)
    columns = Column(Text, nullable=False)                # JSON list of column names
    created_at = Column(DateTime(timezone=True), nullable=False)


def _summary(sid: str, m: dict) -> SessionSummary:
    return SessionSummary(
        session_id=sid, title=m.get("title"), dataset_id=m.get("dataset_id"),
        dataset_name=m.get("dataset_name"), questions=m.get("questions") or "",
        pinned=bool(m.get("pinned")), archived=bool(m.get("archived")), status=m["status"],
        created_at=_utc(m["created_at"]), updated_at=_utc(m["updated_at"]))


def _session_meta(session: AnalysisSession) -> dict:
    """The columns a save copies from the session itself."""
    return {"dataset_name": session.dataset_name, "file_hash": session.file_hash,
            "status": session.status, "dataset_id": session.dataset_id,
            "dataset_version_id": session.dataset_version_id}


class InMemorySessionStore:
    """Fallback store for local dev when PostgreSQL is unavailable."""

    def __init__(self) -> None:
        # session_id -> {"data": json_str, "meta": {user_id, title, questions, pinned, archived,
        #                deleted_at, created_at, updated_at, + _session_meta}}
        self._sessions: dict[str, dict] = {}
        self._charts: dict[str, dict] = {}
        self._datasets: dict[str, dict] = {}   # id -> {user_id, name, kind, conn_key, config,
                                               #        created_at, deleted_at}
        self._versions: dict[str, DatasetVersion] = {}
        self._upload_lock = asyncio.Lock()

    async def init(self) -> None:
        pass

    def _live(self, session_id: str) -> dict | None:
        e = self._sessions.get(session_id)
        return e if e and e["meta"]["deleted_at"] is None else None

    # ── Threads ────────────────────────────────────────────────────────────────

    async def create(self, session: AnalysisSession, title: str | None = None) -> None:
        if session.session_id in self._sessions:
            raise ValueError(f"session {session.session_id} exists")
        now = _now()
        self._sessions[session.session_id] = {"data": session.model_dump_json(), "meta": {
            "user_id": session.user_id, "title": title, "questions": "", "pinned": False,
            "archived": False, "deleted_at": None, "created_at": now, "updated_at": now,
            **_session_meta(session)}}

    async def save(self, session: AnalysisSession) -> bool:
        entry = self._live(session.session_id)
        if entry is None:   # deleted (or never created): a late write must not bring it back
            return False
        entry["data"] = session.model_dump_json()
        entry["meta"].update(_session_meta(session), updated_at=_now())
        return True

    async def note_question(self, session_id: str, question: str) -> None:
        """A new question: searchable, and it brings an archived thread back."""
        if entry := self._live(session_id):
            m = entry["meta"]
            m["questions"] = f"{m['questions']}\n{question}".strip()
            m["archived"], m["updated_at"] = False, _now()

    async def load(self, session_id: str) -> AnalysisSession | None:
        entry = self._live(session_id)
        return AnalysisSession.model_validate_json(entry["data"]) if entry else None

    async def get_summary(self, session_id: str, user_id: str) -> SessionSummary | None:
        entry = self._live(session_id)
        if not entry or entry["meta"]["user_id"] != user_id:
            return None
        return _summary(session_id, entry["meta"])

    async def list_sessions(self, user_id: str, limit: int = 500,
                            dataset_id: str | None = None) -> list[SessionSummary]:
        items = [(sid, e["meta"]) for sid, e in self._sessions.items()
                 if e["meta"]["user_id"] == user_id and e["meta"]["deleted_at"] is None
                 and (dataset_id is None or e["meta"]["dataset_id"] == dataset_id)]
        items.sort(key=lambda kv: kv[1]["updated_at"], reverse=True)
        return [_summary(sid, m) for sid, m in items[:limit]]

    async def update_thread(self, session_id: str, user_id: str, **fields) -> bool:
        entry = self._live(session_id)
        if not entry or entry["meta"]["user_id"] != user_id:
            return False
        entry["meta"].update(fields)
        return True

    async def tombstone(self, session_id: str, user_id: str) -> bool:
        entry = self._live(session_id)
        if not entry or entry["meta"]["user_id"] != user_id:
            return False
        entry["meta"]["deleted_at"] = _now()
        return True

    async def tombstoned(self) -> list[tuple[str, str, datetime]]:
        return [(sid, e["meta"]["user_id"], e["meta"]["deleted_at"])
                for sid, e in self._sessions.items() if e["meta"]["deleted_at"] is not None]

    async def idle_threads(self, before: datetime) -> list[tuple[str, str]]:
        return [(sid, e["meta"]["user_id"]) for sid, e in self._sessions.items()
                if e["meta"]["deleted_at"] is None and _utc(e["meta"]["updated_at"]) < before]

    async def session_ids(self) -> set[str]:
        """Every thread that still has a row, deleted or not (the orphan sweep keeps these)."""
        return set(self._sessions)

    async def purge_session(self, session_id: str) -> None:
        """The last purge step: pinned charts, cached files, then the row (the tombstone)."""
        for cid in [c for c, v in self._charts.items() if v["session_id"] == session_id]:
            self._charts.pop(cid)
        delete_session_files(session_id)
        self._sessions.pop(session_id, None)

    async def count_rows(self, *, session_id: str | None = None,
                         dataset_id: str | None = None) -> dict[str, int]:
        """Rows left per table for a thread or dataset (the deletion test)."""
        return {
            "analysis_sessions": sum(1 for sid, e in self._sessions.items()
                                     if sid == session_id
                                     or dataset_id and e["meta"]["dataset_id"] == dataset_id),
            "dashboard_charts": sum(1 for v in self._charts.values()
                                    if v["session_id"] == session_id),
            "datasets": int(dataset_id in self._datasets),
            "dataset_versions": sum(1 for v in self._versions.values()
                                    if v.dataset_id == dataset_id),
        }

    # ── Dashboard chart pinning ────────────────────────────────────────────────

    async def pin_chart(self, user_id: str, session_id: str, chart_data: dict) -> None:
        from orchestrator.state import ChartConfig
        chart = ChartConfig.model_validate(chart_data)
        self._charts[chart.id] = {
            "user_id": user_id,
            "session_id": session_id,
            "chart": chart.model_dump(),
        }

    async def get_dashboard(self, user_id: str,
                            session_ids: set[str] | None = None) -> list[dict]:
        return [v["chart"] for v in self._charts.values() if v["user_id"] == user_id
                and self._live(v["session_id"])
                and (session_ids is None or v["session_id"] in session_ids)]

    async def unpin_chart(self, chart_id: str, user_id: str) -> None:
        # Mirror PG store: only the owner may unpin. A bare pop() would let
        # any caller delete any user's chart by id.
        existing = self._charts.get(chart_id)
        if existing and existing["user_id"] == user_id:
            self._charts.pop(chart_id, None)

    async def get_latest_analysis(self, user_id: str) -> AnalysisSession | None:
        candidates: list[AnalysisSession] = []
        for entry in self._sessions.values():
            if entry["meta"]["user_id"] != user_id or entry["meta"]["deleted_at"] is not None:
                continue
            s = AnalysisSession.model_validate_json(entry["data"])
            if s.insight_report is not None:
                candidates.append(s)
        if not candidates:
            return None
        return max(candidates, key=lambda s: s.updated_at)

    # ── Datasets ───────────────────────────────────────────────────────────────

    def _dataset(self, did: str) -> Dataset:
        d = self._datasets[did]
        cfg = ConnectorConfig.model_validate_json(d["config"]) if d["config"] else None
        return Dataset(id=did, user_id=d["user_id"], name=d["name"], kind=d["kind"],
                       created_at=d["created_at"], config=_dec_config(cfg) if cfg else None,
                       versions=sorted((v for v in self._versions.values()
                                        if v.dataset_id == did), key=lambda v: v.version))

    def _live_datasets(self, user_id: str) -> list[str]:
        return [did for did, d in self._datasets.items()
                if d["user_id"] == user_id and d["deleted_at"] is None]

    def _new_dataset(self, user_id: str, name: str, kind: str, key: str | None = None,
                     config: ConnectorConfig | None = None) -> str:
        did = str(uuid.uuid4())
        self._datasets[did] = {"user_id": user_id, "name": name, "kind": kind, "conn_key": key,
                               "config": _enc_config(config).model_dump_json() if config else None,
                               "created_at": _now(), "deleted_at": None}
        return did

    def _add_version(self, did: str, content_hash: str, file_path: str | None, rows, columns,
                     user_id: str) -> DatasetVersion:
        n = max((v.version for v in self._versions.values() if v.dataset_id == did), default=0)
        v = DatasetVersion(id=str(uuid.uuid4()), dataset_id=did, version=n + 1,
                           content_hash=content_hash, file_path=file_path, rows=rows,
                           columns=list(columns), created_at=_now())
        self._versions[v.id] = v
        return v

    async def register_upload(self, user_id: str, *, name: str, content_hash: str,
                              columns: list[str], rows: int | None,
                              write_file: Callable[[], str], dataset_id: str | None = None,
                              ) -> tuple[Dataset, DatasetVersion, str] | None:
        """Place an upload: (dataset, version, "existing" | "new_version" | "new_dataset").
        `write_file` is called only when the bytes are new. None: `dataset_id` isn't the user's."""
        async with self._upload_lock:
            live = self._live_datasets(user_id)
            if dataset_id is not None and (dataset_id not in live
                                           or self._datasets[dataset_id]["kind"] != "upload"):
                return None
            for v in self._versions.values():
                if v.dataset_id in live and v.content_hash == content_hash:
                    return self._dataset(v.dataset_id), v, "existing"
            target = dataset_id or next(
                (did for did in sorted(live, key=lambda d: self._datasets[d]["created_at"],
                                       reverse=True)
                 if self._datasets[did]["kind"] == "upload"
                 and self._datasets[did]["name"].lower() == name.lower()
                 and (latest := self._dataset(did).latest)
                 and _same_columns(latest.columns, columns)), None)
            path = write_file()
            status = "new_version" if target else "new_dataset"
            did = target or self._new_dataset(user_id, name, "upload")
            v = self._add_version(did, content_hash, path, rows, columns, user_id)
            return self._dataset(did), v, status

    async def ensure_live_dataset(self, user_id: str, name: str,
                                  config: ConnectorConfig) -> Dataset:
        key = conn_key(config)
        for did in self._live_datasets(user_id):
            if self._datasets[did]["conn_key"] == key:
                self._datasets[did]["config"] = _enc_config(config).model_dump_json()
                return self._dataset(did)
        return self._dataset(self._new_dataset(user_id, name, "live", key, config))

    async def get_dataset(self, dataset_id: str, user_id: str) -> Dataset | None:
        return self._dataset(dataset_id) if dataset_id in self._live_datasets(user_id) else None

    async def get_version(self, version_id: str,
                          user_id: str) -> tuple[Dataset, DatasetVersion] | None:
        v = self._versions.get(version_id)
        if v is None or v.dataset_id not in self._live_datasets(user_id):
            return None
        return self._dataset(v.dataset_id), v

    async def version_by_path(self, file_path: str,
                              user_id: str) -> tuple[Dataset | None, DatasetVersion] | None:
        """The version stored at this upload path: (its dataset, or None if that dataset was
        deleted, version). None: the path is no dataset's (a pre-5.6b upload)."""
        v = next((v for v in self._versions.values() if v.file_path == file_path
                  and self._datasets[v.dataset_id]["user_id"] == user_id), None)
        if v is None:
            return None
        return (self._dataset(v.dataset_id) if v.dataset_id in self._live_datasets(user_id)
                else None), v

    async def list_datasets(self, user_id: str) -> list[tuple[Dataset, int]]:
        """(dataset, live threads using it), newest first."""
        out = [(self._dataset(did), sum(1 for e in self._sessions.values()
                                        if e["meta"]["dataset_id"] == did
                                        and e["meta"]["deleted_at"] is None))
               for did in self._live_datasets(user_id)]
        return sorted(out, key=lambda x: x[0].created_at, reverse=True)

    async def rename_dataset(self, dataset_id: str, user_id: str, name: str) -> bool:
        if dataset_id not in self._live_datasets(user_id):
            return False
        self._datasets[dataset_id]["name"] = name
        return True

    async def split_version(self, version_id: str, user_id: str) -> Dataset | None:
        """D2 undo: move a version into a dataset of its own. DatasetConflict once it has
        analyses (their reports cite it as a version of its dataset) or if it is version 1."""
        got = await self.get_version(version_id, user_id)
        if got is None:
            return None
        ds, v = got
        if v.version == 1 or any(e["meta"]["dataset_version_id"] == version_id
                                 for e in self._sessions.values()):
            raise DatasetConflict(version_id)
        did = self._new_dataset(user_id, ds.name, "upload")
        v.dataset_id, v.version = did, 1
        return self._dataset(did)

    async def tombstone_dataset(self, dataset_id: str, user_id: str) -> list[str] | None:
        """Hide a dataset and every thread on it; returns those threads (None: not yours)."""
        if dataset_id not in self._live_datasets(user_id):
            return None
        now = _now()
        self._datasets[dataset_id]["deleted_at"] = now
        sids = [sid for sid, e in self._sessions.items() if e["meta"]["dataset_id"] == dataset_id
                and e["meta"]["deleted_at"] is None]
        for sid in sids:
            self._sessions[sid]["meta"]["deleted_at"] = now
        return sids

    async def tombstoned_datasets(self) -> list[tuple[str, str]]:
        return [(did, d["user_id"]) for did, d in self._datasets.items()
                if d["deleted_at"] is not None]

    async def idle_datasets(self, before: datetime) -> list[tuple[str, str]]:
        used = {e["meta"]["dataset_id"] for e in self._sessions.values()}
        return [(did, d["user_id"]) for did, d in self._datasets.items()
                if d["deleted_at"] is None and did not in used and d["created_at"] < before]

    async def purge_dataset(self, dataset_id: str) -> bool:
        """Files, versions, then the dataset row. False while threads on it still exist."""
        if any(e["meta"]["dataset_id"] == dataset_id for e in self._sessions.values()):
            return False
        d = self._datasets.get(dataset_id)
        for vid in [vid for vid, v in self._versions.items() if v.dataset_id == dataset_id]:
            _delete_upload(self._versions[vid].file_path, d["user_id"] if d else "")
            self._versions.pop(vid)
        self._datasets.pop(dataset_id, None)
        return True


class SessionStore:
    def __init__(self, database_url: str) -> None:
        self._engine = create_async_engine(database_url, echo=False)
        self._session_factory = async_sessionmaker(self._engine, expire_on_commit=False)

    async def init(self) -> None:
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all, tables=[
                SessionRecord.__table__, DashboardChart.__table__, DatasetRow.__table__,
                DatasetVersionRow.__table__])
            # Backfill columns on pre-existing PG tables. SQLite picks these up
            # at table-create time so we only run on the postgresql dialect.
            if self._engine.dialect.name == "postgresql":
                for ddl in (
                    "ADD COLUMN IF NOT EXISTS dataset_name VARCHAR",
                    "ADD COLUMN IF NOT EXISTS file_hash VARCHAR",
                    "ADD COLUMN IF NOT EXISTS status VARCHAR NOT NULL DEFAULT 'in_progress'",
                    "ADD COLUMN IF NOT EXISTS dataset_id VARCHAR",
                    "ADD COLUMN IF NOT EXISTS dataset_version_id VARCHAR",
                    "ADD COLUMN IF NOT EXISTS title VARCHAR",
                    "ADD COLUMN IF NOT EXISTS questions TEXT NOT NULL DEFAULT ''",
                    "ADD COLUMN IF NOT EXISTS pinned BOOLEAN NOT NULL DEFAULT false",
                    "ADD COLUMN IF NOT EXISTS archived BOOLEAN NOT NULL DEFAULT false",
                    "ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP WITH TIME ZONE",
                ):
                    await conn.execute(text(f"ALTER TABLE analysis_sessions {ddl}"))
                await conn.execute(text(
                    "CREATE INDEX IF NOT EXISTS ix_analysis_sessions_file_hash ON analysis_sessions(file_hash)"
                ))
                await conn.execute(text(
                    "CREATE INDEX IF NOT EXISTS ix_analysis_sessions_dataset_id "
                    "ON analysis_sessions(dataset_id)"))

    # ── Threads ────────────────────────────────────────────────────────────────

    _LIVE = SessionRecord.deleted_at.is_(None)
    _SUMMARY_COLS = (SessionRecord.session_id, SessionRecord.title, SessionRecord.dataset_id,
                     SessionRecord.dataset_name, SessionRecord.questions, SessionRecord.pinned,
                     SessionRecord.archived, SessionRecord.status, SessionRecord.created_at,
                     SessionRecord.updated_at)

    @staticmethod
    def _summary_row(row) -> SessionSummary:
        m = dict(row._mapping)
        return _summary(m.pop("session_id"), m)

    async def create(self, session: AnalysisSession, title: str | None = None) -> None:
        async with self._session_factory() as db:
            db.add(SessionRecord(session_id=session.session_id, user_id=session.user_id,
                                 data=_dump(session), title=title, questions="",
                                 pinned=False, archived=False, **_session_meta(session)))
            await db.commit()

    async def save(self, session: AnalysisSession) -> bool:
        async with self._session_factory() as db:
            done = await db.execute(
                update(SessionRecord)
                .where(SessionRecord.session_id == session.session_id, self._LIVE)
                .values(data=_dump(session), updated_at=_now(), **_session_meta(session)))
            await db.commit()
            return done.rowcount == 1

    async def note_question(self, session_id: str, question: str) -> None:
        async with self._session_factory() as db:
            await db.execute(text(
                "UPDATE analysis_sessions SET questions = trim(both E'\\n' from "
                "questions || E'\\n' || :q), archived = false, updated_at = :now "
                "WHERE session_id = :sid AND deleted_at IS NULL"),
                {"q": question, "sid": session_id, "now": _now()})
            await db.commit()

    async def load(self, session_id: str) -> AnalysisSession | None:
        async with self._session_factory() as db:
            record = await db.get(SessionRecord, session_id)
            if not record or record.deleted_at is not None:
                return None
            return _load(record.data)

    async def get_summary(self, session_id: str, user_id: str) -> SessionSummary | None:
        async with self._session_factory() as db:
            row = (await db.execute(select(*self._SUMMARY_COLS).where(
                SessionRecord.session_id == session_id, SessionRecord.user_id == user_id,
                self._LIVE))).first()
            return self._summary_row(row) if row else None

    async def list_sessions(self, user_id: str, limit: int = 500,
                            dataset_id: str | None = None) -> list[SessionSummary]:
        q = select(*self._SUMMARY_COLS).where(SessionRecord.user_id == user_id, self._LIVE)
        if dataset_id is not None:
            q = q.where(SessionRecord.dataset_id == dataset_id)
        async with self._session_factory() as db:
            rows = (await db.execute(
                q.order_by(SessionRecord.updated_at.desc()).limit(limit))).all()
            return [self._summary_row(r) for r in rows]

    async def update_thread(self, session_id: str, user_id: str, **fields) -> bool:
        async with self._session_factory() as db:
            done = await db.execute(update(SessionRecord).where(
                SessionRecord.session_id == session_id, SessionRecord.user_id == user_id,
                self._LIVE).values(**fields))
            await db.commit()
            return done.rowcount == 1

    async def tombstone(self, session_id: str, user_id: str) -> bool:
        return await self.update_thread(session_id, user_id, deleted_at=_now())

    async def tombstoned(self) -> list[tuple[str, str, datetime]]:
        async with self._session_factory() as db:
            rows = (await db.execute(select(
                SessionRecord.session_id, SessionRecord.user_id, SessionRecord.deleted_at)
                .where(SessionRecord.deleted_at.is_not(None)))).all()
            return [tuple(r) for r in rows]

    async def idle_threads(self, before: datetime) -> list[tuple[str, str]]:
        async with self._session_factory() as db:
            rows = (await db.execute(select(SessionRecord.session_id, SessionRecord.user_id)
                                     .where(self._LIVE, SessionRecord.updated_at
                                            < before.replace(tzinfo=None)))).all()
            return [tuple(r) for r in rows]

    async def session_ids(self) -> set[str]:
        async with self._session_factory() as db:
            return set((await db.execute(select(SessionRecord.session_id))).scalars())

    async def purge_session(self, session_id: str) -> None:
        async with self._session_factory() as db:
            await db.execute(delete(DashboardChart).where(DashboardChart.session_id == session_id))
            await db.commit()
        delete_session_files(session_id)
        async with self._session_factory() as db:   # the tombstone goes last
            await db.execute(delete(SessionRecord).where(SessionRecord.session_id == session_id))
            await db.commit()

    async def count_rows(self, *, session_id: str | None = None,
                         dataset_id: str | None = None) -> dict[str, int]:
        async with self._session_factory() as db:
            one = (await db.execute(text(
                "SELECT (SELECT count(*) FROM analysis_sessions WHERE session_id = :sid "
                "        OR dataset_id = :did),"
                "       (SELECT count(*) FROM dashboard_charts WHERE session_id = :sid),"
                "       (SELECT count(*) FROM datasets WHERE id = :did),"
                "       (SELECT count(*) FROM dataset_versions WHERE dataset_id = :did)"),
                {"sid": session_id, "did": dataset_id})).one()
        return dict(zip(("analysis_sessions", "dashboard_charts", "datasets",
                         "dataset_versions"), one))

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

    async def get_dashboard(self, user_id: str,
                            session_ids: set[str] | None = None) -> list[dict]:
        from orchestrator.state import ChartConfig
        q = (select(DashboardChart.chart_data)
             .join(SessionRecord, SessionRecord.session_id == DashboardChart.session_id)
             .where(DashboardChart.user_id == user_id, self._LIVE))
        if session_ids is not None:
            q = q.where(DashboardChart.session_id.in_(session_ids))
        async with self._session_factory() as db:
            rows = (await db.execute(q.order_by(DashboardChart.pinned_at.desc()))).scalars()
            return [ChartConfig.model_validate_json(c).model_dump() for c in rows]

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
                select(SessionRecord.data)
                .where(SessionRecord.user_id == user_id, self._LIVE)
                .order_by(SessionRecord.updated_at.desc())
                .limit(20)
            )
            for data in result.scalars():
                s = _load(data)
                if s.insight_report is not None:
                    return s
            return None

    # ── Datasets ───────────────────────────────────────────────────────────────

    @staticmethod
    def _version(r: DatasetVersionRow) -> DatasetVersion:
        return DatasetVersion(id=r.id, dataset_id=r.dataset_id, version=r.version,
                              content_hash=r.content_hash, file_path=r.file_path, rows=r.rows,
                              columns=json.loads(r.columns), created_at=r.created_at)

    async def _datasets(self, db, where) -> list[Dataset]:
        rows = (await db.execute(select(DatasetRow).where(*where)
                                 .order_by(DatasetRow.created_at.desc()))).scalars().all()
        if not rows:
            return []
        versions = (await db.execute(
            select(DatasetVersionRow).where(DatasetVersionRow.dataset_id.in_([r.id for r in rows]))
            .order_by(DatasetVersionRow.version))).scalars().all()
        out = []
        for r in rows:
            cfg = ConnectorConfig.model_validate_json(r.config) if r.config else None
            out.append(Dataset(id=r.id, user_id=r.user_id, name=r.name, kind=r.kind,
                               created_at=r.created_at, config=_dec_config(cfg) if cfg else None,
                               versions=[self._version(v) for v in versions
                                         if v.dataset_id == r.id]))
        return out

    @staticmethod
    def _live_ds(user_id: str):
        return (DatasetRow.user_id == user_id, DatasetRow.deleted_at.is_(None))

    async def register_upload(self, user_id: str, *, name: str, content_hash: str,
                              columns: list[str], rows: int | None,
                              write_file: Callable[[], str], dataset_id: str | None = None,
                              ) -> tuple[Dataset, DatasetVersion, str] | None:
        async with self._session_factory() as db:
            # One upload per user at a time: two identical uploads can't make two versions.
            await db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"),
                             {"k": f"upload:{user_id}"})
            live = await self._datasets(db, self._live_ds(user_id))
            by_id = {d.id: d for d in live}
            if dataset_id is not None and (dataset_id not in by_id
                                           or by_id[dataset_id].kind != "upload"):
                return None
            for d in live:
                for v in d.versions:
                    if v.content_hash == content_hash:
                        return d, v, "existing"
            target = by_id.get(dataset_id) if dataset_id else next(
                (d for d in live if d.kind == "upload" and d.name.lower() == name.lower()
                 and d.latest and _same_columns(d.latest.columns, columns)), None)
            path = write_file()
            try:
                if target is None:
                    target = Dataset(id=str(uuid.uuid4()), user_id=user_id, name=name,
                                     kind="upload", created_at=_now())
                    db.add(DatasetRow(id=target.id, user_id=user_id, name=name, kind="upload",
                                      created_at=target.created_at))
                    status = "new_dataset"
                else:
                    status = "new_version"
                v = DatasetVersion(id=str(uuid.uuid4()), dataset_id=target.id,
                                   version=(target.latest.version if target.latest else 0) + 1,
                                   content_hash=content_hash, file_path=path, rows=rows,
                                   columns=list(columns), created_at=_now())
                db.add(DatasetVersionRow(id=v.id, dataset_id=v.dataset_id, user_id=user_id,
                                         version=v.version, content_hash=content_hash,
                                         file_path=path, rows=rows, columns=json.dumps(columns),
                                         created_at=v.created_at))
                await db.commit()
            except Exception:
                _delete_upload(path, user_id)   # never leave a file no row points at
                raise
            target.versions.append(v)
            return target, v, status

    async def ensure_live_dataset(self, user_id: str, name: str,
                                  config: ConnectorConfig) -> Dataset:
        key = conn_key(config)
        enc = _enc_config(config).model_dump_json()
        async with self._session_factory() as db:
            await db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"),
                             {"k": f"live:{user_id}:{key}"})
            found = await self._datasets(db, (*self._live_ds(user_id), DatasetRow.conn_key == key))
            if found:
                await db.execute(update(DatasetRow).where(DatasetRow.id == found[0].id)
                                 .values(config=enc))
                await db.commit()
                return found[0]
            ds = Dataset(id=str(uuid.uuid4()), user_id=user_id, name=name, kind="live",
                         created_at=_now(), config=config)
            db.add(DatasetRow(id=ds.id, user_id=user_id, name=name, kind="live", conn_key=key,
                              config=enc, created_at=ds.created_at))
            await db.commit()
            return ds

    async def get_dataset(self, dataset_id: str, user_id: str) -> Dataset | None:
        async with self._session_factory() as db:
            found = await self._datasets(db, (*self._live_ds(user_id), DatasetRow.id == dataset_id))
            return found[0] if found else None

    async def get_version(self, version_id: str,
                          user_id: str) -> tuple[Dataset, DatasetVersion] | None:
        async with self._session_factory() as db:
            vrow = await db.get(DatasetVersionRow, version_id)
        if vrow is None or vrow.user_id != user_id:
            return None
        ds = await self.get_dataset(vrow.dataset_id, user_id)
        if ds is None:
            return None
        return ds, next(v for v in ds.versions if v.id == version_id)

    async def version_by_path(self, file_path: str,
                              user_id: str) -> tuple[Dataset | None, DatasetVersion] | None:
        async with self._session_factory() as db:
            vrow = (await db.execute(select(DatasetVersionRow).where(
                DatasetVersionRow.file_path == file_path,
                DatasetVersionRow.user_id == user_id))).scalars().first()
        if vrow is None:
            return None
        return await self.get_dataset(vrow.dataset_id, user_id), self._version(vrow)

    async def list_datasets(self, user_id: str) -> list[tuple[Dataset, int]]:
        async with self._session_factory() as db:
            found = await self._datasets(db, self._live_ds(user_id))
            counts = dict((await db.execute(text(
                "SELECT dataset_id, count(*) FROM analysis_sessions WHERE user_id = :uid "
                "AND deleted_at IS NULL AND dataset_id IS NOT NULL GROUP BY dataset_id"),
                {"uid": user_id})).all())
        return [(d, counts.get(d.id, 0)) for d in found]

    async def rename_dataset(self, dataset_id: str, user_id: str, name: str) -> bool:
        async with self._session_factory() as db:
            done = await db.execute(update(DatasetRow).where(
                DatasetRow.id == dataset_id, *self._live_ds(user_id)).values(name=name))
            await db.commit()
            return done.rowcount == 1

    async def split_version(self, version_id: str, user_id: str) -> Dataset | None:
        got = await self.get_version(version_id, user_id)
        if got is None:
            return None
        ds, v = got
        async with self._session_factory() as db:
            used = (await db.execute(text(
                "SELECT count(*) FROM analysis_sessions WHERE dataset_version_id = :v"),
                {"v": version_id})).scalar_one()
            if v.version == 1 or used:
                raise DatasetConflict(version_id)
            new = Dataset(id=str(uuid.uuid4()), user_id=user_id, name=ds.name, kind="upload",
                          created_at=_now())
            db.add(DatasetRow(id=new.id, user_id=user_id, name=ds.name, kind="upload",
                              created_at=new.created_at))
            await db.flush()
            await db.execute(update(DatasetVersionRow).where(DatasetVersionRow.id == version_id)
                             .values(dataset_id=new.id, version=1))
            await db.commit()
        return await self.get_dataset(new.id, user_id)

    async def tombstone_dataset(self, dataset_id: str, user_id: str) -> list[str] | None:
        now = _now()
        async with self._session_factory() as db:
            done = await db.execute(update(DatasetRow).where(
                DatasetRow.id == dataset_id, *self._live_ds(user_id)).values(deleted_at=now))
            if done.rowcount != 1:
                return None
            sids = (await db.execute(
                update(SessionRecord).where(SessionRecord.dataset_id == dataset_id, self._LIVE)
                .values(deleted_at=now).returning(SessionRecord.session_id))).scalars().all()
            await db.commit()
            return list(sids)

    async def tombstoned_datasets(self) -> list[tuple[str, str]]:
        async with self._session_factory() as db:
            rows = (await db.execute(select(DatasetRow.id, DatasetRow.user_id)
                                     .where(DatasetRow.deleted_at.is_not(None)))).all()
            return [tuple(r) for r in rows]

    async def idle_datasets(self, before: datetime) -> list[tuple[str, str]]:
        async with self._session_factory() as db:
            rows = (await db.execute(text(
                "SELECT d.id, d.user_id FROM datasets d WHERE d.deleted_at IS NULL "
                "AND d.created_at < :before AND NOT EXISTS (SELECT 1 FROM analysis_sessions s "
                "WHERE s.dataset_id = d.id)"), {"before": before})).all()
            return [tuple(r) for r in rows]

    async def purge_dataset(self, dataset_id: str) -> bool:
        async with self._session_factory() as db:
            if (await db.execute(text(
                    "SELECT count(*) FROM analysis_sessions WHERE dataset_id = :d"),
                    {"d": dataset_id})).scalar_one():
                return False
            versions = (await db.execute(select(DatasetVersionRow).where(
                DatasetVersionRow.dataset_id == dataset_id))).scalars().all()
        for v in versions:   # files first: a crash here leaves rows that the next sweep retries
            _delete_upload(v.file_path, v.user_id)
        async with self._session_factory() as db:
            await db.execute(delete(DatasetVersionRow).where(
                DatasetVersionRow.dataset_id == dataset_id))
            await db.execute(delete(DatasetRow).where(DatasetRow.id == dataset_id))
            await db.commit()
        return True
