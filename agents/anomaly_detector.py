"""
PROMPT-5: robust per-column anomaly detector.

The previous implementation flagged 40-50% of rows on right-skewed columns
(income, transaction amount, fare) because raw IQR×1.5 catches the entire
right tail of a log-normal distribution. This module replaces it with rules
that work for any dataset shape:

1. Column eligibility filter (skip IDs, binary, low-card ordinals, datetimes,
   columns with >50% missing values).
2. Skew-aware bounds — apply log1p to highly-skewed columns before IQR so the
   detector matches the *shape* of the distribution.
3. Two-tier flagging — extreme (Q±3·IQR) for alerts, mild (Q±1.5·IQR) for
   analyst-tab detail only.
4. 5% heavy-tail guard per column — wide-tailed distributions get an
   informational note, not a flag flood.
5. 10% global cap — if total distinct flagged rows exceed 10% of the dataset,
   re-run in extreme-only mode and record the switch in the methodology note.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from orchestrator.state import AnomalyDetectionReport, AnomalyFlag


# ── Tunable thresholds (centralized so they're easy to audit) ────────────────

_LOG_TRANSFORM_SKEW = 2.0          # |skew| above this triggers log1p before IQR
_MILD_K = 1.5
_EXTREME_K = 3.0
_HEAVY_TAIL_PCT = 0.05             # per-column: skip flagging when extreme% > 5%
_GLOBAL_CAP_PCT = 0.10             # global: re-run in extreme-only when row% > 10%
_MIN_UNIQUE_FOR_DETECTION = 5      # below this, treat as ordinal/low-cardinality and skip
_MAX_NULL_PCT_FOR_DETECTION = 50.0 # above this, the column already has a missing-data alert


# ── Public API ───────────────────────────────────────────────────────────────

def detect_anomalies(
    df: pd.DataFrame, column_roles: dict
) -> tuple[pd.DataFrame, AnomalyDetectionReport]:
    """Detect column-level anomalies. Returns the (unchanged) DataFrame plus a
    structured report. The DataFrame is returned for symmetry with cleaning
    functions in this codebase — callers may add their own flag columns
    downstream."""
    if df is None or len(df) == 0:
        return df, AnomalyDetectionReport()

    eligible, skipped = _filter_columns(df, column_roles)

    # Phase 1: detect both mild and extreme on every eligible column.
    flags = [_analyze_column(df, col, extreme_only=False) for col in eligible]
    flags = [f for f in flags if f is not None]

    # Phase 2: 10% global cap. Count distinct flagged rows across all columns.
    n = len(df)
    methodology_note = ""

    flagged_rows = _union_flagged_rows(df, flags, include_mild=True)
    if n > 0 and len(flagged_rows) / n > _GLOBAL_CAP_PCT:
        # Re-run in extreme-only mode (drops the mild tier entirely).
        flags = [_analyze_column(df, col, extreme_only=True) for col in eligible]
        flags = [f for f in flags if f is not None]
        pct_initial = len(flagged_rows) / n * 100
        methodology_note = (
            f"Initial outlier flagging marked {pct_initial:.1f}% of rows — exceeded "
            f"the {_GLOBAL_CAP_PCT * 100:.0f}% cap. Switched to extreme-only mode "
            f"(Q ± {_EXTREME_K:g}×IQR) across all columns."
        )

    return df, AnomalyDetectionReport(
        flags=flags,
        methodology_note=methodology_note,
        eligible_columns=eligible,
        skipped_columns=skipped,
    )


# ── Column eligibility ───────────────────────────────────────────────────────

def _filter_columns(
    df: pd.DataFrame, column_roles: dict
) -> tuple[list[str], dict[str, str]]:
    """Drop columns that anomaly detection should never run on, recording why.
    Dataset-agnostic: rules reference column_roles + intrinsic shape only."""
    id_cols: set[str] = set(column_roles.get("id_cols") or [])
    binary_cols: set[str] = set(column_roles.get("binary_cols") or [])
    datetime_cols: set[str] = set(column_roles.get("datetime_cols") or [])
    skip_cols: set[str] = set(column_roles.get("skip_cols") or [])
    ordinal_cols: set[str] = set(column_roles.get("ordinal_cols") or [])

    eligible: list[str] = []
    skipped: dict[str, str] = {}

    for col in df.columns:
        if col in id_cols:
            skipped[col] = "id column"
            continue
        if col in binary_cols:
            skipped[col] = "binary column (only 2 values)"
            continue
        if col in datetime_cols:
            skipped[col] = "datetime column"
            continue
        if col in skip_cols:
            skipped[col] = "constant or unsupported"
            continue
        if col in ordinal_cols:
            skipped[col] = "ordinal (low-cardinality numeric, e.g. Likert/class)"
            continue
        if not pd.api.types.is_numeric_dtype(df[col]):
            skipped[col] = "non-numeric"
            continue
        # >50% missing — already gets a "missing data" alert; don't double-flag.
        null_pct = float(df[col].isna().mean() * 100)
        if null_pct > _MAX_NULL_PCT_FOR_DETECTION:
            skipped[col] = f"{null_pct:.1f}% missing"
            continue
        # Low-cardinality numeric (Likert scales 1-5, class labels) — no concept
        # of "outlier" in a discrete small set.
        n_unique = int(df[col].dropna().nunique())
        if n_unique <= _MIN_UNIQUE_FOR_DETECTION:
            skipped[col] = f"low cardinality ({n_unique} unique values)"
            continue
        eligible.append(col)

    return eligible, skipped


# ── Per-column analysis ──────────────────────────────────────────────────────

def _analyze_column(
    df: pd.DataFrame, col: str, *, extreme_only: bool
) -> AnomalyFlag | None:
    """Compute the outlier bounds for a single numeric column. Returns None if
    the column has too little signal to assess (zero IQR, all-NaN, etc).
    `extreme_only=True` skips the mild tier (used after the 10% global cap)."""
    s = pd.to_numeric(df[col], errors="coerce").dropna()
    if len(s) < 5:
        return None

    skew = _safe_skew(s)
    used_log = abs(skew) > _LOG_TRANSFORM_SKEW

    # Work in the analysis space (log1p when highly skewed), but report bounds
    # in the original scale so the UI can show "$4,521" not "log(4521)".
    if used_log:
        # log1p needs non-negative input. If any value is negative we shift by
        # the absolute minimum to retain the relative shape.
        shift = max(0.0, -float(s.min()) + 1e-9)
        analysis = np.log1p(s + shift)
    else:
        shift = 0.0
        analysis = s

    q1 = float(analysis.quantile(0.25))
    q3 = float(analysis.quantile(0.75))
    iqr = q3 - q1
    if iqr <= 0:
        return None

    lo_extreme_a = q1 - _EXTREME_K * iqr
    hi_extreme_a = q3 + _EXTREME_K * iqr
    lo_mild_a = q1 - _MILD_K * iqr
    hi_mild_a = q3 + _MILD_K * iqr

    extreme_mask = (analysis < lo_extreme_a) | (analysis > hi_extreme_a)
    # Parenthesize: "(below mild OR above mild) AND not already extreme".
    mild_mask = ((analysis < lo_mild_a) | (analysis > hi_mild_a)) & (~extreme_mask)

    extreme_count = int(extreme_mask.sum())
    mild_count = int(mild_mask.sum())
    if extreme_only:
        mild_count = 0
    extreme_pct = extreme_count / len(s) * 100

    # Inverse-transform all bounds once so downstream code never deals with
    # log-space numbers — UI, prompt, union-row calc all use originals.
    lo_orig = _to_original(lo_extreme_a, used_log, shift)
    hi_orig = _to_original(hi_extreme_a, used_log, shift)
    lo_mild_orig = _to_original(lo_mild_a, used_log, shift)
    hi_mild_orig = _to_original(hi_mild_a, used_log, shift)

    # 5% per-column heavy-tail guard — flagging the right tail of a power-law
    # distribution is meaningless. Note it but suppress the flag.
    if extreme_pct > _HEAVY_TAIL_PCT * 100:
        return AnomalyFlag(
            column=col,
            extreme_count=0,
            mild_count=0,
            extreme_pct=extreme_pct,
            lower_bound=lo_orig,
            upper_bound=hi_orig,
            mild_lower_bound=lo_mild_orig,
            mild_upper_bound=hi_mild_orig,
            used_log_transform=used_log,
            heavy_tailed=True,
            message=(
                f"{col} has a heavy-tailed distribution — {extreme_pct:.1f}% of values "
                "exceed the outlier threshold. This likely reflects natural variation, "
                "not anomalies."
            ),
        )

    if extreme_count == 0 and mild_count == 0:
        return None  # nothing to report

    # Build the human-readable message. Show the boundary that matters: the
    # tail where the outliers actually sit. If both tails are active, show both.
    extremes_above = int((analysis > hi_extreme_a).sum())
    extremes_below = int((analysis < lo_extreme_a).sum())
    if extremes_above and not extremes_below:
        threshold_phrase = f"values > {_fmt_bound(hi_orig)}"
    elif extremes_below and not extremes_above:
        threshold_phrase = f"values < {_fmt_bound(lo_orig)}"
    else:
        threshold_phrase = f"values outside [{_fmt_bound(lo_orig)}, {_fmt_bound(hi_orig)}]"

    if extreme_count:
        message = (
            f"{extreme_count} extreme outlier{'s' if extreme_count != 1 else ''} in {col} "
            f"({extreme_pct:.1f}% of data, threshold: {threshold_phrase})"
        )
    else:
        message = (
            f"{mild_count} mild outlier{'s' if mild_count != 1 else ''} in {col} "
            f"(threshold: {threshold_phrase}) — see analyst tab"
        )

    return AnomalyFlag(
        column=col,
        extreme_count=extreme_count,
        mild_count=mild_count,
        extreme_pct=extreme_pct,
        lower_bound=lo_orig,
        upper_bound=hi_orig,
        mild_lower_bound=lo_mild_orig,
        mild_upper_bound=hi_mild_orig,
        used_log_transform=used_log,
        heavy_tailed=False,
        message=message,
    )


# ── Helpers ──────────────────────────────────────────────────────────────────

def _safe_skew(s: pd.Series) -> float:
    """Return Fisher–Pearson skewness, or 0.0 if it can't be computed."""
    try:
        v = float(s.skew())
        return v if math.isfinite(v) else 0.0
    except Exception:
        return 0.0


def _to_original(value: float, used_log: bool, shift: float) -> float:
    """Invert the log1p transform so reported bounds match the column's
    original units (currency, count, sensor reading)."""
    if not used_log:
        return float(value)
    try:
        return float(math.expm1(value)) - shift
    except OverflowError:
        return float("inf") if value > 0 else float("-inf")


def _fmt_bound(v: float) -> str:
    """Compact, business-readable formatting for a threshold value."""
    if not math.isfinite(v):
        return "—"
    abs_v = abs(v)
    if abs_v >= 1_000_000:
        return f"{v / 1_000_000:.2f}M"
    if abs_v >= 1_000:
        return f"{v:,.0f}"
    if abs_v >= 1:
        return f"{v:,.2f}"
    return f"{v:.3f}"


def _union_flagged_rows(
    df: pd.DataFrame, flags: list[AnomalyFlag], *, include_mild: bool
) -> pd.Index:
    """Indices of rows flagged by ANY column in the report. Used by the 10%
    global cap. `include_mild` controls whether mild outliers count toward the
    union (Phase 1 yes, Phase 2 no). Uses the bounds stored on each flag (in
    original units) so log1p-transformed columns are handled correctly."""
    if not flags:
        return pd.Index([])
    union = pd.Index([])
    for f in flags:
        if f.heavy_tailed or f.column not in df.columns:
            continue
        s = pd.to_numeric(df[f.column], errors="coerce")
        # Pick the tighter bounds when counting mild; tighter still for extreme.
        lo = f.mild_lower_bound if include_mild else f.lower_bound
        hi = f.mild_upper_bound if include_mild else f.upper_bound
        mask = (s < lo) | (s > hi)
        idx = s.index[mask.fillna(False)]
        union = union.union(idx)
    return union
