"""
Runs and their events (Phase 5.6a). A run is a record, not a request: POST /chat stores one, a
worker claims it from the queue (Postgres `FOR UPDATE SKIP LOCKED`), and every progress event is
written to `run_events` so any client can replay it.

Status: queued → running → waiting_approval → completed | failed | cancelled.
`attempt` counts claims and only ever goes up: it fences a worker that lost its lease (its
heartbeats and event writes name the attempt it claimed, and update nothing once another worker
has the run). `crashes` counts expired leases since the user last acted (answered a gate or
retried); the reaper gives up after MAX_CRASHES.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from memory.session_store import Base

ACTIVE = ("queued", "running", "waiting_approval")
TERMINAL = ("completed", "failed", "cancelled")
LEASE_S = 120          # a worker that misses heartbeats this long has died
HEARTBEAT_S = 5        # also how fast a cancel is noticed
MAX_CRASHES = 3        # expired leases (since the user last acted) before the reaper gives up
CANCELLED = "Cancelled at your request."


class RunConflict(Exception):
    """The session already has an active run (one at a time: they share the session)."""


class RunRow(Base):
    __tablename__ = "runs"
    __table_args__ = (UniqueConstraint("user_id", "client_request_id"),)

    id = Column(String, primary_key=True)
    session_id = Column(String, nullable=False, index=True)
    user_id = Column(String, nullable=False)
    client_request_id = Column(String, nullable=True)
    status = Column(String, nullable=False, index=True)
    input = Column(Text, nullable=False)         # {"message", "is_approval", "approval_context"}
    resume = Column(Text, nullable=True)         # {"answer": …} | {"retry": true}, until it ends
    error = Column(Text, nullable=True)
    attempt = Column(Integer, nullable=False, default=0)
    crashes = Column(Integer, nullable=False, default=0)
    lease_until = Column(DateTime(timezone=True), nullable=True)
    cancel_requested = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)


class RunEventRow(Base):
    __tablename__ = "run_events"

    run_id = Column(String, primary_key=True)
    seq = Column(Integer, primary_key=True)
    type = Column(String, nullable=False)
    payload = Column(Text, nullable=False)       # the SSE event as JSON
    created_at = Column(DateTime(timezone=True), nullable=False)


@dataclass
class Run:
    id: str
    session_id: str
    user_id: str
    status: str
    input: dict
    client_request_id: str | None = None
    resume: dict | None = None
    error: str | None = None
    attempt: int = 0
    crashes: int = 0
    cancel_requested: bool = False
    lease_until: float | None = None             # epoch seconds (in-memory store only)
    events: list[dict] = field(default_factory=list, repr=False)

    def public(self) -> dict:
        return {"run_id": self.id, "session_id": self.session_id, "status": self.status,
                "error": self.error}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _reap_message(n: int) -> str:
    return (f"The worker running this analysis stopped responding {n} times, so it was stopped. "
            "Use \"Retry from where it failed\" to continue from the last finished step.")


class InMemoryRunStore:
    """Development / tests: same contract as RunStore, lost on restart (the UI says so)."""

    persistent = False

    def __init__(self) -> None:
        self._runs: dict[str, Run] = {}

    async def init(self) -> None:
        pass

    async def create(self, *, session_id: str, user_id: str, input: dict,
                     client_request_id: str | None = None,
                     first_events: list[dict] = ()) -> tuple[Run, bool]:
        if client_request_id:
            for r in self._runs.values():
                if r.user_id == user_id and r.client_request_id == client_request_id:
                    return replace(r), False
        if any(r.session_id == session_id and r.status in ACTIVE for r in self._runs.values()):
            raise RunConflict(session_id)
        run = Run(id=str(uuid.uuid4()), session_id=session_id, user_id=user_id, status="queued",
                  input=input, client_request_id=client_request_id)
        run.events = list(first_events)
        self._runs[run.id] = run
        return replace(run), True

    async def by_request(self, user_id: str, client_request_id: str) -> Run | None:
        return next((replace(r) for r in self._runs.values() if r.user_id == user_id
                     and r.client_request_id == client_request_id), None)

    async def get(self, run_id: str) -> Run | None:
        r = self._runs.get(run_id)
        return replace(r) if r else None

    async def latest_for_session(self, session_id: str, user_id: str) -> Run | None:
        mine = [r for r in self._runs.values() if r.session_id == session_id and r.user_id == user_id]
        return replace(mine[-1]) if mine else None

    async def active_for_session(self, session_id: str, user_id: str) -> Run | None:
        live = [r for r in self._runs.values() if r.session_id == session_id
                and r.user_id == user_id and r.status in ACTIVE]
        return replace(live[-1]) if live else None

    async def claim(self, lease_s: float = LEASE_S) -> Run | None:
        for r in self._runs.values():   # dicts keep insertion order: oldest first
            if r.status == "queued":
                r.status, r.attempt, r.lease_until = "running", r.attempt + 1, time.time() + lease_s
                return replace(r)
        return None

    async def heartbeat(self, run_id: str, attempt: int,
                        lease_s: float = LEASE_S) -> tuple[bool, bool]:
        r = self._runs[run_id]
        if r.status != "running" or r.attempt != attempt:
            return False, False
        r.lease_until = time.time() + lease_s
        return True, r.cancel_requested

    async def append(self, run_id: str, events: list[dict], attempt: int | None = None) -> bool:
        r = self._runs[run_id]
        if attempt is not None and (r.status != "running" or r.attempt != attempt):
            return False
        r.events.extend(events)
        return True

    async def events_after(self, run_id: str, after: int) -> list[tuple[int, dict]]:
        r = self._runs.get(run_id)
        return [(i + 1, e) for i, e in enumerate(r.events) if i + 1 > after] if r else []

    async def finish(self, run_id: str, status: str, *, attempt: int | None = None,
                     error: str | None = None, events: list[dict] = ()) -> bool:
        r = self._runs[run_id]
        if attempt is not None and (r.status != "running" or r.attempt != attempt):
            return False
        r.status, r.error, r.lease_until, r.resume = status, error, None, None
        r.events.extend(events)
        return True

    async def resume(self, run_id: str, payload: dict, events: list[dict] = (),
                     from_status: tuple[str, ...] = ("waiting_approval",)) -> bool:
        r = self._runs[run_id]
        if r.status not in from_status or r.status not in ACTIVE and any(
                o.session_id == r.session_id and o.status in ACTIVE for o in self._runs.values()):
            return False
        r.status, r.resume, r.error, r.cancel_requested = "queued", payload, None, False
        r.crashes = 0   # the user acted: a fresh crash budget (attempt keeps counting: the fence)
        r.events.extend(events)
        return True

    async def request_cancel(self, run_id: str, events: list[dict]) -> str:
        r = self._runs[run_id]
        if r.status == "running":
            r.cancel_requested = True
        elif r.status in ACTIVE:
            r.status, r.error = "cancelled", CANCELLED
            r.events.extend(events)
        return r.status

    async def requeue(self, run_id: str, attempt: int) -> None:
        """Graceful shutdown: hand the run back; it isn't a crash."""
        r = self._runs[run_id]
        if r.status == "running" and r.attempt == attempt:
            r.status, r.lease_until = "queued", None

    async def reap(self) -> list[tuple[str, str]]:
        out = []
        for r in self._runs.values():
            if r.status == "running" and r.lease_until is not None and r.lease_until < time.time():
                r.lease_until = None
                r.crashes += 1
                if r.cancel_requested:   # its worker died before it could stop it
                    r.status, r.error = "cancelled", CANCELLED
                    r.events += cancel_events()
                elif r.crashes < MAX_CRASHES:
                    r.status = "queued"
                    r.events.append(_resumed_event())
                else:
                    r.status, r.error = "failed", _reap_message(r.crashes)
                    r.events += [{"type": "error", "message": r.error, "retryable": True},
                                 {"type": "done"}]
                out.append((r.id, r.status))
        return out


def cancel_events() -> list[dict]:
    return [{"type": "error", "message": CANCELLED, "retryable": True}, {"type": "done"}]


def _resumed_event() -> dict:
    return {"type": "run_resumed",
            "message": "The worker restarted; continuing from the last finished step..."}


class RunStore:
    """PostgreSQL: the queue is the runs table itself (no Redis)."""

    persistent = True

    def __init__(self, database_url: str) -> None:
        self._engine = create_async_engine(database_url, echo=False)
        self._db = async_sessionmaker(self._engine, expire_on_commit=False)

    async def init(self) -> None:
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all, tables=[RunRow.__table__,
                                                                 RunEventRow.__table__])
            await conn.execute(text(   # tables made before the crash budget had its own column
                "ALTER TABLE runs ADD COLUMN IF NOT EXISTS crashes INTEGER NOT NULL DEFAULT 0"))
            # One active run per session, enforced by the database (two requests can race).
            await conn.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS runs_one_active_per_session ON runs "
                "(session_id) WHERE status IN ('queued', 'running', 'waiting_approval')"))

    @staticmethod
    def _run(row) -> Run:
        m = row._mapping
        return Run(id=m["id"], session_id=m["session_id"], user_id=m["user_id"],
                   status=m["status"], input=json.loads(m["input"]),
                   client_request_id=m["client_request_id"],
                   resume=json.loads(m["resume"]) if m["resume"] else None, error=m["error"],
                   attempt=m["attempt"], crashes=m["crashes"],
                   cancel_requested=m["cancel_requested"])

    async def _append(self, db, run_id: str, events) -> None:
        """Caller holds the run row lock, so seqs are allocated one writer at a time."""
        if not events:
            return
        last = (await db.execute(text(
            "SELECT coalesce(max(seq), 0) FROM run_events WHERE run_id = :id"),
            {"id": run_id})).scalar_one()
        now = _now()
        await db.execute(text(
            "INSERT INTO run_events (run_id, seq, type, payload, created_at) "
            "VALUES (:run_id, :seq, :type, :payload, :at)"),
            [{"run_id": run_id, "seq": last + i, "type": e.get("type", ""),
              "payload": json.dumps(e, default=str), "at": now}
             for i, e in enumerate(events, 1)])

    async def _locked(self, db, run_id: str):
        return (await db.execute(text("SELECT * FROM runs WHERE id = :id FOR UPDATE"),
                                 {"id": run_id})).first()

    async def create(self, *, session_id: str, user_id: str, input: dict,
                     client_request_id: str | None = None,
                     first_events: list[dict] = ()) -> tuple[Run, bool]:
        run_id, now = str(uuid.uuid4()), _now()
        async with self._db() as db:
            try:
                row = (await db.execute(text(
                    "INSERT INTO runs (id, session_id, user_id, client_request_id, status, input, "
                    "attempt, crashes, cancel_requested, created_at, updated_at) "
                    "VALUES (:id, :sid, :uid, :crid, 'queued', :input, 0, 0, false, :now, :now) "
                    "ON CONFLICT (user_id, client_request_id) DO NOTHING RETURNING *"),
                    {"id": run_id, "sid": session_id, "uid": user_id, "crid": client_request_id,
                     "input": json.dumps(input), "now": now})).first()
            except IntegrityError as exc:
                raise RunConflict(session_id) from exc
            if row is None:   # a double submit: hand back the first run
                row = (await db.execute(text(
                    "SELECT * FROM runs WHERE user_id = :uid AND client_request_id = :crid"),
                    {"uid": user_id, "crid": client_request_id})).first()
                return self._run(row), False
            await self._append(db, run_id, first_events)
            await db.commit()
            return self._run(row), True

    async def by_request(self, user_id: str, client_request_id: str) -> Run | None:
        async with self._db() as db:
            row = (await db.execute(text(
                "SELECT * FROM runs WHERE user_id = :uid AND client_request_id = :crid"),
                {"uid": user_id, "crid": client_request_id})).first()
            return self._run(row) if row else None

    async def get(self, run_id: str) -> Run | None:
        async with self._db() as db:
            row = (await db.execute(text("SELECT * FROM runs WHERE id = :id"),
                                    {"id": run_id})).first()
            return self._run(row) if row else None

    async def latest_for_session(self, session_id: str, user_id: str) -> Run | None:
        async with self._db() as db:
            row = (await db.execute(text(
                "SELECT * FROM runs WHERE session_id = :sid AND user_id = :uid "
                "ORDER BY created_at DESC LIMIT 1"), {"sid": session_id, "uid": user_id})).first()
            return self._run(row) if row else None

    async def active_for_session(self, session_id: str, user_id: str) -> Run | None:
        async with self._db() as db:
            row = (await db.execute(text(
                "SELECT * FROM runs WHERE session_id = :sid AND user_id = :uid "
                "AND status = ANY(:active) ORDER BY created_at DESC LIMIT 1"),
                {"sid": session_id, "uid": user_id, "active": list(ACTIVE)})).first()
            return self._run(row) if row else None

    async def claim(self, lease_s: float = LEASE_S) -> Run | None:
        async with self._db() as db:
            row = (await db.execute(text(
                "UPDATE runs r SET status = 'running', attempt = r.attempt + 1, "
                "lease_until = now() + make_interval(secs => :lease), updated_at = now() "
                "WHERE r.id = (SELECT id FROM runs WHERE status = 'queued' ORDER BY created_at "
                "              FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING r.*"),
                {"lease": lease_s})).first()
            await db.commit()
            return self._run(row) if row else None

    async def heartbeat(self, run_id: str, attempt: int,
                        lease_s: float = LEASE_S) -> tuple[bool, bool]:
        async with self._db() as db:
            row = (await db.execute(text(
                "UPDATE runs SET lease_until = now() + make_interval(secs => :lease), "
                "updated_at = now() WHERE id = :id AND status = 'running' AND attempt = :a "
                "RETURNING cancel_requested"),
                {"id": run_id, "a": attempt, "lease": lease_s})).first()
            await db.commit()
            return (True, row[0]) if row else (False, False)

    async def append(self, run_id: str, events: list[dict], attempt: int | None = None) -> bool:
        async with self._db() as db:
            row = await self._locked(db, run_id)
            if row is None or attempt is not None and (
                    row._mapping["status"] != "running" or row._mapping["attempt"] != attempt):
                return False
            await self._append(db, run_id, events)
            await db.commit()
            return True

    async def events_after(self, run_id: str, after: int) -> list[tuple[int, dict]]:
        async with self._db() as db:
            rows = (await db.execute(text(
                "SELECT seq, payload FROM run_events WHERE run_id = :id AND seq > :after "
                "ORDER BY seq"), {"id": run_id, "after": after})).all()
            return [(s, json.loads(p)) for s, p in rows]

    async def finish(self, run_id: str, status: str, *, attempt: int | None = None,
                     error: str | None = None, events: list[dict] = ()) -> bool:
        async with self._db() as db:
            row = await self._locked(db, run_id)
            if attempt is not None and (
                    row._mapping["status"] != "running" or row._mapping["attempt"] != attempt):
                return False
            await db.execute(text(
                "UPDATE runs SET status = :s, error = :e, lease_until = NULL, resume = NULL, "
                "updated_at = now() WHERE id = :id"), {"id": run_id, "s": status, "e": error})
            await self._append(db, run_id, events)
            await db.commit()
            return True

    async def resume(self, run_id: str, payload: dict, events: list[dict] = (),
                     from_status: tuple[str, ...] = ("waiting_approval",)) -> bool:
        async with self._db() as db:
            row = await self._locked(db, run_id)
            if row is None or row._mapping["status"] not in from_status:
                return False
            try:
                await db.execute(text(
                    "UPDATE runs SET status = 'queued', resume = :r, error = NULL, "
                    "cancel_requested = false, crashes = 0, updated_at = now() WHERE id = :id"),
                    {"id": run_id, "r": json.dumps(payload)})
            except IntegrityError:   # another run became active on this session meanwhile
                return False
            await self._append(db, run_id, events)
            await db.commit()
            return True

    async def request_cancel(self, run_id: str, events: list[dict]) -> str:
        async with self._db() as db:
            row = await self._locked(db, run_id)
            status = row._mapping["status"]
            if status == "running":
                await db.execute(text("UPDATE runs SET cancel_requested = true WHERE id = :id"),
                                 {"id": run_id})
            elif status in ACTIVE:
                status = "cancelled"
                await db.execute(text(
                    "UPDATE runs SET status = 'cancelled', error = :e, "
                    "updated_at = now() WHERE id = :id"), {"id": run_id, "e": CANCELLED})
                await self._append(db, run_id, events)
            await db.commit()
            return status

    async def requeue(self, run_id: str, attempt: int) -> None:
        async with self._db() as db:
            await db.execute(text(
                "UPDATE runs SET status = 'queued', lease_until = NULL "
                "WHERE id = :id AND status = 'running' AND attempt = :a"),
                {"id": run_id, "a": attempt})
            await db.commit()

    async def reap(self) -> list[tuple[str, str]]:
        out = []
        async with self._db() as db:
            rows = (await db.execute(text(
                "SELECT id, crashes + 1, cancel_requested FROM runs WHERE status = 'running' "
                "AND lease_until < now() FOR UPDATE SKIP LOCKED"))).all()
            for run_id, crashes, cancel in rows:
                if cancel:   # its worker died before it could stop it
                    status, error, events = "cancelled", CANCELLED, cancel_events()
                elif crashes < MAX_CRASHES:
                    status, error, events = "queued", None, [_resumed_event()]
                else:
                    error = _reap_message(crashes)
                    status, events = "failed", [{"type": "error", "message": error,
                                                 "retryable": True}, {"type": "done"}]
                await db.execute(text(
                    "UPDATE runs SET status = :s, error = :e, lease_until = NULL, crashes = :c, "
                    "updated_at = now() WHERE id = :id"),
                    {"id": run_id, "s": status, "e": error, "c": crashes})
                await self._append(db, run_id, events)
                out.append((run_id, status))
            await db.commit()
        return out


async def open_run_store(database_url: str) -> RunStore:
    store = RunStore(database_url)
    await store.init()
    return store

