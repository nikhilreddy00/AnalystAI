"""
Visualization Agent — the chart an analyst would draw for each finding, and why.

  1. `charts_for_findings(ctx)` — one chart per analysis finding, picked from what the finding is:
     trend → monthly line, comparison → bars of the metric with 95% CIs, relationship → scatter
     with the driver on x, unusual values → histogram with the fences marked.
  2. `generate_dashboard_charts` — data-profile charts when there are few or no findings (an
     overview); row-count charts go to the Data tab.
  3. `finalize_charts` — every chart (here, targeted answers, chart requests, drill-down) is
     linted (guardrails.chart_lint) and repaired or dropped; the user's own choices get a note.

Deterministic, no LLM: the analysis agent decided what is true; this decides how to show it.
Every chart carries a ChartRationale (rule, columns, aggregation, rows, evidence ids, code).
"""
from __future__ import annotations

import io
import logging
import math
import re
import uuid
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy import stats as scipy_stats

from agents.analysis_agent import segment_id
from agents.utils import pandas_freq, partial_last_month
from guardrails import chart_lint
from llm.client import trace_verdict

from orchestrator.state import (
    AgentCard,
    AnalysisFindings,
    AnalysisSession,
    AnalysisTask,
    ChartConfig,
    ChartRationale,
    ChartType,
    DataContract,
    DataNarrative,
    Evidence,
    KPI,
    KPIRecipe,
    TargetedParams,
)

logger = logging.getLogger(__name__)


# ── Agent card (ADR-0001) ────────────────────────────────────────────────────
# Deterministic: no prompt, no `llm` parameter. Picking a chart has one right answer once the
# finding's kind and its variables' types are known; the analysis agent made the judgment call.

VIZ_CARD = AgentCard(
    name="visualization",
    version="rules-v2",
    scope="Shows each finding with the chart an analyst would draw for it (trend → line, "
          "comparison → bar of the metric, relationship → scatter, unusual values → histogram), "
          "and says why. Every chart is linted before it leaves the backend.",
    inputs=["The analysis findings and their evidence", "The question and its plan",
            "The cleaned data frame and its column roles"],
    outputs=["Up to 6 summary charts and the data-profile charts, each with a rationale "
             "(rule, columns, aggregation, rows, evidence ids, pandas code)"],
    limits=["Rule-based: only the finding kinds in the table above get a dedicated chart; a "
            "statistical test is charted only as its group means.",
            "Totals per month for additive measures, means for values within 0-1 (rates, flags), "
            "the same rule as the analysis agent's month-over-month.",
            "Scatter shows Pearson-style relationships on up to 500 sampled rows.",
            "At most 8 series per chart; no dual axes; bars always start at zero.",
            "The outcome of a relationship is the planned metric with an outcome-like name, then "
            "the detected target; otherwise the chart says no outcome was marked."],
    refusal_rules=["Never chart a column that is not in the data.",
                   "Never ship a chart that fails the lint; repair it or drop it.",
                   "Never claim a confidence of its own: show the finding's confidence only.",
                   "A chart the user chose is drawn as asked, with a note if the lint objects."],
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
        if pd.api.types.is_string_dtype(s) and nunique > 3:
            sample = s.head(100)
            try:
                parsed = pd.to_datetime(sample, format="mixed", errors="coerce")
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
    series = sub.set_index(date_col)[metric_col].resample(pandas_freq(freq)).mean().dropna()
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
        recipe=KPIRecipe(kind="comparison_split", target_column=target_col, segment_column=best_col),
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
        recipe=KPIRecipe(kind="highest_cv", column=best, format_kind=kind),
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
        recipe=KPIRecipe(kind="row_count"),
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
                recipe=KPIRecipe(kind="target_rate", column=target_col),
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
                recipe=KPIRecipe(kind="metric_avg", column=primary, format_kind=kind),
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
                recipe=KPIRecipe(kind="top_cat", column=top_cat),
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
    for k in unique:
        k.how = _kpi_how(k.recipe, len(df))
    return unique[:5]


def _kpi_how(r: KPIRecipe | None, n: int) -> str:
    """The tile's computation in words (Phase 5: every number says how it was made)."""
    if r is None:
        return ""
    return {
        "row_count": f"Count of all {n:,} rows after cleaning.",
        "target_rate": f"Share of rows where {r.column} is the positive value.",
        "metric_avg": f"Mean of {r.column} over its non-missing rows.",
        "top_cat": f"Most frequent value of {r.column}, and its share of rows.",
        "comparison_split": f"{r.target_column} rate in the highest and lowest {r.segment_column} "
                            "group (the column that separates it most, by chi-square).",
        "highest_cv": f"Coefficient of variation (std / |mean|) of {r.column}, the most variable "
                      "measure.",
    }.get(r.kind, "")


def kpi_mismatches(kpis: list[KPI], df: pd.DataFrame, eda: dict | None = None) -> list[str]:
    """Labels of tiles whose number doesn't match the data: each is recomputed from its recipe on
    this frame, and an average must also match the report's own statistics (eda_summary)."""
    bad = []
    for k in kpis:
        r = k.recipe
        if r is None:
            continue
        col = r.column
        try:
            if r.kind == "row_count":
                ok = k.value == _format_count(len(df))
            elif r.kind == "target_rate":
                ok = k.value == f"{float(_to_01(df[col]).mean()) * 100:.1f}%"
            elif r.kind == "metric_avg":
                mean = float(pd.to_numeric(df[col], errors="coerce").dropna().mean())
                ok = k.value == _format_value(mean, r.format_kind or "number")
                stated = ((eda or {}).get(col) or {}).get("mean")
                if ok and isinstance(stated, (int, float)) and mean:
                    ok = abs(float(stated) - mean) <= 0.005 * abs(mean)
            elif r.kind == "top_cat":
                vc = df[col].dropna().value_counts()
                top = vc.index[0]
                ok = k.context.startswith(f"{float(vc.iloc[0]) / float(vc.sum()) * 100:.1f}%") and (
                    str(top) in k.value or k.value == _humanize_value(top, col, df, set(), None))
            elif r.kind == "comparison_split":
                g = pd.Series(_to_01(df[r.target_column]).values, index=df[r.segment_column]) \
                    .groupby(level=0).mean().dropna().sort_values(ascending=False)
                ok = k.value == f"{g.iloc[0] * 100:.1f}% vs {g.iloc[-1] * 100:.1f}%"
            else:   # highest_cv
                s = pd.to_numeric(df[col], errors="coerce").dropna()
                ok = k.value == f"CV {float(s.std()) / abs(float(s.mean())):.2f}"
        except Exception:
            ok = False
        if not ok:
            bad.append(k.label)
    return bad


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
    ctx: "VizContext | None" = None,
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
    ctx = ctx or build_context(df, narrative=narrative)
    target: str | None = column_roles.get("target_col")  # type: ignore[assignment]
    # Phase 5: a measure (`units` 1-8) is never a category axis.
    cat_pool: list[str] = [
        c for c in (list(column_roles.get("low_card_cat") or []) + list(column_roles.get("binary_cols") or []))
        if c != target and c not in ctx.measures
    ]
    continuous: list[str] = list(column_roles.get("continuous") or [])
    # The headline measure: the planned metric, else an outcome-like name, else the first.
    planned = [c for c in (ctx.task.target_metrics if ctx.task else []) if c in continuous]
    primary = next(iter(planned), None) or next(
        (c for c in continuous if _KPI_PRIMARY_RE.search(c)), continuous[0] if continuous else None)
    datetime_cols: list[str] = list(column_roles.get("datetime_cols") or [])
    ordinal_cols: set[str] = set(column_roles.get("ordinal_cols") or [])
    numeric_cols = [
        c for c in df.columns
        if pd.api.types.is_numeric_dtype(df[c]) and c not in (column_roles.get("id_cols") or [])
    ]

    color_map = _build_color_map(df, column_roles)
    target_colors = _target_color_map(df, target) if target else {}
    charts: list[ChartConfig] = []

    # A measure the question names comes first, as its distribution (`units` 1-8 included).
    for c in [m for m in (ctx.task.target_metrics if ctx.task else []) if m in ctx.measures][:2]:
        ch = _build_histogram(df, c)
        if ch:
            ch.x_label = c
            charts.append(ch)

    # When the user's query asks about time/trends, surface the time-series
    # chart ahead of the target-by-cat block instead of after.
    wants_time = (
        bool(datetime_cols) and bool(continuous)
        and any(k in query.lower() for k in _TIME_KEYWORDS)
    )
    if wants_time:
        ch = month_line(df, datetime_cols[0], primary, ctx)
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
        ch = month_line(df, datetime_cols[0], primary, ctx)
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
    composition_cat = composition_cat if composition_cat not in ctx.measures else None
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
            ch = oriented_scatter(df, pair[0], pair[1], ctx)
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
        _profile_rationale(ch, ctx)

    return charts[:8]


def _profile_rationale(ch: ChartConfig, ctx: VizContext) -> None:
    """Rationale for a data-profile chart (overview with no findings to show). Row-count charts
    go to the Data tab: they describe the table, not an answer."""
    target = ctx.roles.get("target_col")
    src, x0 = ch.source_finding, ch.x_label or ch.x_key
    title = ch.title.lower()
    col = next((c for c in ctx.df.columns if c == ch.x_key or c == x0 or
                _display(c, ctx.narrative) == x0), None) or next(   # a donut is keyed "name"
        (c for c in ctx.df.columns if title.startswith((str(c).lower(),
                                                         _display(c, ctx.narrative).lower()))),
        ch.x_key)
    if src == "time_series" and ch.rationale:
        _why(ch, ctx, "profile:trend", "The main measure over time, totalled per month for an "
             f"additive measure and averaged for a rate. {ch.rationale.reason}".strip(), ch.rationale.x, ch.rationale.y,
             ch.rationale.aggregation, ch.rationale.n_rows, code=ch.rationale.code)
    elif src == "target_rate_by_cat" and target:
        for row in ch.data:   # Wilson CI from each bar's rate and n
            rate, n = row[ch.y_keys[0]] / 100, row.get("n", 0)
            ci = _ci(pd.Series([1] * round(rate * n) + [0] * (n - round(rate * n))), "mean") if n else None
            if ci:
                row[f"{ch.y_keys[0]}__ci"] = ci
        ch.error_key = "__ci"
        _why(ch, ctx, "profile:outcome_rate", f"The outcome ({target}) is a 0/1 flag, so each bar "
             f"is its rate (%) in that {col}; whiskers are 95% Wilson intervals. Bars above the "
             "overall rate are green.", col, target, "mean", int(ctx.df[[col, target]].dropna().shape[0]),
             code=f"df.groupby({col!r})[{target!r}].mean() * 100")
    elif src == "continuous_split_by_target" and target:
        col = next((c for c in ctx.df.columns if ch.title.lower().startswith(str(c).lower())), col)
        _why(ch, ctx, "profile:distribution_by_outcome", f"How {col} is distributed for each "
             f"{target} value.", col, "count", "count", int(ctx.df[col].notna().sum()), split=target,
             code=f"df.groupby({target!r})[{col!r}].value_counts(bins=...)")
    elif src == "scatter" and ch.rationale:
        _why(ch, ctx, "profile:relationship", f"The most correlated pair of measures; "
             f"{ch.rationale.reason}.", ch.rationale.x, ch.rationale.y, "none", ch.rationale.n_rows,
             code=ch.rationale.code)
    elif src == "correlation_heatmap":
        _why(ch, ctx, "profile:correlation_matrix", "Pearson r for every pair of numeric columns.",
             "numeric columns", "pearson r", "none", len(ctx.df), code="df.corr(numeric_only=True)")
    elif src == "distribution":
        col = ch.x_label if ch.x_label in ctx.df.columns else col
        if ch.title.startswith(str(col)):
            ch.title = _display(col, ctx.narrative) + ch.title[len(str(col)):]
        _why(ch, ctx, "profile:distribution", f"How {col} is distributed.", col, "count", "count",
             int(ctx.df[col].notna().sum()) if col in ctx.df.columns else len(ctx.df),
             code=f"np.histogram(df[{col!r}].dropna())")
    else:   # value counts, donut, top-N composition, stacked composition: describe the table
        ch.placement = "data"
        stacked = src == "composition_stacked" and target
        _why(ch, ctx, "profile:row_count", f"How many rows fall in each {col}" +
             (f", split by {target}." if stacked else "."), col, "count", "count", len(ctx.df),
             split=target if stacked else None, code=f"df[{col!r}].value_counts()")


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
            placement="data",
            rationale=ChartRationale(
                rule="profile:missing_values", reason="The data frame was not available, so this "
                "shows the share of missing values per column from the data contract.",
                x="column", y="missing %", aggregation="none", n_rows=contract.row_count,
                code="df.isna().mean() * 100"),
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


# ── Chart policy (Phase 5 Visualization): one chart per finding, why, and the lint ──────────
# An analyst's chart choice is a lookup from what the finding is to what shows it:
# trend → line over calendar months; comparison → bars of the metric with 95% CIs (means/rates);
# relationship → scatter, driver on x; unusual values → histogram with the fences marked.
# Every chart leaves through finalize_charts: lint, then repair or drop (agent's charts) or
# warn (the user's charts). Rationale is built here from what was computed, never by a model.

_OUTCOME_NAME = re.compile(
    r"(?i)(revenue|sales|profit|margin|income|convert|churn|retain|retention|surviv|default|"
    r"fraud|target|outcome|label)")
_GENERIC_COLORS = {c.lower() for c in _PALETTE} | {_NEUTRAL_TEAL.lower(), "#6366f1"}
_MAX_SUMMARY = 6
_MAX_SERIES = 8
_AGG_WORD = {"sum": "Total", "mean": "Average", "count": "Number of", "median": "Median",
             "min": "Minimum", "max": "Maximum"}
_LINT_NOTE = {
    "pie_non_additive": "A pie shows parts of a whole, and an average is not one; bars compare "
                        "these values more accurately.",
    "pie_too_many_slices": "More than 5 slices are hard to compare; a bar chart reads more "
                           "accurately.",
    "measure_on_category_axis": "This axis is a measured quantity; a histogram shows its "
                                "distribution better than one bar per value.",
    "outcome_on_x": "The outcome usually goes on the vertical axis, with its driver on x.",
    "too_many_series": "More than 8 series are hard to tell apart.",
    "line_over_unordered": "A line implies an order; these categories have none, so bars fit "
                           "better.",
    "missing_axis_label": "An axis has no label.",
    "empty_chart": "There is no data to plot for this choice.",
    "dual_axis": "Two value axes on one chart invite false comparisons.",
}


@dataclass
class VizContext:
    """Everything chart selection reads (ADR-0001 context builder). No prompt: nothing to render."""
    df: pd.DataFrame
    roles: dict
    measures: set[str]
    date: str | None
    task: AnalysisTask | None = None
    findings: AnalysisFindings | None = None
    evidence: dict[str, Evidence] = field(default_factory=dict)
    narrative: DataNarrative | None = None


def measure_columns(df: pd.DataFrame, roles: dict) -> set[str]:
    """Numeric quantities: continuous columns plus small integer measures (`units` 1-8), but not
    ordinal codes (`Pclass`), flags or ids. A measure is never a category axis."""
    ordinal = set(roles.get("ordinal_cols") or [])
    small = {c for c in roles.get("low_card_cat") or []
             if pd.api.types.is_numeric_dtype(df[c]) and c not in ordinal}
    return set(roles.get("continuous") or []) | small


def build_context(df: pd.DataFrame, task: AnalysisTask | None = None,
                  findings: AnalysisFindings | None = None,
                  narrative: DataNarrative | None = None) -> VizContext:
    roles = classify_columns(df)
    dates = list(roles.get("datetime_cols") or [])
    return VizContext(df=df, roles=roles, measures=measure_columns(df, roles),
                      date=dates[0] if dates else None, task=task, findings=findings,
                      evidence={e.id: e for e in (findings.evidence if findings else [])},
                      narrative=narrative)


def month_agg(s: pd.Series) -> str:
    """The analysis agent's MoM rule: values within 0-1 (rates, flags) by mean, else by total."""
    v = pd.to_numeric(s, errors="coerce").dropna()
    return "mean" if len(v) and v.between(0, 1).all() else "sum"


def _is_flag(s: pd.Series) -> bool:
    v = pd.to_numeric(s, errors="coerce").dropna()
    return len(v) > 0 and set(v.unique()) <= {0, 1}


def pick_outcome(cols: list[str], ctx: VizContext) -> tuple[str | None, str]:
    """Which of a relationship's columns is the outcome (y), and why."""
    planned = [c for c in (ctx.task.target_metrics if ctx.task else []) if c in cols]
    for c in planned:
        if _OUTCOME_NAME.search(c):
            return c, f"{c} is the planned metric and names a business outcome"
    target = ctx.roles.get("target_col")
    if target in cols:
        return target, f"{target} is the dataset's outcome column"
    named = [c for c in cols if _OUTCOME_NAME.search(c)]
    if len(named) == 1:
        return named[0], f"{named[0]} names a business outcome"
    return None, "neither column is marked as the outcome, so the order is the analysis order"


def _label(metric: str, agg: str, ctx: VizContext) -> str:
    if agg == "count":
        return "Rows"
    disp = _display(metric, ctx.narrative)
    if agg == "mean" and _is_flag(ctx.df[metric]):
        return f"{_rate_label(metric, ctx.narrative)} (%)"
    return f"{_AGG_WORD.get(agg, agg.title())} {disp.lower() if agg == 'count' else disp}"


def _why(chart: ChartConfig, ctx: VizContext | None, rule: str, reason: str, x: str, y: str,
         agg: str, n: int, ids: list[str] | None = None, code: str = "",
         split: str | None = None) -> ChartConfig:
    ids = [i for i in (ids or []) if ctx is None or i in ctx.evidence]
    levels = [ctx.evidence[i].confidence for i in ids] if ctx else []
    conf = min(levels, key=["low", "medium", "high"].index) if levels else None
    chart.rationale = ChartRationale(rule=rule, reason=reason, x=x, y=y, split=split,
                                     aggregation=agg, n_rows=int(n), evidence_ids=ids, code=code,
                                     confidence=conf)
    return chart


def _ci(s: pd.Series, agg: str) -> list[float] | None:
    """95% CI as [minus, plus] around the plotted value: Wilson for a 0/1 rate, t for a mean,
    none for totals and counts (no sampling error to show)."""
    v = pd.to_numeric(s, errors="coerce").dropna()
    n = len(v)
    if agg != "mean" or n < 2:
        return None
    if set(v.unique()) <= {0, 1}:
        p, z = float(v.mean()), 1.96
        centre = (p + z * z / (2 * n)) / (1 + z * z / n)
        half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
        return [round(max(p - (centre - half), 0) * 100, 3), round(max(centre + half - p, 0) * 100, 3)]
    half = float(scipy_stats.t.ppf(0.975, n - 1) * v.std(ddof=1) / math.sqrt(n))
    return [round(half, 4), round(half, 4)] if math.isfinite(half) else None


def group_bars(df: pd.DataFrame, x: str, metric: str, agg: str, ctx: VizContext,
               split: str | None = None, top: int = 15) -> ChartConfig | None:
    """Bars of agg(metric) per x (one bar per split value inside each x), zero baseline, 95% CI
    for means and rates, n per bar. Sorted by value unless x has a natural order."""
    if agg == "pct":
        agg = "mean"
    cols = [x, metric] + ([split] if split else [])
    if any(c not in df.columns for c in cols):
        return None
    sub = df[cols].copy()
    sub[metric] = pd.to_numeric(sub[metric], errors="coerce")
    sub = sub.dropna()
    if sub.empty or sub[x].nunique() < 2 and not split:
        return None
    rate = agg == "mean" and _is_flag(sub[metric])
    scale = 100.0 if rate else 1.0
    order = sub.groupby(x)[metric].agg(agg).sort_values(ascending=False)
    natural = _sort_key_for(order.index)   # numeric labels (1, 2, 3) keep their order
    keys = sorted(order.index[:top], key=natural) if natural else list(order.index[:top])
    series = [None] if not split else list(sub[split].value_counts().index[:_MAX_SERIES])
    data, any_ci = [], False
    for k in keys:
        row: dict = {x: str(k)}
        for sv in series:
            part = sub[(sub[x] == k) & ((sub[split] == sv) if split else True)][metric]
            name = metric if sv is None else str(sv)
            if part.empty:
                continue
            row[name] = round(float(part.agg(agg)) * scale, 4)
            row[f"{name}__n"] = int(len(part))
            if (ci := _ci(part, agg)) is not None:
                row[f"{name}__ci"], any_ci = ci, True
        data.append(row)
    y_keys = [metric] if not split else [str(s) for s in series]
    disp_x = _display(x, ctx.narrative)
    title = f"{_label(metric, agg, ctx).replace(' (%)', '')} by {disp_x}" + (
        f" and {_display(split, ctx.narrative)}" if split else "")
    groupby = f"[{x!r}, {split!r}]" if split else repr(x)
    return ChartConfig(
        id=str(uuid.uuid4()),
        chart_type=ChartType.BAR if len(keys) <= 8 else ChartType.HORIZONTAL_BAR,
        title=title, data=data, x_key=x, y_keys=y_keys, colors=[],
        x_label=disp_x, y_label=_label(metric, agg, ctx), show_legend=bool(split),
        source_finding="group_bars", value_format="percent" if rate else "number",
        error_key="__ci" if any_ci else None, split_key=split,
        rationale=ChartRationale(rule="", reason="", x=x, y=metric, split=split, aggregation=agg,
                                 n_rows=len(sub), code=f"df.groupby({groupby})[{metric!r}].{agg}()"),
    )


def month_line(df: pd.DataFrame, date: str, metric: str, ctx: VizContext,
               split: str | None = None, monthly: bool | None = None) -> ChartConfig | None:
    """agg(metric) per calendar month (per day under ~3 months), one line per split value (top 8
    by rows). Months match the analysis agent's MoM, so the chart shows the reported change."""
    cols = [date, metric] + ([split] if split else [])
    if any(c not in df.columns for c in cols):
        return None
    sub = df[cols].copy()
    sub[date] = pd.to_datetime(sub[date], format="mixed", errors="coerce")
    sub[metric] = pd.to_numeric(sub[metric], errors="coerce")
    sub = sub.dropna()
    if sub.empty:
        return None
    agg = month_agg(df[metric])
    if monthly is None:   # a month-over-month finding always gets months
        monthly = (sub[date].max() - sub[date].min()).days >= 90
    note = ""
    if monthly and partial_last_month(sub[date]):   # never compare a partial month with a full one
        cut = sub[date].dt.to_period("M").max()
        note = (f"{cut.strftime('%b %Y')} is left out: the data ends "
                f"{sub[date].max():%d %b %Y}, so that month is incomplete.")
        sub = sub[sub[date].dt.to_period("M") < cut]
    period = sub[date].dt.to_period("M" if monthly else "D").astype(str)
    rate = agg == "mean" and _is_flag(sub[metric])
    scale = 100.0 if rate else 1.0
    keys = [None] if not split else list(sub[split].value_counts().index[:_MAX_SERIES])
    table = (sub.groupby([period] + ([sub[split]] if split else []))[metric].agg(agg) * scale)
    if split:
        table = table.unstack()[keys]
    if len(table) < 2:
        return None
    data = []
    for p, v in table.iterrows() if split else table.items():
        row = {"period": str(p)}
        if split:
            row.update({str(k): round(float(v[k]), 4) for k in keys if pd.notna(v[k])})
        else:
            row[metric] = round(float(v), 4)
        data.append(row)
    step = "month" if monthly else "day"
    by = f", per {_display(split, ctx.narrative)}" if split else ""
    freq = "M" if monthly else "D"
    groupby = f"[_period, {split!r}]" if split else "_period"
    return ChartConfig(
        id=str(uuid.uuid4()), chart_type=ChartType.LINE,
        title=f"{_label(metric, agg, ctx).replace(' (%)', '')} by {step}{by}",
        data=data, x_key="period", y_keys=[metric] if not split else [str(k) for k in keys],
        colors=[], x_label=step.title(), y_label=_label(metric, agg, ctx),
        show_legend=bool(split), source_finding="time_series", split_key=split,
        value_format="percent" if rate else "number", layout_hint="full_width",
        rationale=ChartRationale(
            rule="", reason=note, x=date, y=metric, split=split, aggregation=agg, n_rows=len(sub),
            code=f"_period = pd.to_datetime(df[{date!r}], format='mixed').dt.to_period({freq!r}); "
                 f"df.groupby({groupby})[{metric!r}].{agg}()"),
    )


def fence_histogram(df: pd.DataFrame, col: str, ctx: VizContext, k: float = 3.0) -> ChartConfig | None:
    """Full-range histogram (no tail clipping: the tail is the point) with the Tukey fences
    Q1 - k·IQR and Q3 + k·IQR marked, and the count beyond them in the title."""
    s = pd.to_numeric(df[col], errors="coerce").dropna() if col in df.columns else pd.Series()
    if len(s) < 4:
        return None
    q1, q3 = float(s.quantile(0.25)), float(s.quantile(0.75))
    lo, hi = q1 - k * (q3 - q1), q3 + k * (q3 - q1)
    counts, edges = np.histogram(s, bins=max(_sturges_bins(len(s)), 20))
    fmt = _bin_label_formatter(col, s)
    data = [{"bin": fmt(edges[i]), "count": int(counts[i])} for i in range(len(counts))]

    def bin_of(v: float) -> str:
        return data[int(min(max(np.searchsorted(edges, v, side="right") - 1, 0), len(data) - 1))]["bin"]

    refs = [{"axis": "x", "value": bin_of(hi), "label": f"Q3 + {k:g}×IQR = {hi:,.2f}"}]
    if lo > float(s.min()):
        refs.insert(0, {"axis": "x", "value": bin_of(lo), "label": f"Q1 − {k:g}×IQR = {lo:,.2f}"})
    n_out = int(((s < lo) | (s > hi)).sum())
    disp = _display(col, ctx.narrative)
    return ChartConfig(
        id=str(uuid.uuid4()), chart_type=ChartType.HISTOGRAM,
        title=f"{n_out} unusual {disp.lower()} value{'s' if n_out != 1 else ''} " + (
            f"outside {lo:,.2f} – {hi:,.2f}" if lo > float(s.min()) else f"above {hi:,.2f}"),
        data=data, x_key="bin", y_keys=["count"], colors=[], x_label=disp, y_label="Rows",
        show_legend=False, source_finding="anomaly", reference_lines=refs,
        rationale=ChartRationale(
            rule="", reason="", x=col, y="count", aggregation="count", n_rows=len(s),
            code=f"q1, q3 = df[{col!r}].quantile([.25, .75]); "
                 f"df[{col!r}].between(q1 - {k:g}*(q3-q1), q3 + {k:g}*(q3-q1))"),
    )


def oriented_scatter(df: pd.DataFrame, a: str, b: str, ctx: VizContext) -> ChartConfig | None:
    """Scatter of a relationship with the driver on x and the outcome on y."""
    if a == b or any(c not in df.columns or not pd.api.types.is_numeric_dtype(df[c]) for c in (a, b)):
        return None   # a relationship needs two different numeric columns
    outcome, why = pick_outcome([a, b], ctx)
    x, y = (b, a) if outcome == a else (a, b)
    chart = _build_scatter(df, x, y)
    if chart is None:
        return None
    chart.colors = []
    chart.x_label, chart.y_label = _display(x, ctx.narrative), _display(y, ctx.narrative)
    n = int(df[[x, y]].dropna().shape[0])
    r = df[x].corr(df[y]) if pd.api.types.is_numeric_dtype(df[x]) and pd.api.types.is_numeric_dtype(df[y]) else None
    chart.title = f"{chart.y_label} vs {chart.x_label}" + (f" (r = {r:.2f})" if r is not None and pd.notna(r) else "")
    return _why(chart, None, "", why, x, y, "none", n,
                code=f"df[[{x!r}, {y!r}]].dropna().sample(min(n, 500), random_state=42)")


def _finding_chart(kind: str, ctx: VizContext, ids: list[str], *args) -> ChartConfig | None:
    df = ctx.df
    if kind == "trend":
        metric, split, claim = args
        chart = month_line(df, ctx.date, metric, ctx, split, monthly=True) if ctx.date else None
        rule, reason = ("trend→line", f"A trend is change over time; one line per {split} shows "
                        f"which moved. {claim}") if split else (
            "trend→line", f"A trend is change over time. {claim}")
    elif kind == "segment":
        x, metric, agg, split = args
        chart = group_bars(df, x, metric, agg, ctx, split)
        if chart and agg == "count":   # rows per group describe the table, not an answer
            chart.placement = "data"
        rule = "segment→bars"
        reason = (f"Compares {agg} {metric} across {x}" + (f", one bar per {split} inside each "
                  f"{x}, so the comparison holds within every {x}" if split else "")
                  + ("; whiskers are 95% confidence intervals" if agg in ("mean", "pct") else
                     "; totals have no sampling error, so no interval") + ".")
    elif kind == "test":
        x, metric = args
        chart = group_bars(df, x, metric, "mean", ctx)
        rule, reason = "test→group means", (f"The test compares mean {metric} between {x} groups;"
                                            " bars show each mean with its 95% CI.")
    elif kind == "correlation":
        a, b = args
        chart = oriented_scatter(df, a, b, ctx)
        rule = "correlation→scatter"
        # The outcome's why (chart.rationale.reason) is appended below.
        reason = "A relationship between two measures is a scatter, with the driver on x;"
    else:   # anomaly
        (col,) = args
        chart = fence_histogram(df, col, ctx)
        rule, reason = "anomaly→histogram", ("Unusual values sit in the tail of the distribution; "
                                             "the lines mark the Tukey fences (Q ± 3×IQR).")
    if chart is None or chart.rationale is None:
        return None
    r = chart.rationale
    chart.title = _finding_title(kind, chart, ctx, args) or chart.title
    reason = f"{reason} {r.reason}".strip()   # e.g. a partial last month left out
    return _why(chart, ctx, rule, reason, r.x, r.y, r.aggregation, r.n_rows, ids, r.code, r.split)


def _month_name(period: str) -> str:
    try:
        return pd.Period(period).strftime("%b %Y")
    except Exception:
        return period


def _finding_title(kind: str, chart: ChartConfig, ctx: VizContext, args: tuple) -> str | None:
    """A title that states the finding, from the plotted numbers (never from model prose)."""
    d, r = chart.data, chart.rationale
    disp = _display(r.y, ctx.narrative) if r.y in ctx.df.columns else r.y
    if kind == "trend" and len(d) >= 2:
        keys = chart.y_keys
        moves = {k: (d[-1].get(k), d[-2].get(k)) for k in keys}
        moves = {k: (a / b - 1) * 100 for k, (a, b) in moves.items()
                 if isinstance(a, (int, float)) and isinstance(b, (int, float)) and b}
        if not moves:
            return None
        k = max(moves, key=lambda m: abs(moves[m]))
        who = f"{disp} for {r.split} {k}" if r.split else disp
        verb = "rose" if moves[k] > 0 else "fell" if moves[k] < 0 else "held"
        return (f"{who} {verb} {abs(moves[k]):.1f}% in {_month_name(d[-1]['period'])} "
                f"vs {_month_name(d[-2]['period'])}")
    if kind in ("segment", "test") and not r.split and len(d) >= 2:
        y = chart.y_keys[0]
        ranked = sorted((row for row in d if isinstance(row.get(y), (int, float))),
                        key=lambda row: -row[y])
        if len(ranked) < 2:
            return None
        fmt = (lambda v: f"{v:.1f}%") if chart.value_format == "percent" else (lambda v: f"{v:,.2f}")
        top, low = ranked[0], ranked[-1]
        return (f"{_label(r.y, r.aggregation, ctx).replace(' (%)', '')} by "
                f"{_display(r.x, ctx.narrative)}: {top[r.x]} highest ({fmt(top[y])}), "
                f"{low[r.x]} lowest ({fmt(low[y])})")
    return None


def charts_for_findings(ctx: VizContext) -> list[ChartConfig]:
    """One chart per finding, in the order the plan asked for its kind (then analysis order)."""
    f, task = ctx.findings, ctx.task
    if f is None:
        return []
    cols = set(ctx.df.columns)
    jobs: dict[str, list[tuple]] = {k: [] for k in ("trend", "segment", "correlation", "anomaly", "test")}

    by_col: dict[str, list] = {}
    for t in f.trends:
        if t.column in cols:
            by_col.setdefault(t.column, []).append(t)
    split = next((s for s in (task.segments if task else []) if s in cols), None)
    def tid(t) -> str:   # build_evidence's trend id
        return f"trend:{t.column}" + (f" ({t.segment})" if t.segment else "") + f":{t.period}"

    for col, ts in by_col.items():
        per_seg = [t for t in ts if t.segment]
        overall = [t for t in ts if not t.segment]
        if per_seg and split:
            top = max(per_seg, key=lambda t: abs(t.pct_change))
            claim = f"{top.segment} changed {top.pct_change:+.1f}% {top.period or 'last period'}."
            jobs["trend"].append(((col, split, claim), [tid(t) for t in per_seg]))
        for t in overall:
            claim = f"{col} changed {t.pct_change:+.1f}% {t.period or 'last period'}."
            jobs["trend"].append(((col, None, claim), [tid(t)]))

    seen: set[tuple] = set()
    for s in sorted(f.segments, key=lambda s: "|" not in s.segment_column):   # stratified first
        parts = [c for c in s.segment_column.split("|") if c]
        if not parts or any(c not in cols for c in parts) or s.metric_column not in cols:
            continue
        x, sp = (parts[1], parts[0]) if len(parts) >= 2 else (parts[0], None)
        seen.add((parts[0], s.metric_column))
        jobs["segment"].append(((x, s.metric_column, s.aggregation or "mean", sp),
                                [segment_id(s.segment_column, s.metric_column, s.aggregation)]))
    for i, t in enumerate(f.statistical_tests):
        if not task or not task.target_metrics or not task.segments:
            continue
        x, metric = task.segments[0], task.target_metrics[0]
        if x not in cols or metric not in cols:
            continue
        if (x, metric) in seen:   # already charted as a comparison: cite the test there
            for args, ids in jobs["segment"]:
                if args[0] == x and args[1] == metric or args[3] == x and args[1] == metric:
                    ids.append(f"test:{i}")
                    break
            continue
        seen.add((x, metric))
        jobs["test"].append(((x, metric), [f"test:{i}"]))
    for key in f.correlations:
        a, _, b = key.partition("__")
        if a in cols and b in cols:
            jobs["correlation"].append(((a, b), [f"corr:{key}"]))
    for col in dict.fromkeys(a.column for a in f.anomalies):
        if col in cols:
            jobs["anomaly"].append(((col,), [f"anom:{col}"]))

    order = {"trend": "trend", "segmentation": "segment", "correlation": "correlation",
             "anomaly": "anomaly", "hypothesis": "test"}
    asked = [order[t.value if hasattr(t, "value") else t] for t in (task.analysis_types if task else [])
             if (t.value if hasattr(t, "value") else t) in order]
    kinds = list(dict.fromkeys(asked + list(jobs)))
    # Within a kind, findings about the plan's first metric come first; across kinds, take turns,
    # so a full overview shows its trend, comparison, relationship and outliers, not six bars.
    rank = {m: i for i, m in enumerate(task.target_metrics if task else [])}
    for kind in kinds:
        jobs[kind].sort(key=lambda job: min((rank[a] for a in job[0] if a in rank), default=len(rank)))
    queues = [list(jobs[k]) for k in kinds]
    charts: list[ChartConfig] = []
    while any(queues) and len(charts) < _MAX_SUMMARY:
        for kind, queue in zip(kinds, queues):
            if queue and len(charts) < _MAX_SUMMARY:
                args, ids = queue.pop(0)
                try:
                    chart = _finding_chart(kind, ctx, ids, *args)
                except Exception:   # one malformed finding costs its chart, never the report
                    logger.warning("chart for %s finding skipped", kind, exc_info=True)
                    trace_verdict("visualization", VIZ_CARD.version, f"chart_error:{kind}")
                    chart = None
                if chart:
                    charts.append(chart)
    return charts


def _repair(chart: ChartConfig, label: str, ctx: VizContext) -> ChartConfig | None:
    """The deterministic fix for one lint rule, or None to drop the chart."""
    r = chart.rationale
    if label == "missing_axis_label":
        chart.x_label = chart.x_label or (_display(r.x, ctx.narrative) if r else chart.x_key)
        chart.y_label = chart.y_label or (_label(r.y, r.aggregation, ctx) if r and r.y in ctx.df.columns
                                          else "Rows")
        return chart
    if label in ("pie_too_many_slices", "pie_non_additive"):
        chart.chart_type = ChartType.HORIZONTAL_BAR
        if r:
            r.reason += " Shown as bars: a pie only works for up to 5 parts of a whole."
        return _repair(chart, "missing_axis_label", ctx)
    if label == "line_over_unordered":
        chart.chart_type = ChartType.BAR
        return chart
    if label == "too_many_series":
        totals = {k: sum(float(d.get(k) or 0) for d in chart.data) for k in chart.y_keys}
        keep = sorted(chart.y_keys, key=lambda k: -abs(totals[k]))[:_MAX_SERIES]
        chart.y_keys = [k for k in chart.y_keys if k in keep]
        if r:
            r.reason += f" Showing the {_MAX_SERIES} largest series."
        return chart
    if label == "outcome_on_x" and len(chart.y_keys) == 1:
        x, y = chart.x_key, chart.y_keys[0]
        chart.x_key, chart.y_keys = y, [x]
        chart.x_label, chart.y_label = chart.y_label, chart.x_label
        if r:
            r.x, r.y = y, x
        return chart
    if label == "measure_on_category_axis" and r and r.x in ctx.df.columns:
        hist = _build_histogram(ctx.df, r.x)
        if hist is None:
            return None
        hist.colors, hist.placement = [], chart.placement
        return _why(hist, ctx, "distribution→histogram",
                    f"{r.x} is a measured quantity, so its distribution is a histogram.",
                    r.x, "count", "count", int(ctx.df[r.x].notna().sum()), r.evidence_ids,
                    f"np.histogram(df[{r.x!r}].dropna())")
    return None   # empty_chart, dual_axis: nothing honest to repair


_SWAP_FREE = {ChartType.SCATTER, ChartType.HISTOGRAM, ChartType.HEATMAP, ChartType.COMPOSED}


def swap_options(chart: ChartConfig) -> list[ChartType]:
    """Chart types a user can switch to on the spot without misleading: bars ↔ horizontal bars,
    a line only over an ordered x, a pie/donut only for ≤ 5 additive parts. Mirrors
    frontend ChartRenderer swapOptions()."""
    if chart.chart_type in _SWAP_FREE:
        return []
    xs = [d.get(chart.x_key) for d in chart.data]
    ordered = chart.x_key == "period" or bool(xs) and all(_sort_key_for([x]) for x in xs)
    agg = chart.rationale.aggregation if chart.rationale else None
    parts = len(chart.y_keys) == 1 and len(chart.data) <= 5 and agg in ("sum", "count")
    opts = [ChartType.BAR, ChartType.HORIZONTAL_BAR] + ([ChartType.LINE] if ordered else []) \
        + ([ChartType.DONUT, ChartType.PIE] if parts else [])
    return opts if chart.chart_type in opts else [chart.chart_type, *opts]


def finalize_charts(charts: list[ChartConfig], ctx: VizContext,
                    user_chosen: bool = False) -> list[ChartConfig]:
    """Every chart passes here before it leaves the backend. Agent-chosen charts are repaired or
    dropped until they lint clean (a trace line per drop); a chart the user chose is drawn as
    asked, with a note per lint objection. Generic palette colours become [] (theme decides)."""
    out = []
    for chart in charts:
        if chart.colors and all(c.lower() in _GENERIC_COLORS for c in chart.colors):
            chart.colors = []
        if chart.rationale is None:
            if not user_chosen:
                trace_verdict("visualization", VIZ_CARD.version, "no_rationale")
                continue
            _why(chart, ctx, "user:choice", "Drawn as you asked.", chart.x_key,
                 (chart.y_keys or [""])[0], "?", len(chart.data))
        scatter = chart.chart_type == ChartType.SCATTER
        outcome = pick_outcome([chart.x_key, *chart.y_keys], ctx)[0] if scatter else None
        labels = chart_lint(chart.model_dump(mode="json"), ctx.measures, outcome)
        if user_chosen:
            chart.lint_warnings = [_LINT_NOTE.get(lb, lb) for lb in labels]
            out.append(chart)
            continue
        for _ in range(3):
            if not labels or chart is None:
                break
            chart = _repair(chart, labels[0], ctx)
            if chart is not None:
                labels = chart_lint(chart.model_dump(mode="json"), ctx.measures,
                                    outcome if chart.chart_type == ChartType.SCATTER else None)
        if chart is None or labels:
            trace_verdict("visualization", VIZ_CARD.version, f"dropped:{(labels or ['repair'])[0]}")
            continue
        out.append(chart)
    return out


# ── Public entry point ───────────────────────────────────────────────────────

async def run_visualization_agent(session: AnalysisSession) -> list[ChartConfig]:
    """One chart per finding (charts_for_findings), topped up with data-profile charts when there
    are fewer than 4, then linted (finalize_charts). Falls back to contract metadata when the
    frame isn't available (resume failure, etc)."""
    contract = session.data_contract
    if contract is None:
        raise ValueError("DataContract must be present before visualization.")

    df_parquet: bytes | None = session.__dict__.get("_df_parquet")
    try:
        df = pd.read_parquet(io.BytesIO(df_parquet)) if df_parquet else pd.DataFrame()
    except Exception:
        df = pd.DataFrame()
    if df.empty:
        return _contract_fallback_charts(contract)

    ctx = build_context(df, session.task_plan, session.analysis_findings, session.narrative)
    charts = charts_for_findings(ctx)
    if len(charts) < 4:
        query = session.task_plan.raw_query if session.task_plan else ""
        shown = {(c.rationale.x, c.rationale.y) for c in charts if c.rationale}
        for ch in generate_dashboard_charts(df, ctx.roles, query=query,
                                            narrative=session.narrative, ctx=ctx):
            if ch.rationale and (ch.rationale.x, ch.rationale.y) in shown:
                continue
            if ch.placement == "summary" and sum(c.placement == "summary" for c in charts) >= _MAX_SUMMARY:
                continue
            charts.append(ch)
    charts = finalize_charts(charts, ctx)
    return charts or _contract_fallback_charts(contract)


def build_single_chart(
    df: "pd.DataFrame",
    params: "TargetedParams",
) -> "ChartConfig":
    """One chart the user asked for (CHART_REQUEST). Deterministic; drawn as asked, with a lint
    note when the choice misleads. Bars and pies aggregate the analysis way: totals for additive
    measures, means for rates; a time x becomes a monthly line's axis."""
    chart_type = params.chart_type or ChartType.BAR
    available = list(df.columns)
    x_key = params.group_by if params.group_by in available else None
    y_keys: list[str] = [params.metric_column] if params.metric_column in available else []
    for col in params.target_columns:
        if col not in available:
            continue
        if x_key is None:
            x_key = col
        elif col not in y_keys:
            y_keys.append(col)

    ctx = build_context(df)
    if x_key is None or not y_keys:
        # Last resort: first non-id column on x, first measure on y.
        id_cols = set(ctx.roles.get("id_cols") or [])
        x_key = x_key or next((c for c in available if c not in id_cols), available[0] if available else "")
        y_keys = y_keys or [next((c for c in available if c in ctx.measures and c != x_key),
                                 available[1] if len(available) > 1 else x_key)]
    y = y_keys[0]
    numeric_y = y in df.columns and pd.api.types.is_numeric_dtype(df[y])
    agg = month_agg(df[y]) if numeric_y else "count"

    chart: ChartConfig | None = None
    if chart_type == ChartType.SCATTER and numeric_y:
        chart = _build_scatter(df, x_key, y)
        if chart:
            chart.colors, agg = [], "none"
    elif chart_type == ChartType.HISTOGRAM:
        chart = _build_histogram(df, y if numeric_y else x_key)
        agg = "count"
    elif x_key == ctx.date and numeric_y:
        chart = month_line(df, x_key, y, ctx)
    elif numeric_y:
        chart = group_bars(df, x_key, y, agg, ctx, top=20)
    if chart is None:   # non-numeric y or nothing to aggregate: rows per x
        vc = df[x_key].astype(str).value_counts().head(20)
        chart = ChartConfig(id=str(uuid.uuid4()), chart_type=chart_type, title=f"Rows by {x_key}",
                            data=[{x_key: k, "count": int(v)} for k, v in vc.items()], x_key=x_key,
                            y_keys=["count"], x_label=_display(x_key, None), y_label="Rows",
                            source_finding="user_chart_request")
        agg, y = "count", "count"
    chart.chart_type = chart_type
    chart.source_finding = "user_chart_request"
    chart.error_key = chart.error_key if chart_type in (ChartType.BAR, ChartType.HORIZONTAL_BAR) else None
    r = chart.rationale
    _why(chart, ctx, "user:chart_request", f"Drawn as you asked: {chart_type.value} of "
         f"{agg if agg not in ('none', 'count') else ''} {y} by {x_key}.".replace("  ", " "),
         r.x if r else x_key, r.y if r else y, agg, r.n_rows if r else len(df),
         code=r.code if r else "")
    return finalize_charts([chart], ctx, user_chosen=True)[0]
