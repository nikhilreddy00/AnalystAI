"""
File connector: CSV, TSV, Excel (.xlsx), JSON and Parquet uploads.
One reader for every format (`_read_bytes`): encoding, delimiter, report-title rows, decimal comma
and total footers are detected, and how the file was read is kept on `read_info` for the source
evidence. A file that can't be read as a table raises IngestionError, never loads silently wrong.
"""

from __future__ import annotations

import csv
import asyncio
import io
import json
import re
import zipfile
from collections import Counter
from pathlib import Path

import chardet
import pandas as pd

from agents.errors import IngestionError
from connectors.base import DataConnector, exact_whole_numbers, normalize_frame

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
# A padded quantity ("050" units) is a number, never a code: the leading-zero rule skips it.
_MEASURE_HEADER_RE = re.compile(r"(?:^|[^a-z])(?:qty|quantity|units?|amount|count|total|price|cost|"
                                r"score|weight|hours|days|minutes|sales|revenue)(?:$|[^a-z])", re.I)
_CODE_HEADER_RE = re.compile(r"(?:^|[^a-z])(?:code|id|no|nr|ref|key)$", re.I)   # unit_code "007"
_SAMPLE_LINES = 200
_SAMPLE_LINE_CHARS = 1_000_000   # 10,000 columns of 100 characters


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


UPLOAD_SUFFIXES = {".csv", ".xlsx", ".tsv", ".json", ".jsonl", ".parquet"}   # .xls: XLS_REFUSAL
MAX_UPLOAD_MB = 50
XLS_REFUSAL = ("Old .xls workbooks aren't supported. Save the file as .xlsx (Excel → Save As) "
               "and upload it again.")
_NOT_A_TABLE = ("This file can't be read as a table. Upload a CSV, TSV, Excel (.xlsx), JSON or "
                "Parquet file.")


def _layout(text: str) -> tuple[str, int, str]:
    """(delimiter, header line index, decimal mark) from the first lines, parsed as CSV (quoted
    fields may span lines). The delimiter splits most rows into the same number of fields. The
    header is the first row unless it is narrower than 3/4 of the table: then report titles sit
    above it ("Acme, Inc." splits into 2 fields of 8). A decimal comma is assumed when the delimiter isn't a
    comma and unambiguous comma-decimals ("129,90", "1.249,00") outnumber dot-decimals."""
    # each sampled line cut at a width no real table reaches: csv.reader builds every field of a
    # row, five times (one per delimiter), so a 50 MB line was 25M strings before any check
    # (automated security review of the hardening push)
    sample = "\n".join(line[:_SAMPLE_LINE_CHARS] for line in text.splitlines()[:_SAMPLE_LINES])
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


_LINE_END = re.compile(r"\r\n|\n|\r")


_QUOTED_FIELD = re.compile(r'"(?:[^"]|"")*"')


def _record_fields(text: str, sep: str, pos: int) -> tuple[int, int]:
    """(how many fields the record at `pos` has, where the next record starts), field by field
    as pandas reads them, stopping one past the cap: no field is built, so a 50 MB row costs a
    scan, never its millions of strings. A quote opens a quoted field only at the field's start
    (mid-field it is a character: `a"b,c` is two fields), and a quoted field, which may hold the
    delimiter or a newline, ends at its closing quote; an unclosed one is one field to the end,
    as pandas reads it (automated reviews of the hardening: a free-floating quote token let
    `a"b,…` count as one field while pandas built millions)."""
    rest = re.compile(rf"[^{re.escape(sep)}\r\n]*")
    fields = 1
    while fields <= _MAX_COLUMNS:
        if text.startswith('"', pos):
            if not (m := _QUOTED_FIELD.match(text, pos)):
                return fields, len(text)
            pos = m.end()
        pos = rest.match(text, pos).end()
        if not text.startswith(sep, pos):
            end = _LINE_END.match(text, pos)
            return fields, end.end() if end else len(text)
        fields, pos = fields + 1, pos + len(sep)
    return fields, len(text)


def _header_fields(text: str, sep: str, skip: int) -> int:
    """How many fields the header row has, past `skip` lines (see `_record_fields`)."""
    pos = 0
    for _ in range(skip):
        if not (end := _LINE_END.search(text, pos)):
            return 0
        pos = end.end()
    return _record_fields(text, sep, pos)[0]


def _widest_record(text: str, sep: str) -> int:
    """The most fields any record has, stopping past the cap; only scanned when a raw line holds
    the cap's worth of delimiters (a cheap count first). pandas tokenizes every field of a row
    wider than its header and then drops the extra ones: 2.5 s a MB, so one 50 MB line of commas
    under a two-column header held a worker for minutes (found in the hardening's own probe; the
    deleted cheap gate had refused it, along with narrow files holding a long quoted cell)."""
    # lines as pandas ends them: str.splitlines also splits at \x1c, \x0c, U+2028…, which pandas
    # reads inside a row, so a wide row could hide its width from this count (automated review)
    if max((line.count(sep) for line in _LINE_END.split(text)), default=0) < _MAX_COLUMNS:
        return 0
    widest, pos = 0, 0
    while pos < len(text) and widest <= _MAX_COLUMNS:
        fields, pos = _record_fields(text, sep, pos)
        widest = max(widest, fields)
    return widest


def _read_text_table(content: bytes, suffix: str) -> tuple[pd.DataFrame, dict]:
    text, encoding = _decode(content)
    if sum(ch < " " and ch not in "\t\r\n" for ch in text[:4096]) > 40:   # control bytes: not text
        raise IngestionError("binary content", _NOT_A_TABLE)
    sep, header, decimal = _layout(text)
    if suffix == ".tsv" and sep != "\t" and "\t" in text[:4096]:
        sep = "\t"
    # the header the read below will take (its delimiter, past its preamble, quotes honoured),
    # counted first, stopping at the cap: millions of columns would exhaust memory in the full
    # read (5.8b), and a header read with pandas built them all before the count (hardening)
    if _header_fields(text, sep, header) > _MAX_COLUMNS or _widest_record(text, sep) > _MAX_COLUMNS:
        raise IngestionError("too many columns", f"The file has more than {_MAX_COLUMNS:,} "
                             "columns, past what can be read.")
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
    # Codes written with a leading zero (ZIP 02134, account 00731) are text: read as numbers they
    # lose the zero for good; and long whole numbers (ids past 2**53) a blank turns into rounded
    # floats. Only when the file has either, re-read the columns as text.
    long_digits = re.search(r"\d{16,}", text) is not None
    if long_digits or re.search(rf"(?:^|{re.escape(sep)})\s*\"?0\d", text, re.M):
        try:
            as_text = pd.read_csv(io.StringIO(text), sep=sep, skiprows=header, index_col=False,
                                  dtype=str, low_memory=False)
        except (pd.errors.ParserError, ValueError):   # the first read worked: keep its types
            as_text = pd.DataFrame(columns=df.columns)
        def code_like(t: pd.Series) -> bool:   # 00731, 02134: one width, 1 in 10 zero-padded
            t = t.dropna().str.strip()
            if not (len(t) > 0 and t.str.fullmatch(r"\d+").all() and t.str.len().nunique() == 1
                    and t.str.match(r"0\d").mean() >= 0.1):
                return False
            # 01..12 months, 00..31 days and 0930 / 1400 times are numbers written padded
            return not (t.str.len().iloc[0] == 2 and t.astype(int).max() <= 31) and not (
                t.str.len().iloc[0] == 4 and (t.astype(int) % 100 < 60).all()
                and (t.astype(int) // 100 < 24).all())
        zeros = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])
                 and not (_MEASURE_HEADER_RE.search(str(c)) and not _CODE_HEADER_RE.search(str(c)))
                 and code_like(as_text[c])]
        for col in zeros:
            df[col] = as_text[col].str.strip()
        if zeros:
            info["notes"].append(f"Kept as text (leading zeros): {', '.join(map(str, zeros[:5]))}.")
        for col in df.columns if long_digits else ():
            if col not in zeros and not pd.api.types.is_integer_dtype(df[col]) and \
                    (exact := exact_whole_numbers(as_text[col])) is not None:
                df[col] = exact
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


# What a file may expand to when read (5.8b: uploads and share links): a workbook is a zip that
# openpyxl unpacks whole, so a 50 MB upload could declare gigabytes; a CSV header could name
# millions of columns. zipfile never unpacks a part past its declared size, so the declared
# sizes are a real bound.
# Unpacked: 20 times the upload cap (sheet XML packs 5 to 10 times: a real 50 MB workbook fits;
# 5.8b review: 150 MB refused 20-30 MB workbooks); a bomb packs a thousand times
_MAX_UNPACKED, _MAX_PARTS, _MAX_COLUMNS = 20 * MAX_UPLOAD_MB * 2**20, 10_000, 10_000
_MAX_RATIO, _RATIO_FROM = 200, 10 * 2**20   # a part over 10 MB packed more than 200 times


def _readable_size(content: bytes, suffix: str) -> None:
    if suffix == ".xlsx":
        try:
            parts = zipfile.ZipFile(io.BytesIO(content)).infolist()
        # a broken file's every way of failing to open (5.8b audit: a part's version raised
        # NotImplementedError past the check: a 500 for the user's file)
        except (zipfile.BadZipFile, NotImplementedError, ValueError, OSError, EOFError) as exc:
            raise IngestionError(f"excel: {type(exc).__name__}", _NOT_A_TABLE) from exc
        # the total sized to real workbooks, and the packing of each large part and of the whole
        # to real XML's (5-10x; a bomb packs ~1000x: 5.8b security review of the raised total;
        # the whole too: a bomb spread over parts under 10 MB each, automated review of the push)
        unpacked, packed = (sum(p.file_size for p in parts), sum(p.compress_size for p in parts))
        if unpacked > _MAX_UNPACKED or any(
                p.file_size > _RATIO_FROM and p.file_size > _MAX_RATIO * max(p.compress_size, 1)
                for p in parts) or (unpacked > _RATIO_FROM and unpacked > _MAX_RATIO * max(packed, 1)):
            raise IngestionError("workbook too large", "The workbook unpacks to more than "
                                 f"{_MAX_UNPACKED // 2**20} MB, past what can be read.")
        if len(parts) > _MAX_PARTS or any(p.filename.startswith("/") or ".." in p.filename
                                          for p in parts):
            raise IngestionError("workbook parts", "The workbook's parts aren't an Excel "
                                 "workbook's. Save it again from Excel and upload that.")
    # A CSV's width is checked once, exactly, as the reader takes its header (_read_text_table).
    # 5.8b's cheap per-line gate counted raw delimiters, quotes included, and refused a two-column
    # file with one long quoted cell (senior audit); without it a 5M-comma line is still refused
    # in 0.3 s (tests/test_share_links.py).


def _read_bytes(content: bytes, suffix: str) -> tuple[pd.DataFrame, dict]:
    """The one reader behind every upload: (frame, how it was read). Unreadable input raises
    IngestionError with a message for the user; nothing loads silently wrong."""
    _readable_size(content, suffix)
    if suffix in (".xlsx", ".xls"):
        if suffix == ".xls":
            raise IngestionError("xls needs xlrd", XLS_REFUSAL)
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
    elif suffix in (".json", ".jsonl"):
        content = content.removeprefix(b"\xef\xbb\xbf")   # a byte-order mark (Windows tools write one)
        try:
            # dtype=False: values keep the type they were written with ("02134" stays text).
            # precise_float: the fast parser reads 2.76 as 2.7600000000000002 (5.8a source oracle)
            df = pd.read_json(io.BytesIO(content), dtype=False, precise_float=True)
        except ValueError:
            try:
                df = pd.read_json(io.BytesIO(content), lines=True, dtype=False, precise_float=True)
            except ValueError as exc:
                raise IngestionError("json", "The JSON isn't a list of records (one object per "
                                     "row, or one per line).") from exc
        info = {"format": "json", "notes": []}
        # Nested records ({"holder": {"name": ...}}) become columns (holder_name), so every value
        # is profiled and masked like any other; lists become JSON text.
        if any(df[c].map(lambda v: isinstance(v, dict)).any() for c in df.columns):
            df = pd.json_normalize(df.to_dict(orient="records"), sep="_")
            info["notes"].append("Nested fields were flattened into columns (a_b for a.b).")
        for c in df.columns:
            if df[c].map(lambda v: isinstance(v, list)).any():
                df[c] = df[c].map(lambda v: json.dumps(v, default=str) if isinstance(v, list) else v)
    else:
        df, info = _read_text_table(content, suffix)
    if df.shape[1] == 1 and any(d in str(df.columns[0]) for d in _DELIMITERS if d != " "):
        raise IngestionError("one column", "All the data landed in one column. Check the file's "
                             "delimiter and upload it again.")
    # one column read, but a line holds a table past the cap (a report title above it threw the
    # layout off): said as too wide, never junk text. Only when the read came back one column: a
    # count of raw delimiters elsewhere refused narrow files with a long quoted cell (senior audit)
    if df.shape[1] == 1 and suffix in (".csv", ".tsv") and any(
            line.count(d) >= _MAX_COLUMNS for line in content[:4 * 2**20].splitlines()[:_SAMPLE_LINES]
            for d in (b",", b";", b"\t", b"|")):
        raise IngestionError("too many columns", f"The file has more than {_MAX_COLUMNS:,} "
                             "columns, past what can be read.")
    if df.empty:
        raise IngestionError("no rows", "The file has column headers but no data rows.")
    return normalize_frame(df), info


class CSVConnector(DataConnector):
    async def _read(self) -> pd.DataFrame:
        raw_path = self.config.file_path
        if not raw_path:
            raise ValueError("file_path is required for CSV connector")
        path = _validate_upload_path(raw_path)
        try:
            content = await asyncio.to_thread(path.read_bytes)
        except FileNotFoundError as exc:   # purged, or the disk was replaced
            raise IngestionError("missing upload", "The file behind this dataset is no longer "
                                 "available. Upload it again to continue.") from exc
        df, self.read_info = await asyncio.to_thread(_read_bytes, content, path.suffix.lower())
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
