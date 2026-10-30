"""
Chat endpoint — stores a run and returns its id at once (Phase 5.6a: a run is a record, not a
request). A worker executes it (orchestrator/runner.py); the client streams its progress from
GET /runs/{id}/events, which replays every event after Last-Event-ID.

SSE event types, in order: session_id (seq 1, written here with the question), agent_start,
agent_complete, approval_required, approval_answered, charts, insight, error, done, …
(see CLAUDE.md "SSE Event Protocol").

Multi-turn: include session_id from the prior response to continue a conversation.
Approval answers: is_approval=True resumes the run waiting at that gate.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Request, Depends
from api.auth import get_current_user, UserProfile
from api.rate_limit import ip_limit, limiter
from pydantic import BaseModel, Field

from connectors.csv_connector import check_upload_owner
from orchestrator.state import AnalysisSession, ConnectorConfig, ConnectorType

router = APIRouter()

GATES = ("clarification", "high_null")   # the gates a run waits at (interrupt())


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


def _owned_config(raw: dict, uid: str) -> ConnectorConfig:
    """A CSV config may only point at the caller's own upload (audit B2)."""
    config = ConnectorConfig(**raw)
    if config.type == ConnectorType.CSV and (reason := check_upload_owner(config.file_path, uid)):
        raise HTTPException(status_code=403, detail=reason)
    return config


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
    if not body.connector_config and not body.session_id:
        raise HTTPException(
            status_code=400,
            detail="connector_config is required on the first message (no session_id provided).",
        )
    session_store = request.app.state.session_store
    runs = request.app.state.run_store
    if body.client_request_id and (
            seen := await runs.by_request(current_user.uid, body.client_request_id)):
        return seen.public()

    # Load or create session
    session: AnalysisSession | None = None
    if body.session_id and session_store:
        try:
            session = await session_store.load(body.session_id)
            # Ensure multi-tenant privacy: user can only load their own session
            if session and session.user_id != current_user.uid:
                raise HTTPException(
                    status_code=403,
                    detail="Access denied to requested session."
                )
        except HTTPException:
            raise
        except Exception:
            pass

    if session is None:
        session_id = body.session_id or str(uuid.uuid4())
        session = AnalysisSession(session_id=session_id, user_id=current_user.uid)

        if body.file_hash:
            session.file_hash = body.file_hash
        if body.dataset_name:
            session.dataset_name = body.dataset_name

        if body.connector_config:
            session.connector_config = _owned_config(body.connector_config, current_user.uid)
    elif body.connector_config:
        # Existing session + incoming connector_config: if it points at a different
        # dataset, update the config AND reset Phase-3 overview_done so the next
        # run produces a fresh end-to-end briefing for the new data.
        incoming = _owned_config(body.connector_config, current_user.uid)
        if session.connector_config != incoming:
            # The conversation still quotes the old data: its PII values guard every later run.
            session.prior_pii = sorted(set(session.prior_pii) | _last_turn_pii(session))
            session.connector_config = incoming
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

    active = await runs.active_for_session(session.session_id)
    if body.is_approval and body.approval_context in GATES:
        if active and active.status in ("queued", "running"):   # this answer was already sent
            return active.public()
        if not (active and active.status == "waiting_approval" and await runs.resume(
                active.id, {"answer": body.message},
                events=[{"type": "approval_answered", "context": body.approval_context,
                         "answer": body.message}])):
            raise HTTPException(status_code=409, detail="This question is no longer waiting for "
                                "an answer. Ask it again to start a new analysis.")
        return {**active.public(), "status": "queued"}
    if active and active.status != "waiting_approval":
        raise HTTPException(status_code=409, detail="An analysis is already running in this "
                            "conversation. Wait for it to finish, or stop it.")
    if active:   # a gate nobody answered: the new question replaces it
        await runs.request_cancel(active.id, [{"type": "error", "message": "Replaced by a new "
                                               "question."}, {"type": "done"}])

    await session_store.save(session)   # the worker starts from the stored session
    run, _ = await runs.create(
        session_id=session.session_id, user_id=current_user.uid,
        client_request_id=body.client_request_id,
        input={"message": body.message, "is_approval": body.is_approval,
               "approval_context": body.approval_context},
        first_events=[{"type": "session_id", "value": session.session_id,
                       "message": body.message, "is_approval": body.is_approval}])
    return run.public()
