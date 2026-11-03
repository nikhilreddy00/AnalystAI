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

import asyncio
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
from domain_pack import detect as detect_domain
from domain_pack import layout_columns, one_series, pack_for, words
from semantic_layer import column_hints
from guardrails import (
    AGE_CAP,
    CALENDAR_WORDS,
    MASK,
    POOLED_LABEL,
    SMALL_CELL,
    is_age_column,
    is_diagnosis_column,
    health_signal,
    is_phi_identifier,
    is_zip_column,
    mask_pii,
    mask_row,
    person_shaped,
    pii_kind,
    pii_strings,
    pseudonym_key,
    pseudonymize_columns,
    zip3,
)
from llm.client import OVERFLOW, LLMClient, pii_values
from orchestrator.state import (
    AgentCard,
    AnalysisSession,
    ColumnInfo,
    ConnectorConfig,
    ConnectorType,
    CleaningDecision,
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


def _cadence(df: pd.DataFrame, by: list[str], time: str) -> str | None:
    """A level's snapshot cadence per entity ('monthly' for staggered statement days, which are
    'daily' across the whole file): the median of each entity's median gap."""
    if not by or any(c not in df.columns for c in [*by, time]):
        return None
    t = _as_dates(df[time])
    d = df.assign(_t=t).dropna(subset=["_t"]).sort_values("_t", kind="stable")
    gaps = d.groupby(by)["_t"].diff().dt.days.dropna()
    if gaps.empty:
        return None
    med = gaps.groupby([d.loc[gaps.index, e] for e in by]).median().median()
    # a clean cadence or none (a 14-day cycle is no "monthly")
    return next((g for lo, hi, g in ((0, 1.5, "daily"), (6, 8, "weekly"), (13, 15, "fortnightly"),
                                     (27, 32, "monthly"), (85, 95, "quarterly"), (360, 370, "yearly"))
                 if lo <= med <= hi), None)


def _names(cols: list[str], cap: int = 12) -> str:
    return ", ".join(cols[:cap]) + (f" (+{len(cols) - cap} more)" if len(cols) > cap else "")


def _summary(df: pd.DataFrame, cols: list[ColumnInfo], layout: dict[str, str] | None = None) -> str:
    """Computed facts only: counts, the primary date span and grain, roles, PII, missing values.
    Blanks that are the file's layout (domain_pack.layout_columns) are said as such, never as
    missing values."""
    layout = layout or {}
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
    if missing := sorted((c for c in cols if c.null_pct >= 1 and c.name not in layout),
                         key=lambda c: -c.null_pct)[:5]:
        parts.append("Missing values: " + ", ".join(f"{c.name} {c.null_pct:g}%" for c in missing)
                     + ".")
    for kind, why in (("order", "one value per order, on its first line"),
                      ("conditional", "filled only for the rows whose status needs one")):
        if names := [c for c, k in layout.items() if k == kind]:
            parts.append(f"Blank by the file's layout, not missing ({why}): {_names(names)}.")
    return " ".join(parts)


_SYMBOLS = {"$": "USD", "€": "EUR", "£": "GBP", "₹": "INR"}   # ¥ is JPY or CNY: not guessed


def _written_currency(s: pd.Series) -> str | None:
    """The ISO code of the currency symbol most values are written with ('$' read as USD, a
    stated assumption); None for numbers, mixed symbols or plain text."""
    if not pd.api.types.is_string_dtype(s) and s.dtype != object:
        return None
    v = s.dropna().astype(str).str.strip().head(_SAMPLE)
    if v.empty:
        return None
    # $1,249.00, -$5, ($3,000.00) or 10,50 € (written after the number in much of Europe)
    edge = v.str.strip("-(+) ").map(lambda t: t[:1] if t[:1] in _SYMBOLS else t[-1:])
    sym = edge.mode().iloc[0]
    return _SYMBOLS.get(sym) if sym in _SYMBOLS and (edge == sym).mean() >= 0.8 else None


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


def _patient_signal(cols: list[ColumnInfo], values) -> bool:
    """Patient records: an MRN / patient id, ICD-10 codes in a diagnosis column, or health
    vocabulary in the headers next to personal data. One rule for new loads and old threads."""
    health = health_signal(c.name for c in cols)
    dx = any(is_diagnosis_column(c.name, values(c.name)) for c in cols)
    near = health or dx or health_signal((c.name for c in cols), weak_ok=True)
    return (any(is_phi_identifier(c.name, strong=not near) for c in cols) or dx
            or (health and any(c.pii for c in cols)))


def looks_like_patient_data(contract: DataContract) -> bool:
    """For a contract stored before PHI mode: the same rule, on its column samples."""
    return _patient_signal(contract.columns, lambda n: next(
        (c.sample_values for c in contract.columns if c.name == n), []))


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
    hints, layer_note = column_hints([c.name for c in cols])
    for c in cols:
        h = hints.get(c.name, {})
        c.aggregation, c.aggregation_by, c.unit = h.get("aggregation"), h.get("by", []), h.get("unit")
        c.aggregation_time = h.get("time")
        if c.unit is None and c.is_metric:   # "$1,249.00" in the file: the money's currency
            c.unit = _written_currency(df[c.name])
    if layer_note:
        info = {**info, "notes": [*info.get("notes", []), layer_note]}
    pii = [c.name for c in cols if c.pii]
    head = df.head(5).astype(object)
    rows = head.where(head.notna(), None).to_dict(orient="records")
    fp = fingerprint(df)
    phi = _patient_signal(cols, lambda n: _profiled(df[n]).dropna().head(_SAMPLE).tolist())
    domain, roles, confidence = detect_domain(df, {c.name for c in cols if c.is_date}, set(pii))
    phi = phi or bool((p := pack_for(domain)) and p.phi)
    if pack := pack_for(domain):   # the pack knows a store number is a label, not a quantity
        for c in cols:
            role = next((r for r, col in roles.items() if col == c.name), None)
            if role and pack.roles[role].kind == "category" and c.is_metric:   # a code (DRG 470)
                c.is_metric = False
                c.role_reason = f"category: the {domain} {role.replace('_', ' ')} (a code)"
            if not role or pack.roles[role].kind != "id" or c.is_id:
                continue
            s = df[c.name].dropna().astype(str)   # text: a nested value can't break nunique
            shaped = bool(pack.roles[role].pattern) and \
                s.head(1000).str.fullmatch(pack.roles[role].pattern).mean() >= 0.9
            # many digit-bearing codes (SKU 85123A, customer numbers); "Store 1".."Store 3" are labels
            coded = s.nunique() > 20 and s.head(1000).str.contains(r"\d").mean() >= 0.9
            # a store number, an order number ("#1001"); "Downtown" (few names) stays a category
            if c.is_metric or shaped or coded or s.nunique() >= 0.5 * max(len(s), 1):
                c.is_id, c.is_metric = True, False
                c.role_reason = f"id: the {domain} {role.replace('_', ' ')}"
        key, date = roles.get(pack.snapshot_key), roles.get(pack.date_role)
        for c in cols:   # the pack's meaning, as the semantic layer's (a layer hint wins)
            role = next((r for r, col in roles.items() if col == c.name), None)
            if not role or c.aggregation or not c.is_metric:
                continue
            if pack.roles[role].over_time == "last" and key and date:   # a balance: a level
                c.aggregation, c.aggregation_by, c.aggregation_time = "last", [key], date
            elif pack.roles[role].over_time == "last" and date and one_series(df, pack, roles, role):
                c.aggregation, c.aggregation_by, c.aggregation_time = "last", [], date   # one account
            elif not pack.roles[role].additive:   # a rate, a price, days past due: averaged
                c.aggregation = "mean"
    for c in cols:   # a level's cadence, whichever source made it one
        if c.aggregation in ("last", "first") and c.aggregation_time:
            c.aggregation_every = _cadence(df, c.aggregation_by, c.aggregation_time)
    return DataContract(
        source_type=source_type, row_count=len(df), column_count=df.shape[1], columns=cols,
        sample_rows=[mask_row(r, pii) for r in rows],
        # The data's time axes. A birth date is a date but never a time axis: the report period,
        # the built-in trend and targeted time series all read these (5.5).
        date_columns=[c.name for c in cols if c.is_date and not c.pii], phi=phi,
        id_columns=[c.name for c in cols if c.is_id],
        metric_columns=[c.name for c in cols if c.is_metric],
        schema_summary=_summary(df, cols, layout_columns(df, pack, roles)), fingerprint=fp,
        pii_columns=pii,
        source_evidence=source_evidence(df, cols, source_type, info, fp),
        domain=domain, roles=roles, domain_confidence=confidence)


# ── PII guard ──────────────────────────────────────────────────────────────────

def known_pii_values(df: pd.DataFrame, contract: DataContract) -> set[str]:
    """The strings the LLM client must never send: every value of a PII column, and PII found
    inside other text columns (a note with an email). Short values and calendar words are left
    out: tokenizing "May" would corrupt every date in a prompt."""
    found: set[str] = set()
    pii_kinds = {c.name: c.pii for c in contract.columns}
    guarded = [c for c in contract.pii_columns if not (contract.phi and pii_kinds.get(c) == "dob")]
    numeric_ids: set[str] = set()
    if contract.phi and not contract.deidentified:   # the raw patient data: whatever PHI mode
        # withholds or pseudonymises (the working copy holds none of it)
        withheld, ids = phi_plan(df, contract)
        dates = {c.name for c in contract.columns if c.is_date or c.pii == "dob"}
        guarded += [c for c in [*withheld, *ids] if c not in guarded and c not in dates]
        numeric_ids = set(ids)
    for col in guarded:
        if col not in df.columns:
            continue
        for v in _profiled(df[col]).dropna().unique():
            if isinstance(v, pd.Timestamp):
                found.update({f"{v:%Y-%m-%d}", str(v)})
                continue
            t = str(int(v)) if isinstance(v, float) and v.is_integer() else str(v).strip()
            if col in numeric_ids and t.isdigit() and len(t) < 6:
                continue   # visit 2025 would tokenize every year 2025 in a prompt
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
        # Names first: past the cap, words (names, emails) are kept before bare numbers (ids),
        # which the working copy holds as pseudonyms anyway.
        ranked = sorted(found, key=lambda v: (v.replace("-", "").isdigit(), v))
        guard.update(ranked if len(ranked) <= _PII_CAP else [*ranked[:_PII_CAP], OVERFLOW])


async def _seed_guard_from_last_turn(session: AnalysisSession, llm: LLMClient) -> None:
    """A follow-up on a database is answered by the SQL agent before this turn's rows exist, and
    its memory quotes the last answer: register the last turn's PII values first. Patient data:
    the cached copy holds no names, so the raw source is read too (seed_phi_guard)."""
    if pii_values.get() is None or not (session.data_contract and session.cleaned_data_path):
        return
    from memory.session_data import read_cleaned_parquet
    cached = await asyncio.to_thread(read_cleaned_parquet, session.session_id)
    if cached is not None:
        await asyncio.to_thread(lambda: remember_pii(pd.read_parquet(io.BytesIO(cached)),
                                                     session.data_contract))
    await seed_phi_guard(session, llm)


# ── PHI mode (Phase 5.5): a PHI-protected working copy (not de-identified: exact dates stay) ──────────────────────────

_NOTE_NAME_RE = re.compile(r"(?:^|_)(?:notes?|comments?|remarks?|descr\w*|text|memo|narrative|"
                           r"messages?|summary|observations?)(?:$|_)", re.I)


def _is_free_text(s: pd.Series, name: str = "") -> bool:
    """Notes and comments: phrases, not labels (3+ words in 30% of non-empty values, or any
    value of 6+). They can name anyone, so PHI mode withholds them; categories and codes are
    1-2 words ("General Medicine"). Fail closed: a 3-word diagnosis text is withheld too (its
    ICD code stays)."""
    if not pd.api.types.is_string_dtype(s) and s.dtype != object:
        return False
    v = s.dropna().astype(str).str.strip()
    v = v[v != ""]
    counts = v.value_counts()
    if (len(counts) <= 50 and counts.min() >= 2 and not _NOTE_NAME_RE.search(name)
            and not person_shaped(counts.index)):
        return False   # a few repeated labels ("Obstetrics and Gynecology"): a category
    words = v.str.split().str.len()
    return len(v) > 0 and ((words >= 3).mean() >= 0.3 or bool((words >= 6).any()))


def _decision(column: str, op: str, strategy: str, reason: str, rows: int, df: pd.DataFrame
              ) -> CleaningDecision:
    pct = round(float(df[column].isna().mean()) * 100, 1) if column in df.columns else 0.0
    return CleaningDecision(column=column, operation=op, strategy=strategy, reason=reason,
                            rows_affected=rows, before_null_pct=pct, after_null_pct=pct)


_ID_HEADER_RE = re.compile(r"(?:^|_)(?:member|account|acct|policy|subscriber|insured|beneficiary|"
                           r"claim|case|chart|pat|pt|enrol(?:l?ee)|client|person|resident|mbr)"
                           r"(?:_?(?:id|no|nr|num|number|ref|key))?$", re.I)
# Any `…_no` / `…_ref`: a record number when it holds text or big numbers; `cycle_no` = 1..6 is a
# count kept for analysis (the stems above are identifiers whatever they hold).
_ID_SUFFIX_RE = re.compile(r"(?:^|_)(?:no|nr|ref|key)$", re.I)


def _person_share(s: pd.Series, people: pd.Series | None) -> float:
    """Names under any header, "Lee, Ana" included. Lowercase names count only when each value
    follows one patient (or, with no patient column, most rows differ): a lowercase category
    ("north east") repeats across many people."""
    v = s.dropna()
    if people is not None:
        pairs = pd.DataFrame({"v": s.to_numpy(), "p": people.to_numpy()}).dropna(subset=["v"])
        own = pairs.groupby("v")["p"].nunique().le(1).mean() >= 0.8   # by position: any index
    else:
        own = v.nunique() >= 0.3 * len(v)
    return person_shaped(v.unique(), last_first=True, lowercase=bool(own))


def phi_plan(df: pd.DataFrame, contract: DataContract) -> tuple[list[str], list[str]]:
    """(withheld, pseudonymised) for patient data: one decision, used by prepare_frame and by the
    guard (whatever is withheld must also never reach a model through the user's own words).
    Fail closed: kept are only numbers, non-personal dates, ZIPs (generalised), diagnosis codes
    and short categories that neither look like people's names nor follow the patient 1:1."""
    roles = {c.name: c for c in contract.columns}
    pack = pack_for(contract.domain)
    coded = {col for r, col in contract.roles.items() if pack and r in pack.roles
             and pack.roles[r].pattern and col in df.columns
             and _profiled(df[col]).dropna().astype(str).str.strip()
             .str.fullmatch(pack.roles[r].pattern).all()}   # procedure codes, DRGs: EVERY value
    # matches (one free-text value in a code column withholds the column)
    patient = next((str(n) for n in df.columns if is_phi_identifier(str(n))), None)
    n_patients = df[patient].nunique() if patient else 0
    withheld, ids = [], []
    for n in map(str, df.columns):
        c = roles.get(n)
        s = _profiled(df[n])
        if c is None or c.pii:
            withheld.append(n)
        elif not c.is_metric and _person_share(s, df[patient] if patient else None) >= 0.5:
            withheld.append(n)   # people's names under any header ("Pt", "Resident"), however few
        elif c.is_id or is_phi_identifier(n) or _ID_HEADER_RE.search(_norm(n)) or (
                _ID_SUFFIX_RE.search(_norm(n)) and not (
                    c.is_metric and pd.to_numeric(s, errors="coerce").abs().max() < 100_000)):
            ids.append(n)
        elif c.is_date or is_zip_column(n):
            continue
        elif c.is_metric:   # a 9-digit member number read as a quantity is an identifier
            v = pd.to_numeric(s, errors="coerce").dropna()
            if len(v) and (v % 1 == 0).all() and v.abs().min() >= 100_000 and v.nunique() >= 0.5 * len(v):
                ids.append(n)
        elif is_diagnosis_column(n, s.dropna().head(_SAMPLE).tolist()):
            continue   # the clinical content analysed (not an identifier); generalised later
        elif _is_free_text(s, n) or (s.nunique() > 50 and n not in coded):
            withheld.append(n)   # notes, places: anything too specific to be a category
        elif patient and n_patients >= 2 and s.nunique() >= 0.5 * n_patients \
                and df.groupby(patient)[n].nunique().max() <= 1:
            withheld.append(n)   # one value per patient: it identifies them like a name (the
            # fewer the patients, the more so)
    return withheld, ids


def prepare_frame(df: pd.DataFrame, contract: DataContract, source_type: ConnectorType,
                  info: dict | None = None, scope: str = "") -> tuple[pd.DataFrame, DataContract]:
    """The frame every agent works on. For patient data (`contract.phi`) a PHI-protected copy,
    built from an allowlist (fail closed: what isn't known safe is withheld):
    - kept: numbers (ages ≥ 90 → 90), dates that aren't personal, and short category columns
      (≤ 50 values, not free text; groups under 11 rows pooled);
    - pseudonymised (one-way, keyed per `scope`, the user): every identifier column;
    - generalised: ZIP → ZIP3;
    - withheld: personal columns and every other text column (names under any header, notes).
    The contract is rebuilt from the copy and carries each step as an audited decision. Other
    data passes unchanged. Not HIPAA de-identification: exact admit/discharge dates stay, and
    ponytail: two-way and month × group cells can still be < 11 (stated in the README)."""
    if not contract.phi:
        return df, contract
    decisions: list[CleaningDecision] = []
    # Exact duplicates are judged on the raw rows (cleaning's rule: only with a row key), since
    # withholding names or pooling labels can make two real records identical afterwards.
    dup = df.duplicated()
    keys = [c.name for c in contract.columns if c.is_id and c.name in df.columns
            and df[c.name].nunique() >= 0.9 * max(int(df[c.name].notna().sum()), 1)]
    if (n_dup := int(dup.sum())) and keys:
        df = df[~dup]
        decisions.append(_decision("*", "drop_duplicates", "exact", f"Removed {n_dup} exact "
                                   f"duplicate rows (same {keys[0]} and every other value), "
                                   "before de-identification", n_dup, df))
        decisions[-1].rows_removed = n_dup
    withheld, ids = phi_plan(df, contract)
    for col in withheld:
        decisions.append(_decision(col, "withhold", "drop_column", "personal, free text or too "
                                   "specific to be a category: withheld from analysis", len(df), df))
    out = df.drop(columns=withheld)
    if out.shape[1] == 0:
        raise IngestionError("nothing left after de-identification",
                             "Every column of this patient data is personal or free text, so "
                             "nothing is left to analyse once those are withheld.")
    out = pseudonymize_columns(out, ids, pseudonym_key(scope))
    for col in ids:
        decisions.append(_decision(col, "pseudonymize", "keyed_hash", "identifier replaced by "
                                   "a one-way pseudonym (counts and repeat visits still work)",
                                   int(out[col].notna().sum()), out))
    for col in (str(n) for n in out.columns if is_zip_column(str(n))):
        out[col] = out[col].map(zip3)
        decisions.append(_decision(col, "generalize", "zip3", "ZIP reduced to its first 3 digits "
                                   "(000 for sparsely populated areas)", int(out[col].notna().sum()), out))
    for col in (str(n) for n in out.columns if is_diagnosis_column(
            str(n), _profiled(out[n]).dropna().head(_SAMPLE).tolist())):
        if out[col].nunique() > 50:   # hundreds of codes: their 3-character category, so the
            cat = out[col].astype(str).str.strip().str.upper().str[:3]   # groups can be pooled
            out[col] = cat.where(out[col].notna())
            decisions.append(_decision(col, "generalize", "icd_category", "diagnosis codes "
                                       "reduced to their 3-character ICD-10 category",
                                       int(out[col].notna().sum()), out))
    for col in (str(n) for n in out.columns if is_age_column(str(n))):
        s = pd.to_numeric(out[col], errors="coerce")
        if s.notna().any() and (high := int((s > AGE_CAP).sum())):
            out[col] = s.where(~(s > AGE_CAP), AGE_CAP)
            decisions.append(_decision(col, "cap", "age_90_plus", f"ages over {AGE_CAP} shown as "
                                       f"{AGE_CAP} (90+)", high, out))
    roles = {c.name: c for c in contract.columns}
    pack = pack_for(contract.domain)
    # the values a pack rule reads stay as written (pooling 5 'expired' erases mortality); every
    # other rare value is still pooled, and small counts in what is reported are flagged by the
    # quality check
    exact = {contract.roles[r]: vs for r, vs in (pack.value_words() if pack else {}).items()
             if r in contract.roles}
    for col in (str(n) for n in out.columns):
        c = roles.get(col)
        if c is None or c.is_date or c.is_id or c.is_metric or col in ids or is_zip_column(col):
            continue
        key = out[col].astype(str).str.strip().str.casefold().where(out[col].notna(), "\x00missing")
        counts = key.map(key.value_counts())
        small = (counts < SMALL_CELL) & out[col].notna()   # missing stays missing, never a group
        if col in exact and (kept := small & words(out[col]).isin(exact[col])).any():
            small &= ~kept
            decisions.append(_decision(col, "keep", "rule_values", "values a KPI counts kept as "
                                       f"written although under {SMALL_CELL} records (small "
                                       "counts in reports are flagged instead)", int(kept.sum()), out))
        dx = is_diagnosis_column(col, _profiled(out[col]).dropna().head(_SAMPLE).tolist())
        # A yes/no outcome is never pooled: pooling its rare side would erase it (readmitted
        # 9 of 300 → 0%); diagnosis codes are pooled however many categories they have.
        if out[col].nunique() <= 2 or (key.nunique() > 50 and not dx) or not small.any():
            continue
        out[col] = out[col].astype(object).where(~small, POOLED_LABEL)
        decisions.append(_decision(col, "pool", "small_cells", f"groups under {SMALL_CELL} records "
                                   f"grouped as '{POOLED_LABEL}'", int(small.sum()), out))
    notes = [*((info or {}).get("notes", [])),
             f"Patient data: {', '.join(withheld) or 'no columns'} withheld; "
             f"{', '.join(ids) or 'no ids'} pseudonymised. Groups under {SMALL_CELL} records are "
             f"grouped as '{POOLED_LABEL}'."]
    rebuilt = build_contract(out, source_type, {**(info or {}), "notes": notes})
    for c in rebuilt.columns:
        if c.name in ids:   # a pseudonym is an identifier, never personal data or a category
            c.is_id, c.is_metric, c.is_date, c.pii = True, False, False, None
    rebuilt.id_columns = [c.name for c in rebuilt.columns if c.is_id]
    rebuilt.metric_columns = [c.name for c in rebuilt.columns if c.is_metric]
    rebuilt.date_columns = [d for d in rebuilt.date_columns if d not in ids]
    rebuilt.pii_columns = [c.name for c in rebuilt.columns if c.pii]
    rebuilt.phi, rebuilt.deidentification, rebuilt.deidentified = True, decisions, True
    raw_cols = {c.name: c for c in contract.columns}
    for c in rebuilt.columns:   # the layer matched the raw upload: its meanings carry over
        r = raw_cols.get(c.name)
        if r and not c.aggregation and r.aggregation and all(
                x in out.columns for x in [*r.aggregation_by, r.aggregation_time] if x):
            c.aggregation, c.aggregation_by, c.aggregation_time, c.aggregation_every = (
                r.aggregation, r.aggregation_by, r.aggregation_time, r.aggregation_every)
        if r and not c.unit and r.unit:
            c.unit = r.unit
    rebuilt.schema_summary = _summary(out, rebuilt.columns, layout_columns(
        out, pack_for(rebuilt.domain), {r: c for r, c in rebuilt.roles.items() if c in out.columns})) + (
        f" Patient data: identifiers withheld ({', '.join(withheld)}), record numbers "
        f"pseudonymised; groups under {SMALL_CELL} records appear as '{POOLED_LABEL}'.")
    rebuilt.source_evidence = source_evidence(out, rebuilt.columns, source_type,
                                              {**(info or {}), "notes": notes}, rebuilt.fingerprint)
    return out, rebuilt


async def seed_phi_guard(session: AnalysisSession, llm: LLMClient) -> None:
    """A PHI session's cached frame holds no names (they were withheld), so a follow-up seeds
    this run's guard from the raw source instead; the raw frame is then discarded."""
    if pii_values.get() is None or not (session.data_contract and session.data_contract.phi):
        return
    probe = session.model_copy(deep=True)
    question = session.task_plan.raw_query if session.task_plan else None
    await run_ingestion_agent(probe, llm, question, session.data_contract.source_query,
                              prepare=False, seed_last=False)
    probe.__dict__.pop("_df_parquet", None)


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
                              reuse_query: Evidence | None = None,
                              prepare: bool = True, seed_last: bool = True) -> DataContract:
    """Load the source and return its DataContract; the frame goes on the session as parquet.
    A SQL connector without a table answers `question` with one read-only query (Phase 4);
    `reuse_query` re-runs an earlier answer's query instead (approval answers, follow-ups), so
    the same question never silently gets different rows."""
    if seed_last:   # before the memory is built: it quotes earlier answers
        await _seed_guard_from_last_turn(session, llm)
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
    contract = await asyncio.to_thread(build_contract, df, ctx.config.type, info)
    contract.source_query = source_query
    # The raw values: the guard must know what PHI mode withholds (in place: same set object).
    await asyncio.to_thread(remember_pii, df, contract)
    if prepare:   # every production path; only the ingestion eval scores the raw load
        try:
            df, contract = await asyncio.to_thread(prepare_frame, df, contract, ctx.config.type,
                                                   info, session.user_id)
        except IngestionError:
            raise
        except Exception as exc:   # fail closed: never continue on the raw patient data
            raise IngestionError(f"de-identification failed: {type(exc).__name__}",
                                 "This data holds patient records and couldn't be protected, "
                                 "so it wasn't analysed. Check the file opens as a plain table and "
                                 "upload it again; if it keeps failing, ask your administrator.") from exc
        if contract.phi and source_query is not None:   # its excerpt quotes raw result rows
            source_query = source_query.model_copy(update={
                "result_excerpt": "Patient data: result rows are not quoted."})
        contract.source_query = source_query

    session.__dict__["_df_parquet"] = await asyncio.to_thread(_to_parquet, df)
    return contract


def _to_parquet(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    df.to_parquet(buf, index=False)
    return buf.getvalue()
