"""SaaS imports (5.8c, ADR-0010 amendment): connect a vendor account with its key, get the latest
data on demand. The key is tested before it is saved (each table's endpoint read once, so a
missing permission is named), stored encrypted, and never sent back. A refresh runs as a task the
page polls; one at a time per import, and not more often than the vendor's read allowance takes
(memory/session_store.py: claim_verdict)."""
from __future__ import annotations

import asyncio
import datetime as dt
import io
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from agents.errors import IngestionError
from api.auth import UserProfile, get_current_user
from api.rate_limit import limiter
from api.routes.connectors import readable_file
from connectors import imports as imp
from connectors.base import _SCOPE, credential_scope, register_secrets

router = APIRouter()
log = logging.getLogger(__name__)

_BUSY = {
    "running": "A refresh is already running. It shows here when it ends.",
    "too_soon": "The data was updated less than 30 minutes ago. Try again a little later.",
    "too_many": "Three refreshes were tried in the last hour. Try again in an hour.",
}


class ImportKey(BaseModel):
    vendor: str = Field(max_length=32)
    key: str = Field(min_length=1, max_length=4096)
    shop: str | None = Field(default=None, max_length=255)


class Window(BaseModel):
    last_12_months: bool


def _secrets(body: ImportKey) -> dict:
    return {"key": body.key.strip(), **({"shop": body.shop.strip()} if body.shop else {})}


def _vendor(name: str) -> imp.Vendor:
    if name not in imp.VENDORS:
        raise HTTPException(status_code=404, detail="Unknown source.")
    return imp.VENDORS[name]


async def _tested(vendor: imp.Vendor, secrets: dict) -> tuple[str, bool]:
    """(account, test mode), or a 400 in our words. The key masks this request's own lines."""
    register_secrets(*imp.secret_values(secrets))
    try:
        vendor.check(secrets)
        async with vendor.client(secrets) as client:
            return await vendor.test(client, secrets)
    except imp.ImportFailure as f:
        raise HTTPException(status_code=400, detail=imp.said(vendor.name, f.public())) from None


def _public(rec, request: Request) -> dict:
    now = dt.datetime.now(dt.timezone.utc)
    out = rec.public(now)
    out["label"] = imp.VENDORS[rec.vendor].label if rec.vendor in imp.VENDORS else rec.vendor
    out["tables"] = [{"name": t.name, "label": t.label, "dataset_id": rec.tables.get(t.name),
                      **out["status"].get(t.name, {}),
                      "said": imp.said(rec.vendor, out["status"][t.name]["error"])
                      if out["status"].get(t.name, {}).get("error") else None}
                     for t in imp.VENDORS[rec.vendor].tables]
    if out["interrupted"]:
        out["said"] = imp.said(rec.vendor, {"code": "interrupted"})
    out.pop("status")
    return out


def _file_table(store):
    async def file_table(uid: str, dataset_id: str, frame) -> str:
        """A table's frame to a version of its dataset: written as parquet, read back by the one
        reader (as an upload's bytes), 'unchanged' when equal to the latest."""
        buf = io.BytesIO()
        frame.to_parquet(buf, index=False)
        try:
            digest, df, write_file = await readable_file(buf.getvalue(), ".parquet", uid)
        except IngestionError:
            raise imp.ImportFailure("unexpected") from None
        placed = await store.add_import_version(uid, dataset_id, content_hash=digest,
                                                columns=[str(c) for c in df.columns],
                                                rows=len(df), write_file=write_file)
        if placed is None:   # the dataset was deleted meanwhile
            raise imp.ImportFailure("unexpected")
        return placed[1]
    return file_table


def _start(request: Request, rec, token: str) -> None:
    """The refresh as a task of this process, kept referenced until it ends (an unreferenced
    task can be collected mid-run: red-team 7); its own credential scope (imports.refresh)."""
    store = request.app.state.session_store
    tasks = request.app.state.__dict__.setdefault("import_tasks", set())
    scope = credential_scope()   # a fresh registry the task copies: never this request's
    try:
        task = asyncio.create_task(imp.refresh(store, rec, token, _file_table(store)))
    finally:
        _SCOPE.reset(scope)
    tasks.add(task)
    task.add_done_callback(tasks.discard)


@router.post("", status_code=201)
@limiter.limit("10/minute")
async def create_import(request: Request, body: ImportKey,
                        current_user: UserProfile = Depends(get_current_user)) -> dict:
    vendor = _vendor(body.vendor)
    secrets = _secrets(body)
    account, test_mode = await _tested(vendor, secrets)
    store = request.app.state.session_store
    rec = await store.create_import(current_user.uid, vendor=vendor.name, account=account,
                                    test_mode=test_mode, secrets=secrets,
                                    tables={t.name: t.label for t in vendor.tables})
    token = imp.new_token()
    await store.claim_refresh(rec.id, current_user.uid, token, dt.datetime.now(dt.timezone.utc))
    _start(request, await store.get_import(rec.id, current_user.uid), token)
    return _public(await store.get_import(rec.id, current_user.uid), request)


@router.get("")
async def list_imports(request: Request,
                       current_user: UserProfile = Depends(get_current_user)) -> list[dict]:
    return [_public(r, request) for r in
            await request.app.state.session_store.list_imports(current_user.uid)]


async def _own(request: Request, import_id: str, uid: str):
    rec = await request.app.state.session_store.get_import(import_id, uid)
    if rec is None:
        raise HTTPException(status_code=404, detail="Import not found.")
    return rec


@router.get("/{import_id}")
async def get_import(import_id: str, request: Request,
                     current_user: UserProfile = Depends(get_current_user)) -> dict:
    return _public(await _own(request, import_id, current_user.uid), request)


@router.post("/{import_id}/refresh", status_code=202)
async def refresh_import(import_id: str, request: Request,
                         current_user: UserProfile = Depends(get_current_user)) -> dict:
    store = request.app.state.session_store
    await _own(request, import_id, current_user.uid)
    token = imp.new_token()
    verdict = await store.claim_refresh(import_id, current_user.uid, token,
                                        dt.datetime.now(dt.timezone.utc))
    if verdict != "claimed":
        raise HTTPException(status_code=409, detail=_BUSY.get(verdict, "Try again later."))
    _start(request, await store.get_import(import_id, current_user.uid), token)
    return _public(await store.get_import(import_id, current_user.uid), request)


@router.put("/{import_id}/key")
@limiter.limit("10/minute")
async def replace_key(import_id: str, request: Request, body: ImportKey,
                      current_user: UserProfile = Depends(get_current_user)) -> dict:
    rec = await _own(request, import_id, current_user.uid)
    if body.vendor != rec.vendor:
        raise HTTPException(status_code=400, detail="That key is for another source.")
    secrets = _secrets(body)
    account, test_mode = await _tested(imp.VENDORS[rec.vendor], secrets)
    await request.app.state.session_store.replace_import_key(
        import_id, current_user.uid, secrets=secrets, account=account, test_mode=test_mode)
    return _public(await _own(request, import_id, current_user.uid), request)


@router.put("/{import_id}/window")
async def set_window(import_id: str, body: Window, request: Request,
                     current_user: UserProfile = Depends(get_current_user)) -> dict:
    await _own(request, import_id, current_user.uid)
    since = (dt.date.today() - dt.timedelta(days=365)).isoformat() if body.last_12_months else None
    await request.app.state.session_store.set_import_window(import_id, current_user.uid, since)
    return _public(await _own(request, import_id, current_user.uid), request)


@router.delete("/{import_id}")
async def delete_import(import_id: str, request: Request, tables: bool = True,
                        current_user: UserProfile = Depends(get_current_user)) -> dict:
    if not await request.app.state.session_store.delete_import(import_id, current_user.uid,
                                                               with_tables=tables):
        raise HTTPException(status_code=404, detail="Import not found.")
    return {"deleted": import_id, "tables_deleted": tables}
