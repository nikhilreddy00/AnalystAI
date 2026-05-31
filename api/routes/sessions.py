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
