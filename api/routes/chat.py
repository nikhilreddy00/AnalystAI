"""
Chat endpoint — streams agent pipeline execution via Server-Sent Events.

SSE event types emitted in order:
  session_id     → always first, lets the client track this conversation
  agent_start    → agent is beginning (shows progress bar in UI)
  agent_complete → agent finished with a status message
  approval_required → human gate: clarification or high-null warning
  charts         → list[ChartConfig] (rendered inline in chat)
  insight        → InsightReport (Executive + Analyst views)
  error          → pipeline failure with user-readable message
  done           → stream is finished; client closes the connection

Multi-turn: include session_id from the prior response to continue a conversation.
Approval responses: set is_approval=True so the graph handles approval context.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from typing import AsyncIterator

from fastapi import APIRouter, HTTPException, Request, Depends
from fastapi.responses import StreamingResponse
from api.auth import get_current_user, UserProfile
from api.rate_limit import ip_limit, limiter
from pydantic import BaseModel, Field

from orchestrator.graph import AgentState, build_graph
from connectors.csv_connector import check_upload_owner
from orchestrator.state import AnalysisSession, ConnectorConfig, ConnectorType

router = APIRouter()

# Build once at import time — compiled graph is reusable across requests
_graph = build_graph()


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    session_id: str | None = Field(default=None, max_length=64)
    user_id: str = Field(default="default", max_length=128)
    is_approval: bool = False
    connector_config: dict | None = None   # required on first message when no prior session
    file_hash: str | None = Field(default=None, max_length=128)
    dataset_name: str | None = Field(default=None, max_length=256)
    approval_context: str | None = Field(default=None, max_length=64)


def _owned_config(raw: dict, uid: str) -> ConnectorConfig:
    """A CSV config may only point at the caller's own upload (audit B2)."""
    config = ConnectorConfig(**raw)
    if config.type == ConnectorType.CSV and (reason := check_upload_owner(config.file_path, uid)):
        raise HTTPException(status_code=403, detail=reason)
    return config


@router.post("")
@limiter.limit("30/minute")
@ip_limit
async def chat(
    request: Request,
    body: ChatRequest,
    current_user: UserProfile = Depends(get_current_user)
) -> StreamingResponse:  # noqa: B008
    if not body.connector_config and not body.session_id:
        raise HTTPException(
            status_code=400,
            detail="connector_config is required on the first message (no session_id provided).",
        )
    session_store = request.app.state.session_store
    vector_store = request.app.state.vector_store

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
            session.connector_config = incoming
            session.overview_done = False

    if session is not None and body.is_approval and body.approval_context == "rerun_overview":
        # User chose "Run fresh" on the rerun-overview modal. Reset the
        # session so the graph runs a clean OVERVIEW from intake forward.
        session.overview_done = False
        session.status = "in_progress"
        session.task_plan = None

    event_queue: asyncio.Queue[dict | None] = asyncio.Queue()

    async def run_graph() -> None:
        state: AgentState = {
            "session": session,
            "df_parquet": None,
            "event_queue": event_queue,
            "user_message": body.message,
            "is_approval": body.is_approval,
            "approval_context": body.approval_context,
            "session_store": session_store,
            "vector_store": vector_store,
        }
        try:
            await _graph.ainvoke(state)
        except Exception as exc:
            event_queue.put_nowait({"type": "error", "message": str(exc)})
            event_queue.put_nowait({"type": "done"})
        finally:
            event_queue.put_nowait(None)  # sentinel — stops the generator

    async def event_stream() -> AsyncIterator[str]:
        yield f"data: {json.dumps({'type': 'session_id', 'value': session.session_id})}\n\n"

        graph_task = asyncio.create_task(run_graph())

        while True:
            event = await event_queue.get()
            if event is None:
                break
            yield f"data: {json.dumps(event, default=str)}\n\n"

        await graph_task

    return StreamingResponse(event_stream(), media_type="text/event-stream")
