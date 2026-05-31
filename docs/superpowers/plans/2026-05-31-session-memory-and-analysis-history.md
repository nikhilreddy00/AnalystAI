# Session Memory & Analysis History — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist completed analyses so users can revisit them instantly, deduplicate re-uploads of the same dataset, and browse past sessions from a sidebar.

**Architecture:** Three sequential phases — backend persistence groundwork → graph/chat-route wiring → frontend sidebar. Metadata + full `AnalysisSession` (incl. `insight_report`, `chart_configs`, `conversation_history`) continues to live in the existing `SessionStore` (PG/in-memory). Only the cleaned DataFrame goes to the filesystem as parquet, keyed by `session_id`. Source spec: [docs/superpowers/specs/2026-05-30-session-memory-and-analysis-history-design.md](../specs/2026-05-30-session-memory-and-analysis-history-design.md).

**Tech Stack:** FastAPI + SQLAlchemy async, Pydantic v2, LangGraph, Next.js 16 + React + Tailwind v4, pytest + pytest-asyncio.

**Testing convention.** The project has `pytest>=8.2` and `pytest-asyncio>=0.23` declared in `pyproject.toml` but no test files yet ([CLAUDE.md](../../../CLAUDE.md) note: "testing is manual via the UI"). This plan uses pytest for the two pure-Python pieces where it pays off cheaply (the parquet helper and the store summary methods backed by `InMemorySessionStore`). Everything else (graph nodes, FastAPI endpoints, React components) is verified manually with `curl` / log inspection / UI smoke tests, matching the existing project convention.

**Phase verification gates.** Each phase is shippable on its own and verifies independently — do not start a later phase until the current phase's gate passes.

---

## Phase 1 — Backend persistence groundwork

Invisible to users. Adds the schema fields, the parquet helper, the new store methods, and the new endpoints. Existing `/chat` flow must continue to work unchanged at the end of this phase.

### Task 1.1: Add `SessionSummary` + 4 new fields to `AnalysisSession`

**Files:**
- Modify: `orchestrator/state.py`

- [ ] **Step 1: Add `SessionSummary` and extend `AnalysisSession`**

Add `Literal` to the typing imports if not already present (the file uses `from typing import ...`). Insert the new model directly above `class AnalysisSession`:

```python
class SessionSummary(BaseModel):
    """Lightweight session metadata for the sidebar list and dedup lookup.
    Excludes insight_report / chart_configs to keep list responses cheap.
    """
    session_id: str
    dataset_name: str | None = None
    original_query: str | None = None
    row_count: int | None = None
    column_count: int | None = None
    status: Literal["in_progress", "completed", "failed"] = "in_progress"
    created_at: datetime
    updated_at: datetime
```

Then add four fields to `AnalysisSession` (keep existing fields untouched; insert these alongside `error_message` / `updated_at`):

```python
    dataset_name: str | None = None
    file_hash: str | None = None
    status: Literal["in_progress", "completed", "failed"] = "in_progress"
    cleaned_data_path: str | None = None
```

All four are defaulted so older persisted JSON deserializes cleanly.

- [ ] **Step 2: Verify state.py imports parse**

Run: `cd /Users/uvnikhil/Desktop/AnalystAI && uv run python -c "from orchestrator.state import AnalysisSession, SessionSummary; s=AnalysisSession(session_id='x'); print(s.status, s.file_hash, s.cleaned_data_path)"`
Expected output: `in_progress None None`

- [ ] **Step 3: Commit**

```bash
git add orchestrator/state.py
git commit -m "feat(state): add SessionSummary + dataset_name/file_hash/status/cleaned_data_path on AnalysisSession"
```

---

### Task 1.2: Parquet helper module + unit tests

**Files:**
- Create: `memory/session_data.py`
- Create: `tests/__init__.py` (empty)
- Create: `tests/test_session_data.py`

- [ ] **Step 1: Bootstrap the tests directory**

Create `tests/__init__.py` as an empty file.

- [ ] **Step 2: Write the failing test for the parquet helper**

Create `tests/test_session_data.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from memory import session_data


@pytest.fixture
def tmp_session_dir(tmp_path, monkeypatch):
    # Point SESSION_DATA_DIR at a tmp path for this test only.
    monkeypatch.setattr(session_data, "_DATA_DIR", tmp_path)
    return tmp_path


def test_session_dir_creates_parent(tmp_session_dir):
    d = session_data.session_dir("abc-123")
    assert d == tmp_session_dir / "abc-123"
    assert d.is_dir()


def test_write_and_read_round_trip(tmp_session_dir):
    payload = b"some-parquet-bytes"
    path = session_data.write_cleaned_parquet("sess-1", payload)
    assert Path(path).is_file()
    assert Path(path).name == "cleaned_data.parquet"
    assert session_data.read_cleaned_parquet("sess-1") == payload


def test_read_missing_returns_none(tmp_session_dir):
    assert session_data.read_cleaned_parquet("never-existed") is None


def test_write_is_atomic_replace(tmp_session_dir):
    # Two writes to the same session should leave only the final bytes.
    session_data.write_cleaned_parquet("sess-2", b"v1")
    session_data.write_cleaned_parquet("sess-2", b"v2")
    assert session_data.read_cleaned_parquet("sess-2") == b"v2"
    # No stray .tmp left behind
    assert not (tmp_session_dir / "sess-2" / "cleaned_data.parquet.tmp").exists()


def test_delete_session_files_is_idempotent(tmp_session_dir):
    session_data.write_cleaned_parquet("sess-3", b"x")
    session_data.delete_session_files("sess-3")
    assert not (tmp_session_dir / "sess-3").exists()
    # Calling again on missing dir must not raise.
    session_data.delete_session_files("sess-3")
```

- [ ] **Step 3: Run tests to confirm they fail**

Run: `cd /Users/uvnikhil/Desktop/AnalystAI && uv run pytest tests/test_session_data.py -v`
Expected: collection or import error — `memory.session_data` does not exist yet.

- [ ] **Step 4: Implement the helper**

Create `memory/session_data.py`:

```python
"""Filesystem layer for per-session binary artifacts (the cleaned DataFrame).

Metadata + the full AnalysisSession JSON keeps living in SessionStore.
Only the parquet bytes live here, keyed by session_id under SESSION_DATA_DIR.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from api.config import settings

_DATA_DIR: Path = Path(settings.session_data_dir).resolve()
_PARQUET_NAME = "cleaned_data.parquet"


def session_dir(session_id: str) -> Path:
    d = _DATA_DIR / session_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_cleaned_parquet(session_id: str, df_parquet_bytes: bytes) -> str:
    """Atomic write of parquet bytes for a session. Returns the absolute path."""
    d = session_dir(session_id)
    final = d / _PARQUET_NAME
    tmp = d / f"{_PARQUET_NAME}.tmp"
    tmp.write_bytes(df_parquet_bytes)
    os.replace(tmp, final)
    return str(final.resolve())


def read_cleaned_parquet(session_id: str) -> bytes | None:
    path = _DATA_DIR / session_id / _PARQUET_NAME
    if not path.is_file():
        return None
    return path.read_bytes()


def delete_session_files(session_id: str) -> None:
    """Remove the per-session directory if it exists. Idempotent."""
    d = _DATA_DIR / session_id
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
```

- [ ] **Step 5: Add `session_data_dir` to `api/config.py`**

Add the field to `Settings` (alongside the existing config keys):

```python
    session_data_dir: str = "./session_data"
```

- [ ] **Step 6: Run tests to confirm they pass**

Run: `cd /Users/uvnikhil/Desktop/AnalystAI && uv run pytest tests/test_session_data.py -v`
Expected: 5 passed.

- [ ] **Step 7: Commit**

```bash
git add memory/session_data.py api/config.py tests/__init__.py tests/test_session_data.py
git commit -m "feat(memory): add per-session parquet helper + SESSION_DATA_DIR config"
```

---

### Task 1.3: SessionStore — new columns + ALTER migration + save() writes indexed fields

**Files:**
- Modify: `memory/session_store.py`

- [ ] **Step 1: Add columns to `SessionRecord`**

In `memory/session_store.py`, extend the `SessionRecord` class:

```python
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
```

- [ ] **Step 2: Add idempotent ALTER for Postgres in `init()`**

At the top of the file add `from sqlalchemy import text`. Replace the existing `SessionStore.init` body with:

```python
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
```

- [ ] **Step 3: Update `SessionStore.save()` to write the new columns**

Replace the body of `SessionStore.save()` with:

```python
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
```

- [ ] **Step 4: Update `InMemorySessionStore` to mirror the indexed-column shape**

Replace the body of `InMemorySessionStore.__init__` and `save` so the in-memory implementation tracks the same metadata that PG indexes:

```python
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
```

Then update `load`, `delete`, `get_latest_analysis` in `InMemorySessionStore` to read from the new shape:

```python
    async def load(self, session_id: str) -> AnalysisSession | None:
        entry = self._sessions.get(session_id)
        if not entry:
            return None
        return AnalysisSession.model_validate_json(entry["data"])

    async def delete(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

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
```

- [ ] **Step 5: Smoke-check imports**

Run: `cd /Users/uvnikhil/Desktop/AnalystAI && uv run python -c "from memory.session_store import SessionStore, InMemorySessionStore; s=InMemorySessionStore(); print(type(s).__name__)"`
Expected: `InMemorySessionStore`

- [ ] **Step 6: Commit**

```bash
git add memory/session_store.py
git commit -m "feat(session_store): index dataset_name/file_hash/status, idempotent PG migration"
```

---

### Task 1.4: SessionStore — `list_sessions`, `find_by_hash`, `get_summary` + tests

**Files:**
- Modify: `memory/session_store.py`
- Create: `tests/test_session_store_summaries.py`

- [ ] **Step 1: Write the failing tests against `InMemorySessionStore`**

Create `tests/test_session_store_summaries.py`:

```python
from __future__ import annotations

import pytest

from memory.session_store import InMemorySessionStore
from orchestrator.state import AnalysisSession, Message, MessageRole, DataContract, ColumnInfo


pytestmark = pytest.mark.asyncio


def _make_session(
    sid: str,
    user_id: str = "u1",
    dataset_name: str | None = None,
    file_hash: str | None = None,
    first_message: str | None = None,
    row_count: int | None = None,
    column_count: int | None = None,
    status: str = "in_progress",
) -> AnalysisSession:
    s = AnalysisSession(session_id=sid, user_id=user_id)
    s.dataset_name = dataset_name
    s.file_hash = file_hash
    s.status = status
    if first_message:
        s.conversation_history.append(Message(role=MessageRole.USER, content=first_message))
    if row_count is not None and column_count is not None:
        s.data_contract = DataContract(
            row_count=row_count,
            column_count=column_count,
            columns=[ColumnInfo(name=f"c{i}", dtype="int64") for i in range(column_count)],
            source_summary="test",
        )
    return s


async def test_list_sessions_returns_summaries_for_user_only():
    store = InMemorySessionStore()
    await store.save(_make_session("a", user_id="u1", dataset_name="titanic", first_message="why?", row_count=10, column_count=3, status="completed"))
    await store.save(_make_session("b", user_id="u2", dataset_name="sales", first_message="show me revenue"))

    summaries = await store.list_sessions("u1")
    ids = [s.session_id for s in summaries]
    assert ids == ["a"]
    assert summaries[0].dataset_name == "titanic"
    assert summaries[0].original_query == "why?"
    assert summaries[0].row_count == 10
    assert summaries[0].column_count == 3
    assert summaries[0].status == "completed"


async def test_list_sessions_sorted_by_updated_at_desc():
    store = InMemorySessionStore()
    await store.save(_make_session("old"))
    await store.save(_make_session("new"))
    summaries = await store.list_sessions("u1")
    assert [s.session_id for s in summaries] == ["new", "old"]


async def test_find_by_hash_matches_user_and_hash():
    store = InMemorySessionStore()
    await store.save(_make_session("a", user_id="u1", file_hash="HASH1"))
    await store.save(_make_session("b", user_id="u2", file_hash="HASH1"))   # different user
    await store.save(_make_session("c", user_id="u1", file_hash="HASH2"))

    hit = await store.find_by_hash("u1", "HASH1")
    assert hit is not None
    assert hit.session_id == "a"
    assert await store.find_by_hash("u1", "HASH_MISSING") is None


async def test_find_by_hash_ignores_null_hash():
    store = InMemorySessionStore()
    await store.save(_make_session("a", user_id="u1", file_hash=None))
    # Passing None must not match other null-hash sessions.
    assert await store.find_by_hash("u1", None) is None  # type: ignore[arg-type]


async def test_get_summary_enforces_user_id():
    store = InMemorySessionStore()
    await store.save(_make_session("a", user_id="u1", dataset_name="t"))
    assert (await store.get_summary("a", "u1")).dataset_name == "t"
    assert await store.get_summary("a", "u2") is None
    assert await store.get_summary("missing", "u1") is None
```

- [ ] **Step 2: Confirm failure**

Run: `cd /Users/uvnikhil/Desktop/AnalystAI && uv run pytest tests/test_session_store_summaries.py -v`
Expected: `AttributeError: 'InMemorySessionStore' object has no attribute 'list_sessions'` (or similar).

- [ ] **Step 3: Implement on `InMemorySessionStore`**

Add a private summary-builder + the three new methods inside `InMemorySessionStore`:

```python
    def _summary_from_entry(self, session_id: str, entry: dict) -> "SessionSummary":
        from orchestrator.state import SessionSummary
        s = AnalysisSession.model_validate_json(entry["data"])
        first_user = next(
            (m.content for m in s.conversation_history if m.role.value == "user"),
            None,
        )
        row_count = s.data_contract.row_count if s.data_contract else None
        column_count = s.data_contract.column_count if s.data_contract else None
        meta = entry["meta"]
        return SessionSummary(
            session_id=session_id,
            dataset_name=meta["dataset_name"],
            original_query=first_user,
            row_count=row_count,
            column_count=column_count,
            status=meta["status"],
            created_at=meta["created_at"],
            updated_at=meta["updated_at"],
        )

    async def list_sessions(self, user_id: str, limit: int = 50) -> list["SessionSummary"]:
        items = [
            (sid, entry) for sid, entry in self._sessions.items()
            if entry["meta"]["user_id"] == user_id
        ]
        items.sort(key=lambda kv: kv[1]["meta"]["updated_at"], reverse=True)
        return [self._summary_from_entry(sid, entry) for sid, entry in items[:limit]]

    async def find_by_hash(self, user_id: str, file_hash: str) -> "SessionSummary | None":
        if not file_hash:
            return None
        # Newest first — if multiple sessions share a hash, dedup prompts the most recent.
        items = [
            (sid, entry) for sid, entry in self._sessions.items()
            if entry["meta"]["user_id"] == user_id and entry["meta"]["file_hash"] == file_hash
        ]
        if not items:
            return None
        items.sort(key=lambda kv: kv[1]["meta"]["updated_at"], reverse=True)
        sid, entry = items[0]
        return self._summary_from_entry(sid, entry)

    async def get_summary(self, session_id: str, user_id: str) -> "SessionSummary | None":
        entry = self._sessions.get(session_id)
        if not entry or entry["meta"]["user_id"] != user_id:
            return None
        return self._summary_from_entry(session_id, entry)
```

- [ ] **Step 4: Implement on `SessionStore` (Postgres)**

Add the same three methods on `SessionStore`. They run a single indexed query and only deserialize the JSON to extract `original_query` / `row_count` / `column_count`:

```python
    async def _summary_from_record(self, record: SessionRecord) -> "SessionSummary":
        from orchestrator.state import SessionSummary
        s = AnalysisSession.model_validate_json(record.data)
        first_user = next(
            (m.content for m in s.conversation_history if m.role.value == "user"),
            None,
        )
        row_count = s.data_contract.row_count if s.data_contract else None
        column_count = s.data_contract.column_count if s.data_contract else None
        return SessionSummary(
            session_id=record.session_id,
            dataset_name=record.dataset_name,
            original_query=first_user,
            row_count=row_count,
            column_count=column_count,
            status=record.status,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )

    async def list_sessions(self, user_id: str, limit: int = 50) -> list["SessionSummary"]:
        async with self._session_factory() as db:
            result = await db.execute(
                select(SessionRecord)
                .where(SessionRecord.user_id == user_id)
                .order_by(SessionRecord.updated_at.desc())
                .limit(limit)
            )
            return [await self._summary_from_record(r) for r in result.scalars()]

    async def find_by_hash(self, user_id: str, file_hash: str) -> "SessionSummary | None":
        if not file_hash:
            return None
        async with self._session_factory() as db:
            result = await db.execute(
                select(SessionRecord)
                .where(SessionRecord.user_id == user_id, SessionRecord.file_hash == file_hash)
                .order_by(SessionRecord.updated_at.desc())
                .limit(1)
            )
            record = result.scalars().first()
            return await self._summary_from_record(record) if record else None

    async def get_summary(self, session_id: str, user_id: str) -> "SessionSummary | None":
        async with self._session_factory() as db:
            record = await db.get(SessionRecord, session_id)
            if not record or record.user_id != user_id:
                return None
            return await self._summary_from_record(record)
```

- [ ] **Step 5: Configure pytest-asyncio**

Add to `pyproject.toml` under the existing `[tool.pytest.ini_options]` block (if it's empty, add the line):

```toml
asyncio_mode = "auto"
```

(Required so `pytestmark = pytest.mark.asyncio` works without explicit decorators on every test.)

- [ ] **Step 6: Run tests to confirm they pass**

Run: `cd /Users/uvnikhil/Desktop/AnalystAI && uv run pytest tests/test_session_store_summaries.py -v`
Expected: 5 passed.

- [ ] **Step 7: Commit**

```bash
git add memory/session_store.py pyproject.toml tests/test_session_store_summaries.py
git commit -m "feat(session_store): list_sessions, find_by_hash, get_summary on both store impls"
```

---

### Task 1.5: SessionStore.delete removes the session directory

**Files:**
- Modify: `memory/session_store.py`

- [ ] **Step 1: Extend both `delete()` implementations to remove session files**

At the top of `memory/session_store.py`, add `from memory.session_data import delete_session_files`.

Update `InMemorySessionStore.delete`:

```python
    async def delete(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)
        delete_session_files(session_id)
```

Update `SessionStore.delete`:

```python
    async def delete(self, session_id: str) -> None:
        async with self._session_factory() as db:
            await db.execute(delete(SessionRecord).where(SessionRecord.session_id == session_id))
            await db.commit()
        delete_session_files(session_id)
```

- [ ] **Step 2: Add a test for file cleanup on delete**

Append to `tests/test_session_store_summaries.py`:

```python
async def test_delete_removes_session_files(tmp_path, monkeypatch):
    from memory import session_data
    monkeypatch.setattr(session_data, "_DATA_DIR", tmp_path)
    store = InMemorySessionStore()
    await store.save(_make_session("a", user_id="u1"))
    # Drop a parquet file as if cleaning had written one.
    session_data.write_cleaned_parquet("a", b"bytes")
    assert (tmp_path / "a" / "cleaned_data.parquet").exists()

    await store.delete("a")
    assert not (tmp_path / "a").exists()
```

- [ ] **Step 3: Run the new test**

Run: `cd /Users/uvnikhil/Desktop/AnalystAI && uv run pytest tests/test_session_store_summaries.py::test_delete_removes_session_files -v`
Expected: pass.

- [ ] **Step 4: Commit**

```bash
git add memory/session_store.py tests/test_session_store_summaries.py
git commit -m "feat(session_store): delete() also removes per-session filesystem dir"
```

---

### Task 1.6: Bootstrap `SESSION_DATA_DIR` on startup

**Files:**
- Modify: `api/main.py`

- [ ] **Step 1: Create the session data dir during the lifespan startup**

In `api/main.py`, inside the `lifespan` context, before the `try: session_store = SessionStore(...)` block, add:

```python
    # Bootstrap on-disk artifact dir for per-session parquet cache.
    from pathlib import Path
    Path(settings.session_data_dir).mkdir(parents=True, exist_ok=True)
```

- [ ] **Step 2: Verify the app boots and creates the dir**

Run: `cd /Users/uvnikhil/Desktop/AnalystAI && rm -rf session_data && uv run uvicorn api.main:app --port 8765 &`
Wait 2 seconds, then: `ls session_data/`
Expected: directory exists (may be empty). Stop the dev server.

- [ ] **Step 3: Add `session_data/` to `.gitignore`**

Append a line to `.gitignore`:

```
session_data/
```

- [ ] **Step 4: Commit**

```bash
git add api/main.py .gitignore
git commit -m "chore(api): bootstrap SESSION_DATA_DIR on startup, ignore session_data/"
```

---

### Task 1.7: Sessions router — six endpoints

**Files:**
- Create: `api/routes/sessions.py`
- Modify: `api/main.py`

- [ ] **Step 1: Create the sessions router**

Create `api/routes/sessions.py`:

```python
"""Session history endpoints — list, fetch, delete cached analyses.

All endpoints enforce user_id ownership against the authenticated user.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse

from api.auth import UserProfile, get_current_user
from api.rate_limit import limiter

router = APIRouter()


def _store(request: Request):
    return request.app.state.session_store


@router.get("")
@limiter.limit("60/minute")
async def list_sessions(
    request: Request,
    limit: int = 50,
    current_user: UserProfile = Depends(get_current_user),
):
    store = _store(request)
    summaries = await store.list_sessions(current_user.uid, limit=limit)
    return [s.model_dump(mode="json") for s in summaries]


@router.get("/{session_id}")
@limiter.limit("60/minute")
async def get_session_summary(
    request: Request,
    session_id: str,
    current_user: UserProfile = Depends(get_current_user),
):
    store = _store(request)
    summary = await store.get_summary(session_id, current_user.uid)
    if not summary:
        # 404 covers both "missing" and "exists but not yours" — don't leak existence.
        raise HTTPException(status_code=404, detail="Session not found")
    return summary.model_dump(mode="json")


@router.get("/{session_id}/results")
@limiter.limit("60/minute")
async def get_session_results(
    request: Request,
    session_id: str,
    current_user: UserProfile = Depends(get_current_user),
):
    store = _store(request)
    session = await store.load(session_id)
    if not session or session.user_id != current_user.uid:
        raise HTTPException(status_code=404, detail="Session not found")

    first_user = next(
        (m.content for m in session.conversation_history if m.role.value == "user"),
        None,
    )
    return {
        "session_id": session.session_id,
        "dataset_name": session.dataset_name,
        "status": session.status,
        "original_query": first_user,
        "row_count": session.data_contract.row_count if session.data_contract else None,
        "column_count": session.data_contract.column_count if session.data_contract else None,
        "insight_report": session.insight_report.model_dump(mode="json") if session.insight_report else None,
        "targeted_response": session.targeted_response.model_dump(mode="json") if session.targeted_response else None,
        "expert_review": session.expert_review.model_dump(mode="json") if session.expert_review else None,
        "chart_configs": [c.model_dump(mode="json") for c in session.chart_configs],
        "conversation_history": [m.model_dump(mode="json") for m in session.conversation_history],
    }


@router.get("/{session_id}/conversation")
@limiter.limit("60/minute")
async def get_session_conversation(
    request: Request,
    session_id: str,
    current_user: UserProfile = Depends(get_current_user),
):
    store = _store(request)
    session = await store.load(session_id)
    if not session or session.user_id != current_user.uid:
        raise HTTPException(status_code=404, detail="Session not found")
    return [m.model_dump(mode="json") for m in session.conversation_history]


@router.get("/{session_id}/data")
@limiter.limit("30/minute")
async def get_session_data(
    request: Request,
    session_id: str,
    current_user: UserProfile = Depends(get_current_user),
):
    store = _store(request)
    session = await store.load(session_id)
    if not session or session.user_id != current_user.uid:
        raise HTTPException(status_code=404, detail="Session not found")
    if not session.cleaned_data_path or not Path(session.cleaned_data_path).is_file():
        raise HTTPException(status_code=404, detail="No cached data for this session")
    return FileResponse(
        session.cleaned_data_path,
        media_type="application/octet-stream",
        filename=f"{session.dataset_name or session_id}.parquet",
    )


@router.delete("/{session_id}")
@limiter.limit("30/minute")
async def delete_session(
    request: Request,
    session_id: str,
    current_user: UserProfile = Depends(get_current_user),
):
    store = _store(request)
    # Ownership check first to avoid letting a delete reveal that some other user's session exists.
    summary = await store.get_summary(session_id, current_user.uid)
    if not summary:
        raise HTTPException(status_code=404, detail="Session not found")
    await store.delete(session_id)
    return {"deleted": True}
```

- [ ] **Step 2: Mount the router**

In `api/main.py`, extend the import line and add the include:

```python
from api.routes import chat, dashboard, connectors, sessions  # noqa: E402

app.include_router(chat.router, prefix="/chat", tags=["chat"])
app.include_router(dashboard.router, prefix="/dashboard", tags=["dashboard"])
app.include_router(connectors.router, prefix="/connectors", tags=["connectors"])
app.include_router(sessions.router, prefix="/api/sessions", tags=["sessions"])
```

- [ ] **Step 3: Verify the endpoints respond**

Start the dev server: `cd /Users/uvnikhil/Desktop/AnalystAI && uv run uvicorn api.main:app --port 8765 &`. Wait 2 seconds.

`curl -s http://localhost:8765/api/sessions` — expected: 401/403 (auth required). The shape doesn't matter; what matters is the route is mounted (no 404).

`curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8765/api/sessions/nonexistent` — expected: 401 or 403, not 404 (because auth runs before the handler).

Stop the dev server.

- [ ] **Step 4: Commit**

```bash
git add api/routes/sessions.py api/main.py
git commit -m "feat(api): sessions router with list/get/results/conversation/data/delete endpoints"
```

---

### Phase 1 verification gate

Before starting Phase 2, confirm:

- `uv run pytest tests/ -v` — all tests pass (no test files outside `tests/` are expected).
- `uv run uvicorn api.main:app --port 8765` boots clean. `session_data/` directory exists at the repo root after boot.
- An existing `/chat` flow (upload CSV + ask question) still runs end-to-end with no regression. The created session can be fetched via `GET /api/sessions/{id}` (returns metadata) and `GET /api/sessions/{id}/results` (returns the full snapshot).
- `GET /api/sessions` lists the user's sessions sorted newest-first.
- `DELETE /api/sessions/{id}` returns `{"deleted": true}` and the row vanishes from the list.
- `curl` from a different `uid` token returns 404 for a session it doesn't own (not 403 — we don't leak existence).

---

## Phase 2 — Graph + chat-route + upload wiring

Backend-only changes that make the persisted data actually useful. After this phase, re-uploading the same CSV produces an `existing_session` hint, follow-ups on a past session use the cached parquet, and the status column transitions correctly.

### Task 2.1: Status transitions in graph nodes

**Files:**
- Modify: `orchestrator/graph.py`

- [ ] **Step 1: Set `in_progress` at the start of every run**

In `ingestion_node` (around line 99–104), after `session.error_message = None` and `session.quality_check_attempts = 0`, add:

```python
    session.status = "in_progress"
```

- [ ] **Step 2: Set `completed` on insight success**

In `insight_node` (around line 322), immediately after `session.insight_report = report`, add:

```python
        session.status = "completed"
```

(Inside the same `try` block — so a failed insight_node does NOT mark the session completed.)

- [ ] **Step 3: Set `failed` in `error_node`**

In `error_node` (around line 499), as the first statement after retrieving `session`:

```python
    session.status = "failed"
```

- [ ] **Step 4: Smoke-verify the transitions**

Run an end-to-end CSV analysis. After completion, query: `curl http://localhost:8765/api/sessions/<id>` — expect `"status": "completed"`. Trigger a failure (e.g. upload a corrupt CSV) — expect `"status": "failed"`.

- [ ] **Step 5: Commit**

```bash
git add orchestrator/graph.py
git commit -m "feat(graph): set session.status across ingestion/insight/error nodes"
```

---

### Task 2.2: `cleaning_node` writes cached parquet

**Files:**
- Modify: `orchestrator/graph.py`

- [ ] **Step 1: Add a module-level logger**

Near the top of `orchestrator/graph.py`, just under the existing imports:

```python
import logging

logger = logging.getLogger(__name__)
```

- [ ] **Step 2: Write parquet at the end of `cleaning_node`**

At the top of `orchestrator/graph.py`, add the import: `from memory.session_data import write_cleaned_parquet`.

In `cleaning_node` (around line 187 — just before `df_parquet: bytes | None = session.__dict__.pop(...)`), insert:

```python
    # Best-effort cache of cleaned data for fast follow-ups. Never blocks the pipeline.
    cached = session.__dict__.get("_df_parquet") or state.get("df_parquet")
    if cached and not session.error_message:
        try:
            path = write_cleaned_parquet(session.session_id, cached)
            session.cleaned_data_path = path
        except Exception as exc:
            logger.warning("cached parquet write failed for %s: %s", session.session_id, exc)
```

- [ ] **Step 3: Verify the parquet appears on disk**

Run an end-to-end CSV analysis. Inspect: `ls session_data/<session_id>/` — expect `cleaned_data.parquet`. Confirm size > 0.

- [ ] **Step 4: Verify the path is in the persisted session**

`curl http://localhost:8765/api/sessions/<id>` — `cleaned_data_path` is not in the summary (by design), so verify via the JSON of `GET .../results`: the path is reflected by the existence of `/data` endpoint returning the parquet (`curl -o /tmp/out.parquet http://.../api/sessions/<id>/data` succeeds).

- [ ] **Step 5: Commit**

```bash
git add orchestrator/graph.py
git commit -m "feat(graph): cleaning_node persists cleaned parquet to SESSION_DATA_DIR"
```

---

### Task 2.3: `ingestion_node` reads cached parquet on resume

**Files:**
- Modify: `orchestrator/graph.py`

- [ ] **Step 1: Branch on cached parquet before calling the ingestion agent**

At the top of `orchestrator/graph.py`, extend the helper import: `from memory.session_data import write_cleaned_parquet, read_cleaned_parquet`.

Replace the body of `ingestion_node` from `if not is_resuming:` (the existing emit) through the `try / except` block that calls `run_ingestion_agent`. The new control flow uses the cache when resuming:

```python
    if not is_resuming:
        _emit(state, {"type": "agent_start", "agent": "ingestion", "message": "Loading your data..."})

    cached_used = False
    if is_resuming and session.cleaned_data_path:
        # Try the on-disk cache before re-loading from the connector. Cuts a full
        # connector round-trip on every follow-up question and detaches the
        # follow-up from connector drift (a re-queried SQL table won't surprise us).
        try:
            payload = read_cleaned_parquet(session.session_id)
            if payload is not None:
                session.__dict__["_df_parquet"] = payload
                cached_used = True
                logger.info("loaded cached parquet for session %s", session.session_id)
        except Exception as exc:
            logger.warning("cached parquet read failed for %s: %s", session.session_id, exc)

    if not cached_used:
        try:
            contract = await run_ingestion_agent(session)

            if not is_resuming:
                # First-run: store the contract and emit progress
                session.data_contract = contract
                _emit(state, {
                    "type": "agent_complete",
                    "agent": "ingestion",
                    "message": f"Loaded {contract.row_count:,} rows × {contract.column_count} columns",
                })

                # FEAT-01: Auto-suggest questions after data connect
                try:
                    questions = await generate_proactive_questions(contract)
                    if questions:
                        _emit(state, {
                            "type": "suggested_questions",
                            "questions": questions,
                        })
                except Exception:
                    pass

                # PROMPT-2 follow-up: build display-name + entity narrative once per dataset.
                if session.narrative is None:
                    try:
                        session.narrative = await generate_data_narrative(contract)
                    except Exception:
                        pass
            # On resume without cache: data_contract already set from prior session; df_parquet just restored.
        except Exception as exc:
            session.error_message = f"Data ingestion failed: {exc}"
```

(Keep the existing `df_parquet: bytes | None = session.__dict__.pop("_df_parquet", None)` block + `BUG-13` validation at the end of the function — those still apply.)

- [ ] **Step 2: Verify the cached path is used**

Tail the backend log (e.g. `tail -f` the stderr from uvicorn or capture stdout). Run an initial analysis on a CSV, then a follow-up question on the same session. Confirm log line: `loaded cached parquet for session <id>`. The follow-up should also be noticeably faster (no re-read of the upload).

- [ ] **Step 3: Verify the fallback still works**

`rm -rf session_data/<id>/` and ask another follow-up. The follow-up must succeed (it falls through to `run_ingestion_agent`). No log line about cache, and no error.

- [ ] **Step 4: Commit**

```bash
git add orchestrator/graph.py
git commit -m "feat(graph): ingestion_node prefers cached parquet on resume, falls back to connector"
```

---

### Task 2.4: `ChatRequest` accepts `file_hash` + `dataset_name`

**Files:**
- Modify: `api/routes/chat.py`

- [ ] **Step 1: Extend `ChatRequest`**

In `api/routes/chat.py`, extend the model:

```python
class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    session_id: str | None = Field(default=None, max_length=64)
    user_id: str = Field(default="default", max_length=128)
    is_approval: bool = False
    connector_config: dict | None = None
    file_hash: str | None = Field(default=None, max_length=128)
    dataset_name: str | None = Field(default=None, max_length=256)
```

- [ ] **Step 2: Stamp them on new sessions only**

In `chat()` (around the existing `if session is None:` block), inside the branch that creates the new session, immediately after `session = AnalysisSession(session_id=session_id, user_id=current_user.uid)`, add:

```python
        if body.file_hash:
            session.file_hash = body.file_hash
        if body.dataset_name:
            session.dataset_name = body.dataset_name
```

On the resume branch (existing session loaded), the body's `file_hash` / `dataset_name` are ignored — the persisted values win.

- [ ] **Step 3: Verify**

POST `/chat` with `file_hash="testhash"` and `dataset_name="foo"`, capture the `session_id` from the SSE stream, then `curl /api/sessions/<id>` — expect `"dataset_name":"foo"`. `find_by_hash` should now hit for `"testhash"`: re-POST with a different upload but same hash and confirm dedup logic in Task 2.5.

- [ ] **Step 4: Commit**

```bash
git add api/routes/chat.py
git commit -m "feat(chat): accept file_hash and dataset_name on new-session creation"
```

---

### Task 2.5: `/connectors/upload` computes hash + dedup lookup + new response fields

**Files:**
- Modify: `api/routes/connectors.py`

- [ ] **Step 1: Compute SHA256 during the chunked write**

Replace the existing chunked-read block in `upload_csv` with a streaming-hash version. Keep the same byte-counting / size-cap behavior:

```python
    import hashlib

    max_size = 50 * 1024 * 1024  # 50 MB
    chunk_size = 1024 * 1024     # 1 MB per read
    chunks: list[bytes] = []
    total = 0
    hasher = hashlib.sha256()
    while True:
        chunk = await file.read(chunk_size)
        if not chunk:
            break
        total += len(chunk)
        if total > max_size:
            raise HTTPException(status_code=413, detail="File too large (max 50 MB)")
        hasher.update(chunk)
        chunks.append(chunk)
    content = b"".join(chunks)
    file_hash = hasher.hexdigest()
```

- [ ] **Step 2: Look up existing session by hash and extend the response**

At the very top of the function add the dependency: `from fastapi import Request`. Change the signature so we can reach `app.state.session_store`:

```python
@router.post("/upload")
async def upload_csv(
    request: Request,
    file: UploadFile = File(...),
    current_user: UserProfile = Depends(get_current_user),
) -> dict:
```

At the end of the function, replace the existing `return { ... }` block:

```python
    config = ConnectorConfig(type=ConnectorType.CSV, file_path=save_path)
    dataset_name = Path(safe_name).stem or "dataset"

    existing_session = None
    store = request.app.state.session_store
    if store:
        try:
            hit = await store.find_by_hash(current_user.uid, file_hash)
            if hit:
                existing_session = {
                    "session_id": hit.session_id,
                    "dataset_name": hit.dataset_name,
                    "updated_at": hit.updated_at.isoformat(),
                    "original_query": hit.original_query,
                }
        except Exception:
            # Dedup is best-effort — never block an upload on lookup failure.
            existing_session = None

    return {
        "status": "uploaded",
        "filename": file.filename,
        "file_path": save_path,
        "connector_config": config.model_dump(),
        "file_hash": file_hash,
        "dataset_name": dataset_name,
        "existing_session": existing_session,
    }
```

- [ ] **Step 3: Verify the upload response shape**

`curl -s -X POST http://localhost:8765/connectors/upload -H "Authorization: Bearer <token>" -F "file=@/path/to/sample.csv" | python -m json.tool`
Expected fields present: `file_hash` (hex string), `dataset_name`, `existing_session` (null on first upload).

Re-upload the same file after running an analysis on it:
Expected: `existing_session` is non-null and contains the prior session's `session_id`.

- [ ] **Step 4: Commit**

```bash
git add api/routes/connectors.py
git commit -m "feat(connectors): upload returns file_hash, dataset_name, and dedup hit"
```

---

### Phase 2 verification gate

Before starting Phase 3, confirm:

- Fresh CSV upload + analysis end-to-end → `session_data/<id>/cleaned_data.parquet` exists; `GET /api/sessions/<id>` shows `"status":"completed"`.
- Re-upload the same file → `/connectors/upload` returns `existing_session` referencing the prior id. (The frontend doesn't act on this yet.)
- Follow-up question on the prior session via `/chat` (passing only `session_id`) → backend log line `loaded cached parquet for session <id>` appears; follow-up renders correctly.
- `DELETE /api/sessions/<id>` removes both the DB row and the `session_data/<id>/` directory.
- A SQL/Sheets/API session (no file_hash) appears in `GET /api/sessions` with `file_hash` null. Subsequent SQL connections never trigger a dedup hit (`find_by_hash` skips null hashes).

---

## Phase 3 — Frontend sidebar and load flow

User-facing. Adds the sidebar, the dedup modal, the cached-load hydration, and the "New analysis" button.

> **Frontend reminder.** Per [frontend/AGENTS.md](../../../frontend/AGENTS.md), this is not the Next.js you know — APIs may differ from your training. Stay within existing patterns: `"use client"` on interactive components, hooks in `frontend/src/hooks/`, components in `frontend/src/components/`, type mirrors in `frontend/src/types/index.ts`. Do not introduce new Next.js features speculatively. Before editing a Next.js-specific API, check `node_modules/next/dist/docs/` if uncertain.

### Task 3.1: Types + `lib/api.ts` wrappers

**Files:**
- Modify: `frontend/src/types/index.ts`
- Modify: `frontend/src/lib/api.ts`

- [ ] **Step 1: Add the TypeScript types**

Append to `frontend/src/types/index.ts`:

```ts
export type SessionStatus = "in_progress" | "completed" | "failed";

export interface SessionSummary {
  session_id: string;
  dataset_name: string | null;
  original_query: string | null;
  row_count: number | null;
  column_count: number | null;
  status: SessionStatus;
  created_at: string;
  updated_at: string;
}

export interface SessionResultsSnapshot {
  session_id: string;
  dataset_name: string | null;
  status: SessionStatus;
  original_query: string | null;
  row_count: number | null;
  column_count: number | null;
  insight_report: InsightReport | null;
  targeted_response: TargetedResponse | null;
  expert_review: ExpertReview | null;
  chart_configs: ChartConfig[];
  conversation_history: { role: "user" | "assistant" | "system"; content: string }[];
}

export interface ExistingSessionHint {
  session_id: string;
  dataset_name: string | null;
  updated_at: string;
  original_query: string | null;
}

export interface UploadResponse {
  status: string;
  filename: string;
  file_path: string;
  connector_config: Record<string, unknown>;
  file_hash: string;
  dataset_name: string;
  existing_session: ExistingSessionHint | null;
}
```

- [ ] **Step 2: Export the existing auth helper + add the fetch wrappers**

In `frontend/src/lib/api.ts`, change the existing `function getAuthHeaders(...)` declaration to `export function getAuthHeaders(...)` so other components can reuse it (Task 3.6 imports it).

Then append (above the final `export { API_URL };`):

```ts
import type { SessionSummary, SessionResultsSnapshot } from "@/types";

export async function listSessions(): Promise<SessionSummary[]> {
  const res = await fetch(`${API_URL}/api/sessions`, { headers: getAuthHeaders() });
  if (!res.ok) return [];
  return (await res.json()) as SessionSummary[];
}

export async function getSessionResults(sessionId: string): Promise<SessionResultsSnapshot | null> {
  const res = await fetch(`${API_URL}/api/sessions/${sessionId}/results`, {
    headers: getAuthHeaders(),
  });
  if (!res.ok) return null;
  return (await res.json()) as SessionResultsSnapshot;
}

export async function deleteSession(sessionId: string): Promise<boolean> {
  const res = await fetch(`${API_URL}/api/sessions/${sessionId}`, {
    method: "DELETE",
    headers: getAuthHeaders(),
  });
  return res.ok;
}
```

- [ ] **Step 3: Verify the frontend compiles**

Run: `cd /Users/uvnikhil/Desktop/AnalystAI/frontend && npm run build 2>&1 | tail -20`
Expected: build succeeds (no TypeScript errors).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/types/index.ts frontend/src/lib/api.ts
git commit -m "feat(frontend): types + fetch wrappers for sessions endpoints"
```

---

### Task 3.2: `useSessions` hook

**Files:**
- Create: `frontend/src/hooks/useSessions.ts`

- [ ] **Step 1: Implement the hook**

Create `frontend/src/hooks/useSessions.ts`:

```ts
"use client";

import { useCallback, useEffect, useState } from "react";
import { listSessions, deleteSession as apiDeleteSession } from "@/lib/api";
import type { SessionSummary } from "@/types";

export function useSessions() {
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [loading, setLoading] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      setSessions(await listSessions());
    } finally {
      setLoading(false);
    }
  }, []);

  const deleteSession = useCallback(
    async (id: string) => {
      const ok = await apiDeleteSession(id);
      if (ok) {
        setSessions((prev) => prev.filter((s) => s.session_id !== id));
      }
      return ok;
    },
    [],
  );

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return { sessions, loading, refresh, deleteSession };
}
```

- [ ] **Step 2: Compile-check**

Run: `cd /Users/uvnikhil/Desktop/AnalystAI/frontend && npm run build 2>&1 | tail -20`
Expected: build succeeds.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/hooks/useSessions.ts
git commit -m "feat(frontend): useSessions hook (list + delete + refresh)"
```

---

### Task 3.3: `SessionSidebar` component

**Files:**
- Create: `frontend/src/components/SessionSidebar.tsx`

- [ ] **Step 1: Implement the component**

Create `frontend/src/components/SessionSidebar.tsx`:

```tsx
"use client";

import { Trash2, PlusCircle } from "lucide-react";
import type { SessionSummary } from "@/types";

interface Props {
  sessions: SessionSummary[];
  activeSessionId: string | null;
  loading: boolean;
  onLoadSession: (id: string) => void;
  onDeleteSession: (id: string) => void;
  onNewAnalysis: () => void;
}

function relativeTime(iso: string): string {
  const then = new Date(iso).getTime();
  const diff = Date.now() - then;
  const sec = Math.floor(diff / 1000);
  if (sec < 60) return "just now";
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const day = Math.floor(hr / 24);
  return `${day}d ago`;
}

function statusPillClass(status: SessionSummary["status"]): string {
  switch (status) {
    case "completed":
      return "bg-green-100 text-green-800";
    case "failed":
      return "bg-red-100 text-red-800";
    default:
      return "bg-amber-100 text-amber-800";
  }
}

export function SessionSidebar({
  sessions,
  activeSessionId,
  loading,
  onLoadSession,
  onDeleteSession,
  onNewAnalysis,
}: Props) {
  return (
    <div className="flex flex-col gap-2 p-3 border-t border-gray-200">
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-500">History</h3>
        <button
          type="button"
          onClick={onNewAnalysis}
          className="flex items-center gap-1 text-xs text-blue-600 hover:text-blue-800"
        >
          <PlusCircle size={14} />
          New
        </button>
      </div>

      {loading && sessions.length === 0 && (
        <p className="text-xs text-gray-400">Loading…</p>
      )}
      {!loading && sessions.length === 0 && (
        <p className="text-xs text-gray-400">No past analyses yet.</p>
      )}

      <ul className="flex flex-col gap-1">
        {sessions.map((s) => {
          const isActive = s.session_id === activeSessionId;
          return (
            <li
              key={s.session_id}
              className={`group rounded-md px-2 py-1.5 cursor-pointer ${
                isActive ? "bg-blue-50 border border-blue-200" : "hover:bg-gray-50"
              }`}
              onClick={() => onLoadSession(s.session_id)}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="text-sm font-medium text-gray-800 truncate">
                  {s.dataset_name ?? "Untitled dataset"}
                </span>
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    onDeleteSession(s.session_id);
                  }}
                  className="text-gray-400 hover:text-red-600 opacity-0 group-hover:opacity-100 transition-opacity"
                  aria-label="Delete session"
                >
                  <Trash2 size={14} />
                </button>
              </div>
              <div className="flex items-center justify-between gap-2 text-xs text-gray-500">
                <span>{relativeTime(s.updated_at)}</span>
                <span className={`px-1.5 py-0.5 rounded ${statusPillClass(s.status)}`}>
                  {s.status}
                </span>
              </div>
              {s.row_count !== null && (
                <p className="text-xs text-gray-400">{s.row_count.toLocaleString()} rows</p>
              )}
              {s.original_query && (
                <p className="text-xs text-gray-500 truncate" title={s.original_query}>
                  {s.original_query}
                </p>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
```

- [ ] **Step 2: Compile-check**

Run: `cd /Users/uvnikhil/Desktop/AnalystAI/frontend && npm run build 2>&1 | tail -20`
Expected: build succeeds. If `lucide-react` is missing, install it: `npm install lucide-react` and rerun. (Confirm whether the project already uses it by `grep -r "lucide-react" frontend/src` — if any existing component imports `lucide-react`, it's installed; otherwise install it.)

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/SessionSidebar.tsx
git commit -m "feat(frontend): SessionSidebar component"
```

---

### Task 3.4: `useAnalysis.hydrateFromSnapshot` for cached loads

**Files:**
- Modify: `frontend/src/hooks/useAnalysis.ts`

- [ ] **Step 1: Add the hydration method**

In `frontend/src/hooks/useAnalysis.ts`, import the new types:

```ts
import type { AgentEvent, ApprovalRequiredEvent, BriefingData, ChatMessage, ChartConfig, ExpertReview, InsightReport, SessionResultsSnapshot, TargetedResponse } from "@/types";
```

Inside the `useAnalysis()` function body, before the `return { … }`, add:

```ts
  const hydrateFromSnapshot = useCallback((snapshot: SessionResultsSnapshot) => {
    setSessionId(snapshot.session_id);
    setIsLoading(false);
    setPendingApproval(null);
    setSuggestedQuestions([]);
    setAgentEvents([]);

    // Replay conversation as immutable history; attach the final analysis artifacts
    // to the most recent assistant message so the same panels render as a live run.
    const replay: ChatMessage[] = snapshot.conversation_history.map((m) => ({
      role: m.role === "user" ? "user" : "assistant",
      content: m.content,
    }));

    if (replay.length > 0) {
      const lastAssistantIdx = (() => {
        for (let i = replay.length - 1; i >= 0; i--) {
          if (replay[i].role === "assistant") return i;
        }
        return -1;
      })();
      if (lastAssistantIdx >= 0) {
        replay[lastAssistantIdx] = {
          ...replay[lastAssistantIdx],
          charts: snapshot.chart_configs.length ? snapshot.chart_configs : undefined,
          insight: snapshot.insight_report ?? undefined,
          expertReview: snapshot.expert_review ?? undefined,
          targetedResponse: snapshot.targeted_response ?? undefined,
        };
      }
    }
    setMessages(replay);
  }, []);
```

Add `hydrateFromSnapshot` to the returned object:

```ts
  return {
    messages,
    sessionId,
    isLoading,
    agentEvents,
    pendingApproval,
    setPendingApproval,
    suggestedQuestions,
    sendMessage,
    setConnectorConfig: (cfg: Record<string, unknown>) => {
      connectorConfig.current = cfg;
    },
    hydrateFromSnapshot,
    resetWorkspace: () => {
      setMessages([]);
      setSessionId(null);
      setIsLoading(false);
      setAgentEvents([]);
      setPendingApproval(null);
      setSuggestedQuestions([]);
      connectorConfig.current = null;
    },
  };
```

- [ ] **Step 2: Compile-check**

Run: `cd /Users/uvnikhil/Desktop/AnalystAI/frontend && npm run build 2>&1 | tail -20`
Expected: build succeeds.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/hooks/useAnalysis.ts
git commit -m "feat(frontend): hydrateFromSnapshot + resetWorkspace on useAnalysis"
```

---

### Task 3.5: Wire sidebar into `ChatInterface`

**Files:**
- Modify: `frontend/src/components/ChatInterface.tsx`

- [ ] **Step 1: Inspect the existing component first**

Read `frontend/src/components/ChatInterface.tsx` end-to-end before editing — pick the right insertion point (below the existing data-source buttons) and import the existing pattern for using `useAnalysis`. Do not restructure unrelated layout.

- [ ] **Step 2: Wire the sidebar**

At the top of `ChatInterface.tsx`, add imports:

```ts
import { useSessions } from "@/hooks/useSessions";
import { SessionSidebar } from "@/components/SessionSidebar";
import { getSessionResults } from "@/lib/api";
```

Inside the component body, after the existing `const analysis = useAnalysis()` (or whatever the hook variable is named — verify in Step 1):

```ts
  const { sessions, loading: sessionsLoading, refresh: refreshSessions, deleteSession } = useSessions();

  const handleLoadSession = async (id: string) => {
    const snapshot = await getSessionResults(id);
    if (snapshot) {
      analysis.hydrateFromSnapshot(snapshot);
    }
  };

  const handleNewAnalysis = () => {
    analysis.resetWorkspace();
  };

  const handleDeleteSession = async (id: string) => {
    const ok = await deleteSession(id);
    if (ok && analysis.sessionId === id) {
      analysis.resetWorkspace();
    }
  };
```

In the sidebar JSX (below the data-source buttons), render:

```tsx
        <SessionSidebar
          sessions={sessions}
          activeSessionId={analysis.sessionId}
          loading={sessionsLoading}
          onLoadSession={handleLoadSession}
          onDeleteSession={handleDeleteSession}
          onNewAnalysis={handleNewAnalysis}
        />
```

- [ ] **Step 3: Refresh the list when a pipeline run finishes**

Find the existing handler in `ChatInterface.tsx` (or wherever) that observes `agentEvents` / the final `done` event from `useAnalysis`. Add a `useEffect` that refreshes sessions after a run completes:

```tsx
  useEffect(() => {
    if (!analysis.isLoading && analysis.sessionId) {
      void refreshSessions();
    }
  }, [analysis.isLoading, analysis.sessionId, refreshSessions]);
```

- [ ] **Step 4: Verify in the browser**

Start backend + frontend dev servers. Upload a CSV + analyze. Confirm a new row appears in the sidebar after the run completes, with status transitioning `in_progress` → `completed`. Click the row — UI hydrates to the same view with no network spinner from `/chat`. Trash icon on a row removes it. "New" button clears the workspace.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ChatInterface.tsx
git commit -m "feat(frontend): wire SessionSidebar + cached load into ChatInterface"
```

---

### Task 3.6: Dedup modal in `ConnectorSetup`

**Files:**
- Modify: `frontend/src/components/ConnectorSetup.tsx`

- [ ] **Step 1: Inspect the existing upload flow**

Read `frontend/src/components/ConnectorSetup.tsx` end-to-end. Locate the existing fetch to `${API_URL}/connectors/upload` and how it currently calls back into the parent to set `connectorConfig`. Note the parent (likely `ChatInterface`) signature — the parent will need a new callback for "load existing session".

- [ ] **Step 2: Update the upload response handling**

The response shape changed in Task 2.5. Update the existing fetch handler to read the new fields and surface the modal when `existing_session` is set. The handler stores the response in component-local state and conditionally renders the modal:

```tsx
import { API_URL, getAuthHeaders } from "@/lib/api";
import type { UploadResponse, ExistingSessionHint } from "@/types";

// ...inside the component:
const [pendingUpload, setPendingUpload] = useState<UploadResponse | null>(null);

async function handleFileSelected(file: File) {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`${API_URL}/connectors/upload`, {
    method: "POST",
    headers: getAuthHeaders(),
    body: form,
  });
  if (!res.ok) {
    // existing error UI
    return;
  }
  const upload = (await res.json()) as UploadResponse;
  if (upload.existing_session) {
    setPendingUpload(upload);
    return;  // wait for user choice in modal
  }
  // No dedup hit — proceed exactly as today, but pass file_hash + dataset_name back to the parent.
  props.onConnectorReady({
    connector_config: upload.connector_config,
    file_hash: upload.file_hash,
    dataset_name: upload.dataset_name,
  });
}

function acceptLoadPrevious(hint: ExistingSessionHint) {
  setPendingUpload(null);
  props.onLoadExistingSession(hint.session_id);
}

function acceptRunFresh(upload: UploadResponse) {
  setPendingUpload(null);
  props.onConnectorReady({
    connector_config: upload.connector_config,
    file_hash: upload.file_hash,
    dataset_name: upload.dataset_name,
  });
}

function cancelDedup() {
  setPendingUpload(null);
  // Intentionally do NOT delete the uploaded file from `uploads/` — orphan-file GC is out of scope.
}
```

Render the modal at the bottom of the component when `pendingUpload?.existing_session` is set. Reuse the visual style of `ApprovalModal.tsx`:

```tsx
{pendingUpload?.existing_session && (
  <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
    <div className="bg-white rounded-lg p-6 max-w-md shadow-xl">
      <h3 className="text-lg font-semibold mb-2">
        Previously analyzed dataset
      </h3>
      <p className="text-sm text-gray-600 mb-4">
        <span className="font-medium">{pendingUpload.filename}</span> was analyzed on{" "}
        {new Date(pendingUpload.existing_session.updated_at).toLocaleDateString()}.
        Load the previous analysis or run fresh?
      </p>
      <div className="flex gap-2 justify-end">
        <button
          type="button"
          onClick={cancelDedup}
          className="px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-100 rounded"
        >
          Cancel
        </button>
        <button
          type="button"
          onClick={() => acceptRunFresh(pendingUpload)}
          className="px-3 py-1.5 text-sm border border-gray-300 rounded hover:bg-gray-50"
        >
          Run fresh
        </button>
        <button
          type="button"
          onClick={() => acceptLoadPrevious(pendingUpload.existing_session!)}
          className="px-3 py-1.5 text-sm bg-blue-600 text-white rounded hover:bg-blue-700"
        >
          Load previous
        </button>
      </div>
    </div>
  </div>
)}
```

- [ ] **Step 3: Extend `useAnalysis` so the hash + dataset name reach the `/chat` body as top-level fields**

The backend `ChatRequest` (Task 2.4) reads `file_hash` and `dataset_name` as top-level request fields, NOT inside `connector_config`. Wire them through the hook:

In `frontend/src/hooks/useAnalysis.ts`, near the existing `const connectorConfig = useRef<...>(null);` line, add two more refs:

```ts
  const fileHashRef = useRef<string | null>(null);
  const datasetNameRef = useRef<string | null>(null);
```

Extend the fetch body inside `sendMessage` so `JSON.stringify` includes the new fields:

```ts
          body: JSON.stringify({
            message: content,
            session_id: sessionId,
            is_approval: isApproval,
            connector_config: connectorConfig.current,
            file_hash: fileHashRef.current,
            dataset_name: datasetNameRef.current,
          }),
```

Add a new setter to the hook's returned object alongside `setConnectorConfig`:

```ts
    setUploadMeta: (hash: string | null, name: string | null) => {
      fileHashRef.current = hash;
      datasetNameRef.current = name;
    },
```

Also make `resetWorkspace` (added in Task 3.4) clear these refs:

```ts
    resetWorkspace: () => {
      setMessages([]);
      setSessionId(null);
      setIsLoading(false);
      setAgentEvents([]);
      setPendingApproval(null);
      setSuggestedQuestions([]);
      connectorConfig.current = null;
      fileHashRef.current = null;
      datasetNameRef.current = null;
    },
```

- [ ] **Step 4: Wire the parent (`ChatInterface`) callbacks**

In `ChatInterface.tsx`, define and pass the handlers:

```tsx
const handleConnectorReady = (payload: {
  connector_config: Record<string, unknown>;
  file_hash: string;
  dataset_name: string;
}) => {
  analysis.setConnectorConfig(payload.connector_config);
  analysis.setUploadMeta(payload.file_hash, payload.dataset_name);
};

const handleLoadExistingSession = (id: string) => handleLoadSession(id);
```

Pass both into the component:

```tsx
<ConnectorSetup
  onConnectorReady={handleConnectorReady}
  onLoadExistingSession={handleLoadExistingSession}
  /* ...existing props... */
/>
```

- [ ] **Step 5: End-to-end verification in the browser**

1. Fresh user: upload `titanic.csv`, run an analysis to completion. Confirm sidebar entry appears with `completed` status.
2. Re-upload `titanic.csv`: modal appears with the dataset name and the prior date.
3. Click **Load previous** → UI hydrates instantly, no `/chat` call (verify in Network tab).
4. Re-upload again, click **Run fresh** → new SSE run starts, two sidebar entries exist for the same hash.
5. Re-upload, click **Cancel** → modal closes, workspace unchanged, file remains on disk in `uploads/`.
6. Sign in as a second user, hit the upload endpoint — `find_by_hash` returns null even for the same content (multi-tenant isolation).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/ConnectorSetup.tsx frontend/src/components/ChatInterface.tsx frontend/src/hooks/useAnalysis.ts
git commit -m "feat(frontend): dedup modal + carry file_hash/dataset_name through /chat"
```

---

### Phase 3 verification gate

End-to-end smoke matrix to run before declaring the feature complete:

| Action | Expected |
|---|---|
| Fresh user, empty DB | Sidebar empty, "New" button visible. |
| Upload CSV + analyze | New sidebar row appears; status transitions in_progress → completed; charts/insight render. |
| Click a completed sidebar row | UI hydrates from `/api/sessions/{id}/results`; no SSE opened (verify Network tab). |
| Ask a follow-up on a loaded session | `/chat` POST happens; backend log shows `loaded cached parquet`; follow-up renders. |
| Re-upload same CSV | Dedup modal appears with prior session's date. |
| Modal → Load previous | Instant hydration, no SSE. |
| Modal → Run fresh | New SSE run, new sidebar entry, both entries share `file_hash`. |
| Modal → Cancel | Modal closes; workspace unchanged. |
| Trash icon on a sidebar row | Row disappears; if it was active, workspace clears. |
| Second user logs in | Sees only their own sessions; same-hash upload does NOT trigger first user's dedup. |
| SQL/Sheets/API connector + analyze | Session appears in sidebar with `dataset_name: null`; future SQL connections never dedup. |

---

## Spec coverage check

- Spec §3 (Schema Changes) → Tasks 1.1, 1.3, 2.4.
- Spec §4 (Persistence Layer) → Tasks 1.2, 1.3, 1.4, 1.5.
- Spec §5 (Graph & Chat-Route Wiring) → Tasks 2.1, 2.2, 2.3, 2.4, 2.5.
- Spec §6 (API Endpoints) → Tasks 1.6, 1.7.
- Spec §7 (Frontend) → Tasks 3.1–3.6.
- Spec §8 (Phasing & Verification) → Phase verification gates after Tasks 1.7, 2.5, 3.6.
- Spec §1 (Non-goals) — pagination, alembic, orphan-file GC, parquet schema-evolution — explicitly preserved as non-goals; no tasks created for them.
