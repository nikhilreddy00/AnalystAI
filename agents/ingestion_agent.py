"""
Ingestion agent: the agent contract (docs/agent-contract.md), PURE PYTHON (no LLM).

load the source (file reader / SQL agent / Sheets / API) → build_contract: each column's role
(date / id / metric / category) from its values first, its personal-data kind, PII-masked samples,
a computed summary, a content fingerprint and a `source` Evidence item → DataContract. The dataset's
PII values go to the LLM client's guard (llm.client.pii_values), which pseudonymizes them in every
request of the run.

Phase 5 Ingestion removed the old Haiku schema summary: it summarized 5 head rows ("November
2025" on 12 months of data), was read only by Intake, and Intake's eval never saw it. Every fact a
prompt needs here has one right answer, so code computes it.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
from dataclasses import dataclass

import pandas as pd

from agents.data_cleaning import try_numeric_conversion
from agents.errors import IngestionError
from agents.utils import conversation_context, looks_like_sequential_id
from connectors.base import _looks_like_dates, get_connector, parse_text_dates
from guardrails import CALENDAR_WORDS, MASK, mask_pii, mask_row, pii_kind, pii_strings
from llm.client import OVERFLOW, LLMClient, pii_values
from orchestrator.state import (
    AgentCard,
    AnalysisSession,
    ColumnInfo,
    ConnectorConfig,
    ConnectorType,
    DataContract,
    Evidence,
)

INGESTION_CARD = AgentCard(
    name="ingestion",
    version="contract-v1",
    scope="Loads the user's data source into one table and describes it: each column's role, the "
          "personal data it holds, masked samples, a computed summary and where the rows came from.",
    inputs=["Connector config (file, database, Google Sheet or API)",
            "For a database with no table: the question (answered by one read-only SQL query)"],
    outputs=["DataContract: roles with reasons, PII kinds, masked samples, summary, fingerprint, "
             "source evidence"],
    limits=[
        "Files: CSV, TSV, Excel .xlsx (the largest sheet only), JSON, Parquet. Old .xls is refused.",
        "A column's role comes from its values; the name only breaks ties (`lifetime_value` is a "
        "number, not a date). Years, zip codes and order numbers are never metrics.",
        "Personal data is found by column name, by values (≥ 30% of 200 sampled values hold an "
        "email, phone, SSN, card or IP address), or by a role name (`sales_rep`) holding people's "
        "names. A person's name inside free text in an unlabelled column is not detected.",
        "Values under 4 characters and calendar words ('May', 'June') are masked in samples but "
        "not tokenized in later prompts.",
        "Delimiter, report-title rows above the header, a decimal comma and a 'Total' footer row "
        "are detected from the first 200 lines.",
    ],
    refusal_rules=["The file is empty, not a table, or has headers but no rows",
                   "A database source with no table and no question"],
)

_SAMPLE = 200              # values profiled per column (ponytail: a head sample, not random)
_PII_CAP = 200_000         # values given to the client guard; beyond it the guard also pattern-masks
_STRONG_ID = re.compile(r"(?:^|_)(?:id|uuid|guid|key|pk)$")
_WEAK_ID = re.compile(r"(?:^|_)(?:no|nr|num|number|code|ref|sku)$")
_ZIP = re.compile(r"(?:^|_)(?:zip|zipcode|postal|postcode)(?:_code)?$")
_YEAR = re.compile(r"(?:^|_)(?:year|yr|fy|season|vintage)$")
_EPOCH = re.compile(r"(?:^|_)(?:ts|timestamp|epoch|time|created|updated|at|unix)$")
_LEADING_ZERO = re.compile(r"^0\d+$")


def _norm(name: str) -> str:
    name = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", str(name))
    return re.sub(r"[^0-9a-z]+", "_", name.lower()).strip("_")


# ── Context ────────────────────────────────────────────────────────────────────

@dataclass
class IngestContext:
    config: ConnectorConfig
    question: str | None         # a SQL source without a table is answered with it
    reuse_query: Evidence | None  # re-run an earlier answer's query (approvals, follow-ups)
    memory: str                  # conversation memory: a SQL follow-up isn't answerable alone


def build_context(session: AnalysisSession, question: str | None = None,
                  reuse_query: Evidence | None = None) -> IngestContext:
    if not session.connector_config:
        raise IngestionError("no connector config", "Connect a data source first.")
    return IngestContext(config=session.connector_config, question=question,
                         reuse_query=reuse_query, memory=conversation_context(session))


# ── Roles ──────────────────────────────────────────────────────────────────────

def _whole(s: pd.Series) -> bool:
    try:
        return bool((s % 1 == 0).all())
    except TypeError:
        return False


def infer_role(s: pd.Series, name: str) -> tuple[str, str]:
    """(role, reason): date | id | metric | category, decided by the values first."""
    norm = _norm(name)
    vals = s.dropna()
    if vals.empty:
        return "category", "no values"
    unique = vals.nunique() / len(vals)
    if pd.api.types.is_bool_dtype(s):
        return "category", "true/false values"
    if pd.api.types.is_datetime64_any_dtype(s) or _looks_like_dates(s):
        return "date", "dates"
    if pd.api.types.is_numeric_dtype(s):
        whole = _whole(vals)
        if whole and (_STRONG_ID.search(norm) or (_WEAK_ID.search(norm) and unique >= 0.5)):
            return "id", f"whole numbers named as an identifier ('{name}')"
        if _ZIP.search(norm):
            return "category", "a postal code: a label, not a quantity"
        if whole and _YEAR.search(norm) and vals.between(1900, 2100).all():
            return "category", "years: a label, not a quantity"
        if whole and _EPOCH.search(norm) and (vals.between(9e8, 2.2e9).all()
                                              or vals.between(9e11, 2.2e12).all()):
            return "date", "Unix timestamps"
        if whole and unique >= 0.98 and looks_like_sequential_id(vals, len(vals)):
            return "id", "consecutive whole numbers, one per row"
        return "metric", "numbers"
    text = vals.astype(str).str.strip()
    if parse_text_dates(s, share=0.8) is not None:
        return "date", "text dates"
    if _ZIP.search(norm):
        return "category", "a postal code: a label, not a quantity"
    if _STRONG_ID.search(norm) or (_WEAK_ID.search(norm) and unique >= 0.5):
        return "id", f"named as an identifier ('{name}')"
    if _WEAK_ID.search(norm):
        return "category", "a code with few distinct values: a label"
    if not (text.str.match(_LEADING_ZERO).mean() >= 0.1) and try_numeric_conversion(s) is not None:
        return "metric", "numbers stored as text (currency, %, thousands separators)"
    if unique >= 0.95 and len(vals) >= 5 and not text.str.contains(" ").any() \
            and text.str.contains(r"\d").mean() >= 0.9 and text.str.len().max() <= 40:
        return "id", "unique codes, one per row"
    return "category", "text labels"


# ── Contract ───────────────────────────────────────────────────────────────────

def _profiled(s: pd.Series) -> pd.Series:
    """The column as profiled: nested JSON cells (lists, dicts) as their JSON text, so counting
    and pattern checks work; the data itself is left as loaded (Cleaning skips nested columns)."""
    if s.dtype == object and s.map(lambda v: isinstance(v, (list, dict, set, tuple))).any():
        return s.map(lambda v: json.dumps(v, default=str)
                     if isinstance(v, (list, dict, set, tuple)) else v)
    return s


def fingerprint(df: pd.DataFrame) -> str:
    """Content hash of the loaded table (names, types, values): same file → same contract."""
    try:
        rows = pd.util.hash_pandas_object(df, index=False).values
    except TypeError:   # unhashable cells (lists from JSON)
        rows = pd.util.hash_pandas_object(df.astype(str), index=False).values
    head = json.dumps([[str(c), str(t)] for c, t in df.dtypes.items()]).encode()
    return hashlib.sha256(head + rows.tobytes()).hexdigest()


def _as_dates(s: pd.Series) -> pd.Series:
    if pd.api.types.is_datetime64_any_dtype(s):
        return s
    if pd.api.types.is_numeric_dtype(s):
        unit = "ms" if s.dropna().gt(1e11).all() else "s"
        return pd.to_datetime(s, unit=unit, errors="coerce")
    parsed = parse_text_dates(s, share=0.8)
    return parsed if parsed is not None else pd.to_datetime(s.astype("string"), format="mixed",
                                                            errors="coerce")


def _grain(dates: pd.Series) -> str:
    days = dates.dropna().dt.normalize().drop_duplicates().sort_values().diff().dt.days.dropna()
    if days.empty:
        return "a single date"
    med = days.median()
    return next(g for limit, g in ((1.5, "daily"), (8, "weekly"), (32, "monthly"),
                                   (95, "quarterly"), (float("inf"), "yearly")) if med <= limit)


def _names(cols: list[str], cap: int = 12) -> str:
    return ", ".join(cols[:cap]) + (f" (+{len(cols) - cap} more)" if len(cols) > cap else "")


def _summary(df: pd.DataFrame, cols: list[ColumnInfo]) -> str:
    """Computed facts only: counts, the primary date span and grain, roles, PII, missing values."""
    by = lambda role: [c.name for c in cols if _role(c) == role and not c.pii]   # noqa: E731
    parts = [f"{len(df):,} rows × {df.shape[1]} columns."]
    if date := next((c.name for c in cols if c.is_date and not c.pii), None):
        d = _as_dates(df[date]).dropna()
        if not d.empty:
            parts.append(f"Dates: {date} from {d.min():%Y-%m-%d} to {d.max():%Y-%m-%d} "
                         f"({_grain(d)}).")
    for label, role in (("Metrics", "metric"), ("Identifiers", "id"), ("Categories", "category")):
        if names := by(role):
            parts.append(f"{label}: {_names(names)}.")
    if pii := [f"{c.name} ({c.pii})" for c in cols if c.pii]:
        parts.append(f"Personal data, masked before any model sees it: {_names(pii)}.")
    if missing := sorted((c for c in cols if c.null_pct >= 1), key=lambda c: -c.null_pct)[:5]:
        parts.append("Missing values: " + ", ".join(f"{c.name} {c.null_pct:g}%" for c in missing)
                     + ".")
    return " ".join(parts)


def _role(c: ColumnInfo) -> str:
    return "date" if c.is_date else "id" if c.is_id else "metric" if c.is_metric else "category"


_SOURCE = {ConnectorType.CSV: "file", ConnectorType.SQL: "database",
           ConnectorType.SHEETS: "Google Sheet", ConnectorType.API: "API"}


def _reader(source_type: ConnectorType, info: dict) -> str:
    """How the rows were read, as the call that reproduces it."""
    fmt = info.get("format")
    if fmt in ("csv", "tsv"):
        args = [f"encoding={info['encoding']!r}", f"sep={info['delimiter']!r}"]
        if info.get("decimal") == ",":
            args.append("decimal=','")
        if info.get("header_line", 1) > 1:
            args.append(f"skiprows={info['header_line'] - 1}")
        return f"pd.read_csv(<uploaded file>, {', '.join(args)})"
    if fmt == "xlsx":
        return "pd.read_excel(<uploaded file>, sheet_name=<largest sheet>)"
    if fmt in ("json", "parquet"):
        return f"pd.read_{fmt}(<uploaded file>)"
    return f"{_SOURCE.get(source_type, 'source')} connector load()"


def source_evidence(df: pd.DataFrame, cols: list[ColumnInfo], source_type: ConnectorType,
                    info: dict, fp: str) -> Evidence:
    notes = list(info.get("notes", []))
    pii = {c.name: c.pii for c in cols if c.pii}
    fmt = info.get("format")
    origin = f"a {fmt.upper()} file" if fmt else f"the {_SOURCE.get(source_type, 'source')}"
    roles: dict[str, list[str]] = {}
    for c in cols:
        roles.setdefault(_role(c), []).append(c.name)
    excerpt = json.dumps({"roles": roles, "personal_data": pii, "fingerprint": fp[:16]})
    if pii:
        notes.append(f"{len(pii)} column{'s' * (len(pii) != 1)} hold personal data; their values "
                     "are masked before any model sees them.")
    return Evidence(
        id="source:data", kind="source",
        claim=f"Loaded {len(df):,} rows × {df.shape[1]} columns from {origin}.",
        code=_reader(source_type, info), result_excerpt=excerpt[:500], row_count=len(df),
        confidence="medium" if info.get("notes") else "high",
        confidence_reason=("The layout was detected, not given: check the notes."
                           if info.get("notes") else "Read as-is: no layout guessing was needed."),
        assumptions=notes)


def build_contract(df: pd.DataFrame, source_type: ConnectorType, info: dict | None = None
                   ) -> DataContract:
    """The DataContract every downstream agent reads. Deterministic: the same frame gives the
    same contract. PII never leaves here unmasked: every prompt reads samples from it."""
    info = info or {}
    cols: list[ColumnInfo] = []
    for name in df.columns:
        s = _profiled(df[name])
        role, why = infer_role(s, str(name))
        sample = s.dropna().head(_SAMPLE).tolist()
        found = pii_kind(str(name), sample)
        if found and role == "metric":   # a phone number stored as an integer
            role, why = "category", "personal data, not a quantity"
        shown = sample[:5]
        null_pct = round(float(s.isna().mean()) * 100, 2)
        cols.append(ColumnInfo(
            name=str(name), dtype=str(s.dtype), nullable=null_pct > 0,
            sample_values=[MASK] * len(shown) if found else [mask_pii(v) for v in shown],
            is_date=role == "date", is_id=role == "id", is_metric=role == "metric",
            null_pct=null_pct, role_reason=why, pii=found[0] if found else None))
    pii = [c.name for c in cols if c.pii]
    head = df.head(5).astype(object)
    rows = head.where(head.notna(), None).to_dict(orient="records")
    fp = fingerprint(df)
    return DataContract(
        source_type=source_type, row_count=len(df), column_count=df.shape[1], columns=cols,
        sample_rows=[mask_row(r, pii) for r in rows],
        date_columns=[c.name for c in cols if c.is_date],
        id_columns=[c.name for c in cols if c.is_id],
        metric_columns=[c.name for c in cols if c.is_metric],
        schema_summary=_summary(df, cols), fingerprint=fp, pii_columns=pii,
        source_evidence=source_evidence(df, cols, source_type, info, fp))


# ── PII guard ──────────────────────────────────────────────────────────────────

def known_pii_values(df: pd.DataFrame, contract: DataContract) -> set[str]:
    """The strings the LLM client must never send: every value of a PII column, and PII found
    inside other text columns (a note with an email). Short values and calendar words are left
    out: tokenizing "May" would corrupt every date in a prompt."""
    found: set[str] = set()
    for col in contract.pii_columns:
        if col not in df.columns:
            continue
        for v in _profiled(df[col]).dropna().unique():
            if isinstance(v, pd.Timestamp):
                found.update({f"{v:%Y-%m-%d}", str(v)})
                continue
            t = str(int(v)) if isinstance(v, float) and v.is_integer() else str(v).strip()
            found.update({t, *pii_strings(t)})   # the cell, and an email inside a longer note
    for col in df.columns:
        found.update(pii_strings(col))   # a pivoted export's headers can be emails
        s = _profiled(df[col])
        if col not in contract.pii_columns and pd.api.types.is_string_dtype(s):
            for v in s.dropna().astype(str).head(_PII_CAP):   # ponytail: a head scan
                found.update(pii_strings(v))
    return {v for v in found if len(v) >= 4 and v not in CALENDAR_WORDS}


def remember_pii(df: pd.DataFrame, contract: DataContract) -> None:
    """Give this run's PII values to the client guard (a no-op outside a guarded run)."""
    guard = pii_values.get()
    if guard is not None:
        found = known_pii_values(df, contract)
        # Fail closed: past the cap, every request is also pattern-masked (emails, phones, SSNs,
        # cards, IPs). Names past the cap are a stated limit.
        guard.update(sorted(found)[:_PII_CAP] if len(found) <= _PII_CAP else
                     [*sorted(found)[:_PII_CAP], OVERFLOW])


def _seed_guard_from_last_turn(session: AnalysisSession) -> None:
    """A follow-up on a database is answered by the SQL agent before this turn's rows exist, and
    its memory quotes the last answer: register the last turn's PII values first."""
    if pii_values.get() is None or not (session.data_contract and session.cleaned_data_path):
        return
    from memory.session_data import read_cleaned_parquet
    if (cached := read_cleaned_parquet(session.session_id)) is not None:
        remember_pii(pd.read_parquet(io.BytesIO(cached)), session.data_contract)


# ── Agent ──────────────────────────────────────────────────────────────────────

def _mixed_as_text(df: pd.DataFrame) -> list[str]:
    """Object columns holding more than one kind of scalar (an API field that is 1 in one record
    and "x" in the next) become text, missing values kept: parquet needs one type per column,
    and text loses nothing. Nested values (lists, dicts) are left as they are."""
    mixed = []
    for col in df.columns:
        s = df[col]
        if s.dtype != object:
            continue
        kinds = {("number" if isinstance(v, (int, float)) and not isinstance(v, bool) else
                  type(v).__name__) for v in s.dropna()}
        if len(kinds) > 1 and not kinds & {"list", "dict", "tuple", "set"}:
            df[col] = s.map(lambda v: v if v is None or (isinstance(v, float) and v != v)
                            else str(v)).astype(object)
            mixed.append(str(col))
    return mixed


def unique_names(columns) -> list[str]:
    seen: dict[str, int] = {}
    out = []
    for c in map(str, columns):
        name, n = c, seen.get(c, 0)
        while name in seen:
            n += 1
            name = f"{c}.{n}"
        seen[c] = n
        seen[name] = seen.get(name, 0)
        out.append(name)
    return out


async def run_ingestion_agent(session: AnalysisSession, llm: LLMClient,
                              question: str | None = None,
                              reuse_query: Evidence | None = None) -> DataContract:
    """Load the source and return its DataContract; the frame goes on the session as parquet.
    A SQL connector without a table answers `question` with one read-only query (Phase 4);
    `reuse_query` re-runs an earlier answer's query instead (approval answers, follow-ups), so
    the same question never silently gets different rows."""
    _seed_guard_from_last_turn(session)   # before the memory is built: it quotes earlier answers
    ctx = build_context(session, question, reuse_query)
    connector = get_connector(ctx.config)

    source_query = None
    if ctx.config.type == ConnectorType.SQL and not ctx.config.table_name:
        from agents.sql_agent import run_sql_agent
        from semantic_layer import configured_layer
        if ctx.reuse_query:
            df, source_query = (await connector.query(ctx.reuse_query.code)).frame(), ctx.reuse_query
        elif not ctx.question:
            raise IngestionError("no question", "Ask a question to query the database, or name a "
                                 "table to load.")
        else:
            answer = await run_sql_agent(ctx.question, connector, configured_layer(), llm, ctx.memory)
            df, source_query = answer.result.frame(), answer.evidence
    else:
        df = await connector.load()
    if df.empty:
        raise IngestionError("no rows", "The source returned no rows.")

    # Every connector's frame gets unique string names (read_csv's "id", "id.1" rule): a SQL join
    # can return two "id" columns, and parquet rejects duplicates and non-string names.
    names = unique_names(df.columns)
    renamed = [f"'{a}' → '{b}'" for a, b in zip(map(str, df.columns), names) if a != b]
    df.columns = names
    info = dict(connector.read_info)
    if renamed:
        info["notes"] = [*info.get("notes", []),
                         f"Duplicate column names were renamed: {', '.join(renamed[:5])}."]
    if mixed := _mixed_as_text(df):
        info["notes"] = [*info.get("notes", []), f"Columns mixing numbers and text were read as "
                         f"text (Cleaning parses them, with an audit entry): {', '.join(mixed[:5])}."]
    contract = build_contract(df, ctx.config.type, info)
    contract.source_query = source_query
    remember_pii(df, contract)

    buf = io.BytesIO()
    df.to_parquet(buf, index=False)
    session.__dict__["_df_parquet"] = buf.getvalue()
    return contract
