"""
Phase A — Targeted analysis agent.

Deterministic pandas/scipy handlers, one per targeted question intent. No LLM,
no E2B — the questions are well-shaped enough that the right answer is a small
amount of clearly-correct code. Returns a compact TargetedResponse rendered as
a single chat card.

Routing: the graph's targeted_analysis_node calls run_targeted_analysis() instead
of analysis_agent + visualization_agent + insight_agent + quality_critic
when task.question_intent is anything other than OVERVIEW or DRILL_DOWN.
"""
from __future__ import annotations

import io
from typing import Callable
from uuid import uuid4

import numpy as np
import pandas as pd

from agents.utils import pandas_freq, partial_last_month
from agents.visualization_agent import (
    VizContext,
    _why,
    build_context,
    fence_histogram,
    finalize_charts,
    group_bars,
    oriented_scatter,
)
from scipy import stats

from orchestrator.state import (
    AnalysisSession,
    ChartConfig,
    ChartType,
    DataContract,
    QuestionIntent,
    TargetedParams,
    TargetedResponse,
)


# ── Public entry point ────────────────────────────────────────────────────────

def run_targeted_analysis(session: AnalysisSession) -> TargetedResponse:
    """Dispatch to the right handler based on session.task_plan.question_intent."""
    if session.task_plan is None or session.data_contract is None:
        return _unanswerable(
            QuestionIntent.OVERVIEW,
            "Missing task plan or data contract.",
            ["Re-upload your data and ask again."],
        )

    df_parquet = session.__dict__.get("_df_parquet")
    if df_parquet is None:
        return _unanswerable(
            session.task_plan.question_intent,
            "DataFrame not available in this session.",
            ["Re-upload your data and ask again."],
        )

    df = pd.read_parquet(io.BytesIO(df_parquet))
    intent = session.task_plan.question_intent
    params = session.task_plan.targeted_params
    contract = session.data_contract

    handler = _HANDLERS.get(intent)
    if handler is None:
        return _unanswerable(
            intent,
            f"Intent '{intent.value}' is not handled by the targeted pipeline.",
            ["Ask for an overview", "Rephrase as a top-N or comparison question"],
        )
    # Phase 5: every chart a user sees follows the visualization policy and its lint.
    ctx = build_context(df, session.task_plan, narrative=session.narrative)
    extra = {"strata": session.task_plan.segments} if intent == QuestionIntent.AB_SIGNIFICANCE else {}
    response = handler(df, params, contract, ctx=ctx, **extra)
    response.chart_configs = finalize_charts(response.chart_configs, ctx)
    return response


# ── Helpers ──────────────────────────────────────────────────────────────────

def _unanswerable(intent: QuestionIntent, reason: str, suggestions: list[str]) -> TargetedResponse:
    return TargetedResponse(
        intent=intent,
        answer_text=f"I couldn't answer this — {reason}",
        unanswerable_reason=reason,
        suggestions=suggestions,
    )


def _chart(chart_type: ChartType, title: str, data: list[dict], x_key: str, y_keys: list[str],
           x_label: str | None = None, y_label: str | None = None, source: str = "targeted") -> ChartConfig:
    return ChartConfig(
        id=str(uuid4()),
        chart_type=chart_type,
        title=title,
        data=data,
        x_key=x_key,
        y_keys=y_keys,
        colors=[],  # let the frontend palette (B1) decide
        x_label=x_label,
        y_label=y_label,
        source_finding=source,
    )


def _numeric_cols(df: pd.DataFrame, contract: DataContract) -> list[str]:
    return [c for c in contract.metric_columns if c in df.columns and pd.api.types.is_numeric_dtype(df[c])]


def _suggest_metric_questions(contract: DataContract) -> list[str]:
    return [f"Show the distribution of {c}" for c in contract.metric_columns[:2]]


# ── Per-intent handlers ──────────────────────────────────────────────────────

def handle_correlation(df: pd.DataFrame, params: TargetedParams, contract: DataContract,
                       ctx: VizContext | None = None) -> TargetedResponse:
    requested = [c for c in params.target_columns if c in df.columns]
    cols = [c for c in (requested or _numeric_cols(df, contract))
            if pd.api.types.is_numeric_dtype(df[c])]
    if len(cols) < 2:
        return _unanswerable(
            QuestionIntent.CORRELATION,
            "Need at least 2 numeric columns to compute correlation.",
            _suggest_metric_questions(contract) or ["Upload data with more than one numeric column"],
        )

    corr = df[cols].corr(numeric_only=True).round(3)
    table_columns = ["variable", *cols]
    table_data = [{"variable": c, **{k: float(v) for k, v in corr.loc[c].items()}} for c in cols]

    pairs: list[tuple[str, str, float]] = []
    for i, ci in enumerate(cols):
        for cj in cols[i + 1:]:
            val = corr.loc[ci, cj]
            if pd.notna(val):
                pairs.append((ci, cj, float(val)))
    pairs.sort(key=lambda p: abs(p[2]), reverse=True)

    if not pairs:
        return _unanswerable(
            QuestionIntent.CORRELATION,
            "All pairwise correlations were undefined (likely constant or all-NaN columns).",
            ["Try a different set of columns", "Show distribution of one column instead"],
        )

    strongest = pairs[0]
    sign = "positively" if strongest[2] > 0 else "negatively"
    answer = (
        f"Strongest correlation: {strongest[0]} and {strongest[1]} are {sign} "
        f"correlated (r={strongest[2]:.2f}). Showing top {min(10, len(pairs))} pairs."
    )

    # The pair asked about (else the strongest) as a scatter: a bar of r values hides the data.
    ctx = ctx or build_context(df)
    a, b = (requested[0], requested[1]) if len(requested) == 2 else strongest[:2]
    chart = oriented_scatter(df, a, b, ctx)
    if chart and chart.rationale:
        r = chart.rationale
        _why(chart, ctx, "targeted:correlation→scatter", f"A relationship between two measures is "
             f"a scatter; {r.reason}. All pairwise r values are in the table.", r.x, r.y, "none",
             r.n_rows, code=r.code)
    return TargetedResponse(
        intent=QuestionIntent.CORRELATION,
        answer_text=answer,
        chart_configs=[chart] if chart else [],
        table_data=table_data,
        table_columns=table_columns,
    )


def handle_distribution(df: pd.DataFrame, params: TargetedParams, contract: DataContract,
                        ctx: VizContext | None = None) -> TargetedResponse:
    col = (params.target_columns[0] if params.target_columns else None) \
        or (contract.metric_columns[0] if contract.metric_columns else None)
    if not col or col not in df.columns:
        return _unanswerable(
            QuestionIntent.DISTRIBUTION,
            f"Column '{col}' not found." if col else "No column to plot a distribution of.",
            _suggest_metric_questions(contract) or ["Specify a column to plot"],
        )

    series = df[col].dropna()
    if series.empty:
        return _unanswerable(
            QuestionIntent.DISTRIBUTION,
            f"Column '{col}' has no non-null values.",
            ["Try a different column", "Run a missing-data scan"],
        )

    if pd.api.types.is_numeric_dtype(series):
        counts, edges = np.histogram(series, bins=20)
        chart_data = [
            {"bin": f"{edges[i]:.2f}", "count": int(counts[i])} for i in range(len(counts))
        ]
        chart = _chart(
            ChartType.HISTOGRAM, f"Distribution of {col}", chart_data,
            x_key="bin", y_keys=["count"], x_label=col, y_label="Count", source="distribution",
        )
        _why(chart, ctx, "targeted:distribution→histogram", f"How {col} is distributed, in 20 "
             "equal-width bins.", col, "count", "count", len(series),
             code=f"np.histogram(df[{col!r}].dropna(), bins=20)")
        answer = (
            f"{col}: mean={series.mean():.2f}, median={series.median():.2f}, "
            f"std={series.std():.2f}, range=[{series.min():.2f}, {series.max():.2f}]."
        )
    else:
        vc = series.astype(str).value_counts().head(20)
        chart_data = [{"value": str(k), "count": int(v)} for k, v in vc.items()]
        chart = _chart(
            ChartType.BAR, f"Value counts: {col}", chart_data,
            x_key="value", y_keys=["count"], x_label=col, y_label="Count", source="distribution",
        )
        _why(chart, ctx, "targeted:distribution→bars", f"{col} is categorical, so its "
             "distribution is the number of rows per value (top 20).", col, "count", "count",
             len(series), code=f"df[{col!r}].value_counts().head(20)")
        answer = (
            f"{col}: {series.nunique()} unique values; top = '{vc.index[0]}' "
            f"({int(vc.iloc[0])} rows, {vc.iloc[0] / len(series) * 100:.1f}%)."
        )

    return TargetedResponse(intent=QuestionIntent.DISTRIBUTION, answer_text=answer, chart_configs=[chart])


def handle_top_n(df: pd.DataFrame, params: TargetedParams, contract: DataContract,
                 ctx: VizContext | None = None) -> TargetedResponse:
    metric = params.metric_column or (contract.metric_columns[0] if contract.metric_columns else None)
    group = params.group_by
    if not metric or metric not in df.columns:
        return _unanswerable(
            QuestionIntent.TOP_N,
            f"Metric column '{metric}' not found." if metric else "No metric column specified.",
            [f"Top 10 by {c}" for c in contract.metric_columns[:2]] or ["Specify a numeric column to rank"],
        )
    if not group or group not in df.columns:
        candidates = [c.name for c in contract.columns if not c.is_metric and not c.is_date][:2]
        return _unanswerable(
            QuestionIntent.TOP_N,
            f"Group-by column '{group}' not found." if group else "No grouping column specified.",
            [f"Top 10 {c} by {metric}" for c in candidates] or ["Specify a grouping column"],
        )

    n = params.n or 10
    agg = (params.aggregation or "sum").lower()
    asc = (params.order or "desc").lower() == "asc"
    valid_aggs = {"sum", "mean", "median", "count", "min", "max"}
    if agg not in valid_aggs:
        agg = "sum"

    try:
        grouped = df.groupby(group)[metric].agg(agg).sort_values(ascending=asc).head(n)
    except Exception as exc:
        return _unanswerable(
            QuestionIntent.TOP_N,
            f"Aggregation failed: {exc}",
            ["Try a different aggregation (sum/mean/count)", "Pick a different grouping column"],
        )

    chart_data = [{group: str(k), metric: float(v)} for k, v in grouped.items()]
    direction = "Bottom" if asc else "Top"
    answer = (
        f"{direction} {len(grouped)} {group} by {agg}({metric}). "
        f"Leader: {grouped.index[0]} = {grouped.iloc[0]:,.2f}."
    )
    chart = _chart(
        ChartType.HORIZONTAL_BAR, f"{direction} {len(grouped)} {group} by {agg}({metric})",
        chart_data, x_key=group, y_keys=[metric], x_label=group, y_label=f"{agg}({metric})",
        source="top_n",
    )
    _why(chart, ctx, "targeted:top_n→ranked bars", f"Ranks {group} by {agg} {metric}, "
         f"{'lowest' if asc else 'highest'} first.", group, metric, agg,
         int(df[[group, metric]].dropna().shape[0]),
         code=f"df.groupby({group!r})[{metric!r}].{agg}().sort_values(ascending={asc}).head({n})")
    return TargetedResponse(intent=QuestionIntent.TOP_N, answer_text=answer, chart_configs=[chart])


def _named_group(df: pd.DataFrame, params: TargetedParams) -> str | None:
    """The group the user named ("segment B"), if it is a real value of the segment column."""
    col, val = params.segment_column, params.segment_value
    if not col or val is None or col not in df.columns:
        return None
    values = {str(v) for v in df[col].dropna().unique()}
    if str(val) in values:
        return str(val)
    lower = {v.lower(): v for v in values}
    return lower.get(str(val).lower())


def handle_time_series(df: pd.DataFrame, params: TargetedParams, contract: DataContract,
                       ctx: VizContext | None = None) -> TargetedResponse:
    metric = params.metric_column or (contract.metric_columns[0] if contract.metric_columns else None)
    date = params.date_column or (contract.date_columns[0] if contract.date_columns else None)
    if not metric or metric not in df.columns:
        return _unanswerable(
            QuestionIntent.TIME_SERIES,
            f"Metric column '{metric}' not found." if metric else "No metric column specified.",
            _suggest_metric_questions(contract),
        )
    if not date or date not in df.columns:
        return _unanswerable(
            QuestionIntent.TIME_SERIES,
            "No date column available to plot against.",
            [f"Show distribution of {metric}", f"Top 10 by {metric}"],
        )

    freq = (params.frequency or "M").upper()
    if freq not in {"D", "W", "M", "Q", "Y"}:
        freq = "M"
    agg = (params.aggregation or "sum").lower()
    if agg not in {"sum", "mean", "median", "count", "min", "max"}:
        agg = "sum"

    group = _named_group(df, params)
    if group is not None:
        df = df[df[params.segment_column].astype(str) == group]
    try:
        s = df[[date, metric]].copy()
        s[date] = pd.to_datetime(s[date], errors="coerce")
        s = s.dropna(subset=[date, metric])
        if s.empty:
            raise ValueError("no valid (date, metric) rows after coercion")
        series = s.set_index(date)[metric].resample(pandas_freq(freq)).agg(agg).dropna()
    except Exception as exc:
        return _unanswerable(
            QuestionIntent.TIME_SERIES,
            f"Could not build time series: {exc}",
            [f"Top 10 by {metric}", f"Distribution of {metric}"],
        )

    if series.empty:
        return _unanswerable(
            QuestionIntent.TIME_SERIES,
            "Resampled series was empty.",
            ["Try a coarser frequency (Q or Y)", "Pick a different metric"],
        )

    partial = ""
    if freq == "M" and len(series) >= 3 and partial_last_month(s[date]):
        # ponytail: monthly only; Q/Y exports cut mid-period have the same trap.
        partial = (f" {series.index[-1]:%b %Y} is left out: the data ends {s[date].max():%d %b %Y}, "
                   "so that month is incomplete.")
        series = series.iloc[:-1]
    chart_data = [{"period": idx.strftime("%Y-%m-%d"), metric: float(v)} for idx, v in series.items()]
    first, last = series.iloc[0], series.iloc[-1]
    pct = (last - first) / first * 100 if first not in (0, None) else float("nan")
    direction = "up" if last > first else ("down" if last < first else "flat")
    scope = f" for {params.segment_column} {group}" if group is not None else ""
    answer = (
        f"{metric}{scope} trended {direction} {abs(pct):.1f}% from {series.index[0].date()} to "
        f"{series.index[-1].date()} (freq={freq}, agg={agg})."
    )
    if len(series) >= 2 and series.iloc[-2]:
        # "How did X change last month?" asks about the latest step, not the whole range.
        step = (series.iloc[-1] / series.iloc[-2] - 1) * 100
        answer += (f" Latest period ({series.index[-1].date()}) vs the one before: "
                   f"{step:+.1f}% ({series.iloc[-2]:,.2f} → {series.iloc[-1]:,.2f}).")
    answer += partial
    chart = _chart(
        ChartType.LINE, f"{agg}({metric}) over time", chart_data,
        x_key="period", y_keys=[metric], x_label="Period", y_label=f"{agg}({metric})",
        source="time_series",
    )
    _why(chart, ctx, "targeted:time_series→line", f"{metric} over time, {agg} per period "
         f"({freq}){scope}.", date, metric, agg, len(s),
         code=f"df.set_index(pd.to_datetime(df[{date!r}]))[{metric!r}].resample({freq!r}).{agg}()")
    return TargetedResponse(intent=QuestionIntent.TIME_SERIES, answer_text=answer, chart_configs=[chart])


def handle_segment_compare(df: pd.DataFrame, params: TargetedParams, contract: DataContract,
                           ctx: VizContext | None = None) -> TargetedResponse:
    metric = params.metric_column or (contract.metric_columns[0] if contract.metric_columns else None)
    segment = params.segment_column
    if not metric or metric not in df.columns:
        return _unanswerable(
            QuestionIntent.SEGMENT_COMPARE,
            f"Metric column '{metric}' not found." if metric else "No metric column specified.",
            _suggest_metric_questions(contract),
        )
    if not segment or segment not in df.columns:
        candidates = [c.name for c in contract.columns if not c.is_metric and not c.is_date][:2]
        return _unanswerable(
            QuestionIntent.SEGMENT_COMPARE,
            f"Segment column '{segment}' not found." if segment else "No segment column specified.",
            [f"Compare {metric} across {c}" for c in candidates],
        )

    agg = (params.aggregation or "mean").lower()
    if agg not in {"sum", "mean", "median", "count", "min", "max"}:
        agg = "mean"

    try:
        grouped = df.groupby(segment)[metric].agg(agg).sort_values(ascending=False).head(20)
    except Exception as exc:
        return _unanswerable(
            QuestionIntent.SEGMENT_COMPARE,
            f"Aggregation failed: {exc}",
            ["Try a different aggregation", "Pick a different segment"],
        )

    top_seg, top_val = grouped.index[0], grouped.iloc[0]
    bottom_seg, bottom_val = grouped.index[-1], grouped.iloc[-1]
    answer = (
        f"{agg}({metric}) by {segment}: '{top_seg}' leads at {top_val:,.2f}, "
        f"'{bottom_seg}' trails at {bottom_val:,.2f}."
    )
    group = _named_group(df, params)
    if group is not None:
        value = df.loc[df[segment].astype(str) == group, metric].agg(agg)
        answer = f"{agg}({metric}) for {segment} '{group}': {value:,.2f}. " + answer
    ctx = ctx or build_context(df)
    chart = group_bars(df, segment, metric, agg, ctx, top=20)
    if chart and chart.rationale:
        r = chart.rationale
        _why(chart, ctx, "targeted:segment_compare→bars", f"Compares {agg} {metric} across "
             f"{segment}" + ("; whiskers are 95% confidence intervals." if chart.error_key else "."),
             r.x, r.y, r.aggregation, r.n_rows, code=r.code)
    return TargetedResponse(intent=QuestionIntent.SEGMENT_COMPARE, answer_text=answer,
                            chart_configs=[chart] if chart else [])


def handle_hypothesis_test(df: pd.DataFrame, params: TargetedParams, contract: DataContract,
                           ctx: VizContext | None = None) -> TargetedResponse:
    cols = [c for c in params.target_columns if c in df.columns]
    test = (params.test_type or "ttest").lower()
    if len(cols) < 2:
        return _unanswerable(
            QuestionIntent.HYPOTHESIS_TEST,
            "Need at least 2 columns to run a hypothesis test.",
            ["Pick two numeric columns to compare", "Try a correlation question instead"],
        )

    try:
        if test == "ttest":
            a = pd.to_numeric(df[cols[0]], errors="coerce").dropna()
            b = pd.to_numeric(df[cols[1]], errors="coerce").dropna()
            stat, p = stats.ttest_ind(a, b, equal_var=False)
            test_name = "Welch's t-test"
        elif test == "chi2":
            ct = pd.crosstab(df[cols[0]], df[cols[1]])
            chi2, p, _, _ = stats.chi2_contingency(ct)
            stat = chi2
            test_name = "Chi-square independence"
        elif test == "anova":
            groups = [pd.to_numeric(g, errors="coerce").dropna()
                      for _, g in df.groupby(cols[1])[cols[0]]]
            groups = [g for g in groups if len(g) > 1]
            if len(groups) < 2:
                raise ValueError("need at least 2 groups with >1 observation each")
            stat, p = stats.f_oneway(*groups)
            test_name = "One-way ANOVA"
        else:
            raise ValueError(f"unsupported test_type '{test}'")
    except Exception as exc:
        return _unanswerable(
            QuestionIntent.HYPOTHESIS_TEST,
            f"Could not run {test}: {exc}",
            ["Try a t-test on two numeric columns", "Try a correlation between the same columns"],
        )

    verdict = "statistically significant" if p < 0.05 else "not statistically significant"
    answer = (
        f"{test_name} on {cols[0]} vs {cols[1]}: statistic={stat:.3f}, p={p:.4f} "
        f"({verdict} at α=0.05)."
    )
    table_data = [{"metric": "statistic", "value": round(float(stat), 4)},
                  {"metric": "p_value", "value": round(float(p), 4)},
                  {"metric": "significant_at_0.05", "value": "yes" if p < 0.05 else "no"}]
    return TargetedResponse(
        intent=QuestionIntent.HYPOTHESIS_TEST,
        answer_text=answer,
        table_data=table_data,
        table_columns=["metric", "value"],
    )


def handle_ab_significance(df: pd.DataFrame, params: TargetedParams, contract: DataContract,
                           ctx: VizContext | None = None,
                           strata: list[str] | tuple = ()) -> TargetedResponse:
    variant = params.variant_column
    metric = params.metric_column or (contract.metric_columns[0] if contract.metric_columns else None)
    if not variant or variant not in df.columns:
        return _unanswerable(
            QuestionIntent.AB_SIGNIFICANCE,
            f"Variant column '{variant}' not found." if variant else "No variant column specified.",
            ["Specify which column identifies the A/B groups", "Try a segment comparison instead"],
        )
    if not metric or metric not in df.columns:
        return _unanswerable(
            QuestionIntent.AB_SIGNIFICANCE,
            f"Metric column '{metric}' not found." if metric else "No metric specified.",
            _suggest_metric_questions(contract),
        )

    levels = df[variant].dropna().unique()
    if len(levels) != 2:
        return _unanswerable(
            QuestionIntent.AB_SIGNIFICANCE,
            f"A/B test needs exactly 2 variant levels — found {len(levels)}.",
            [f"Compare {metric} across {variant}", "Filter to 2 variants first"],
        )

    a = pd.to_numeric(df[df[variant] == levels[0]][metric], errors="coerce").dropna()
    b = pd.to_numeric(df[df[variant] == levels[1]][metric], errors="coerce").dropna()
    try:
        stat, p = stats.ttest_ind(a, b, equal_var=False)
    except Exception as exc:
        return _unanswerable(
            QuestionIntent.AB_SIGNIFICANCE,
            f"t-test failed: {exc}",
            [f"Compare {metric} by {variant}", "Check the metric is numeric"],
        )

    mean_a, mean_b = a.mean(), b.mean()
    lift = (mean_b - mean_a) / mean_a * 100 if mean_a not in (0,) else float("nan")
    verdict = "significant" if p < 0.05 else "not significant"
    answer = (
        f"{levels[0]} mean={mean_a:.3f} (n={len(a)}), {levels[1]} mean={mean_b:.3f} (n={len(b)}). "
        f"Lift = {lift:+.2f}%; p={p:.4f} ({verdict})."
    )
    # Group means with 95% CIs; within each stratum when the plan names one, because a pooled
    # comparison can reverse inside every stratum (Simpson's paradox).
    ctx = ctx or build_context(df)
    stratum = next((c for c in strata if c in df.columns and c != variant), None)
    chart = group_bars(df, stratum or variant, metric, "mean", ctx, split=variant if stratum else None)
    if chart and chart.rationale:
        r = chart.rationale
        _why(chart, ctx, "targeted:ab→group means", f"Mean {metric} per {variant} with 95% "
             "confidence intervals" + (f", inside each {stratum} so a confounded pooled "
             "comparison can't mislead" if stratum else "") + ". The test result is in the table.",
             r.x, r.y, r.aggregation, r.n_rows, code=r.code, split=r.split)
    table_data = [
        {"metric": f"mean({levels[0]})", "value": round(float(mean_a), 4)},
        {"metric": f"mean({levels[1]})", "value": round(float(mean_b), 4)},
        {"metric": "lift_pct", "value": round(float(lift), 4)},
        {"metric": "p_value", "value": round(float(p), 4)},
        {"metric": "significant_at_0.05", "value": "yes" if p < 0.05 else "no"},
    ]
    return TargetedResponse(
        intent=QuestionIntent.AB_SIGNIFICANCE,
        answer_text=answer,
        chart_configs=[chart] if chart else [],
        table_data=table_data,
        table_columns=["metric", "value"],
    )


def handle_outlier_scan(df: pd.DataFrame, params: TargetedParams, contract: DataContract,
                        ctx: VizContext | None = None) -> TargetedResponse:
    requested = [c for c in params.target_columns if c in df.columns]
    cols = [c for c in (requested or _numeric_cols(df, contract))
            if pd.api.types.is_numeric_dtype(df[c])]
    if not cols:
        return _unanswerable(
            QuestionIntent.OUTLIER_SCAN,
            "No numeric columns available to scan for outliers.",
            ["Upload data with numeric columns", "Try a distribution question"],
        )

    rows = []
    for c in cols:
        s = df[c].dropna()
        if s.empty:
            continue
        q1, q3 = s.quantile(0.25), s.quantile(0.75)
        iqr = q3 - q1
        lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
        n_out = int(((s < lo) | (s > hi)).sum())
        rows.append({
            "column": c,
            "outliers": n_out,
            "pct": round(n_out / len(s) * 100, 2) if len(s) else 0.0,
            "lower_bound": round(float(lo), 4),
            "upper_bound": round(float(hi), 4),
        })

    rows.sort(key=lambda r: r["outliers"], reverse=True)
    worst = rows[0] if rows else None
    answer = (
        f"Scanned {len(cols)} numeric column(s). "
        + (f"'{worst['column']}' has the most outliers: {worst['outliers']} ({worst['pct']}%)."
           if worst else "No outliers detected.")
    )
    # The worst column's distribution with the same 1.5×IQR fences as the table.
    ctx = ctx or build_context(df)
    chart = fence_histogram(df, worst["column"], ctx, k=1.5) if worst else None
    if chart and chart.rationale:
        r = chart.rationale
        _why(chart, ctx, "targeted:outliers→histogram", f"Outliers sit in the tails of {r.x}'s "
             "distribution; the lines mark the Tukey fences (Q ± 1.5×IQR). Every scanned column "
             "is in the table.", r.x, r.y, r.aggregation, r.n_rows, code=r.code)
    return TargetedResponse(
        intent=QuestionIntent.OUTLIER_SCAN,
        answer_text=answer,
        chart_configs=[chart] if chart else [],
        table_data=rows,
        table_columns=["column", "outliers", "pct", "lower_bound", "upper_bound"],
    )


# ── Dispatch table (kept at the bottom so handler functions are already defined) ──

_HANDLERS: dict[QuestionIntent, Callable[..., TargetedResponse]] = {
    QuestionIntent.CORRELATION: handle_correlation,
    QuestionIntent.DISTRIBUTION: handle_distribution,
    QuestionIntent.TOP_N: handle_top_n,
    QuestionIntent.TIME_SERIES: handle_time_series,
    QuestionIntent.SEGMENT_COMPARE: handle_segment_compare,
    QuestionIntent.HYPOTHESIS_TEST: handle_hypothesis_test,
    QuestionIntent.AB_SIGNIFICANCE: handle_ab_significance,
    QuestionIntent.OUTLIER_SCAN: handle_outlier_scan,
}
