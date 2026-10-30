"""
Runs (Phase 5.6a): status, replayable progress, cancel and retry.

GET /runs/{id}/events is server-sent events with `id: <seq>`. A client that reconnects sends
Last-Event-ID (or ?after=) and gets every later event exactly once, in order: a refreshed tab, a
dropped connection or a second device picks up where it left off. The stream ends once the run
is no longer queued or running and every event has been sent (a waiting gate ends it too; the
client attaches again after answering).
"""
from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from api.auth import UserProfile, get_current_user
from memory.run_store import Run, cancel_events

router = APIRouter()

POLL_S = 0.5        # ponytail: polls run_events; LISTEN/NOTIFY if many idle streams ever matter
KEEPALIVE_S = 15.0


async def _owned(request: Request, run_id: str, user: UserProfile) -> Run:
    run = await request.app.state.run_store.get(run_id)
    if run is None or run.user_id != user.uid:
        raise HTTPException(status_code=404, detail="Run not found.")
    return run


@router.get("/{run_id}")
async def get_run(run_id: str, request: Request,
                  current_user: UserProfile = Depends(get_current_user)) -> dict:  # noqa: B008
    return (await _owned(request, run_id, current_user)).public()


@router.get("/{run_id}/events")
async def run_events(run_id: str, request: Request, after: int = 0,
                     current_user: UserProfile = Depends(get_current_user)  # noqa: B008
                     ) -> StreamingResponse:
    await _owned(request, run_id, current_user)
    store = request.app.state.run_store
    last = request.headers.get("last-event-id")
    cursor = int(last) if last and last.isdigit() else after

    async def stream() -> AsyncIterator[str]:
        nonlocal cursor
        quiet = 0.0
        while True:
            batch = await store.events_after(run_id, cursor)
            for seq, event in batch:
                cursor = seq
                yield f"id: {seq}\ndata: {json.dumps(event, default=str)}\n\n"
            if batch:
                quiet = 0.0
                continue
            run = await store.get(run_id)
            if run.status not in ("queued", "running"):
                if not await store.events_after(run_id, cursor):   # nothing written meanwhile
                    return
                continue
            if await request.is_disconnected():
                return
            await asyncio.sleep(POLL_S)
            quiet += POLL_S
            if quiet >= KEEPALIVE_S:
                quiet = 0.0
                yield ": keep-alive\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/{run_id}/cancel")
async def cancel_run(run_id: str, request: Request,
                     current_user: UserProfile = Depends(get_current_user)) -> dict:  # noqa: B008
    run = await _owned(request, run_id, current_user)
    status = await request.app.state.run_store.request_cancel(run.id, cancel_events())
    return {**run.public(), "status": status}


@router.post("/{run_id}/retry")
async def retry_run(run_id: str, request: Request,
                    current_user: UserProfile = Depends(get_current_user)) -> dict:  # noqa: B008
    """Retry from where it failed: resumes from the checkpoint before the failed step."""
    run = await _owned(request, run_id, current_user)
    store = request.app.state.run_store
    other = await store.active_for_session(run.session_id, run.user_id)
    if other and other.id != run.id:
        raise HTTPException(status_code=409, detail="Another analysis is running in this "
                            "conversation. Wait for it to finish, or stop it.")
    # A retry continues from the session as this run saw it; after a newer question that would
    # overwrite the newer turn. Only the conversation's latest run can be retried.
    latest = await store.latest_for_session(run.session_id, run.user_id)
    if latest and latest.id != run.id:
        raise HTTPException(status_code=409, detail="A newer question has been asked since. "
                            "Ask this one again instead.")
    if not await store.resume(run.id, {"retry": True}, from_status=("failed", "cancelled"),
                              events=[{"type": "run_resumed",
                                       "message": "Retrying from where it stopped..."}]):
        raise HTTPException(status_code=409, detail="Only a failed or stopped run can be retried.")
    return {**run.public(), "status": "queued"}
