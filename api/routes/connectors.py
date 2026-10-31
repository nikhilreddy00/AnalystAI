"""
Connector endpoints — test data source connections and handle CSV file uploads.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from agents.errors import IngestionError
from connectors.csv_connector import _read_bytes, user_upload_dir
from api.auth import get_current_user, UserProfile
from pydantic import BaseModel

from connectors.base import get_connector
from orchestrator.state import ConnectorConfig

router = APIRouter()


class TestConnectionResponse(BaseModel):
    success: bool
    message: str


@router.post("/test", response_model=TestConnectionResponse)
async def test_connection(
    config: ConnectorConfig,
    current_user: UserProfile = Depends(get_current_user)
) -> TestConnectionResponse:
    try:
        connector = get_connector(config)
        success, message = await connector.test_connection()
        return TestConnectionResponse(success=success, message=message)
    except Exception as e:
        return TestConnectionResponse(success=False, message=str(e))


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
    # PROMPT-8: accept text formats (CSV, TSV, JSON) and binary formats
    # (Parquet, Excel) the UI now advertises.
    allowed = {".csv", ".xlsx", ".xls", ".tsv", ".json", ".parquet"}
    # Strip any path components from the attacker-supplied filename before reading the suffix
    safe_name = Path(file.filename or "").name
    suffix = Path(safe_name).suffix.lower()
    if suffix not in allowed:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {suffix}")

    # Chunked reading with early exit to prevent OOM on large uploads.
    # UploadFile is not async-iterable; read in fixed-size chunks instead.
    max_size = 50 * 1024 * 1024  # 50 MB
    chunk_size = 1024 * 1024     # 1 MB per read
    chunks: list[bytes] = []
    total = 0
    hasher = hashlib.sha256()
    while True:
        chunk = await file.read(chunk_size)
        if not chunk:
            break
        total += len(chunk)
        if total > max_size:
            raise HTTPException(status_code=413, detail="File too large (max 50 MB)")
        hasher.update(chunk)
        chunks.append(chunk)
    content = b"".join(chunks)
    file_hash = hasher.hexdigest()
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
        "filename": file.filename,
        "dataset": ds.public(),
        "dataset_id": ds.id,
        "dataset_version_id": version.id,
        "version": version.version,
        "dataset_name": ds.name,
        "file_hash": file_hash,
    }
