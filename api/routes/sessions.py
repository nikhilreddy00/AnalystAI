"""Analysis threads (Phase 5.6b workspace): list for the sidebar, rename / pin / archive, delete,
and the older per-session reads.

All endpoints enforce user_id ownership against the authenticated user; another user's thread is
a 404 (never revealing that it exists). Deleting is a tombstone: the thread disappears at once,
its runs are cancelled and its memory point removed; memory/retention.py purges the rest.
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator

from api.auth import UserProfile, get_current_user
from api.rate_limit import limiter
from memory.run_store import cancel_events
from memory.session_store import TITLE_MAX
from orchestrator.state import MessageRole

logger = logging.getLogger(__name__)
router = APIRouter()
LIST_MAX = 500   # ponytail: the sidebar loads every thread and searches in the browser


class ThreadPatch(BaseModel):
    title: str | None = Field(default=None, max_length=TITLE_MAX * 2)
    pinned: bool | None = None
    archived: bool | None = None

    @field_validator("title")
    @classmethod
    def _title(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = " ".join(v.split())
        if not 1 <= len(v) <= TITLE_MAX:
            raise ValueError(f"a title has 1 to {TITLE_MAX} characters")
        return v


def _store(request: Request):
    return request.app.state.session_store


@router.get("")
@limiter.limit("60/minute")
async def list_sessions(
    request: Request,
    limit: int = LIST_MAX,
    current_user: UserProfile = Depends(get_current_user),
):
    store = _store(request)
    summaries = await store.list_sessions(current_user.uid, limit=min(max(limit, 1), LIST_MAX))
    runs = await request.app.state.run_store.latest_statuses(
        current_user.uid, [s.session_id for s in summaries])
    return [{**s.model_dump(mode="json"), "run_status": runs.get(s.session_id)}
            for s in summaries]


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
        (m.content for m in session.conversation_history if m.role == MessageRole.USER),
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
        "evidence": [e.model_dump(mode="json") for e in session.analysis_findings.evidence]
        if session.analysis_findings else [],
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


@router.patch("/{session_id}")
@limiter.limit("60/minute")
async def update_session(
    request: Request,
    session_id: str,
    body: ThreadPatch,
    current_user: UserProfile = Depends(get_current_user),
):
    fields = body.model_dump(exclude_none=True)
    if not fields:
        raise HTTPException(status_code=422, detail="Nothing to change")
    store = _store(request)
    if not await store.update_thread(session_id, current_user.uid, **fields):
        raise HTTPException(status_code=404, detail="Session not found")
    return (await store.get_summary(session_id, current_user.uid)).model_dump(mode="json")


async def forget_thread(request: Request, session_id: str, user_id: str) -> None:
    """After a tombstone: stop its run and drop its memory now. Purge does the rest, and repeats
    both, so a failure here is logged and retried, never silent."""
    runs = request.app.state.run_store
    if active := await runs.active_for_session(session_id, user_id):
        await runs.request_cancel(active.id, cancel_events())
    if vs := request.app.state.vector_store:
        try:   # else this thread's summary keeps feeding new reports until the purge
            await vs.delete_session(session_id)
        except Exception:
            logger.warning("memory delete for %s failed; the purge retries it", session_id,
                           exc_info=True)


@router.delete("/{session_id}", status_code=202)
@limiter.limit("30/minute")
async def delete_session(
    request: Request,
    session_id: str,
    current_user: UserProfile = Depends(get_current_user),
):
    # Scoped to the caller: another user's thread is a 404, like a missing one.
    if not await _store(request).tombstone(session_id, current_user.uid):
        raise HTTPException(status_code=404, detail="Session not found")
    await forget_thread(request, session_id, current_user.uid)
    return {"deleted": True}
