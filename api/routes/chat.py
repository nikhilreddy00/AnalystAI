"""
Chat endpoint — stores a run and returns its id at once (Phase 5.6a: a run is a record, not a
request). A worker executes it (orchestrator/runner.py); the client streams its progress from
GET /runs/{id}/events, which replays every event after Last-Event-ID.

SSE event types, in order: session_id (seq 1, written here with the question), agent_start,
agent_complete, approval_required, approval_answered, charts, insight, error, done, …
(see CLAUDE.md "SSE Event Protocol").

Multi-turn: include session_id from the prior response to continue a conversation.
Approval answers: is_approval=True resumes the run waiting at that gate.

Data (Phase 5.6b): a new thread names its data by `dataset_version_id` (an upload) or
`dataset_id` (any dataset; an upload means its latest version), so the client never sends a file
path. A raw `connector_config` still works (live sources become a dataset; a CSV path is a
legacy thread with no dataset). One dataset per thread: other data on an existing thread is a
409; the same dataset's version (re-run on current data) is switched in place.
"""
from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, HTTPException, Request, Depends
from api.auth import get_current_user, UserProfile
from api.rate_limit import ip_limit, limiter
from pydantic import BaseModel, Field

from connectors.csv_connector import check_upload_owner
from memory.run_store import RunConflict
from memory.session_store import DatasetGone, make_title
from orchestrator.state import AnalysisSession, ConnectorConfig, ConnectorType

router = APIRouter()

GATES = ("clarification", "high_null")   # the gates a run waits at (interrupt())
GATE_WAIT_POLLS = 30   # × 0.1 s
BUSY = "An analysis is already running in this conversation. Wait for it to finish, or stop it."
OTHER_DATA = ("This analysis is about another dataset. Start a new analysis to ask about this "
              "data.")


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    session_id: str | None = Field(default=None, max_length=64)
    user_id: str = Field(default="default", max_length=128)
    is_approval: bool = False
    connector_config: dict | None = None   # required on first message when no prior session
    file_hash: str | None = Field(default=None, max_length=128)
    dataset_name: str | None = Field(default=None, max_length=256)
    approval_context: str | None = Field(default=None, max_length=64)
    client_request_id: str | None = Field(default=None, max_length=64)   # double submit → one run
    dataset_version_id: str | None = Field(default=None, max_length=64)
    dataset_id: str | None = Field(default=None, max_length=64)


class _Source(BaseModel):
    config: ConnectorConfig
    dataset_id: str | None = None
    version_id: str | None = None
    name: str | None = None
    file_hash: str | None = None


def _owned_config(raw: dict, uid: str) -> ConnectorConfig:
    """A CSV config may only point at the caller's own upload (audit B2)."""
    config = ConnectorConfig(**raw)
    if config.type == ConnectorType.CSV and (reason := check_upload_owner(config.file_path, uid)):
        raise HTTPException(status_code=403, detail=reason)
    return config


async def _source(body: ChatRequest, uid: str, store) -> _Source | None:
    """The data this request names, checked against the caller's own datasets (404 if not)."""
    if body.dataset_version_id or body.dataset_id:
        if body.dataset_version_id:
            got = await store.get_version(body.dataset_version_id, uid)
            ds, version = got if got else (None, None)
        else:
            ds = await store.get_dataset(body.dataset_id, uid)
            version = ds.latest if ds else None
        if ds is None or (ds.kind == "upload" and version is None):
            raise HTTPException(status_code=404, detail="Dataset not found")
        if ds.kind == "live":
            return _Source(config=ds.config, dataset_id=ds.id, name=ds.name)
        return _Source(config=ConnectorConfig(type=ConnectorType.CSV, file_path=version.file_path),
                       dataset_id=ds.id, version_id=version.id, name=ds.name,
                       file_hash=version.content_hash)
    if not body.connector_config:
        return None
    config = _owned_config(body.connector_config, uid)
    if config.type == ConnectorType.CSV:
        # A path that is a dataset version's file is that version; a deleted dataset's file
        # (not yet purged) is gone. Any other path is a pre-5.6b upload: a thread with no dataset.
        if hit := await store.version_by_path(config.file_path, uid):
            ds, version = hit
            if ds is None:
                raise HTTPException(status_code=404, detail="Dataset not found")
            return _Source(config=config, dataset_id=ds.id, version_id=version.id, name=ds.name,
                           file_hash=version.content_hash)
        return _Source(config=config, name=body.dataset_name, file_hash=body.file_hash)
    name = body.dataset_name or config.table_name or config.sheet_name or config.url or "data"
    ds = await store.ensure_live_dataset(uid, name, config)
    return _Source(config=config, dataset_id=ds.id, name=ds.name)


def _last_turn_pii(session: AnalysisSession) -> set[str]:
    """The PII values of the session's previous data (its cached cleaned frame), so earlier
    answers quoting that data stay guarded after the user switches datasets."""
    if not (session.data_contract and session.cleaned_data_path):
        return set()
    import io

    import pandas as pd

    from agents.ingestion_agent import known_pii_values
    from memory.session_data import read_cleaned_parquet
    cached = read_cleaned_parquet(session.session_id)
    return known_pii_values(pd.read_parquet(io.BytesIO(cached)), session.data_contract) \
        if cached else set()


@router.post("")
@limiter.limit("30/minute")
@ip_limit
async def chat(
    request: Request,
    body: ChatRequest,
    current_user: UserProfile = Depends(get_current_user)
) -> dict:  # noqa: B008
    session_store = request.app.state.session_store
    runs = request.app.state.run_store
    if body.client_request_id and (
            seen := await runs.by_request(current_user.uid, body.client_request_id)):
        return seen.public()
    src = await _source(body, current_user.uid, session_store)
    if src is None and not body.session_id:
        raise HTTPException(
            status_code=400,
            detail="A dataset is required on the first message (no session_id provided).",
        )

    # Load or create session
    session: AnalysisSession | None = None
    if body.session_id:
        try:
            session = await session_store.load(body.session_id)
        except Exception as exc:
            # Fail closed: never fall through to a new session under an id we couldn't check.
            raise HTTPException(status_code=503, detail="Your conversation could not be loaded. "
                                "Try again in a moment.") from exc
        if session is None:   # deleted, or never existed: never (re)create it under that id
            raise HTTPException(status_code=404, detail="This conversation no longer exists.")
        # Multi-tenant privacy: another user's thread is a 404, like one that doesn't exist.
        if session.user_id != current_user.uid:
            raise HTTPException(status_code=404, detail="This conversation no longer exists.")

    new = session is None
    if new:
        session = AnalysisSession(
            session_id=str(uuid.uuid4()), user_id=current_user.uid, connector_config=src.config,
            dataset_id=src.dataset_id, dataset_version_id=src.version_id,
            dataset_name=src.name, file_hash=src.file_hash)
    elif src and src.config != session.connector_config:
        # Another version of this thread's dataset ("re-run on current data"): update the
        # config AND reset Phase-3 overview_done so the next run produces a fresh end-to-end
        # briefing for the new data. Any other data is a new thread (ADR-0007).
        if not (src.dataset_id and src.dataset_id == session.dataset_id):
            raise HTTPException(status_code=409, detail=OTHER_DATA)
        # The conversation still quotes the old data: its PII values guard every later run.
        session.prior_pii = sorted(set(session.prior_pii) | _last_turn_pii(session))
        session.connector_config = src.config
        session.dataset_version_id, session.file_hash = src.version_id, src.file_hash
        session.overview_done = False
        # Answers and caches about the old data don't carry over.
        session.high_null_answer = None
        session.cleaned_data_path = None

    if session is not None and body.is_approval and body.approval_context == "rerun_overview":
        # User chose "Run fresh" on the rerun-overview modal. Reset the
        # session so the graph runs a clean OVERVIEW from intake forward.
        session.overview_done = False
        session.high_null_answer = None   # a fresh run asks the high-null question again
        session.status = "in_progress"
        session.task_plan = None

    active = await runs.active_for_session(session.session_id, current_user.uid)
    if body.is_approval and body.approval_context in GATES:
        # The gate's event is stored a moment before the run is marked waiting: an answer sent in
        # that window waits for it. Still running after that = this answer was already sent.
        for _ in range(GATE_WAIT_POLLS):
            if not (active and active.status == "running"):
                break
            await asyncio.sleep(0.1)
            active = await runs.get(active.id)
        if active and active.status in ("queued", "running"):
            return active.public()
        if not (active and active.status == "waiting_approval" and await runs.resume(
                active.id, {"answer": body.message},
                events=[{"type": "approval_answered", "context": body.approval_context,
                         "answer": body.message}])):
            raise HTTPException(status_code=409, detail="This question is no longer waiting for "
                                "an answer. Ask it again to start a new analysis.")
        return {**active.public(), "status": "queued"}
    if active and active.status != "waiting_approval":
        raise HTTPException(status_code=409, detail=BUSY)
    if active:   # a gate nobody answered: the new question replaces it
        await runs.request_cancel(active.id, [{"type": "error", "message": "Replaced by a new "
                                               "question."}, {"type": "done"}])

    # The worker starts from the stored session. Only this route creates one; a save of a
    # thread deleted meanwhile does nothing.
    if new:
        try:   # the dataset may have been deleted since it was looked up above
            await session_store.create(session, title=make_title(
                session.dataset_name, None if body.is_approval else body.message))
        except DatasetGone:
            raise HTTPException(status_code=404, detail="Dataset not found") from None
    elif not await session_store.save(session):
        raise HTTPException(status_code=404, detail="This conversation no longer exists.")
    if not body.is_approval:
        await session_store.note_question(session.session_id, body.message)
    try:
        run, _ = await runs.create(
            session_id=session.session_id, user_id=current_user.uid,
            client_request_id=body.client_request_id,
            input={"message": body.message, "is_approval": body.is_approval,
                   "approval_context": body.approval_context,
                   # Provenance: the data version this question was asked of (uploads).
                   "dataset_version_id": session.dataset_version_id},
            first_events=[{"type": "session_id", "value": session.session_id,
                           "message": body.message, "is_approval": body.is_approval}])
    except RunConflict:   # another request started one a moment ago
        raise HTTPException(status_code=409, detail=BUSY) from None
    return run.public()
