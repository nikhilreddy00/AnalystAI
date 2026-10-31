"""Datasets (Phase 5.6b, ADR-0007): every upload or live connection the user has, its versions, the
analyses on it and their pinned charts. Uploads land here through POST /connectors/upload.

Scoped to the caller: another user's dataset is a 404. Deleting a dataset hides it and every
thread on it at once (right to erasure, GDPR Art. 17); memory/retention.py purges the files and
rows once their runs have stopped.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from api.auth import UserProfile, get_current_user
from api.rate_limit import limiter
from api.routes.sessions import forget_thread
from memory.session_store import DatasetConflict

logger = logging.getLogger(__name__)
router = APIRouter()
NAME_MAX = 120


class DatasetPatch(BaseModel):
    name: str = Field(max_length=NAME_MAX * 2)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = " ".join(v.split())
        if not 1 <= len(v) <= NAME_MAX:
            raise ValueError(f"a name has 1 to {NAME_MAX} characters")
        return v


def _store(request: Request):
    return request.app.state.session_store


@router.get("")
@limiter.limit("60/minute")
async def list_datasets(request: Request, current_user: UserProfile = Depends(get_current_user)):
    return [ds.public(analyses=n) for ds, n in await _store(request).list_datasets(current_user.uid)]


@router.get("/{dataset_id}")
@limiter.limit("60/minute")
async def get_dataset(request: Request, dataset_id: str,
                      current_user: UserProfile = Depends(get_current_user)):
    store = _store(request)
    ds = await store.get_dataset(dataset_id, current_user.uid)
    if ds is None:
        raise HTTPException(status_code=404, detail="Dataset not found")
    threads = await store.list_sessions(current_user.uid, dataset_id=dataset_id)
    charts = await store.get_dashboard(current_user.uid, {t.session_id for t in threads}) \
        if threads else []
    return {**ds.public(analyses=len(threads)),
            "threads": [t.model_dump(mode="json") for t in threads], "charts": charts}


@router.patch("/{dataset_id}")
@limiter.limit("60/minute")
async def rename_dataset(request: Request, dataset_id: str, body: DatasetPatch,
                         current_user: UserProfile = Depends(get_current_user)):
    store = _store(request)
    if not await store.rename_dataset(dataset_id, current_user.uid, body.name):
        raise HTTPException(status_code=404, detail="Dataset not found")
    return (await store.get_dataset(dataset_id, current_user.uid)).public()


@router.post("/versions/{version_id}/split")
@limiter.limit("30/minute")
async def split_version(request: Request, version_id: str,
                        current_user: UserProfile = Depends(get_current_user)):
    """"Keep as a separate dataset": undo an automatic new version while it has no analyses."""
    try:
        ds = await _store(request).split_version(version_id, current_user.uid)
    except DatasetConflict:
        raise HTTPException(status_code=409, detail="This version already has analyses, or is "
                            "the dataset's first version.") from None
    if ds is None:
        raise HTTPException(status_code=404, detail="Version not found")
    return ds.public()


@router.delete("/{dataset_id}", status_code=202)
@limiter.limit("30/minute")
async def delete_dataset(request: Request, dataset_id: str,
                         current_user: UserProfile = Depends(get_current_user)):
    threads = await _store(request).tombstone_dataset(dataset_id, current_user.uid)
    if threads is None:
        raise HTTPException(status_code=404, detail="Dataset not found")
    for sid in threads:
        await forget_thread(request, sid, current_user.uid)
    if vs := request.app.state.vector_store:
        try:
            await vs.delete_dataset(dataset_id)
        except Exception:
            logger.warning("memory delete for dataset %s failed; the purge retries it",
                           dataset_id, exc_info=True)
    return {"deleted": True, "threads": len(threads)}
