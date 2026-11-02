"""
Data Cleaning Agent: makes the uploaded table analysable and logs every change (CLEANING_CARD).

Deterministic, no LLM (Phase 5, like visualization): changes to the user's data must be
reproducible and testable, and the old model call only repeated a fixed decision table.

Allowed operations, in order. Each change is one CleaningDecision:
1. skip            triage: URL / long text / image path / secondary ID columns are left alone
2. sentinel_null   placeholder text ("-", "?", "N/A" …) becomes missing
3. type_coerce     numbers and dates parsed from text; values that don't parse are counted
                   (code-like columns, e.g. leading-zero codes, stay text)
4. normalize       label variants equal after trim / space-collapse / casefold merge to the most
                   common spelling (OpenRefine's key-collision method, conservative key)
5. drop_duplicates exact duplicate rows, only when a row key (ID column) exists; else flagged
6. drop_rows       rows with no value at all
7. fill_unknown    missing labels become "Unknown"; keep: missing numbers, IDs, dates, targets
                   and >40%-empty columns stay missing (nothing is imputed)
8. drop_column     only when the user chose it at the high-null gate
9. flag            outliers (agents/anomaly_detector.py); never removed

When the data matched a domain pack (Phase 5.7, domain_pack.py), the pack adds, with the same
operations: its numeric sentinels (9999 units) → sentinel_null; ids written as whole floats
(CustomerID 17850.0) → type_coerce; duplicates keyed by order + product → drop_duplicates;
fields an export writes on an order's first line only → fill_within_order (the order's own
value, never a guess: an order with two different values is left as it is); return, refunded,
cancelled, test and non-product lines and out-of-range values → flag (kept: KPIs leave them out
by rule). The audit records the grain: line, order or aggregate.

Before returning, three guardrails re-check the result against the input: rows reconcile with
the decisions, every changed column has a decision, protected columns gained no values.
LangGraph routes on CleaningAudit.high_null_columns to the approval gate.
"""
from __future__ import annotations

import io
import json
import re
from collections import Counter
from dataclasses import dataclass, field

import pandas as pd

from agents.anomaly_detector import detect_anomalies
from agents.data_cleaning import (
    detect_dates_aggressive,
    triage_columns,
    try_numeric_conversion,
)
from agents.errors import GuardrailViolation
from agents.utils import partial_last_month, pii_columns
from agents.visualization_agent import classify_columns
from domain_pack import Pack, first_line_only, main_currency, pack_for, row_kinds
from guardrails import is_pii_column, mask_pii, protected_filled, unlogged_changes, unreconciled_rows
from llm.client import trace_verdict
from orchestrator.state import (
    AgentCard,
    AnalysisSession,
    CleaningAudit,
    CleaningDecision,
    Evidence,
)

CLEANING_CARD = AgentCard(
    name="cleaning",
    version="rules-v1",
    scope="Makes the uploaded table analysable without changing what it says: turns placeholder "
          "text into missing values, parses numbers and dates, merges spelling variants of a label, "
          "removes exact duplicate and fully blank rows, and flags unusual values. Every change is "
          "one logged decision.",
    inputs=["The ingested data frame and its DataContract (column roles)",
            "The analysis plan (its target metrics are protected)",
            "The user's answer at the high-null gate, if any"],
    outputs=["The cleaned frame and a CleaningAudit: one decision per change, with rows and "
             "missing % before and after; rows_before - rows removed = rows_after",
             "One Evidence item (kind 'cleaning') listing the decisions, counts only"],
    limits=["Rule-based: no fuzzy or near-duplicate matching. Exact duplicate rows are removed only "
            "when an ID column defines a record; without one they are kept and flagged.",
            "Missing numbers stay missing (totals and means use the non-missing rows); missing "
            "labels become 'Unknown'. Nothing is imputed.",
            "A text column becomes numeric or a date only when >= 80% of its values parse; the "
            "rest become missing and the decision counts them.",
            "Ambiguous dates (03/04) are read month-first unless the column proves day-first.",
            "Label variants merge only when equal after trimming, collapsing spaces and ignoring "
            "case; the most common spelling wins.",
            "Targeted follow-up questions skip cleaning (they read the uploaded data as-is)."],
    refusal_rules=["Never fill ID, date or target-metric columns (carrying an order's own value "
                   "to its other lines is not a fill).",
                   "Never remove a row without a decision that counts it.",
                   "Never change a column's values without a decision that names it.",
                   "Never remove outliers: flag them only.",
                   "Never turn code-like text with leading zeros (zip, account codes) into numbers."],
)

HIGH_NULL_PCT = 40.0
UNKNOWN = "Unknown"
# Compared after strip + casefold. Only tokens that are never a real label: "NA" (North
# America), "None" (a tier) and "Unknown" are labels and stay.
_SENTINELS = {"", "-", "--", "---", "?", "??", "n/a", "#n/a", "null", "nan", "missing", "nil"}
_LEADING_ZERO_RE = re.compile(r"^0\d+$")

_SKIP_REASON = {
    "url": "URL column — not analytically useful",
    "long_text": "Long free-text column — not categorical",
    "image_path": "Image/file path column — not analytically useful",
    "secondary_id": "Secondary identifier — first ID kept; this one skipped",
}


@dataclass
class CleanContext:
    """What the cleaning rules read: the frame, the column roles and the user's gate answer."""
    df: pd.DataFrame
    ids: set[str]           # identifiers and codes: never parsed, merged or filled
    dates: set[str]         # never filled
    targets: set[str]       # the plan's metrics: never filled
    drop_high_null: bool    # the user chose "Drop those columns" at the high-null gate
    pii: set[str] = frozenset()   # personal-data columns: the audit never quotes their values
    # PHI mode's working copy (5.5): withheld columns and pooled labels can make distinct records
    # look identical, so duplicates were judged on the raw rows already (prepare_frame).
    deidentified: bool = False
    pack: Pack | None = None   # 5.7: the data's domain pack and its roles → columns
    roles: dict[str, str] = field(default_factory=dict)
    strong: bool = False       # a shape only that domain has: the pack may rewrite rows


def build_context(session: AnalysisSession, drop_high_null: bool = False) -> CleanContext:
    df_parquet = session.__dict__.get("_df_parquet")
    if df_parquet is None:
        raise ValueError("No DataFrame in session. Run ingestion first.")
    contract = session.data_contract
    if contract is None:
        raise ValueError("No DataContract in session.")
    df = pd.read_parquet(io.BytesIO(df_parquet))
    ids = {c.name for c in contract.columns if c.is_id}
    dates = {c.name for c in contract.columns if c.is_date} - ids
    task = session.task_plan
    targets = {m for m in (task.target_metrics if task else []) if m in df.columns}
    pack = pack_for(contract.domain)
    return CleanContext(df=df, ids=ids, dates=dates, targets=targets,
                        drop_high_null=drop_high_null, pii=pii_columns(contract),
                        deidentified=contract.deidentified, pack=pack,
                        strong=contract.domain_confidence == "strong",
                        roles={r: c for r, c in contract.roles.items() if c in df.columns} if pack else {})


def _code_like(s: pd.Series) -> bool:
    """Text where 1 in 10 values is a digit string with a leading zero (zip, account and store
    codes): a number would silently drop the zeros."""
    if not pd.api.types.is_string_dtype(s):
        return False
    v = s.dropna().astype(str).str.strip()
    return len(v) > 0 and v.str.match(_LEADING_ZERO_RE).mean() >= 0.1


def _whole_orders(flat: pd.DataFrame, dup: pd.Series, order: str) -> set:
    """The orders with exact duplicate rows that are repeated whole (each of their lines the
    same number of times, at least twice): an export overlap, not a second scan of one item.
    `flat` has nested values as text, so rows can be compared."""
    hit = flat.loc[dup, order].dropna().unique()
    rows = flat[flat[order].isin(hit)]
    if rows.empty:
        return set()
    counts = rows.groupby(list(rows.columns), dropna=False).size()
    per = counts.groupby(level=list(rows.columns).index(order)).agg(["nunique", "min"])
    return set(per.index[(per["nunique"] == 1) & (per["min"] >= 2)])


def _null_pct(s: pd.Series) -> float:
    return round(float(s.isna().mean() * 100), 1) if len(s) else 0.0


def _text_cols(df: pd.DataFrame, skip: set[str]) -> list[str]:
    return [c for c in df.columns if c not in skip and pd.api.types.is_string_dtype(df[c])]


def _shown(col: str, value: str, pii: set[str]) -> str:
    """A label as the audit may quote it: masked, and never from a PII column."""
    return "[REDACTED]" if col in pii or is_pii_column(col) else repr(mask_pii(value))[:40]


def _fingerprint(s: pd.Series) -> pd.Series:
    return s.str.strip().str.replace(r"\s+", " ", regex=True).str.casefold()


async def run_cleaning_agent(session: AnalysisSession, drop_high_null: bool = False
                             ) -> tuple[pd.DataFrame, CleaningAudit]:
    """Clean the session's frame. Returns (cleaned_df, CleaningAudit) and stores the cleaned
    parquet back on the session. The frame keeps the input's row labels."""
    ctx = build_context(session, drop_high_null)
    df, audit = clean(ctx)
    if session.data_contract and session.data_contract.deidentification:
        # Patient data: ingestion's de-identification steps head the audit (Phase 5.5).
        deid = session.data_contract.deidentification
        audit.decisions = [*deid, *audit.decisions]
        audit.duplicates_removed += sum(d.rows_removed for d in deid if d.operation == "drop_duplicates")
        audit.summary = (f"Patient data de-identified first ({len(deid)} steps: identifiers "
                         f"withheld or pseudonymised, small groups pooled). " + audit.summary)
    dropped = {d.column for d in audit.decisions if d.operation == "drop_column"}
    if dropped and session.data_contract:   # downstream agents read columns from the contract
        c = session.data_contract
        c.columns = [ci for ci in c.columns if ci.name not in dropped]
        c.column_count = len(c.columns)
        for name in ("date_columns", "id_columns", "metric_columns"):
            setattr(c, name, [x for x in getattr(c, name) if x not in dropped])
        c.roles = {r: col for r, col in c.roles.items() if col not in dropped}
        c.sample_rows = [{k: v for k, v in r.items() if k not in dropped} for r in c.sample_rows]
    buf = io.BytesIO()
    df.to_parquet(buf, index=False)
    session.__dict__["_df_parquet"] = buf.getvalue()
    return df, audit


def clean(ctx: CleanContext) -> tuple[pd.DataFrame, CleaningAudit]:
    before = ctx.df
    df = before.copy()
    decisions: list[CleaningDecision] = []
    # Codes are identifiers wherever the rules run, whoever built the context.
    ids = ctx.ids | {c for c in df.columns if _code_like(df[c])}
    protected = ids | ctx.dates | ctx.targets

    def log(column: str, operation: str, strategy: str, reason: str, rows: int,
            before_pct: float = 0.0, after_pct: float = 0.0, removed: int = 0) -> None:
        decisions.append(CleaningDecision(
            column=column, operation=operation, strategy=strategy, reason=reason,
            rows_affected=rows, before_null_pct=before_pct, after_null_pct=after_pct,
            rows_removed=removed))

    # 1. Triage: obvious non-analytic columns are left exactly as they are. Nested values
    #    (lists / objects from an API) can't be compared, parsed or merged: skipped too.
    nested = [c for c in df.columns if df[c].dtype == object
              and df[c].map(lambda v: isinstance(v, (list, dict, set, tuple))).any()]
    skip: set[str] = set(nested)
    for col in nested:
        log(col, "skip", "nested", "Nested values (lists / objects) — left as-is",
            int(df[col].notna().sum()))
    for kind, cols in triage_columns(df.drop(columns=nested)).items():
        strategy = kind.removesuffix("_cols")
        for col in cols:
            skip.add(col)
            log(col, "skip", strategy, _SKIP_REASON[strategy], int(df[col].notna().sum()))

    # 2. Placeholder text means missing.
    for col in _text_cols(df, skip):
        s = df[col]
        hit = s.notna() & s.astype(str).str.strip().str.casefold().isin(_SENTINELS)
        n = int(hit.sum())
        if n:
            pct = _null_pct(s)
            df[col] = s.mask(hit)
            log(col, "sentinel_null", "to_missing",
                f"{n} placeholder values ('-', '?', 'N/A' …) read as missing.",
                n, pct, _null_pct(df[col]))

    # 3. Types: numbers, then dates. IDs and codes stay as they are.
    coerced: list[str] = []
    for col in df.columns:
        if col in skip or col in ids:
            continue
        s = df[col]
        new, strategy, how = None, "", ""
        if pd.api.types.is_string_dtype(s):
            res = try_numeric_conversion(s)
            if res is not None:
                new, strategy = res[0], "to_numeric"
                how = ("Stripped " + ", ".join(res[1])) if res[1] != ["parsed as numeric"] \
                    else "Parsed as numeric"
        if new is None and (pd.api.types.is_string_dtype(s) or col in ctx.dates):
            new = detect_dates_aggressive(s)
            strategy, how = "to_datetime", "Parsed as dates"
        if new is None:
            continue
        lost = int((s.notna() & new.isna()).sum())
        pct = _null_pct(s)
        df[col] = new
        coerced.append(col)
        reason = f"{how}." + (f" {lost} values could not be read and are now missing."
                              if lost else "")
        log(col, "type_coerce", strategy, reason, int(new.notna().sum()), pct, _null_pct(new))

    # 3b. The domain pack: numeric codes for "unknown" (9999 units) and ids read as floats.
    if ctx.pack:
        for role, codes in ctx.pack.sentinels.items():
            col = ctx.roles.get(role)
            if col is None or not pd.api.types.is_numeric_dtype(df[col]):
                continue
            hit = df[col].isin(codes)
            others = df.loc[~hit, col].abs().max()
            # a code stands apart from the real values (₹9,999 among ₹500–₹12,000 is a price)
            hit &= (df[col] < 0) | (df[col] >= 2 * others) if pd.notna(others) else hit
            if n := int(hit.sum()):
                pct, found = _null_pct(df[col]), sorted(df.loc[hit, col].unique())
                df[col] = df[col].mask(hit)
                log(col, "sentinel_null", "numeric_sentinel",
                    f"{n} values coded {', '.join(f'{c:g}' for c in found)} (the export's 'unknown', "
                    f"not a real {role.replace('_', ' ')}) read as missing.", n, pct, _null_pct(df[col]))
        for role, col in ctx.roles.items():
            s = df[col]
            if ctx.pack.roles[role].kind == "id" and pd.api.types.is_float_dtype(s) and \
                    s.notna().any() and (s.dropna() % 1 == 0).all():
                df[col] = s.astype("Int64")
                log(col, "type_coerce", "whole_number_id", "Identifiers read as decimals "
                    "(17850.0, because some are blank) shown as whole numbers.", int(s.notna().sum()),
                    _null_pct(s), _null_pct(s))

    # 4. Label variants: trim, and merge spellings equal up to case and spacing.
    for col in _text_cols(df, skip | ids):
        s = df[col]
        v = s.dropna().astype(str)
        if v.empty:
            continue
        counts = Counter(v)
        keys = _fingerprint(v)
        mapping: dict[str, str] = {}
        for _, group in v.groupby(keys):
            spellings = sorted(set(group), key=lambda x: (-counts[x], x))
            canon = " ".join(spellings[0].split())
            mapping.update({sp: canon for sp in spellings if sp != canon})
        if not mapping:
            continue
        changed = s.isin(list(mapping))
        df[col] = s.where(~changed, s.map(mapping))
        merged = sorted({c for c in mapping.values()})[:3]
        examples = "; ".join(
            f"{', '.join(_shown(col, k, ctx.pii) for k in mapping if mapping[k] == c)[:80]} → "
            f"{_shown(col, c, ctx.pii)}"
            for c in merged)
        log(col, "normalize", "merge_variants",
            f"Merged spelling variants: {examples}.", int(changed.sum()))

    # 5. Exact duplicate rows, when a row key says what one record is. Without one, identical
    #    rows can be real repeats (two equal purchases, two equal survey answers): kept, flagged.
    flat = df.assign(**{c: df[c].map(repr) for c in nested})
    dup = flat.duplicated()
    n_dup = int(dup.sum())
    keys = [c for c in ids if c in df.columns
            and df[c].nunique() >= 0.9 * max(int(df[c].notna().sum()), 1)]
    pack = ctx.pack
    order = ctx.roles.get(pack.order_key) if pack and pack.order_key else None
    item = next((ctx.roles[r] for r in (pack.item_roles if pack else []) if r in ctx.roles), None)
    if n_dup and not keys and order and item and not ctx.deidentified:   # whole orders: any match
        # Order lines: an export overlap repeats whole orders (their copies go); one line
        # repeated inside an order is a second scan of the item (kept, flagged). Per order.
        whole = _whole_orders(flat, dup, order)
        overlap = dup & flat[order].isin(whole)
        if n := int(overlap.sum()):
            df, flat, dup = df[~overlap], flat[~overlap], dup[~overlap]
            log("*", "drop_duplicates", "exact", f"Removed {n} exact duplicate rows: "
                f"{len(whole)} orders exported more than once (same {order} + {item} and every "
                "other value).", n, removed=n)
            n_dup = int(dup.sum())
        if n_dup:
            log("*", "flag", "repeated_rows", f"{n_dup} rows repeat another row exactly; kept, as "
                "a second scan of an item is a real sale.", n_dup)
            n_dup = 0
    if n_dup and keys and not ctx.deidentified:
        df = df[~dup]
        log("*", "drop_duplicates", "exact", f"Removed {n_dup} exact duplicate rows "
            f"(same {keys[0]} and every other value).", n_dup, removed=n_dup)
    elif n_dup:
        why = ("the patient data was de-identified first, and duplicates were judged on the "
               "original rows" if ctx.deidentified else
               "there is no ID column to tell a copy from a repeated record")
        log("*", "flag", "repeated_rows", f"{n_dup} rows repeat another row exactly; kept, as "
            f"{why}.", n_dup)

    # 5b. Order-level fields on an order's first line only (the layout, seen in the data, not
    #     guessed from the domain): carried down the order.
    if ctx.pack and order:
        by = df[order]
        for role in ctx.pack.order_fields:
            col = ctx.roles.get(role)
            if col is None or col == order or not df[col].isna().any() or not first_line_only(df, by, col):
                continue
            values = df[col].groupby(by).transform("nunique")
            gap = df[col].isna() & by.notna() & values.eq(1)
            if n := int(gap.sum()):
                pct = _null_pct(df[col])
                df.loc[gap, col] = df[col].groupby(by).transform("first")[gap]
                log(col, "fill_within_order", "order_value",
                    f"{n} lines took their order's own {role.replace('_', ' ')} (the export writes it "
                    "on an order's first line only).", n, pct, _null_pct(df[col]))
            mixed = int(df[col].groupby(by).transform("nunique").gt(1).sum())
            if mixed:
                log(col, "flag", "order_conflict", f"{mixed} lines belong to orders with two "
                    f"different {role.replace('_', ' ')} values; left as they are.", mixed)
    grain = None
    if ctx.pack:
        grain = ("aggregate" if not order else
                 "line" if df[order].dropna().duplicated().any() else "order")

    # 6. Rows with no value at all.
    n = len(df)
    df = df[df.notna().any(axis=1)]
    if len(df) < n:
        log("*", "drop_rows", "all_missing", f"Removed {n - len(df)} empty rows.",
            n - len(df), removed=n - len(df))

    # 7. Missing values: labels become "Unknown"; everything else stays missing.
    high_null: list[str] = []
    for col in df.columns:
        missing = int(df[col].isna().sum())
        if col in skip or not missing:
            continue
        pct = _null_pct(df[col])
        known = len(df) - missing
        first = df[order].notna() & ~df[order].duplicated() if ctx.pack and order else None
        first_pct = _null_pct(df.loc[first, col]) if first is not None and first.any() else 0.0
        if (ctx.pack and order and col != order and first_line_only(df, df[order], col)
                and first_pct <= HIGH_NULL_PCT):
            # an order-level field written on each order's first line (Shopify's discount, total,
            # billing name): blank later lines are the layout, never "missing" or "Unknown". What
            # is missing is judged on the orders' first lines (a blank there is a real gap)
            log(col, "keep", "order_level", f"Written on each order's first line only, so later "
                f"lines are blank by the export's layout; {first_pct:g}% of orders have no value. "
                "An order-level field, kept as it is.", 0, pct, first_pct)
            continue
        if pct > HIGH_NULL_PCT:
            high_null.append(col)
            share = ">99.9%" if known and pct >= 99.9 else f"{pct:g}%"   # never "100%" with data
            log(col, "keep", "high_null", f"{missing} of {len(df)} values missing ({share}): too "
                "empty to use with confidence; left as missing.", 0, pct, pct)
        elif (pd.api.types.is_string_dtype(df[col]) and col not in protected
              and col not in coerced):
            spelled = next((u for u in df[col].dropna().unique()
                            if str(u).casefold() == UNKNOWN.casefold()), UNKNOWN)
            df[col] = df[col].fillna(spelled)
            log(col, "fill_unknown", "unknown_label",
                f"{missing} missing labels grouped as '{spelled}' (not guessed).", missing, pct, 0.0)
        else:
            role = ("identifier" if col in ids else "date" if col in ctx.dates
                    else "metric of the question" if col in ctx.targets
                    else "number" if pd.api.types.is_numeric_dtype(df[col]) else "value")
            log(col, "keep", "left_missing",
                f"{missing} missing ({pct:g}%), {role}: left as missing; totals and averages "
                f"use the {known} known rows.", 0, pct, pct)

    # 8. The user's choice at the high-null gate.
    if ctx.drop_high_null:
        for col in high_null:
            df = df.drop(columns=col)
            log(col, "drop_column", "user_choice", "Dropped at your request (mostly empty).", 0)

    # 9. Outliers: flagged, never removed.
    flat = df.drop(columns=[c for c in nested if c in df.columns])
    _, anomaly_report = detect_anomalies(flat, classify_columns(flat))
    outliers = [f.message for f in anomaly_report.flags]

    # 9b. The domain pack's row kinds and valid ranges: flagged and kept. KPIs leave them out
    #     by rule (domain_pack.row_kinds); the rows stay for any other question. Roles of a
    #     column dropped at the high-null gate are gone.
    roles = {r: c for r, c in ctx.roles.items() if c in df.columns}
    currency, other = None, 0
    if ctx.pack:
        kinds = row_kinds(df, roles, ctx.pack).value_counts()
        for kind in ("return", "refunded", "cancel", "test", "non_product"):
            what = ctx.pack.row_flags.get(kind, f"{ctx.pack.label(kind)}: left out of the figures")
            if n := int(kinds.get(kind, 0)):
                log("*", "flag", kind, f"{n} {what}; kept in the data.", n)
        _, currency, other = main_currency(df, roles)
        if other:
            log(roles["currency"], "flag", "other_currency", f"{other} rows in other currencies "
                f"than {currency}; kept, left out of money totals.", other)
        for role, bounds in ctx.pack.ranges.items():
            col = roles.get(role)
            if col is None or not pd.api.types.is_numeric_dtype(df[col]):
                continue
            s = df[col]
            bad = pd.Series(False, index=df.index)
            for op, v in bounds.items():
                bad |= {"gt": s <= v, "ge": s < v, "lt": s >= v, "le": s > v}[op]
            if n := int(bad.sum()):
                log(col, "flag", "out_of_range", f"{n} values outside the valid range "
                    f"({', '.join(f'{op} {v:g}' for op, v in bounds.items())}); kept, check them.", n)

    rows_before, rows_after = len(before), len(df)
    for rule, reason in (
            ("unreconciled_rows", unreconciled_rows(rows_before, rows_after, decisions)),
            ("unlogged_changes", unlogged_changes(before, df, {d.column for d in decisions})),
            # an order's own value carried down its lines is not a fill (5b): those columns only
            ("protected_filled", protected_filled(before, df, protected - {
                d.column for d in decisions if d.operation == "fill_within_order"}))):
        if reason:   # a bug in the rules above, never the user's data: fail loudly
            trace_verdict("cleaning", CLEANING_CARD.version, rule)
            raise GuardrailViolation("cleaning", reason,
                                     "The cleaning step broke its own audit rules; nothing was changed.")

    duplicates = sum(d.rows_removed for d in decisions if d.operation == "drop_duplicates")
    audit = CleaningAudit(
        decisions=decisions,
        rows_before=rows_before,
        rows_after=rows_after,
        columns_coerced=coerced,
        duplicates_removed=duplicates,
        outliers_flagged=outliers,
        high_null_columns=[] if ctx.drop_high_null else high_null,
        summary=_build_summary(decisions, outliers, high_null, rows_before, rows_after,
                               anomaly_report.methodology_note),
        anomaly_report=anomaly_report,
        skipped_columns=sorted(skip),
        date_ranges=_date_ranges(df, ctx.pii),
        grain=grain,
        partial_months={c: [str(df[c].max().to_period("M")), int(df[c].max().day)]
                        for c in _date_ranges(df, ctx.pii) if partial_last_month(df[c])},
        main_currency=currency if ctx.pack and other else None,
    )
    return df, audit


def _date_ranges(df: pd.DataFrame, pii: set[str] = frozenset()) -> dict[str, list]:
    """[first, last, months spanned] per parsed date column: the data's period, computed, so the
    report never has to guess it from a few sample rows. Not a birth date: it is no period, and
    its ends are two people's birthdays."""
    out: dict[str, list] = {}
    for col in df.columns:
        if col in pii or not pd.api.types.is_datetime64_any_dtype(df[col]):
            continue
        s = df[col].dropna()
        if s.empty:
            continue
        lo, hi = s.min(), s.max()
        out[str(col)] = [lo.strftime("%Y-%m-%d"), hi.strftime("%Y-%m-%d"),
                         (hi.year - lo.year) * 12 + hi.month - lo.month + 1]
    return out


def _build_summary(decisions: list[CleaningDecision], outliers: list[str], high_null: list[str],
                   rows_before: int, rows_after: int, anomaly_methodology: str = "") -> str:
    ops = Counter(d.operation for d in decisions)
    removed = {d.operation: d.rows_removed for d in decisions if d.rows_removed}
    parts: list[str] = []
    if removed.get("drop_duplicates"):
        parts.append(f"Removed {removed['drop_duplicates']} duplicate rows.")
    if removed.get("drop_rows"):
        parts.append(f"Removed {removed['drop_rows']} empty rows.")
    if ops["type_coerce"]:
        parts.append(f"Parsed numbers or dates in {ops['type_coerce']} column(s).")
    if ops["normalize"]:
        parts.append(f"Merged label spelling variants in {ops['normalize']} column(s).")
    if ops["fill_unknown"]:
        parts.append(f"Grouped missing labels as 'Unknown' in {ops['fill_unknown']} column(s).")
    kept = [d.column for d in decisions if d.operation == "keep" and d.strategy == "left_missing"]
    if kept:
        parts.append(f"Left missing values as missing in {len(kept)} column(s); nothing was imputed.")
    if outliers:
        names = sorted({f.split(" in ")[1].split()[0] if " in " in f else f.split()[0]
                        for f in outliers})
        parts.append(f"Outliers flagged (kept) in: {', '.join(names)}.")
    if anomaly_methodology:
        parts.append(anomaly_methodology)
    if high_null:
        parts.append(f"More than 40% missing in: {', '.join(high_null)} — review recommended.")
    parts.append(f"Final dataset: {rows_after} rows (started with {rows_before}).")
    return " ".join(parts)


def build_evidence(audit: CleaningAudit) -> Evidence:
    """One Evidence item for what cleaning changed, from the audit only: counts and the
    decisions' reasons, no cell values beyond masked labels."""
    # Medium only when it matters: a mostly-empty column, or > 5% of a column unreadable.
    risky = [d.column for d in audit.decisions
             if d.after_null_pct > HIGH_NULL_PCT
             or (d.operation == "type_coerce" and d.after_null_pct - d.before_null_pct > 5)]
    level, why = (("medium", f"many missing or unreadable values in {', '.join(risky[:3])}")
                  if risky else ("high", "rule-based; every change below is logged"))
    return Evidence(
        id="clean:audit", kind="cleaning",
        claim=f"Cleaning: {audit.rows_before} → {audit.rows_after} rows, "
              f"{sum(d.operation != 'skip' for d in audit.decisions)} logged changes",
        code=f"# {CLEANING_CARD.name} {CLEANING_CARD.version}\n" + "\n".join(
            f"{d.operation}({d.column!r}, {d.strategy})" for d in audit.decisions),
        result_excerpt=json.dumps({"rows_before": audit.rows_before, "rows_after": audit.rows_after,
                                   "decisions": [[d.column, d.operation, d.rows_affected,
                                                  d.rows_removed] for d in audit.decisions]})[:500],
        row_count=audit.rows_after, confidence=level, confidence_reason=why,
        assumptions=[f"{d.column}: {d.reason}" for d in audit.decisions][:20],
    )
