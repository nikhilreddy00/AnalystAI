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
# Unambiguous comma-decimals only: "1,234" is a US thousand, so 3 digits after a lone comma
# don't count.
_EU_NUMBER_RE = re.compile(r"^-?\d{1,3}(?:\.\d{3})+,\d+$|^-?\d+,\d{1,2}$")
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
    """(delimiter, header line index, decimal mark) from the first lines, parsed as CSV (quoted
    fields may span lines). The delimiter splits most rows into the same number of fields. The
    header is the first row unless it is narrower than 3/4 of the table: then report titles sit
    above it ("Acme, Inc." splits into 2 fields of 8). A decimal comma is assumed when the delimiter isn't a
    comma and unambiguous comma-decimals ("129,90", "1.249,00") outnumber dot-decimals."""
    sample = "\n".join(text.splitlines()[:_SAMPLE_LINES])
    best: tuple = (False, 0.0, 0)
    sep, rows = ",", []
    for d in _DELIMITERS:
        reader = csv.reader(io.StringIO(sample), delimiter=d)
        parsed = []
        try:
            for r in reader:
                parsed.append((reader.line_num, r))   # physical lines read so far
        except csv.Error:
            continue
        counts = [len(r) for _, r in parsed if r]
        if not counts:
            continue
        width = Counter(counts).most_common(1)[0][0]
        score = (width > 1, round(sum(c == width for c in counts) / len(counts), 2), width)
        if score > best:
            best, sep, rows = score, d, parsed
    width = best[2]
    filled = [(end, sum(bool(c.strip()) for c in r), r) for end, r in rows]
    first = next((i for i, (_, n, _) in enumerate(filled) if n), 0)
    header = first
    if filled and filled[first][1] < 0.75 * width:   # narrower than the table: a report title
        header = next((i for i, (_, n, _) in enumerate(filled) if n >= 0.75 * width), first)
    # Lines to skip: those read before the header row (a quoted row may span several).
    line = filled[header - 1][0] if header else 0
    cells = [c.strip() for _, _, r in filled[header + 1:] for c in r]
    eu = sum(bool(_EU_NUMBER_RE.match(c)) for c in cells)
    us = sum(bool(_US_DECIMAL_RE.match(c)) for c in cells)
    return sep, line, "," if sep != "," and eu > us else "."


def _read_text_table(content: bytes, suffix: str) -> tuple[pd.DataFrame, dict]:
    text, encoding = _decode(content)
    if sum(ch < " " and ch not in "\t\r\n" for ch in text[:4096]) > 40:   # control bytes: not text
        raise IngestionError("binary content", _NOT_A_TABLE)
    sep, header, decimal = _layout(text)
    if suffix == ".tsv" and sep != "\t" and "\t" in text[:4096]:
        sep = "\t"
    try:
        # index_col=False: a trailing delimiter must not turn the first column into the index.
        df = pd.read_csv(io.StringIO(text), sep=sep, skiprows=header, decimal=decimal,
                         index_col=False, low_memory=False)
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
        df = df.iloc[:-1].copy()
        for col in df.select_dtypes("float").columns:   # the total's blanks made ints float
            if df[col].notna().all() and (df[col] % 1 == 0).all():
                df[col] = df[col].astype("int64")
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
        try:
            df = pd.read_parquet(io.BytesIO(content))
        except Exception as exc:   # pyarrow raises its own types for a corrupt file
            raise IngestionError(f"parquet: {type(exc).__name__}", _NOT_A_TABLE) from exc
        info = {"format": "parquet", "notes": []}
    elif suffix == ".json":
        try:
            df = pd.read_json(io.BytesIO(content))
        except ValueError:
            try:
                df = pd.read_json(io.BytesIO(content), lines=True)
            except ValueError as exc:
                raise IngestionError("json", "The JSON isn't a list of records (one object per "
                                     "row, or one per line).") from exc
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
