"""
File connector: CSV, TSV, Excel (.xlsx), JSON and Parquet uploads.
One reader for every format (`_read_bytes`): encoding, delimiter, report-title rows, decimal comma
and total footers are detected, and how the file was read is kept on `read_info` for the source
evidence. A file that can't be read as a table raises IngestionError, never loads silently wrong.
"""

from __future__ import annotations

import csv
import io
import re
from collections import Counter
from pathlib import Path

import chardet
import pandas as pd

from agents.errors import IngestionError
from connectors.base import DataConnector, parse_text_dates

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


_DELIMITERS = (",", ";", "\t", "|")
_EU_NUMBER_RE = re.compile(r"^-?\d{1,3}(?:\.\d{3})+,\d+$|^-?\d+,\d+$")
_US_DECIMAL_RE = re.compile(r"^-?\d+\.\d+$")
_FOOTER_RE = re.compile(r"^\s*(?:grand\s+)?totals?\b", re.I)
_SAMPLE_LINES = 200


def _decode(content: bytes) -> tuple[str, str]:
    """(text, encoding). UTF-8 first (a BOM is dropped), UTF-16 by its BOM, then chardet's
    guess, then cp1252, which reads every Western export."""
    if not content.strip():
        raise IngestionError("empty file", "The file is empty.")
    if content[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return content.decode("utf-16"), "utf-16"
    if b"\x00" in content[:4096]:
        raise IngestionError("binary content", _NOT_A_TABLE)
    try:
        return content.decode("utf-8-sig"), "utf-8"
    except UnicodeDecodeError:
        pass
    guess = chardet.detect(content[:50_000])["encoding"]
    for enc in (guess, "cp1252", "latin-1"):
        try:
            text = content.decode(enc) if enc else None
        except (UnicodeDecodeError, LookupError):
            continue
        if text is not None:
            return text, enc.lower()
    raise IngestionError("undecodable", _NOT_A_TABLE)


_NOT_A_TABLE = ("This file can't be read as a table. Upload a CSV, TSV, Excel (.xlsx), JSON or "
                "Parquet file.")


def _layout(text: str) -> tuple[str, int, str]:
    """(delimiter, header line index, decimal mark) from the first lines. The delimiter is the
    one that splits most lines into the same number of fields; the header is the first line with
    that many (report titles above it have one); a decimal comma is assumed when the delimiter
    isn't a comma and comma-decimals outnumber dot-decimals."""
    lines = text.splitlines()[:_SAMPLE_LINES]
    best: tuple = (False, 0.0, 0)
    sep, rows = ",", []
    for d in _DELIMITERS:
        split = [next(csv.reader([ln], delimiter=d)) if ln.strip() else [] for ln in lines]
        counts = [len(r) for r in split if r]
        if not counts:
            continue
        width = Counter(counts).most_common(1)[0][0]
        score = (width > 1, round(sum(c == width for c in counts) / len(counts), 2), width)
        if score > best:
            best, sep, rows = score, d, split
    width = best[2]
    header = next((i for i, r in enumerate(rows) if len(r) == width), 0)
    cells = [c.strip() for r in rows[header + 1:] for c in r]
    eu = sum(bool(_EU_NUMBER_RE.match(c)) for c in cells)
    us = sum(bool(_US_DECIMAL_RE.match(c)) for c in cells)
    return sep, header, "," if sep != "," and eu > us else "."


def _read_text_table(content: bytes, suffix: str) -> tuple[pd.DataFrame, dict]:
    text, encoding = _decode(content)
    if sum(ch < " " and ch not in "\t\r\n" for ch in text[:4096]) > 40:   # control bytes: not text
        raise IngestionError("binary content", _NOT_A_TABLE)
    sep, header, decimal = _layout(text)
    if suffix == ".tsv" and sep != "\t" and "\t" in text[:4096]:
        sep = "\t"
    try:
        df = pd.read_csv(io.StringIO(text), sep=sep, skiprows=header, decimal=decimal,
                         low_memory=False)
    except pd.errors.ParserError as exc:
        raise IngestionError(f"parse error: {exc}",
                             "The file's rows don't line up into columns. Check that it is a plain "
                             "table with one header row.") from exc
    except pd.errors.EmptyDataError as exc:
        raise IngestionError("no columns", "The file is empty.") from exc
    info = {"format": suffix.lstrip("."), "encoding": encoding, "delimiter": sep,
            "decimal": decimal, "header_line": header + 1, "notes": []}
    if header:
        info["notes"].append(f"Skipped {header} line{'s' * (header != 1)} above the header "
                             "(a report title).")
    if decimal == ",":   # "1.249,00": a thousands dot read_csv's decimal mark can't take
        for col in df.columns:
            s = df[col]
            if pd.api.types.is_string_dtype(s) and s.dropna().astype(str).str.match(_EU_NUMBER_RE).all() \
                    and s.notna().any():
                df[col] = pd.to_numeric(s.str.replace(".", "", regex=False)
                                        .str.replace(",", ".", regex=False))
    if len(df) and isinstance(df.iloc[-1, 0], str) and _FOOTER_RE.match(df.iloc[-1, 0]) \
            and df.iloc[-1, 1:].isna().mean() >= 0.5:
        df = df.iloc[:-1]
        info["notes"].append("Dropped the last row: a report total, not a record.")
    return df, info


def _read_bytes(content: bytes, suffix: str) -> tuple[pd.DataFrame, dict]:
    """The one reader behind every upload: (frame, how it was read). Unreadable input raises
    IngestionError with a message for the user; nothing loads silently wrong."""
    if suffix in (".xlsx", ".xls"):
        if suffix == ".xls":
            raise IngestionError("xls needs xlrd", "Old .xls workbooks aren't supported. Save the "
                                 "file as .xlsx (Excel → Save As) and upload it again.")
        try:
            sheets = pd.read_excel(io.BytesIO(content), sheet_name=None, engine="openpyxl")
        except Exception as exc:   # zip/openpyxl errors: not a workbook
            raise IngestionError(f"excel: {type(exc).__name__}", _NOT_A_TABLE) from exc
        sheets = {k: v for k, v in sheets.items() if not v.empty}
        if not sheets:
            raise IngestionError("no rows", "The workbook has no data.")
        name = max(sheets, key=lambda k: sheets[k].size)
        info = {"format": "xlsx", "sheets": len(sheets), "notes": []}
        if len(sheets) > 1:
            info["notes"].append(f"Loaded the largest of {len(sheets)} sheets; the others were "
                                 "ignored.")
        df = sheets[name]
    elif suffix == ".parquet":
        df, info = pd.read_parquet(io.BytesIO(content)), {"format": "parquet", "notes": []}
    elif suffix == ".json":
        try:
            df = pd.read_json(io.BytesIO(content))
        except ValueError:
            df = pd.read_json(io.BytesIO(content), lines=True)
        info = {"format": "json", "notes": []}
    else:
        df, info = _read_text_table(content, suffix)
    if df.shape[1] == 1 and any(d in str(df.columns[0]) for d in _DELIMITERS if d != " "):
        raise IngestionError("one column", "All the data landed in one column. Check the file's "
                             "delimiter and upload it again.")
    if df.empty:
        raise IngestionError("no rows", "The file has column headers but no data rows.")
    for col in df.columns:
        parsed = parse_text_dates(df[col])
        if parsed is not None:
            df[col] = parsed
    return df, info


class CSVConnector(DataConnector):
    async def load(self) -> pd.DataFrame:
        raw_path = self.config.file_path
        if not raw_path:
            raise ValueError("file_path is required for CSV connector")
        path = _validate_upload_path(raw_path)
        df, self.read_info = _read_bytes(path.read_bytes(), path.suffix.lower())
        return df

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
