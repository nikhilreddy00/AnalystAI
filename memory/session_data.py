"""Filesystem layer for per-session binary artifacts (the cleaned DataFrame).

Metadata + the full AnalysisSession JSON keeps living in SessionStore.
Only the parquet bytes live here, keyed by session_id under SESSION_DATA_DIR.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from api.config import settings

_DATA_DIR: Path = Path(settings.session_data_dir).resolve()
_PARQUET_NAME = "cleaned_data.parquet"


def session_dir(session_id: str) -> Path:
    d = _DATA_DIR / session_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_cleaned_parquet(session_id: str, df_parquet_bytes: bytes) -> str:
    """Atomic write of parquet bytes for a session. Returns the absolute path."""
    d = session_dir(session_id)
    final = d / _PARQUET_NAME
    tmp = d / f"{_PARQUET_NAME}.tmp"
    tmp.write_bytes(df_parquet_bytes)
    os.replace(tmp, final)
    return str(final.resolve())


def read_cleaned_parquet(session_id: str) -> bytes | None:
    path = _DATA_DIR / session_id / _PARQUET_NAME
    if not path.is_file():
        return None
    return path.read_bytes()


def delete_session_files(session_id: str) -> None:
    """Remove the per-session directory if it exists. Idempotent."""
    d = _DATA_DIR / session_id
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
