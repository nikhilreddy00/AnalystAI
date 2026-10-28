"""
Connector endpoints — test data source connections and handle CSV file uploads.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, File, HTTPException, Request, UploadFile, Depends
from connectors.csv_connector import user_upload_dir
from api.auth import get_current_user, UserProfile
from pydantic import BaseModel

from connectors.base import get_connector
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
    current_user: UserProfile = Depends(get_current_user)
) -> dict:
    """
    Accept a CSV or Excel file upload.
    Saves it to the uploads/ directory and returns a ConnectorConfig ready to use.
    """
    # PROMPT-8: accept text formats (CSV, TSV, JSON) and binary formats
    # (Parquet, Excel) the UI now advertises.
    allowed = {".csv", ".xlsx", ".xls", ".tsv", ".json", ".parquet"}
    # Strip any path components from the attacker-supplied filename before reading the suffix
    from pathlib import Path
    safe_name = Path(file.filename or "").name
    suffix = Path(safe_name).suffix.lower()
    if suffix not in allowed:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {suffix}")

    # Chunked reading with early exit to prevent OOM on large uploads.
    # UploadFile is not async-iterable; read in fixed-size chunks instead.
    import hashlib

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

    # Use a stable absolute path anchored to the repo (not CWD) so all
    # workers/processes agree on the uploads location and the connector's
    # path-traversal guard matches the same directory.
    upload_dir = user_upload_dir(current_user.uid)
    upload_dir.mkdir(parents=True, exist_ok=True)
    save_path = str(upload_dir / f"{uuid.uuid4()}{suffix}")
    with open(save_path, "wb") as f:
        f.write(content)

    config = ConnectorConfig(type=ConnectorType.CSV, file_path=save_path)
    dataset_name = Path(safe_name).stem or "dataset"

    existing_session = None
    store = request.app.state.session_store
    if store:
        try:
            hit = await store.find_by_hash(current_user.uid, file_hash)
            if hit:
                existing_session = {
                    "session_id": hit.session_id,
                    "dataset_name": hit.dataset_name,
                    "updated_at": hit.updated_at.isoformat(),
                    "original_query": hit.original_query,
                }
        except Exception:
            # Dedup is best-effort — never block an upload on lookup failure.
            existing_session = None

    return {
        "status": "uploaded",
        "filename": file.filename,
        "file_path": save_path,
        "connector_config": config.model_dump(),
        "file_hash": file_hash,
        "dataset_name": dataset_name,
        "existing_session": existing_session,
    }
