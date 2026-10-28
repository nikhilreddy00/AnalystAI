"""
CSV / Excel connector.
Handles encoding detection, large files (chunked read), and both .csv/.xlsx/.xls.
"""

from __future__ import annotations

import io
import re
from pathlib import Path

import chardet
import pandas as pd

from connectors.base import DataConnector

# Path traversal guard: only files inside <repo>/uploads/ may be loaded.
# Resolved once at import time so symlink games can't bypass the check at request time.
_UPLOADS_DIR = (Path(__file__).resolve().parent.parent / "uploads").resolve()


def user_upload_dir(uid: str) -> Path:
    """Each user's uploads live in uploads/<uid>/ (audit B2: the folder used to be shared)."""
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", str(uid)) or "_"
    return _UPLOADS_DIR / safe


def check_upload_owner(raw_path: str | None, uid: str) -> str | None:
    """None if `raw_path` is inside the caller's upload folder, else the reason it isn't."""
    if not raw_path:
        return "no file_path"
    try:
        Path(raw_path).resolve().relative_to(user_upload_dir(uid).resolve())
    except ValueError:
        return "file is not one of your uploads"
    return None


def _validate_upload_path(raw_path: str) -> Path:
    """Resolve `raw_path` and confirm it lives inside the uploads directory."""
    candidate = Path(raw_path).resolve()
    try:
        candidate.relative_to(_UPLOADS_DIR)
    except ValueError as exc:
        raise ValueError(
            f"file_path must be inside the uploads directory ({_UPLOADS_DIR})."
        ) from exc
    return candidate


class CSVConnector(DataConnector):
    async def load(self) -> pd.DataFrame:
        raw_path = self.config.file_path
        if not raw_path:
            raise ValueError("file_path is required for CSV connector")

        path = str(_validate_upload_path(raw_path))
        suffix = Path(path).suffix.lower()

        if suffix in (".xlsx", ".xls"):
            df = pd.read_excel(path, engine="openpyxl" if suffix == ".xlsx" else "xlrd")
        elif suffix == ".parquet":
            df = pd.read_parquet(path)
        elif suffix == ".json":
            df = self._read_json(path)
        elif suffix == ".tsv":
            encoding = self._detect_encoding(path)
            df = pd.read_csv(path, sep="\t", encoding=encoding, low_memory=False)
        else:
            encoding = self._detect_encoding(path)
            df = pd.read_csv(path, encoding=encoding, low_memory=False)

        df = self._parse_dates(df)
        return df

    async def load_from_bytes(self, content: bytes, filename: str) -> pd.DataFrame:
        """Load from uploaded file bytes (used by the FastAPI upload endpoint)."""
        suffix = Path(filename).suffix.lower()

        if suffix in (".xlsx", ".xls"):
            # BUG-27: Use correct engine for each Excel format
            engine = "openpyxl" if suffix == ".xlsx" else "xlrd"
            df = pd.read_excel(io.BytesIO(content), engine=engine)
        elif suffix == ".parquet":
            df = pd.read_parquet(io.BytesIO(content))
        elif suffix == ".json":
            df = self._read_json_bytes(content)
        elif suffix == ".tsv":
            encoding = chardet.detect(content[:50_000])["encoding"] or "utf-8"
            df = pd.read_csv(io.BytesIO(content), sep="\t", encoding=encoding, low_memory=False)
        else:
            encoding = chardet.detect(content[:50_000])["encoding"] or "utf-8"
            df = pd.read_csv(io.BytesIO(content), encoding=encoding, low_memory=False)

        df = self._parse_dates(df)
        return df

    def _read_json(self, path: str) -> pd.DataFrame:
        """Read JSON arrays-of-objects or newline-delimited JSON. Falls back to
        the line-delimited variant when the top-level parse fails."""
        try:
            return pd.read_json(path)
        except ValueError:
            return pd.read_json(path, lines=True)

    def _read_json_bytes(self, content: bytes) -> pd.DataFrame:
        buf = io.BytesIO(content)
        try:
            return pd.read_json(buf)
        except ValueError:
            buf.seek(0)
            return pd.read_json(buf, lines=True)

    async def test_connection(self) -> tuple[bool, str]:
        raw_path = self.config.file_path
        if not raw_path:
            return False, "file_path not provided"
        try:
            path = _validate_upload_path(raw_path)
        except ValueError as exc:
            return False, str(exc)
        if not path.exists():
            return False, f"File not found: {path}"
        return True, "File accessible"

    def _detect_encoding(self, path: str) -> str:
        with open(path, "rb") as f:
            raw = f.read(50_000)
        result = chardet.detect(raw)
        return result["encoding"] or "utf-8"

    def _parse_dates(self, df: pd.DataFrame) -> pd.DataFrame:
        for col in df.columns:
            # BUG-32: Use endswith('_at') instead of 'at' substring to avoid
            # false positives on 'category', 'platform', 'status', etc.
            if any(kw in col.lower() for kw in ("date", "time", "created", "updated")) or \
               col.lower().endswith("_at") or col.lower() == "at":
                try:
                    df[col] = pd.to_datetime(df[col], format="mixed", errors="coerce")
                except Exception:
                    pass
        return df
