"""
Visualization Agent — smart chart selection from a DataFrame.

Two stages:
  1. `classify_columns(df)` — bucket every column into a role (id / target /
     binary / categorical / continuous / datetime / skip / ordinal).
  2. `auto_generate_charts(df, roles, query)` — pick the highest-value chart
     types based on those roles. Priorities:
       a) target rate by top categorical features
       b) continuous distribution split by target
       c) time series (auto-granularity)
       d) value counts / histograms / scatter when no target exists

Drives off the *raw* DataFrame so chart correctness no longer depends on the
LLM-generated analysis code in E2B emitting exact distribution payloads.
"""
from __future__ import annotations

import io
import math
import re
import uuid

import numpy as np
import pandas as pd

from orchestrator.state import (
    AnalysisSession,
    ChartConfig,
    ChartType,
    DataContract,
    DataNarrative,
    KPI,
    TargetedParams,
)


# ── Palettes ─────────────────────────────────────────────────────────────────

# B1: Tableau 10 categorical palette — distinct from indigo/purple UI accents.
_PALETTE = [
    "#4E79A7", "#F28E2B", "#E15759", "#76B7B2", "#59A14F",
    "#EDC948", "#B07AA1", "#FF9DA7", "#9C755F", "#BAB0AC",
]
_NEUTRAL_TEAL = "#76B7B2"

# Semantic colors for binary target values. Used consistently across every
# chart that breaks out by the target so the eye can track positive/negative.
_POSITIVE = "#1D9E75"
_NEGATIVE = "#A32D2D"
# PROMPT-2: rate-vs-overall coloring on target-rate charts. Above-average green,
# below-average warm coral so categories instantly read as good/bad.
_BELOW_AVERAGE = "#D85A30"

# Sequential ordinal ramp (light → dark) — used for ordered low-cardinality categories.
_ORDINAL_RAMP = ["#D4E6F5", "#9CC5E5", "#5E97C9", "#2F6CA8", "#1B4577"]


# ── Name-based heuristics ────────────────────────────────────────────────────

_ID_NAME = re.compile(r"(?i)(^id$|_id$|_pk$|^index$|^key$|^row)")
_TARGET_NAME = re.compile(
    r"(?i)(target|label|outcome|class|result|survived|churn|default|fraud|"
    r"converted|conversion|click(ed)?|signup|win|loss|fail(ed)?)"
)
_ORDINAL_NAME = re.compile(r"(?i)(level|grade|tier|class|rating|priority|stage|rank|score)")
_POSITIVE_LABELS = {"yes", "true", "1", "t", "y", "positive", "survived", "churned", "win", "passed", "converted", "default"}


def _looks_like_sequential_id(s: pd.Series, n: int) -> bool:
    """True if the column looks like a row index / sequential id:
    whole-number values that fill an interval close to len(df)."""
    arr = s.dropna()
    if arr.empty or not pd.api.types.is_numeric_dtype(arr):
        return False
    if not pd.api.types.is_integer_dtype(arr):
        # Float column containing only whole numbers also counts.
        try:
            if not bool((arr % 1 == 0).all()):
                return False
        except Exception:
            return False
    nu = int(arr.nunique())
    if nu / max(n, 1) < 0.95:
        return False
    spread = float(arr.max()) - float(arr.min())
    return spread <= n * 1.5


# ── Stage 1: column classifier ───────────────────────────────────────────────

def classify_columns(df: pd.DataFrame) -> dict:
    """Bucket every column into a role. Returns a dict with the following keys:

    id_cols       — sequential integers (nunique/len > 0.95) or name matching the id regex.
    target_col    — auto-detected binary outcome column (or None).
    binary_cols   — exactly 2 unique non-null values.
    low_card_cat  — categorical / low-cardinality numeric with 3–10 unique values.
    high_card_cat — categorical with 11–30 unique values.
    continuous    — numeric with >15 unique values and not an id.
    datetime_cols — datetime dtype or string parseable as dates (>80%).
    ordinal_cols  — subset of low_card_cat/binary detected as ordered.
    skip_cols     — constants, >30-cardinality categoricals, all-null.
    """
    n = max(len(df), 1)
    roles: dict[str, list[str] | str | None] = {
        "id_cols": [],
        "target_col": None,
        "binary_cols": [],
        "low_card_cat": [],
        "high_card_cat": [],
        "continuous": [],
        "datetime_cols": [],
        "ordinal_cols": [],
        "skip_cols": [],
    }

    for col in df.columns:
        s = df[col].dropna()
        if s.empty:
            roles["skip_cols"].append(col)  # type: ignore[union-attr]
            continue

        nunique = int(s.nunique())

        # Constants
        if nunique <= 1:
            roles["skip_cols"].append(col)  # type: ignore[union-attr]
            continue

        # IDs — name match OR a sequential integer column (1..N or 0..N-1).
        # Continuous floats are near-unique too; restricting the uniqueness
        # rule to whole-number sequences prevents Age/Fare/revenue from being
        # mis-flagged as ids.
        name_is_id = bool(_ID_NAME.search(col))
        if name_is_id or _looks_like_sequential_id(s, n):
            roles["id_cols"].append(col)  # type: ignore[union-attr]
            continue

        # Datetime — native dtype or parseable strings
        if pd.api.types.is_datetime64_any_dtype(s):
            roles["datetime_cols"].append(col)  # type: ignore[union-attr]
            continue
        if s.dtype == object and nunique > 3:
            sample = s.head(100)
            try:
                parsed = pd.to_datetime(sample, errors="coerce")
                if parsed.notna().sum() / len(parsed) > 0.8:
                    roles["datetime_cols"].append(col)  # type: ignore[union-attr]
                    continue
            except Exception:
                pass

        # Binary
        if nunique == 2:
            roles["binary_cols"].append(col)  # type: ignore[union-attr]
            continue

        # Categorical (low / high cardinality)
        if not pd.api.types.is_numeric_dtype(s):
            if nunique <= 10:
                roles["low_card_cat"].append(col)  # type: ignore[union-attr]
            elif nunique <= 30:
                roles["high_card_cat"].append(col)  # type: ignore[union-attr]
            else:
                roles["skip_cols"].append(col)  # type: ignore[union-attr]
            continue

        # Numeric: low-unique → treat as categorical/ordinal; high-unique → continuous
        if nunique <= 10:
            roles["low_card_cat"].append(col)  # type: ignore[union-attr]
            continue
        if nunique <= 15:
            # Borderline — call it continuous so we get a histogram, not 15 bars
            roles["continuous"].append(col)  # type: ignore[union-attr]
            continue
        roles["continuous"].append(col)  # type: ignore[union-attr]

    # Ordinal detection (subset of low_card_cat + binary): name match OR small numeric set
    for col in list(roles["low_card_cat"]) + list(roles["binary_cols"]):  # type: ignore[arg-type]
        if _ORDINAL_NAME.search(col):
            roles["ordinal_cols"].append(col)  # type: ignore[union-attr]
            continue
        if pd.api.types.is_numeric_dtype(df[col]):
            nu = df[col].dropna().nunique()
            if 2 <= nu < 6:
                roles["ordinal_cols"].append(col)  # type: ignore[union-attr]

    # Target detection: binary cols with target-like name OR numeric {0,1}
    candidates: list[str] = []
    for col in roles["binary_cols"]:  # type: ignore[union-attr]
        s = df[col].dropna()
        name_match = bool(_TARGET_NAME.search(col))
        is_01 = False
        if pd.api.types.is_numeric_dtype(s):
            vals = set(pd.to_numeric(s, errors="coerce").dropna().unique())
            is_01 = vals in ({0, 1}, {0.0, 1.0})
        if name_match or is_01:
            candidates.append(col)

    if candidates:
        if len(candidates) == 1:
            roles["target_col"] = candidates[0]
        else:
            scored: list[tuple[str, float]] = []
            for c in candidates:
                t = _to_01(df[c])
                if t is None:
                    continue
                corrs = []
                for other in list(roles["continuous"])[:5]:  # type: ignore[arg-type]
                    try:
                        v = pd.to_numeric(df[other], errors="coerce")
                        r = t.corr(v)
                        if pd.notna(r):
                            corrs.append(abs(float(r)))
                    except Exception:
                        pass
                scored.append((c, sum(corrs) / len(corrs) if corrs else 0.0))
            scored.sort(key=lambda x: x[1], reverse=True)
            roles["target_col"] = scored[0][0] if scored else candidates[0]

    return roles


# ── Helpers ──────────────────────────────────────────────────────────────────

def _to_01(s: pd.Series) -> pd.Series | None:
    """Map a binary column to a 0/1 numeric Series. Picks the "positive" value
    via a label dictionary when string-typed; otherwise the larger numeric value
    is 1. Returns None if the column isn't binary."""
    vals = s.dropna().unique()
    if len(vals) != 2:
        return None
    if pd.api.types.is_numeric_dtype(s):
        nums = pd.to_numeric(s, errors="coerce")
        uvals = sorted(set(nums.dropna().unique()))
        if uvals == [0, 1] or uvals == [0.0, 1.0]:
            return nums.astype(float)
        return nums.map({uvals[0]: 0.0, uvals[1]: 1.0})
    # String binary — pick positive by label dictionary, fall back to sorted-order
    str_vals = [str(v) for v in vals]
    positive = next((v for v in str_vals if v.lower() in _POSITIVE_LABELS), None)
    if positive is None:
        positive = sorted(str_vals)[1]
    mapping = {v: (1.0 if str(v) == positive else 0.0) for v in vals}
    return s.map(mapping).astype(float)


def _target_color_map(df: pd.DataFrame, target: str) -> dict[str, str]:
    """Stable color assignment for each value of the target column.
    Positive value → green, negative → red. Used wherever the target is shown."""
    pos = _positive_value(df, target)
    if pos is None:
        return {}
    vals = df[target].dropna().unique()
    return {str(v): (_POSITIVE if str(v) == str(pos) else _NEGATIVE) for v in vals}


def _target_value_to_label(df: pd.DataFrame, target: str) -> dict[str, str]:
    """PROMPT-6 fix 2: raw target value → human-readable label
    ('0' → 'Did not survive', '1' → 'Survived'). Used by every chart that
    surfaces target values in legends / axis labels / data labels."""
    pos = _positive_value(df, target)
    vals = [str(v) for v in df[target].dropna().unique()]
    if pos is None or len(vals) != 2:
        return {v: v for v in vals}  # non-binary or undetectable → pass through
    neg_label, pos_label = _binary_label_pair(target)
    return {v: (pos_label if str(v) == str(pos) else neg_label) for v in vals}


def _sturges_bins(n: int) -> int:
    return max(5, int(math.ceil(math.log2(max(n, 2)) + 1)))


_INTEGER_COL_NAME_RE = re.compile(r"(?i)(age|year|count|qty|quantity|num|day|month|week)")


def _bin_label_formatter(col_name: str, series: pd.Series):
    """Return a function that formats a histogram bin edge as a string.
    Integers (by column name OR by values) → '24'; otherwise → '24.3'."""
    name_match = bool(_INTEGER_COL_NAME_RE.search(str(col_name)))
    int_share = 0.0
    try:
        s = pd.to_numeric(series, errors="coerce").dropna()
        if len(s) > 0:
            int_share = float(s.apply(float.is_integer).mean())
    except Exception:
        int_share = 0.0
    if name_match or int_share > 0.8:
        return lambda v: f"{int(round(v))}"
    return lambda v: f"{v:.1f}"


# PROMPT-2 fix 6: human-readable labels for binary target values, keyed by
# substring of the target column name. Fallback is ("No", "Yes").
_BINARY_LABELS: list[tuple[str, tuple[str, str]]] = [
    ("survived",  ("Did not survive", "Survived")),
    ("survive",   ("Did not survive", "Survived")),
    ("churn",     ("Retained",        "Churned")),
    ("fraud",     ("Legitimate",      "Fraudulent")),
    ("default",   ("No default",      "Defaulted")),
    ("conver",    ("Did not convert", "Converted")),
    ("click",     ("Did not click",   "Clicked")),
    ("signup",    ("Did not sign up", "Signed up")),
    ("win",       ("Did not win",     "Won")),
    ("loss",      ("Did not lose",    "Lost")),
    ("fail",      ("Passed",          "Failed")),
]


def _binary_label_pair(target_name: str) -> tuple[str, str]:
    """Return (negative_label, positive_label) for a binary target."""
    name_low = target_name.lower()
    for keyword, pair in _BINARY_LABELS:
        if keyword in name_low:
            return pair
    return ("No", "Yes")


def _positive_value(df: pd.DataFrame, target: str):
    """Return the raw value that represents the "positive" outcome for a binary
    target column. Numeric → larger value; string → label-dictionary match,
    falling back to sorted-order. Returns None if target isn't binary."""
    vals = df[target].dropna().unique()
    if len(vals) != 2:
        return None
    if pd.api.types.is_numeric_dtype(df[target]):
        nums = sorted(pd.to_numeric(df[target], errors="coerce").dropna().unique())
        return nums[1]
    str_vals = [str(v) for v in vals]
    positive = next((v for v in str_vals if v.lower() in _POSITIVE_LABELS), None)
    if positive is None:
        positive = sorted(str_vals)[1]
    return positive


# PROMPT-2 fix 1: "Survival Rate", "Churn Rate" etc. derived from the target name.
def _rate_label(target_name: str, narrative: "DataNarrative | None" = None) -> str:
    name = target_name.strip()
    name_low = name.lower()
    overrides = {
        "survived": "Survival Rate", "survive": "Survival Rate",
        "churn": "Churn Rate", "churned": "Churn Rate",
        "fraud": "Fraud Rate", "default": "Default Rate",
        "conver": "Conversion Rate", "click": "Click-through Rate",
        "signup": "Signup Rate", "fail": "Failure Rate",
    }
    for keyword, label in overrides.items():
        if keyword in name_low:
            return label
    # Prefer the LLM-generated display name + " Rate"; fall back to title-cased col.
    pretty = _display(target_name, narrative) if narrative else name.replace("_", " ").title()
    return f"{pretty} Rate"


# PROMPT-2 fix 2: sort categorical x-keys numerically when they parse as numbers.
def _sort_key_for(values) -> "callable | None":
    """If every value in the iterable parses as a number, return a numeric sort
    function. Else None (caller should fall back to natural ordering)."""
    try:
        for v in values:
            float(str(v))
        return lambda x: float(str(x))
    except (ValueError, TypeError):
        return None


# PROMPT-2 fix 3: skew-aware binning. For right-skewed continuous distributions
# (Sturges' rule with all data would dump 95% into one bar), clip the tail at
# the 95th percentile and append a "N values > X clipped" note to the title.
def _skew_aware_edges(
    s: pd.Series,
) -> tuple[np.ndarray, pd.Series, str]:
    """Return (bin_edges, possibly-clipped series, clip_note)."""
    s = pd.to_numeric(s, errors="coerce").dropna()
    if len(s) < 3:
        edges = np.histogram_bin_edges(s, bins=_sturges_bins(len(s)))
        return edges, s, ""
    skewness = float(s.skew())
    if abs(skewness) > 2:
        p95 = float(s.quantile(0.95))
        n_over = int((s > p95).sum())
        if n_over > 0:
            s_clipped = s[s <= p95]
            bins = _sturges_bins(len(s_clipped))
            edges = np.histogram_bin_edges(s_clipped, bins=bins)
            note = f" ({n_over} values > {p95:,.0f} clipped)"
            return edges, s_clipped, note
    bins = _sturges_bins(len(s))
    edges = np.histogram_bin_edges(s, bins=bins)
    return edges, s, ""


def _ranked_categoricals_by_target_separation(
    df: pd.DataFrame, target: str, cat_cols: list[str]
) -> list[str]:
    """Order categorical columns by how strongly they separate the target.
    Score = std-dev of target rate across groups (higher = more discriminative)."""
    t = _to_01(df[target])
    if t is None:
        return cat_cols
    ranked: list[tuple[str, float]] = []
    for c in cat_cols:
        try:
            grouped = pd.Series(t.values, index=df[c]).groupby(level=0).mean()
            if grouped.shape[0] < 2:
                continue
            ranked.append((c, float(grouped.std())))
        except Exception:
            continue
    ranked.sort(key=lambda x: x[1], reverse=True)
    return [c for c, _ in ranked]


def _best_correlated_pair(df: pd.DataFrame, cols: list[str], threshold: float = 0.3) -> tuple[str, str] | None:
    best: tuple[str, str] | None = None
    best_abs = threshold
    for i in range(len(cols)):
        a = pd.to_numeric(df[cols[i]], errors="coerce")
        for j in range(i + 1, len(cols)):
            b = pd.to_numeric(df[cols[j]], errors="coerce")
            try:
                r = a.corr(b)
                if pd.notna(r) and abs(r) > best_abs:
                    best_abs = abs(float(r))
                    best = (cols[i], cols[j])
            except Exception:
                continue
    return best


# ── Chart builders ───────────────────────────────────────────────────────────

def _build_target_rate_by_cat(
    df: pd.DataFrame, cat_col: str, target: str
) -> ChartConfig | None:
    sub = df[[cat_col, target]].dropna()
    if sub.empty or sub[cat_col].nunique() < 2:
        return None
    t = _to_01(sub[target])
    if t is None:
        return None

    rate = pd.Series(t.values, index=sub[cat_col]).groupby(level=0).mean()
    counts = sub.groupby(cat_col).size()
    if rate.empty:
        return None

    # PROMPT-2 fix 2: sort numerically when keys are number-parseable
    # (e.g., Pclass = 1,2,3), otherwise by rate descending as before.
    numeric_sort = _sort_key_for(rate.index)
    if numeric_sort is not None:
        rate = rate.sort_index(key=lambda idx: idx.map(numeric_sort)).head(10)
    else:
        rate = rate.sort_values(ascending=False).head(10)

    # PROMPT-2 fix 4: color each bar by whether it's above/below the overall
    # target rate across the full dataset (not just this slice).
    overall = float(_to_01(df[target]).mean())  # type: ignore[union-attr]

    # PROMPT-2 fix 1: pre-multiply by 100 so the frontend can render "%".
    rate_label = _rate_label(target)
    data = []
    colors = []
    for k, v in rate.items():
        pct = round(float(v) * 100, 1)
        data.append({cat_col: str(k), rate_label: pct, "n": int(counts.get(k, 0))})
        colors.append(_POSITIVE if float(v) >= overall else _BELOW_AVERAGE)

    return ChartConfig(
        id=str(uuid.uuid4()),
        chart_type=ChartType.BAR,
        title=f"{rate_label} by {cat_col}",
        data=data,
        x_key=cat_col,
        y_keys=[rate_label],
        colors=colors,
        x_label=cat_col,
        y_label=rate_label,
        show_legend=False,
        source_finding="target_rate_by_cat",
        value_format="percent",
    )


def _build_continuous_split_by_target(
    df: pd.DataFrame, num_col: str, target: str, target_colors: dict[str, str]
) -> ChartConfig | None:
    sub = df[[num_col, target]].copy()
    sub[num_col] = pd.to_numeric(sub[num_col], errors="coerce")
    sub = sub.dropna(subset=[num_col, target])
    if sub.empty:
        return None
    target_vals = [str(v) for v in sub[target].dropna().unique()]
    if not 2 <= len(target_vals) <= 5:
        return None

    # PROMPT-2 fix 3: skew-aware binning. Clip the tail at p95 for right-skewed
    # data so we don't get one giant bar + empty tail (e.g., Titanic Fare).
    edges, clipped, clip_note = _skew_aware_edges(sub[num_col])
    clipped_idx = clipped.index
    centers = [(edges[i] + edges[i + 1]) / 2 for i in range(len(edges) - 1)]

    # PROMPT-2 fix 6: humanize binary target legend labels.
    pos_val = _positive_value(df, target)
    is_binary = pos_val is not None and len(target_vals) == 2
    neg_label, pos_label = _binary_label_pair(target)

    def label_for(tv: str) -> str:
        if not is_binary:
            return f"{target}={tv}"
        return pos_label if str(tv) == str(pos_val) else neg_label

    # PROMPT-6 fix 3: integer-valued columns get integer bin labels.
    fmt = _bin_label_formatter(num_col, sub[num_col])

    rows: list[dict] = []
    sub_clipped = sub.loc[clipped_idx]
    for i in range(len(centers)):
        rec: dict = {"bin": fmt(edges[i]), "_lo": float(edges[i]), "_hi": float(edges[i + 1])}
        inclusive = "both" if i == len(centers) - 1 else "left"
        for tv in target_vals:
            mask = (sub_clipped[target].astype(str) == tv) & sub_clipped[num_col].between(
                edges[i], edges[i + 1], inclusive=inclusive
            )
            rec[label_for(tv)] = int(mask.sum())
        rows.append(rec)

    y_keys = [label_for(tv) for tv in target_vals]
    colors = [target_colors.get(tv, _PALETTE[i % len(_PALETTE)]) for i, tv in enumerate(target_vals)]

    return ChartConfig(
        id=str(uuid.uuid4()),
        chart_type=ChartType.BAR,
        title=f"{num_col} distribution by {target}{clip_note}",
        data=rows,
        x_key="bin",
        y_keys=y_keys,
        colors=colors,
        x_label=num_col,
        y_label="Count",
        show_legend=True,
        source_finding="continuous_split_by_target",
    )


def _build_time_series(
    df: pd.DataFrame, date_col: str, metric_col: str
) -> ChartConfig | None:
    sub = df[[date_col, metric_col]].copy()
    sub[date_col] = pd.to_datetime(sub[date_col], errors="coerce")
    sub[metric_col] = pd.to_numeric(sub[metric_col], errors="coerce")
    sub = sub.dropna(subset=[date_col, metric_col])
    if sub.empty:
        return None
    span = (sub[date_col].max() - sub[date_col].min()).days
    freq = "D" if span < 60 else ("W" if span < 730 else "Q")
    series = sub.set_index(date_col)[metric_col].resample(freq).mean().dropna()
    if series.empty:
        return None
    data = [{"period": idx.strftime("%Y-%m-%d"), metric_col: float(v)} for idx, v in series.items()]
    return ChartConfig(
        id=str(uuid.uuid4()),
        chart_type=ChartType.LINE,
        title=f"{metric_col} over time",
        data=data,
        x_key="period",
        y_keys=[metric_col],
        colors=[_NEUTRAL_TEAL],
        x_label="Period",
        y_label=metric_col,
        show_legend=False,
        source_finding="time_series",
    )


def _build_histogram(df: pd.DataFrame, col: str) -> ChartConfig | None:
    s = pd.to_numeric(df[col], errors="coerce").dropna()
    if s.empty:
        return None
    # PROMPT-2 fix 3: skew-aware binning so we don't waste the x-axis on outliers.
    edges, clipped, clip_note = _skew_aware_edges(s)
    counts, edges = np.histogram(clipped, bins=edges)
    # PROMPT-6 fix 3: integer-valued columns (age, year, qty…) → integer bin labels.
    fmt = _bin_label_formatter(col, s)
    data = [{"bin": fmt(edges[i]), "count": int(counts[i])} for i in range(len(counts))]
    return ChartConfig(
        id=str(uuid.uuid4()),
        chart_type=ChartType.HISTOGRAM,
        title=f"{col} distribution{clip_note}",
        data=data,
        x_key="bin",
        y_keys=["count"],
        colors=[_NEUTRAL_TEAL],
        x_label=col,
        y_label="Count",
        show_legend=False,
        source_finding="distribution",
    )


def _build_value_counts(df: pd.DataFrame, col: str, ordinal: bool = False) -> ChartConfig | None:
    s = df[col].dropna()
    if s.empty:
        return None
    if ordinal:
        # Preserve natural sort order for ordinal categories
        vc = s.value_counts().sort_index()
        ramp = _ORDINAL_RAMP
        colors = [ramp[min(i, len(ramp) - 1)] for i in range(len(vc))]
    else:
        vc = s.value_counts().head(10)
        colors = _PALETTE[: len(vc)]
    if vc.empty:
        return None
    data = [{col: str(k), "count": int(v)} for k, v in vc.items()]
    return ChartConfig(
        id=str(uuid.uuid4()),
        chart_type=ChartType.BAR,
        title=f"{col} breakdown",
        data=data,
        x_key=col,
        y_keys=["count"],
        colors=colors,
        x_label=col,
        y_label="Count",
        show_legend=False,
        source_finding="value_counts",
    )


def _build_scatter(df: pd.DataFrame, x_col: str, y_col: str) -> ChartConfig | None:
    sub = df[[x_col, y_col]].copy()
    sub[x_col] = pd.to_numeric(sub[x_col], errors="coerce")
    sub[y_col] = pd.to_numeric(sub[y_col], errors="coerce")
    sub = sub.dropna()
    if sub.empty:
        return None
    if len(sub) > 500:
        sub = sub.sample(500, random_state=42)
    data = [{x_col: float(r[x_col]), y_col: float(r[y_col])} for _, r in sub.iterrows()]
    return ChartConfig(
        id=str(uuid.uuid4()),
        chart_type=ChartType.SCATTER,
        title=f"{y_col} vs {x_col}",
        data=data,
        x_key=x_col,
        y_keys=[y_col],
        colors=[_NEUTRAL_TEAL],
        x_label=x_col,
        y_label=y_col,
        show_legend=False,
        source_finding="scatter",
    )


# ── Stage 2: smart chart selector ────────────────────────────────────────────

_TIME_KEYWORDS = ("trend", "over time", "weekly", "monthly", "yearly", "quarterly", "growth", "year over year", "month over month")


def auto_generate_charts(
    df: pd.DataFrame, column_roles: dict, query: str = ""
) -> list[ChartConfig]:
    """Pick the most informative charts for this dataset. Max 6, at most one
    standalone histogram, relationship charts prioritized. `query` is used as a
    soft hint — when it mentions trend/time keywords, the time-series chart is
    bumped ahead of the target-vs-cat charts."""
    charts: list[ChartConfig] = []
    target: str | None = column_roles.get("target_col")  # type: ignore[assignment]
    target_colors = _target_color_map(df, target) if target else {}
    cat_pool: list[str] = [
        c for c in (list(column_roles["low_card_cat"]) + list(column_roles["binary_cols"]))  # type: ignore[arg-type]
        if c != target
    ]
    continuous: list[str] = list(column_roles["continuous"])  # type: ignore[assignment]
    datetime_cols: list[str] = list(column_roles["datetime_cols"])  # type: ignore[assignment]
    ordinal_cols: set[str] = set(column_roles["ordinal_cols"])  # type: ignore[arg-type]

    q_lower = query.lower()
    wants_time = bool(datetime_cols) and bool(continuous) and any(k in q_lower for k in _TIME_KEYWORDS)

    # Priority boost — if the question asks about trends and we have what we
    # need, lead with the time-series chart instead of the target-rate charts.
    if wants_time:
        chart = _build_time_series(df, datetime_cols[0], continuous[0])
        if chart:
            charts.append(chart)

    # Priority 1 — target rate by top categoricals (most informative when a target exists)
    if target and cat_pool:
        ranked = _ranked_categoricals_by_target_separation(df, target, cat_pool)
        for c in ranked[:3]:
            chart = _build_target_rate_by_cat(df, c, target)
            if chart:
                charts.append(chart)
                if len(charts) >= 6:
                    return charts

    # Priority 2 — continuous distribution split by target
    if target and continuous:
        for c in continuous[:2]:
            chart = _build_continuous_split_by_target(df, c, target, target_colors)
            if chart:
                charts.append(chart)
                if len(charts) >= 6:
                    return charts

    # Priority 3 — time series of primary continuous over primary date (skip if already added by the time-keyword boost)
    if datetime_cols and continuous and not wants_time:
        chart = _build_time_series(df, datetime_cols[0], continuous[0])
        if chart:
            charts.append(chart)
            if len(charts) >= 6:
                return charts

    # Priority 4 — no-target fallbacks (only when no target exists)
    if not target:
        if continuous and not any(ch.chart_type == ChartType.HISTOGRAM for ch in charts):
            chart = _build_histogram(df, continuous[0])
            if chart:
                charts.append(chart)
        if cat_pool:
            top_cat = cat_pool[0]
            chart = _build_value_counts(df, top_cat, ordinal=top_cat in ordinal_cols)
            if chart:
                charts.append(chart)
        if len(continuous) >= 2:
            pair = _best_correlated_pair(df, continuous)
            if pair:
                chart = _build_scatter(df, pair[0], pair[1])
                if chart:
                    charts.append(chart)

    # Priority 5 — value_counts for remaining categoricals, BUT only when no
    # target exists. PROMPT-2 fix 5: a standalone "Pclass breakdown" (count) is
    # always less informative than "Survival Rate by Pclass" — and we already
    # ran that in Priority 1. So skip value_counts entirely when target is set.
    if target is None:
        used = {ch.x_key for ch in charts}
        for c in cat_pool:
            if len(charts) >= 6:
                break
            if c in used:
                continue
            chart = _build_value_counts(df, c, ordinal=c in ordinal_cols)
            if chart:
                charts.append(chart)
                used.add(c)

    # Spec invariant: at most ONE standalone histogram (split-by-target charts don't count).
    standalone = [ch for ch in charts if ch.chart_type == ChartType.HISTOGRAM and ch.source_finding == "distribution"]
    if len(standalone) > 1:
        keep_id = standalone[0].id
        charts = [
            ch for ch in charts
            if not (ch.chart_type == ChartType.HISTOGRAM and ch.source_finding == "distribution" and ch.id != keep_id)
        ]

    return charts[:6]


# ── PROMPT-2: smart KPI selector ─────────────────────────────────────────────

_KPI_CURRENCY_RE = re.compile(r"(?i)(price|cost|fare|revenue|amount|salary|income|spend|budget|fee|total)")
_KPI_PERCENT_RE = re.compile(r"(?i)(rate|pct|percent|ratio|share|proportion)")
_KPI_COUNT_RE = re.compile(r"(?i)(count|num|qty|quantity|total|volume)")
_KPI_PRIMARY_RE = re.compile(r"(?i)(revenue|sales|amount|score|value|total|profit)")

# Stem → plural label. Singular is matched as a substring of any column name.
_KPI_ENTITY_KEYWORDS: list[tuple[str, str]] = [
    ("transaction", "Transactions"),
    ("order",       "Orders"),
    ("customer",    "Customers"),
    ("patient",     "Patients"),
    ("student",     "Students"),
    ("employee",    "Employees"),
    ("product",     "Products"),
    ("response",    "Responses"),
    ("ticket",      "Tickets"),
    ("session",     "Sessions"),
    ("visit",       "Visits"),
    ("event",       "Events"),
    ("sale",        "Sales"),
    ("user",        "Users"),
]


def _title_case(name: str) -> str:
    """snake_case / camelCase / kebab-case → Title Case."""
    if not name:
        return ""
    # Insert space between lower→upper boundary (camelCase)
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", str(name))
    s = s.replace("_", " ").replace("-", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s.title()


def _display(col: str, narrative: DataNarrative | None) -> str:
    """Human-readable label for a column. Prefers the narrative dict (LLM-generated
    at intake) and falls back to deterministic Title-Case of the raw column name."""
    if narrative and col in narrative.column_display_names:
        v = narrative.column_display_names[col].strip()
        if v:
            return v
    return _title_case(col)


def _detect_entity_singular(columns, query: str = "") -> str:
    """Deterministic fallback when the narrative wasn't generated. Picks a noun
    from the entity-keyword table based on column names + the user's query."""
    blob = " ".join(str(c).lower() for c in columns)
    if query:
        blob = f"{blob} {query.lower()}"
    for keyword, _ in _KPI_ENTITY_KEYWORDS:
        if keyword in blob:
            return keyword
    return "record"


def _pluralize(noun: str) -> str:
    """Tiny English pluralizer — adequate for the common entity nouns we emit."""
    if not noun:
        return "records"
    low = noun.lower()
    if low.endswith("s"):
        return noun
    if low.endswith("y") and len(low) > 1 and low[-2] not in "aeiou":
        return noun[:-1] + "ies"
    if low.endswith(("ch", "sh", "x", "z")):
        return noun + "es"
    return noun + "s"


def _entity_pair(
    columns, narrative: DataNarrative | None, query: str = ""
) -> tuple[str, str]:
    """Return (singular, plural) entity nouns. Prefers the narrative; falls back
    to the deterministic keyword detector."""
    if narrative and narrative.entity_singular and narrative.entity_singular != "record":
        sg = narrative.entity_singular.lower().strip()
        pl = (narrative.entity_plural or _pluralize(sg)).lower().strip()
        return sg, pl
    sg = _detect_entity_singular(columns, query)
    return sg, _pluralize(sg)


_ORDINAL_SUFFIX = {1: "st", 2: "nd", 3: "rd"}


def _ordinal_word(n: int) -> str:
    """1 → '1st', 2 → '2nd', 11 → '11th', etc."""
    if 10 <= (n % 100) <= 20:
        suffix = "th"
    else:
        suffix = _ORDINAL_SUFFIX.get(n % 10, "th")
    return f"{n}{suffix}"


def _humanize_value(
    val,
    col: str,
    df: pd.DataFrame,
    ordinal_cols: set[str],
    narrative: DataNarrative | None,
) -> str:
    """Render a categorical value for display. Ordinal integer columns get
    contextualized: 'Pclass' (display name contains 'class') → 'Class 3';
    other ordinal ints → '3rd'. Everything else passes through as a string."""
    if col in ordinal_cols:
        s = df[col].dropna()
        try:
            is_int_like = pd.api.types.is_integer_dtype(s) or bool((s % 1 == 0).all())
        except Exception:
            is_int_like = False
        if is_int_like:
            try:
                n = int(val)
            except (ValueError, TypeError):
                return str(val)
            disp = _display(col, narrative).lower()
            # If the column itself is a "class"/"tier"/"grade" axis, prefix the noun
            # so '1' reads as 'Class 1' rather than '1st' (which loses the noun).
            for noun in ("class", "tier", "grade", "level", "rank"):
                if noun in disp:
                    return f"{noun.title()} {n}"
            return _ordinal_word(n)
    return str(val)


def _ensure_verb(phrase: str) -> str:
    """Make a phrase work after '342 of 891 passengers '. Past-tense verbs
    ('survived', 'churned') pass through; adjectives ('fraudulent') get a
    'were' prefix so the sentence reads as English."""
    p = (phrase or "").strip().lower()
    if not p:
        return ""
    first = p.split()[0]
    auxiliaries = {"did", "were", "was", "had", "are", "is", "have"}
    irregular_past = {"won", "lost", "had", "made", "sent"}
    if first in auxiliaries or first.endswith("ed") or first in irregular_past:
        return p
    return f"were {p}"


def _binary_phrase_pair(
    target_col: str, narrative: DataNarrative | None
) -> tuple[str, str]:
    """Return (negative_phrase, positive_phrase) suitable for completing a sentence
    like '342 of 891 passengers {positive_phrase}'. Uses the keyword dictionary
    when it knows the target (survived, churned, etc); otherwise builds a generic
    phrase from the column's display name. The phrases are always grammatical
    after a plural-noun subject."""
    neg, pos = _binary_label_pair(target_col)
    if (neg, pos) != ("No", "Yes"):
        return _ensure_verb(neg), _ensure_verb(pos)
    disp = _display(target_col, narrative).lower()
    return f"were not {disp}", f"were {disp}"


def _format_currency(v: float) -> str:
    return f"${v:,.2f}"


def _format_percent(v: float) -> str:
    return f"{v:.1f}%"


def _format_count(v: float) -> str:
    return f"{int(round(v)):,}"


def _format_number(v: float) -> str:
    return f"{v:,.2f}"


def _kpi_format_kind(col: str, s: pd.Series, binary_cols: set[str]) -> str:
    """Pick a formatter for a numeric column. Returns one of:
    'currency' | 'percent' | 'count' | 'number'."""
    if col in binary_cols:
        return "percent"
    if _KPI_CURRENCY_RE.search(col):
        return "currency"
    if _KPI_PERCENT_RE.search(col):
        return "percent"
    nums = pd.to_numeric(s, errors="coerce").dropna()
    if not nums.empty:
        try:
            if float(nums.min()) >= 0.0 and float(nums.max()) <= 1.0:
                return "percent"
        except Exception:
            pass
    if _KPI_COUNT_RE.search(col) and pd.api.types.is_integer_dtype(s):
        return "count"
    return "number"


def _format_value(v: float, kind: str) -> str:
    """Format a raw number per the inferred kind. Percent assumes the value is
    already on a 0–1 scale and multiplies by 100."""
    if kind == "currency":
        return _format_currency(v)
    if kind == "percent":
        return _format_percent(v * 100)
    if kind == "count":
        return _format_count(v)
    return _format_number(v)


def _pick_primary_metric(
    df: pd.DataFrame, continuous: list[str], id_cols: set[str]
) -> str | None:
    """Pick the 'number that matters'. Name match wins; otherwise highest |mean·std|."""
    candidates = [c for c in continuous if c not in id_cols]
    if not candidates:
        return None
    for c in candidates:
        if _KPI_PRIMARY_RE.search(c):
            return c
    best: str | None = None
    best_score = -1.0
    for c in candidates:
        s = pd.to_numeric(df[c], errors="coerce").dropna()
        if len(s) < 2:
            continue
        try:
            score = abs(float(s.mean()) * float(s.std()))
        except Exception:
            continue
        if score > best_score:
            best, best_score = c, score
    return best


def _pick_top_category(
    df: pd.DataFrame, low_card_cat: list[str], target_col: str | None
) -> str | None:
    """Lowest-cardinality categorical (3-10 unique) that isn't the target."""
    candidates: list[tuple[str, int]] = []
    for c in low_card_cat:
        if c == target_col:
            continue
        try:
            nu = int(df[c].dropna().nunique())
        except Exception:
            continue
        if 3 <= nu <= 10:
            candidates.append((c, nu))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[1])
    return candidates[0][0]


def _comparison_kpi_chi2(
    df: pd.DataFrame,
    target_col: str,
    cat_cols: list[str],
    ordinal_cols: set[str],
    narrative: DataNarrative | None,
) -> KPI | None:
    """Find the categorical column with the highest chi² against the target and
    surface the high-vs-low target rate across its groups."""
    from scipy import stats as scipy_stats

    best_col: str | None = None
    best_chi2 = -1.0
    for c in cat_cols:
        if c == target_col:
            continue
        try:
            ct = pd.crosstab(df[c], df[target_col])
            if ct.shape[0] < 2 or ct.shape[1] < 2:
                continue
            chi2, _, _, _ = scipy_stats.chi2_contingency(ct)
            if float(chi2) > best_chi2:
                best_chi2 = float(chi2)
                best_col = c
        except Exception:
            continue
    if best_col is None:
        return None
    t = _to_01(df[target_col])
    if t is None:
        return None
    grouped = pd.Series(t.values, index=df[best_col]).groupby(level=0).mean().dropna()
    if grouped.shape[0] < 2:
        return None
    g_sorted = grouped.sort_values(ascending=False)
    high_name, high_rate = g_sorted.index[0], float(g_sorted.iloc[0])
    low_name, low_rate = g_sorted.index[-1], float(g_sorted.iloc[-1])
    high_label = _humanize_value(high_name, best_col, df, ordinal_cols, narrative)
    low_label = _humanize_value(low_name, best_col, df, ordinal_cols, narrative)
    target_label = _rate_label(target_col, narrative)
    return KPI(
        label=f"{target_label} by {_display(best_col, narrative)}",
        value=f"{high_rate * 100:.1f}% vs {low_rate * 100:.1f}%",
        context=f"{high_label}: {high_rate * 100:.1f}% vs {low_label}: {low_rate * 100:.1f}%",
    )


def _highest_cv_kpi(
    df: pd.DataFrame,
    continuous: list[str],
    id_cols: set[str],
    binary_cols: set[str],
    narrative: DataNarrative | None,
) -> KPI | None:
    """Continuous column with the highest coefficient of variation (std / |mean|).
    Used as the 5th KPI when there's no target to compare against."""
    best: str | None = None
    best_cv = -1.0
    for c in continuous:
        if c in id_cols:
            continue
        s = pd.to_numeric(df[c], errors="coerce").dropna()
        if len(s) < 3:
            continue
        try:
            mean = float(s.mean())
            std = float(s.std())
        except Exception:
            continue
        if mean == 0 or not math.isfinite(mean) or not math.isfinite(std):
            continue
        cv = std / abs(mean)
        if cv > best_cv:
            best, best_cv = c, cv
    if best is None:
        return None
    s = pd.to_numeric(df[best], errors="coerce").dropna()
    kind = _kpi_format_kind(best, s, binary_cols)
    mean = float(s.mean())
    std = float(s.std())
    return KPI(
        label=f"{_display(best, narrative)} Variability",
        value=f"CV {best_cv:.2f}",
        context=f"Mean {_format_value(mean, kind)} ± {_format_value(std, kind)}",
    )


def generate_smart_kpis(
    df: pd.DataFrame,
    column_roles: dict,
    query: str = "",
    narrative: DataNarrative | None = None,
) -> list[KPI]:
    """Build up to 5 hero KPIs from a DataFrame and the visualization agent's
    column roles. Works on any dataset: row count + (optional) target rate +
    primary metric + top category + comparison split.

    `narrative` carries LLM-generated column display names + entity nouns
    (built once at ingestion). When absent, helpers fall back to deterministic
    title-casing / keyword detection."""
    if df is None or len(df) == 0:
        return []

    id_cols: set[str] = set(column_roles.get("id_cols") or [])
    binary_cols: set[str] = set(column_roles.get("binary_cols") or [])
    target_col: str | None = column_roles.get("target_col")  # type: ignore[assignment]
    continuous: list[str] = list(column_roles.get("continuous") or [])
    low_card_cat: list[str] = list(column_roles.get("low_card_cat") or [])
    high_card_cat: list[str] = list(column_roles.get("high_card_cat") or [])
    ordinal_cols: set[str] = set(column_roles.get("ordinal_cols") or [])

    entity_sg, entity_pl = _entity_pair(df.columns, narrative, query)

    kpis: list[KPI] = []

    # KPI 1 — row count, labeled by the detected entity (column count belongs
    # in the briefing tables, not on the KPI strip)
    kpis.append(KPI(
        label=f"Total {entity_pl.title()}",
        value=_format_count(len(df)),
        context=f"{len(df):,} {entity_sg} records",
    ))

    # KPI 2 — target positive-class rate (never reference raw '0'/'1' values)
    if target_col and target_col in df.columns:
        t = _to_01(df[target_col])
        if t is not None:
            rate = float(t.mean())
            pos_count = int(round(t.sum()))
            total = int(t.shape[0])
            _, pos_phrase = _binary_phrase_pair(target_col, narrative)
            kpis.append(KPI(
                label=_rate_label(target_col, narrative),
                value=f"{rate * 100:.1f}%",
                context=f"{pos_count:,} of {total:,} {entity_pl} {pos_phrase}",
            ))

    # KPI 3 — primary continuous metric
    primary = _pick_primary_metric(df, continuous, id_cols)
    if primary:
        s = pd.to_numeric(df[primary], errors="coerce").dropna()
        if len(s) >= 2:
            kind = _kpi_format_kind(primary, df[primary], binary_cols)
            mean = float(s.mean())
            mn = float(s.min())
            mx = float(s.max())
            kpis.append(KPI(
                label=f"Avg {_display(primary, narrative)}",
                value=_format_value(mean, kind),
                context=f"Range {_format_value(mn, kind)} – {_format_value(mx, kind)}",
            ))

    # KPI 4 — top category (humanize ordinal numerics: 3 → "Class 3" or "3rd")
    top_cat = _pick_top_category(df, low_card_cat, target_col)
    if top_cat:
        s = df[top_cat].dropna()
        if not s.empty:
            vc = s.value_counts()
            top_val = vc.index[0]
            share = float(vc.iloc[0]) / float(vc.sum()) * 100
            kpis.append(KPI(
                label=f"Top {_display(top_cat, narrative)}",
                value=_humanize_value(top_val, top_cat, df, ordinal_cols, narrative),
                context=f"{share:.1f}% of {entity_pl}",
            ))

    # KPI 5 — comparison split (target ⊥ cat) OR highest-CV continuous
    if target_col:
        comp_pool = [c for c in low_card_cat + high_card_cat if c != target_col]
        comp = _comparison_kpi_chi2(df, target_col, comp_pool, ordinal_cols, narrative)
        if comp:
            kpis.append(comp)
    else:
        cv = _highest_cv_kpi(df, continuous, id_cols, binary_cols, narrative)
        if cv:
            kpis.append(cv)

    # De-dupe by label in case heuristics collide; trim to 5.
    seen: set[str] = set()
    unique: list[KPI] = []
    for k in kpis:
        if k.label in seen:
            continue
        seen.add(k.label)
        unique.append(k)
    return unique[:5]


# ── PROMPT-6: dashboard generator with layout hints + color consistency ─────

def _build_color_map(df: pd.DataFrame, column_roles: dict) -> dict[str, str]:
    """Stable value→hex map shared by every chart in the dashboard so that
    'Region A' is always blue, target 'Yes' is always green, etc. Two scopes:
    target values get semantic colors (green/red); top categorical values get
    palette assignment by frequency."""
    cmap: dict[str, str] = {}

    target = column_roles.get("target_col")
    if target and target in df.columns:
        pos = _positive_value(df, target)
        for v in df[target].dropna().unique():
            cmap[str(v)] = _POSITIVE if str(v) == str(pos) else _NEGATIVE

    cat_pool = list(column_roles.get("low_card_cat") or [])
    if cat_pool:
        top_cat = cat_pool[0]
        if top_cat in df.columns:
            vc = df[top_cat].dropna().value_counts()
            for i, v in enumerate(vc.index):
                sv = str(v)
                if sv not in cmap:
                    cmap[sv] = _PALETTE[i % len(_PALETTE)]
    return cmap


def _colors_for_values(values, color_map: dict[str, str]) -> list[str]:
    """Resolve a sequence of category values to consistent hex colors. Values
    not in the map fall through to the Tableau-10 palette in order."""
    out: list[str] = []
    palette_idx = 0
    for v in values:
        sv = str(v)
        if sv in color_map:
            out.append(color_map[sv])
        else:
            out.append(_PALETTE[palette_idx % len(_PALETTE)])
            palette_idx += 1
    return out


def _build_donut(
    df: pd.DataFrame, col: str, color_map: dict[str, str],
    narrative: DataNarrative | None = None,
) -> ChartConfig | None:
    """Donut composition: each slice = a category value, sized by share."""
    s = df[col].dropna()
    if s.empty:
        return None
    vc = s.value_counts().head(7)
    if vc.empty:
        return None
    total = float(vc.sum())
    values = [str(k) for k in vc.index]
    data = [
        {"name": v, "value": int(c), "pct": round(int(c) / total * 100, 1)}
        for v, c in zip(values, vc.values)
    ]
    return ChartConfig(
        id=str(uuid.uuid4()),
        chart_type=ChartType.DONUT,
        title=f"{_display(col, narrative)} Breakdown",
        data=data,
        x_key="name",
        y_keys=["value"],
        colors=_colors_for_values(values, color_map),
        x_label=None,
        y_label=None,
        show_legend=True,
        source_finding="composition_donut",
        layout_hint="half_width",
    )


def _build_horizontal_bar_top_n(
    df: pd.DataFrame, col: str, color_map: dict[str, str],
    narrative: DataNarrative | None = None,
) -> ChartConfig | None:
    """Horizontal bar of the top categories by count — works when cardinality
    is too high for a donut but still useful (8-15 unique values)."""
    s = df[col].dropna()
    if s.empty:
        return None
    vc = s.value_counts().head(15)
    if vc.empty:
        return None
    values = [str(k) for k in vc.index]
    data = [{col: v, "count": int(c)} for v, c in zip(values, vc.values)]
    return ChartConfig(
        id=str(uuid.uuid4()),
        chart_type=ChartType.HORIZONTAL_BAR,
        title=f"{_display(col, narrative)} Breakdown",
        data=data,
        x_key=col,
        y_keys=["count"],
        colors=_colors_for_values(values, color_map),
        x_label=_display(col, narrative),
        y_label="Count",
        show_legend=False,
        source_finding="composition_horizontal_bar",
        layout_hint="half_width",
    )


def _build_stacked_bar_normalized(
    df: pd.DataFrame, cat_col: str, target: str,
    narrative: DataNarrative | None = None,
) -> ChartConfig | None:
    """Composition stack: each bar = one category value, segments = target
    values, normalized to 100%. Lets the reader compare composition across
    categories at a glance."""
    sub = df[[cat_col, target]].dropna()
    if sub.empty or sub[cat_col].nunique() < 2 or sub[target].nunique() < 2:
        return None

    ct = pd.crosstab(sub[cat_col], sub[target], normalize="index") * 100
    if ct.empty:
        return None

    # Sort categories numerically when possible, otherwise alphabetically.
    numeric_sort = _sort_key_for(ct.index)
    if numeric_sort is not None:
        ct = ct.sort_index(key=lambda idx: idx.map(numeric_sort))
    else:
        ct = ct.sort_index()
    ct = ct.head(10)

    # PROMPT-6 fix 2: humanize target value labels (0 → 'Did not survive', etc).
    target_labels = _target_value_to_label(df, target)
    target_values = [str(v) for v in ct.columns]
    y_keys = [target_labels.get(v, v) for v in target_values]

    rows: list[dict] = []
    for cat_value, series in ct.iterrows():
        rec: dict = {cat_col: str(cat_value)}
        for tv, pct in zip(target_values, series.values):
            rec[target_labels.get(tv, tv)] = round(float(pct), 1)
        rows.append(rec)

    # Colors aligned to the humanized y_keys via target_colors (raw value → hex).
    target_colors = _target_color_map(df, target)
    colors = [target_colors.get(tv, _PALETTE[i % len(_PALETTE)]) for i, tv in enumerate(target_values)]

    return ChartConfig(
        id=str(uuid.uuid4()),
        chart_type=ChartType.BAR,
        title=f"{_display(target, narrative)} Composition by {_display(cat_col, narrative)}",
        data=rows,
        x_key=cat_col,
        y_keys=y_keys,
        colors=colors,
        x_label=_display(cat_col, narrative),
        y_label="Share (%)",
        show_legend=True,
        source_finding="composition_stacked",
        value_format="percent",
        layout_hint="half_width",
    )


def _build_correlation_heatmap(
    df: pd.DataFrame, numeric_cols: list[str],
    narrative: DataNarrative | None = None,
) -> ChartConfig | None:
    """Pairwise Pearson correlations as a true heatmap matrix. Triggered only
    when more than four numeric columns exist so the grid is informative. The
    frontend's HeatmapView reads `data` as a flat list of {row, col, value} and
    pivots it into an HTML table colored by value."""
    cols = [c for c in numeric_cols if c in df.columns]
    if len(cols) < 5:
        return None
    corr = df[cols].apply(pd.to_numeric, errors="coerce").corr().fillna(0)
    if corr.empty:
        return None
    # PROMPT-6 fix 1: flat row/col/value records — frontend pivots into a grid.
    data = []
    for ci in corr.index:
        for cj in corr.columns:
            r = float(corr.loc[ci, cj])
            data.append({
                "row": _display(str(ci), narrative),
                "col": _display(str(cj), narrative),
                "value": round(r, 3),
            })
    return ChartConfig(
        id=str(uuid.uuid4()),
        chart_type=ChartType.HEATMAP,
        title="Correlation Heatmap",
        data=data,
        x_key="col",
        y_keys=["row"],
        colors=[],
        x_label=None,
        y_label=None,
        show_legend=False,
        source_finding="correlation_heatmap",
        layout_hint="full_width",
    )


def _rank_continuous_by_target_assoc(
    df: pd.DataFrame, target: str, continuous: list[str]
) -> list[str]:
    """Order continuous columns by absolute point-biserial correlation with the
    binary target. Higher = stronger separation between target classes."""
    t = _to_01(df[target])
    if t is None:
        return continuous
    scored: list[tuple[str, float]] = []
    for col in continuous:
        try:
            v = pd.to_numeric(df[col], errors="coerce")
            r = t.corr(v)
            if pd.notna(r):
                scored.append((col, abs(float(r))))
        except Exception:
            continue
    scored.sort(key=lambda x: x[1], reverse=True)
    return [c for c, _ in scored]


def _humanize_title(title: str, narrative: DataNarrative | None) -> str:
    """Replace raw column-name tokens in a chart title with their narrative
    display names. Chart titles are deterministic Python strings (not
    LLM-generated prose), so a pure casing fix like 'revenue' → 'Revenue' is
    safe and desired — we only skip the swap when raw and display are byte-
    for-byte identical."""
    if not title or not narrative or not narrative.column_display_names:
        return title
    out = title
    for raw, display in sorted(
        narrative.column_display_names.items(),
        key=lambda kv: len(kv[0]), reverse=True,
    ):
        if not raw or raw == display:
            continue
        out = re.sub(rf"\b{re.escape(raw)}\b", display, out, flags=re.IGNORECASE)
    return out


def _insight_title_for_target_rate(
    chart: ChartConfig, base_title: str
) -> str:
    """Augment a target-rate-by-category chart title with the top group's rate:
    'Survival Rate by Class — 1st Highest at 63%'."""
    if not chart.data or len(chart.y_keys) != 1:
        return base_title
    y = chart.y_keys[0]
    try:
        rows = [r for r in chart.data if isinstance(r.get(y), (int, float))]
        if not rows:
            return base_title
        top = max(rows, key=lambda r: r[y])
        return f"{base_title} — {top[chart.x_key]} Highest at {top[y]:.1f}%"
    except Exception:
        return base_title


def _insight_title_for_breakdown(
    chart: ChartConfig, base_title: str
) -> str:
    """Augment a donut / value-count title with the top share:
    'Region Breakdown — South Leads at 28%'."""
    if not chart.data:
        return base_title
    try:
        if chart.chart_type == ChartType.DONUT and chart.data[0].get("pct") is not None:
            top = max(chart.data, key=lambda r: r["pct"])
            return f"{base_title} — {top['name']} Leads at {top['pct']:.0f}%"
        # Count-based bar: derive share from total.
        count_key = chart.y_keys[0] if chart.y_keys else "count"
        total = sum(float(r.get(count_key, 0) or 0) for r in chart.data)
        if total <= 0:
            return base_title
        top = max(chart.data, key=lambda r: float(r.get(count_key, 0) or 0))
        pct = float(top.get(count_key, 0) or 0) / total * 100
        return f"{base_title} — {top[chart.x_key]} Leads at {pct:.0f}%"
    except Exception:
        return base_title


def generate_dashboard_charts(
    df: pd.DataFrame,
    column_roles: dict,
    query: str = "",
    narrative: DataNarrative | None = None,
) -> list[ChartConfig]:
    """Build a varied, insight-driven dashboard for any dataset shape.

    Priority order:
      1. Target-driven (rate by cat, distribution by target)
      2. Time series (auto-granularity, optional grouping)
      3. Composition (donut / horizontal-bar / stacked-bar)
      4. Relationship (scatter, correlation heatmap)
      5. Single-variable fallback (only when <4 charts so far)

    Every chart carries a layout_hint ('half_width' / 'full_width') and shares
    a global color_map so categorical values render consistently across charts.
    """
    target: str | None = column_roles.get("target_col")  # type: ignore[assignment]
    cat_pool: list[str] = [
        c for c in (list(column_roles.get("low_card_cat") or []) + list(column_roles.get("binary_cols") or []))
        if c != target
    ]
    continuous: list[str] = list(column_roles.get("continuous") or [])
    datetime_cols: list[str] = list(column_roles.get("datetime_cols") or [])
    ordinal_cols: set[str] = set(column_roles.get("ordinal_cols") or [])
    numeric_cols = [
        c for c in df.columns
        if pd.api.types.is_numeric_dtype(df[c]) and c not in (column_roles.get("id_cols") or [])
    ]

    color_map = _build_color_map(df, column_roles)
    target_colors = _target_color_map(df, target) if target else {}
    charts: list[ChartConfig] = []

    # When the user's query asks about time/trends, surface the time-series
    # chart ahead of the target-by-cat block instead of after.
    wants_time = (
        bool(datetime_cols) and bool(continuous)
        and any(k in query.lower() for k in _TIME_KEYWORDS)
    )
    if wants_time:
        ch = _build_time_series(df, datetime_cols[0], continuous[0])
        if ch:
            ch.layout_hint = "full_width"
            charts.append(ch)

    # ── Priority 1: target-driven ─────────────────────────────────────────
    if target:
        ranked_cats = _ranked_categoricals_by_target_separation(df, target, cat_pool)
        for c in ranked_cats[:3]:
            ch = _build_target_rate_by_cat(df, c, target)
            if ch:
                base = ch.title
                ch.title = _insight_title_for_target_rate(ch, base)
                ch.layout_hint = "half_width"
                charts.append(ch)
                if len(charts) >= 8:
                    return charts

        ranked_cont = _rank_continuous_by_target_assoc(df, target, continuous)
        for c in ranked_cont[:2]:
            ch = _build_continuous_split_by_target(df, c, target, target_colors)
            if ch:
                ch.layout_hint = "half_width"
                charts.append(ch)
                if len(charts) >= 8:
                    return charts

    # ── Priority 2: time series (skip if already pushed up by `wants_time`) ─
    if datetime_cols and continuous and not wants_time:
        ch = _build_time_series(df, datetime_cols[0], continuous[0])
        if ch:
            ch.layout_hint = "full_width"
            charts.append(ch)
            if len(charts) >= 8:
                return charts

    # ── Priority 3: composition ──────────────────────────────────────────
    used_cats = {ch.x_key for ch in charts}
    composition_cat = next(
        (c for c in (column_roles.get("low_card_cat") or []) if c != target and c not in used_cats),
        None,
    )
    if composition_cat is None and cat_pool:
        composition_cat = cat_pool[0]
    if composition_cat:
        nu = int(df[composition_cat].dropna().nunique())
        ch: ChartConfig | None = None
        if 3 <= nu <= 7:
            ch = _build_donut(df, composition_cat, color_map, narrative)
        elif 8 <= nu <= 15:
            ch = _build_horizontal_bar_top_n(df, composition_cat, color_map, narrative)
        if ch:
            ch.title = _insight_title_for_breakdown(ch, ch.title)
            charts.append(ch)

    # 3b: stacked composition by target
    if target and cat_pool and len(charts) < 8:
        # Pick a cat we haven't used as the x-axis yet, prefer low cardinality.
        used = {ch.x_key for ch in charts}
        stack_cat = next((c for c in cat_pool if c not in used), None)
        if stack_cat:
            ch = _build_stacked_bar_normalized(df, stack_cat, target, narrative)
            if ch:
                charts.append(ch)

    # ── Priority 4: relationships ────────────────────────────────────────
    if len(charts) < 8 and len(continuous) >= 2:
        pair = _best_correlated_pair(df, continuous)
        if pair:
            ch = _build_scatter(df, pair[0], pair[1])
            if ch:
                ch.layout_hint = "half_width"
                charts.append(ch)

    if len(charts) < 8 and len(numeric_cols) > 4:
        ch = _build_correlation_heatmap(df, numeric_cols, narrative)
        if ch:
            charts.append(ch)

    # ── Priority 5: single-variable fallback ─────────────────────────────
    if len(charts) < 4:
        used_x = {ch.x_key for ch in charts}
        if continuous:
            primary = next((c for c in continuous if c not in used_x), None)
            if primary:
                ch = _build_histogram(df, primary)
                if ch:
                    ch.layout_hint = "half_width"
                    charts.append(ch)
        if cat_pool:
            top_cat = next((c for c in cat_pool if c not in used_x), None)
            if top_cat:
                ch = _build_value_counts(df, top_cat, ordinal=top_cat in ordinal_cols)
                if ch:
                    ch.layout_hint = "half_width"
                    charts.append(ch)

    # Hard cap. Keep at most ONE standalone histogram (split-by-target counts separately).
    standalone_hist = [c for c in charts if c.chart_type == ChartType.HISTOGRAM and c.source_finding == "distribution"]
    if len(standalone_hist) > 1:
        keep = standalone_hist[0].id
        charts = [
            c for c in charts
            if not (c.chart_type == ChartType.HISTOGRAM and c.source_finding == "distribution" and c.id != keep)
        ]

    # PROMPT-6: humanize titles using narrative display names ('product_category'
    # → 'Product Category'). Done as a post-pass so the existing chart builders
    # don't need to know about the narrative.
    for ch in charts:
        ch.title = _humanize_title(ch.title, narrative)

    return charts[:8]


# ── Last-resort fallback (DataContract only, no df available) ────────────────

def _contract_fallback_charts(contract: DataContract) -> list[ChartConfig]:
    """When the DataFrame isn't available (e.g. resume failure), draw at least
    one chart from contract metadata so the UI isn't empty."""
    charts: list[ChartConfig] = []
    null_data = [
        {"label": c.name, "count": round(c.null_pct, 1)}
        for c in contract.columns
        if c.null_pct > 0
    ]
    if null_data:
        charts.append(ChartConfig(
            id=str(uuid.uuid4()),
            chart_type=ChartType.HORIZONTAL_BAR,
            title="Missing values by column (%)",
            data=sorted(null_data, key=lambda x: x["count"], reverse=True)[:12],
            x_key="label",
            y_keys=["count"],
            colors=[_NEUTRAL_TEAL],
            x_label="Column",
            y_label="Missing %",
            show_legend=False,
            source_finding="contract_fallback",
        ))
    return charts


# ── PROMPT-10: briefing-table extras ─────────────────────────────────────────

def compute_briefing_extras(
    df: pd.DataFrame, column_roles: dict, contract: "DataContract | None" = None
) -> dict:
    """Compute the per-column quality + descriptive-stats inputs that the
    chat briefing tables render. Returns plain Python types (str/int/float/list)
    so the caller can drop the dict directly onto AnalysisFindings."""
    if df is None or len(df) == 0:
        return {}

    out: dict = {}

    # nunique per column
    unique_counts: dict[str, int] = {}
    sample_values: dict[str, list] = {}
    for col in df.columns:
        try:
            s = df[col].dropna()
            unique_counts[str(col)] = int(s.nunique())
            sample_values[str(col)] = [_safe_sample(v) for v in s.head(3).tolist()]
        except Exception:
            continue
    out["unique_counts"] = unique_counts
    out["sample_values_per_col"] = sample_values

    # Skewness per numeric column
    skew: dict[str, float] = {}
    for col in df.columns:
        if not pd.api.types.is_numeric_dtype(df[col]):
            continue
        try:
            sk = float(df[col].dropna().skew())
            if math.isfinite(sk):
                skew[str(col)] = round(sk, 3)
        except Exception:
            pass
    out["skewness"] = skew

    # Pre-cleaning missing % from the contract (null_pct was captured at
    # ingestion before the cleaner ran).
    if contract is not None:
        out["pre_cleaning_missing_pct"] = {
            c.name: round(float(c.null_pct), 2) for c in contract.columns
        }

    # Target column + per-column Pearson r with target. _to_01 handles both
    # numeric {0,1} and string binary targets.
    target_col = column_roles.get("target_col")
    out["target_col"] = target_col
    corrs: dict[str, float] = {}
    if target_col and target_col in df.columns:
        t = _to_01(df[target_col])
        if t is not None:
            for col in df.columns:
                if col == target_col or not pd.api.types.is_numeric_dtype(df[col]):
                    continue
                try:
                    r = pd.to_numeric(df[col], errors="coerce").corr(t)
                    if pd.notna(r):
                        corrs[str(col)] = round(float(r), 3)
                except Exception:
                    pass
    out["target_correlations"] = corrs

    # Roles summary: column → role label. Frontend uses this to filter the
    # descriptive-stats table to continuous columns only.
    roles_summary: dict[str, str] = {}
    for role in (
        "id_cols", "binary_cols", "low_card_cat", "high_card_cat",
        "continuous", "datetime_cols", "ordinal_cols", "skip_cols",
    ):
        for col in column_roles.get(role) or []:
            # First match wins so we get a single primary role per column.
            roles_summary.setdefault(str(col), role)
    out["column_roles_summary"] = roles_summary

    return out


def _safe_sample(v):
    """Coerce a sample value to a JSON-serializable primitive."""
    if v is None:
        return None
    if isinstance(v, (str, int, float, bool)):
        # Strip very long strings for the preview (sample values are tooltips).
        if isinstance(v, str) and len(v) > 80:
            return v[:80] + "…"
        return v
    # Datetime, numpy scalars, etc — stringify defensively.
    try:
        return str(v)
    except Exception:
        return None


# ── Public entry point ───────────────────────────────────────────────────────

async def run_visualization_agent(session: AnalysisSession) -> list[ChartConfig]:
    """Build Recharts chart configs by classifying the DataFrame's columns and
    picking informative visualizations. Falls back to contract metadata if df
    isn't available (resume failure, etc)."""
    contract = session.data_contract
    if contract is None:
        raise ValueError("DataContract must be present before visualization.")

    df_parquet: bytes | None = session.__dict__.get("_df_parquet")
    if df_parquet is None:
        return _contract_fallback_charts(contract)

    try:
        df = pd.read_parquet(io.BytesIO(df_parquet))
    except Exception:
        return _contract_fallback_charts(contract)

    if df.empty:
        return _contract_fallback_charts(contract)

    query = session.task_plan.raw_query if session.task_plan else ""
    roles = classify_columns(df)
    # PROMPT-6: generate_dashboard_charts replaces the old auto_generate_charts.
    # It adds composition charts (donut, stacked-bar, horizontal-bar),
    # correlation heatmaps, layout hints, color-consistency across charts, and
    # insight-bearing titles. `narrative` provides humanized column labels.
    charts = generate_dashboard_charts(df, roles, query=query, narrative=session.narrative)

    if not charts:
        return _contract_fallback_charts(contract)
    return charts


def build_single_chart(
    df: "pd.DataFrame",
    params: "TargetedParams",
) -> "ChartConfig":
    """Build one ChartConfig from CHART_REQUEST params + DataFrame.

    Deterministic — no LLM call. Falls back to a reasonable default when
    columns the user named aren't present in the dataframe (returns a chart
    over whatever columns are valid).
    """
    chart_type = params.chart_type or ChartType.BAR

    # Resolve x and y from TargetedParams. Prefer explicit metric_column for y,
    # group_by for x. Fall back to the first column the user listed in
    # target_columns. Only keep columns that actually exist in df.
    available = list(df.columns)
    x_key = None
    y_keys: list[str] = []

    if params.group_by and params.group_by in available:
        x_key = params.group_by
    if params.metric_column and params.metric_column in available:
        y_keys = [params.metric_column]
    for col in params.target_columns:
        if col not in available:
            continue
        if x_key is None:
            x_key = col
        elif col not in y_keys:
            y_keys.append(col)

    if x_key is None or not y_keys:
        # Last-resort fallback: pick first non-id column for x, first continuous for y.
        roles = classify_columns(df)
        id_cols: list[str] = roles.get("id_cols", [])  # type: ignore[assignment]
        continuous_cols: list[str] = roles.get("continuous", [])  # type: ignore[assignment]
        for col in df.columns:
            if col not in id_cols:
                x_key = x_key or col
                break
        for col in df.columns:
            if col in continuous_cols:
                if col != x_key:
                    y_keys = [col]
                    break
        if x_key is None and len(df.columns) > 0:
            x_key = df.columns[0]
        if not y_keys and len(df.columns) > 1:
            y_keys = [df.columns[1]]

    # Build chart data: small aggregation so chat-rendered charts stay light.
    grouped_types = {ChartType.BAR, ChartType.HORIZONTAL_BAR, ChartType.PIE, ChartType.DONUT}
    if chart_type in grouped_types and x_key and y_keys:
        try:
            agg = df.groupby(x_key)[y_keys].sum().reset_index().head(20)
            data = agg.to_dict("records")
        except Exception:
            data = df[[x_key] + y_keys].head(20).to_dict("records")
    elif x_key and y_keys:
        # Line / scatter / histogram: pass raw rows, truncated.
        data = df[[x_key] + y_keys].dropna().head(200).to_dict("records")
    else:
        data = []

    return ChartConfig(
        id=str(uuid.uuid4()),
        chart_type=chart_type,
        title=f"{y_keys[0]} by {x_key}" if y_keys and x_key else f"{x_key or 'data'} distribution",
        data=data,
        x_key=x_key or "",
        y_keys=y_keys,
        colors=["#6366f1"] * max(len(y_keys), 1),
        show_legend=True,
        show_grid=True,
        source_finding="user_chart_request",
    )
