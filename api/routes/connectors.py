"""
Connector endpoints — test data source connections and handle CSV file uploads.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from agents.errors import AgentError, IngestionError
from connectors.csv_connector import (MAX_UPLOAD_MB, UPLOAD_SUFFIXES, XLS_REFUSAL, _read_bytes,
                                      check_upload_owner, user_upload_dir)
from api.auth import get_current_user, UserProfile
from api.rate_limit import limiter
from pydantic import BaseModel, Field

from connectors.base import get_connector, without_secrets
from connectors.link import TooLarge, download
from guardrails import mask_pii
from orchestrator.state import ConnectorConfig, ConnectorType

router = APIRouter()


class TestConnectionResponse(BaseModel):
    success: bool
    message: str


@router.post("/test", response_model=TestConnectionResponse)
async def test_connection(
    config: ConnectorConfig,
    current_user: UserProfile = Depends(get_current_user)
) -> TestConnectionResponse:
    # A file is tested only inside the caller's own upload folder: never say whether a path
    # exists in another user's (5.5 review).
    if config.type == ConnectorType.CSV and check_upload_owner(config.file_path, current_user.uid):
        return TestConnectionResponse(success=False, message="File not found.")
    try:
        connector = get_connector(config)
        success, message = await connector.test_connection()
        return TestConnectionResponse(success=success,
                                      message=mask_pii(without_secrets(message, config)))
    except AgentError as e:   # a refusal written for the user (SQLite in production, a key)
        return TestConnectionResponse(success=False, message=without_secrets(e.user_message, config))
    except Exception as e:
        return TestConnectionResponse(success=False,
                                      message=mask_pii(without_secrets(str(e), config)))


@router.post("/upload")
async def upload_csv(
    request: Request,
    file: UploadFile = File(...),
    dataset_id: str | None = Form(default=None, max_length=64),
    current_user: UserProfile = Depends(get_current_user)
) -> dict:
    """
    Accept a CSV or Excel file upload as a version of a dataset (Phase 5.6b, ADR-0007).
    Same bytes → the existing version (no new file). Same name and columns → a new version.
    `dataset_id` ("Upload new version" on the dataset page) always makes a version of it.
    """
    # Strip any path components from the attacker-supplied filename before reading the suffix
    safe_name = Path(file.filename or "").name
    suffix = Path(safe_name).suffix.lower()
    if suffix not in UPLOAD_SUFFIXES:
        raise HTTPException(status_code=400, detail=XLS_REFUSAL if suffix == ".xls"
                            else f"Unsupported file type: {suffix}")

    # The body is already bounded (api.main's body limit) and spooled to an unnamed temp file by
    # Starlette; one read of at most the cap + 1 byte, never chunks plus their join (5.8c: 2×).
    max_size = MAX_UPLOAD_MB * 1024 * 1024
    content = await file.read(max_size + 1)
    if len(content) > max_size:
        raise HTTPException(status_code=413, detail=f"File too large (max {MAX_UPLOAD_MB} MB)")
    return await _register(request, current_user, content, safe_name, suffix, dataset_id)


class LinkRequest(BaseModel):
    url: str = Field(max_length=4096)   # a link is a new dataset; a new version: upload, or re-paste


@router.post("/link")
@limiter.limit("10/minute")   # each holds up to 50 MB for up to 120 s (code review, audit pass 2)
async def link_file(
    request: Request,
    body: LinkRequest,
    current_user: UserProfile = Depends(get_current_user)
) -> dict:
    """A public share link (Google Sheets/Drive, OneDrive/SharePoint, Dropbox, any https CSV or
    xlsx) as a version of a dataset (5.8b, ADR-0010): downloaded once through the guarded
    client, then exactly an upload of the same bytes. The link isn't kept (it is a credential);
    pasting it again makes a new version when the file changed."""
    try:
        content, suffix, name = await download(body.url)
    except TooLarge as exc:
        raise HTTPException(status_code=413, detail=exc.user_message) from None
    except IngestionError as exc:   # every message names at most the host: never the link
        raise HTTPException(status_code=400, detail=exc.user_message) from None
    return await _register(request, current_user, content, name, suffix, None)


async def _register(request: Request, current_user: UserProfile, content: bytes, filename: str,
                    suffix: str, dataset_id: str | None) -> dict:
    """THE path a file's bytes take to a dataset version, an upload's or a link's: read as
    ingestion will read them (refused now if they aren't a table), hashed, stored, versioned."""
    file_hash = hashlib.sha256(content).hexdigest()
    # a name only ever names the dataset (the file is stored as <uuid><suffix>): its base name,
    # whoever wrote it (the browser's, or a link server's Content-Disposition, '\' included)
    safe_name = Path(filename.replace("\\", "/")).name
    filename = safe_name
    try:   # the same reader ingestion uses: a file that isn't a table is refused now, not later
        df, _ = await asyncio.to_thread(_read_bytes, content, suffix)
    except IngestionError as exc:
        raise HTTPException(status_code=400, detail=exc.user_message) from exc

    def write_file() -> str:
        # Use a stable absolute path anchored to the repo (not CWD) so all
        # workers/processes agree on the uploads location and the connector's
        # path-traversal guard matches the same directory.
        upload_dir = user_upload_dir(current_user.uid)
        upload_dir.mkdir(parents=True, exist_ok=True)
        save_path = upload_dir / f"{uuid.uuid4()}{suffix}"
        save_path.write_bytes(content)
        return str(save_path)

    placed = await request.app.state.session_store.register_upload(
        current_user.uid, name=Path(safe_name).stem or "dataset", content_hash=file_hash,
        columns=[str(c) for c in df.columns], rows=len(df), write_file=write_file,
        dataset_id=dataset_id)
    if placed is None:
        raise HTTPException(status_code=404, detail="Dataset not found")
    ds, version, status = placed
    return {
        "status": status,   # existing | new_version | new_dataset
        "filename": filename,
        "dataset": ds.public(),
        "dataset_id": ds.id,
        "dataset_version_id": version.id,
        "version": version.version,
        "dataset_name": ds.name,
        "file_hash": file_hash,
    }
