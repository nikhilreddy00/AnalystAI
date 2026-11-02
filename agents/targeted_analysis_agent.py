"""
Phase A — Targeted analysis agent.

Deterministic pandas/scipy handlers, one per targeted question intent. No LLM,
no E2B — the questions are well-shaped enough that the right answer is a small
amount of clearly-correct code. Returns a compact TargetedResponse rendered as
a single chat card.

Routing: the graph's targeted_analysis_node calls run_targeted_analysis() instead
of analysis_agent + visualization_agent + insight_agent + quality_agent
when task.question_intent is anything other than OVERVIEW or DRILL_DOWN.
"""
from __future__ import annotations

import io
import re
from typing import Callable
from uuid import uuid4

import numpy as np
import pandas as pd

from agents.utils import cut_partial as _cut_partial
from agents.utils import (
    ranked,
    LevelUnavailable,
    aggregate,
    column_hint,
    effective_agg,
    _local,
    current_window,
    filter_period,
    pandas_freq,
    period_window,
    pii_columns,
    partial_last_month,
    time_axis,
    weekly_cadence,
    year_earlier,
)
from agents.visualization_agent import (
    VizContext,
    _format_value,
    _why,
    build_context,
    fence_histogram,
    finalize_charts,
    group_bars,
    oriented_scatter,
)
from scipy import stats

from domain_pack import (
    additive,
    money_rows,
    compute_kpi,
    computable,
    defaults_pack,
    describe_kpi,
    main_currency,
    money_columns,
    pack_caveats,
    pack_for,
    row_kinds,
)
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
    pack = pack_for(contract.domain)
    conventions = defaults_pack(contract)   # strong match: the domain's defaults apply
    params = _asked_agg(params, contract, session.task_plan.raw_query)
    ids = {c for r, c in contract.roles.items() if conventions and conventions.roles[r].kind == "id"}
    # a pack id (Shopify's order number in 'Name') names no one, whatever the header rule says
    named = {params.group_by, params.segment_column} & (pii_columns(contract) - ids) if params else set()
    if conventions and named:   # a sales report names no consumers ("top 10 customers by email")
        return _unanswerable(intent, f"{sorted(named)[0]} identifies people, so answers aren't "
                             "grouped by it", ["Ask by customer id, country or product instead"])
    grouped = intent != QuestionIntent.TOP_N or bool(params and params.group_by)   # "top 5 lines": rows
    if params and conventions and intent in _KPI_INTENTS and not params.kpi and grouped and \
            (params.aggregation or "sum").lower() == "sum" and (kpi := _summed_kpi(pack, contract, params, df)):
        params = params.model_copy(update={"kpi": kpi})   # "sales by country": the pack's net sales
    if params and params.kpi and pack and pack.kpi(params.kpi) and intent in _KPI_INTENTS and grouped:
        roles = {r: c for r, c in contract.roles.items() if c in df.columns}
        if not computable(pack, params.kpi, roles):   # named, but the data lacks what it needs
            return _unanswerable(intent, f"{pack.kpi(params.kpi).label.lower()} needs "
                                 f"{_missing(pack, params.kpi, roles)}, which this data does not have",
                                 ["Ask about a column the data has"])
        handler = handle_kpi   # "basket size by country": the pack's AOV, not a column's mean
    if handler is None:
        return _unanswerable(
            intent,
            f"Intent '{intent.value}' is not handled by the targeted pipeline.",
            ["Ask for an overview", "Rephrase as a top-N or comparison question"],
        )
    # Phase 5: every chart a user sees follows the visualization policy and its lint.
    ctx = build_context(df, session.task_plan, narrative=session.narrative,
                        contract=session.data_contract)
    extra = {"strata": session.task_plan.segments} if intent == QuestionIntent.AB_SIGNIFICANCE else {}
    full, period = df, ""
    if params and (params.period_start or params.period_end):
        date = _date_of(contract, params, df)
        df, period, why = filter_period(df, date, params.period_start, params.period_end)
        if why:
            return _unanswerable(intent, why, ["Ask about a period the data covers",
                                               "Ask without a period (the whole data)"])
    if handler is handle_kpi:
        extra = {"full": full, "intent": intent}
    response = handler(df, params, contract, ctx=ctx, **extra)
    if period and handler is not handle_kpi:
        response.answer_text += f" Period: {period}."
    response.chart_configs = finalize_charts(response.chart_configs, ctx)
    return response


# ── Helpers ──────────────────────────────────────────────────────────────────

_TOTAL_WORDS = re.compile(r"\b(?:total|sum|summed|combined|altogether|overall amount|payroll|"
                          r"bed[- ]?days|how much)\b", re.I)


_AVERAGE_WORDS = re.compile(r"\b(?:average|avg|mean|typical|per (?:order|line|row|customer|"
                            r"transaction|unit|item))\b", re.I)
_MEANING = re.compile(r"\b(?:do|does|did) (?:that|this|it|you|they|these) mean\b|\bmean(?:s)? that\b|"
                      r"\bi mean\b|\bwhat i mean\b", re.I)   # "what does that mean", "I mean": no average


def _asked_agg(params: TargetedParams | None, contract: DataContract, question: str):
    """Intake fills top-N's aggregation with "sum" by default, which would rank lines by their
    number of temperature readings: for a column the layer averages, a "sum" the question
    never asked for ("total payroll" does) is left to the layer. Phase 5.7: intake fills a
    comparison's with "mean", but sales by region is a total to a retail analyst; a domain
    pack's money or count role is summed unless the question asks for an average."""
    if params is None or not params.metric_column:
        return params
    pack = defaults_pack(contract)
    role = next((r for r, c in contract.roles.items() if c == params.metric_column), None)
    spec = pack.roles[role] if pack and role else None
    asked = (params.aggregation or "mean").lower()
    if spec and spec.kind in ("money", "count") and spec.additive and spec.level == "row" \
            and asked == "mean" and not _AVERAGE_WORDS.search(_MEANING.sub(" ", question or "")):
        return params.model_copy(update={"aggregation": "sum"})
    if spec and not spec.additive and asked == "sum" and not _TOTAL_WORDS.search(question or ""):
        return params.model_copy(update={"aggregation": "mean"})   # a unit price is never summed
    if (params.aggregation or "").lower() != "sum":
        return params
    hint = column_hint(contract, params.metric_column)
    if hint is None or hint.aggregation != "mean" or _TOTAL_WORDS.search(question or ""):
        return params
    return params.model_copy(update={"aggregation": None})


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
    asked = (params.aggregation or "").lower() or None
    valid_aggs = {"sum", "mean", "median", "count", "min", "max"}
    asked = asked if asked in valid_aggs else None
    agg = effective_agg(contract, metric, asked) or "sum"   # a balance is its current total
    asc = (params.order or "desc").lower() == "asc"

    try:
        grouped = ranked(aggregate(df, [group], metric, agg, contract), ascending=asc).head(n)
    except Exception as exc:
        return _unanswerable(
            QuestionIntent.TOP_N,
            f"Aggregation failed ({_why_failed(exc)})",
            ["Try a different aggregation (sum/mean/count)", "Pick a different grouping column"],
        )
    if grouped.empty:
        return _unanswerable(QuestionIntent.TOP_N, f"no rows have both {group} and {metric}",
                             [f"Distribution of {metric}", "Pick a different grouping column"])

    chart_data = [{group: str(k), metric: float(v)} for k, v in grouped.items()]
    direction = "Bottom" if asc else "Top"
    answer = (
        f"{direction} {len(grouped)} {group} by {_of(agg, metric, False)}. "
        f"Leader: {grouped.index[0]} = {_num(grouped.iloc[0])}."
    )
    chart = _chart(
        ChartType.HORIZONTAL_BAR, f"{direction} {len(grouped)} {group} by {_of(agg, metric, False)}",
        chart_data, x_key=group, y_keys=[metric], x_label=group, y_label=_with_unit(_of(agg, metric, False), metric, contract),
        source="top_n",
    )
    _why(chart, ctx, "targeted:top_n→ranked bars", f"Ranks {group} by {_agg_name(agg)} {metric}, "
         f"{'lowest' if asc else 'highest'} first.", group, metric, agg,
         int(df[[group, metric]].dropna().shape[0]),
         code=_code(f"df.groupby({group!r})[{metric!r}]", agg, metric, contract)
         + f".sort_index(kind='stable').sort_values(ascending={asc}, kind='stable').head({n})")
    return TargetedResponse(intent=QuestionIntent.TOP_N, answer_text=answer, chart_configs=[chart])


def _why_failed(exc: Exception) -> str:
    """What failed, for the answer: our own message for a level that can't be totalled, else the
    exception's type only (pandas quotes cell values)."""
    return str(exc) if isinstance(exc, LevelUnavailable) else type(exc).__name__


def _num(v: float) -> str:
    """An answer's number: 2 decimals, 4 under 1 (a rate of 0.0743 isn't 0.07)."""
    return f"{v:,.4f}" if abs(v) < 1 else f"{v:,.2f}"


def _with_unit(label: str, metric: str, contract) -> str:
    unit = next((c.unit for c in (contract.columns if contract else []) if c.name == metric and c.unit),
                None)
    return f"{label} ({unit})" if unit else label


def _code(base: str, agg: str, metric: str, contract, over: str | None = None) -> str:
    """The pandas that reproduces the number. A level: over time, each entity's snapshot per
    period then summed; by group, each entity's latest snapshot in the newest period, summed."""
    hint = column_hint(contract, metric)
    if agg in ("last", "first") and hint:
        by = hint.aggregation_by
        if over:   # base = the period grouper
            return (f"df.sort_values({hint.aggregation_time!r}).groupby([{over}, *{by!r}])"
                    f"[{metric!r}].{agg}().groupby(level=0).sum()")
        return (f"# each {', '.join(by)}'s latest snapshot (newest period), then summed\n"
                f"latest_rows(df, {metric!r}){base[2:]}.sum()")
    return f"{base}.{agg}()"


def _agg_name(agg: str, over_time: bool = False) -> str:
    """How an aggregation reads in an answer. A level's 'last' over time is each period's end
    total; by group it is the latest total (each entity's latest snapshot)."""
    if agg == "last":
        return "period-end total" if over_time else "latest total"
    return "period-start total" if agg == "first" else agg


def _of(agg: str, metric: str, over_time: bool = False) -> str:
    """'sum(revenue)', or for a level 'latest total of balance' (the old form kept for the rest)."""
    if agg in ("last", "first"):
        return f"{_agg_name(agg, over_time)} of {metric}"
    return f"{agg}({metric})"


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
    date = params.date_column or time_axis(contract, metric, df)
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

    asked = (params.aggregation or "").lower() or None
    asked = asked if asked in {"sum", "mean", "median", "count", "min", "max"} else None
    agg = effective_agg(contract, metric, asked) or "sum"   # "balance at month end": each account's last
    conventions = defaults_pack(contract)   # a domain's conventions only on a strong match
    freq = (params.frequency or (_default_freq(df[date], agg) if conventions else "M")).upper()
    if freq not in {"D", "W", "M", "Q", "Y"}:
        freq = "M"
    if agg in ("last", "first") and freq == "D":
        freq = "W"   # staggered snapshots: a day holds only some accounts, a week each once

    group = _named_group(df, params)
    if group is not None:
        df = df[df[params.segment_column].astype(str) == group]
    try:
        hint = column_hint(contract, metric)
        extra = [c for c in ([*hint.aggregation_by, hint.aggregation_time] if hint else [])
                 if c and c in df.columns and c not in (date, metric)]
        avg_week = ""
        rows, kept = money_rows(df, contract, metric)   # a domain's money: the rows its chart counts
        s = rows[[date, metric, *extra]].copy()
        s[date] = _local(pd.to_datetime(s[date], errors="coerce"))
        s = s.dropna(subset=[date, metric])
        if s.empty:
            raise ValueError("no valid (date, metric) rows after coercion")
        rule = _week_anchor(s[date], contract) if freq == "W" and conventions else pandas_freq(freq)
        if agg in ("last", "first"):
            series = aggregate(s, [pd.Grouper(key=date, freq=rule)], metric, agg,
                               contract, over_time=True).dropna()
        else:
            series = s.set_index(date)[metric].resample(rule).agg(agg).dropna()
            if conventions and freq == "M" and agg == "sum" and weekly_cadence(s[date]):
                # weekly flows asked by month: each month is its average week (4 or 5 weeks)
                weeks = s[date].groupby(s[date].dt.to_period("M")).nunique()
                series = series / weeks.reindex(series.index.to_period("M")).to_numpy()
                avg_week = " (average week)"
    except Exception as exc:
        return _unanswerable(
            QuestionIntent.TIME_SERIES,
            f"Could not build time series ({_why_failed(exc)})",
            [f"Top 10 by {metric}", f"Distribution of {metric}"],
        )

    if series.empty:
        return _unanswerable(
            QuestionIntent.TIME_SERIES,
            "Resampled series was empty.",
            ["Try a coarser frequency (Q or Y)", "Pick a different metric"],
        )

    partial = ""
    if conventions and freq in ("W", "M", "Q", "Y"):   # a domain's rule: either end, any bucket
        series, partial = _cut_partial(series, s[date], freq, rule if freq == "W" else None)
        if series.empty:
            return _unanswerable(QuestionIntent.TIME_SERIES, partial.strip(),
                                 ["Ask for a finer period (week or day)", "Ask for the total"])
    elif freq == "M" and len(series) >= 3 and partial_last_month(s[date]):
        partial = (f" {series.index[-1]:%b %Y} is left out: the data ends {s[date].max():%d %b %Y}, "
                   "so that month is incomplete.")
        series = series.iloc[:-1]
    chart_data = [{"period": idx.strftime("%Y-%m-%d"), metric: float(v)} for idx, v in series.items()]
    first, last = series.iloc[0], series.iloc[-1]
    pct = (last - first) / first * 100 if first not in (0, None) else float("nan")
    direction = "up" if last > first else ("down" if last < first else "flat")
    scope = f" for {params.segment_column} {group}" if group is not None else ""
    answer = (
        f"{metric}{avg_week}{scope} trended {direction} {abs(pct):.1f}% from {series.index[0].date()} to "
        f"{series.index[-1].date()} (freq={freq}, agg={_agg_name(agg, True)})."
    ) if len(series) > 1 else (   # partial ends cut: one complete period is no trend
        f"{metric}{avg_week}{scope} in {_period_name(series.index[-1], freq)} (freq={freq}, agg={_agg_name(agg, True)}): "
        f"{_num(series.iloc[-1])}; there is no earlier complete period to compare with.")
    if avg_week:
        answer += " Weekly data: each month is its average week (months have 4 or 5 week-endings)."
    answer += f" {kept}" if kept else ""
    if len(series) >= 2 and series.iloc[-2]:
        # "How did X change last month?" asks about the latest step, not the whole range.
        step = (series.iloc[-1] / series.iloc[-2] - 1) * 100
        answer += (f" Latest period ({series.index[-1].date()}) vs the one before: "
                   f"{step:+.1f}% ({_num(series.iloc[-2])} → {_num(series.iloc[-1])}).")
    answer += partial
    chart = _chart(
        ChartType.LINE, f"{_of(agg, metric, True)}{avg_week} over time", chart_data,
        x_key="period", y_keys=[metric], x_label="Period",
        y_label=_with_unit(_of(agg, metric, True), metric, contract) + avg_week,
        source="time_series",
    )
    code = _code(f"df.set_index(pd.to_datetime(df[{date!r}]))[{metric!r}].resample({freq!r})",
                 agg, metric, contract, over=f"pd.Grouper(key={date!r}, freq={freq!r})")
    if kept:
        code = f"# rows: {kept}\n" + code
    if avg_week:
        code += (f"\n# each month ÷ its week-endings: "
                 f"/ df.groupby(pd.to_datetime(df[{date!r}]).dt.to_period('M'))[{date!r}].nunique()")
    _why(chart, ctx, "targeted:time_series→line", f"{metric} over time, {agg} per period "
         f"({freq}){scope}{'; each month is its average week' if avg_week else ''}. {kept}".strip(), date, metric,
         agg, len(s), code=code)
    return TargetedResponse(intent=QuestionIntent.TIME_SERIES, answer_text=answer, chart_configs=[chart])


def _lead_and_last(order: pd.Series) -> str:
    """The highest and the lowest group of a ranking, saying so when groups tie."""
    def names(at) -> str:
        tied = [f"'{g}'" for g in order.index[order == at]]
        return tied[0] if len(tied) == 1 else (
            f"{', '.join(tied[:-1])} and {tied[-1]}" if len(tied) <= 3
            else f"{', '.join(tied[:2])} and {len(tied) - 2} more")
    top, low = order.iloc[0], order.iloc[-1]
    if top == low:
        return f"all {len(order)} groups are at {_num(top)}"
    lead = names(top)
    lead += f" tie at {_num(top)}" if (order == top).sum() > 1 else f" leads at {_num(top)}"
    return f"{lead}; {names(low)} is lowest at {_num(low)}" if (order == low).sum() == 1 else \
        f"{lead}; {names(low)} are lowest at {_num(low)}"


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

    asked = (params.aggregation or "").lower() or None
    asked = asked if asked in {"sum", "mean", "median", "count", "min", "max"} else None
    agg = effective_agg(contract, metric, asked) or "mean"

    try:
        full = aggregate(df, [segment], metric, agg, contract)
        grouped = ranked(full).head(20)
    except Exception as exc:
        return _unanswerable(
            QuestionIntent.SEGMENT_COMPARE,
            f"Aggregation failed ({_why_failed(exc)})",
            ["Try a different aggregation", "Pick a different segment"],
        )
    if grouped.empty:
        return _unanswerable(QuestionIntent.SEGMENT_COMPARE, f"no rows have both {segment} and "
                             f"{metric}", [f"Distribution of {metric}", "Pick a different segment"])

    order = ranked(full)
    answer = f"{_of(agg, metric, False)} by {segment}: {_lead_and_last(order)}."
    group = _named_group(df, params)
    if group is not None:
        # from the same totals as the comparison (a group that stopped reporting is 0, not stale)
        value = float(full[full.index.astype(str) == group].sum())
        answer = f"{_of(agg, metric, False)} for {segment} '{group}': {_num(value)}. " + answer
    ctx = ctx or build_context(df)
    chart = group_bars(df, segment, metric, agg, ctx, top=20)
    if chart and chart.rationale:
        r = chart.rationale
        _why(chart, ctx, "targeted:segment_compare→bars", f"Compares {_agg_name(agg)} {metric} across "
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
            f"Could not run {test} ({_why_failed(exc)})",
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
            f"t-test failed ({_why_failed(exc)})",
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

def _default_freq(dates: pd.Series, agg: str = "sum") -> str:
    """Weekly flows (one date a week: POS week endings) stay weekly: summed into calendar months
    they show a ~25% sawtooth in months with five week-endings. A level (a balance: each month's
    last snapshot) or a mean has no sawtooth: monthly, like anything else."""
    return "W" if agg in ("sum", "count") and weekly_cadence(dates) else "M"


def _week_anchor(dates: pd.Series, contract: DataContract | None = None) -> str:
    """Weekly data's own week end (a POS week ending Saturday: W-SAT), so a week's label is its
    week-ending date; daily data: the domain's calendar week (retail: Sun–Sat), else ISO weeks.
    Never the export's last weekday (a bucket that moved with every refresh)."""
    if weekly_cadence(dates):
        last = pd.to_datetime(dates, errors="coerce").max()
        return f"W-{['MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT', 'SUN'][last.weekday()]}"
    pack = pack_for(contract.domain) if contract is not None else None
    return f"W-{pack.week_ends if pack else 'SUN'}"


def _missing(pack, kpi: str, roles: dict) -> str:
    """What a KPI needs that the data lacks, in plain words: for each side that can't be computed,
    every alternative's missing columns ('profit, or cost')."""
    k = pack.kpi(kpi)

    def lacks(alt) -> list[str]:
        need = {r for t in alt if not t.optional and not t.kpi for r in t.roles()} - set(roles)
        need |= {t.kpi for t in alt if t.kpi and not computable(pack, t.kpi, roles)}
        return sorted(n.replace("_", " ") for n in need)

    sides = []
    for alts in (k.num, k.den or []):
        options = [lacks(a) for a in alts]
        if options and all(options):   # no alternative works: say each one's gap
            sides.append(", or ".join(dict.fromkeys(" and ".join(o) for o in options)))
    return " and ".join(sides) or "other columns"


def _summed_kpi(pack, contract: DataContract, params: TargetedParams, df: pd.DataFrame) -> str | None:
    """The tile KPI that is a plain sum of the asked column's role (revenue → net sales, quantity
    → units), when its rules change the number: the data has refunds, test orders, non-product
    lines or other currencies, which the tiles leave out. Otherwise the column's own sum is the
    same number and its chart stays as it was."""
    role = next((r for r, c in contract.roles.items() if c == params.metric_column), None)
    kpi = next((k.name for k in pack.kpis if k.tile and k.num and k.num[0]
                and k.num[0][0].sum == role), None) if role else None
    if kpi is None:
        return None
    roles = {r: c for r, c in contract.roles.items() if c in df.columns}
    kinds = set(row_kinds(df, roles, pack).unique())
    return kpi if kinds - {"sale"} or main_currency(df, roles)[2] else None


def stores_note(contract: DataContract, df: pd.DataFrame, date: str | None,
                compared: tuple[pd.DataFrame, pd.DataFrame] | None = None) -> str:
    """Growth with stores opening or closing between the compared periods is total growth, not
    like-for-like: said when a store's first or last sale falls more than four weeks inside the
    span compared (`compared`: the earlier and the later rows; default the whole data). Opening
    dates are inferred from first sales."""
    pack = pack_for(contract.domain)
    store = contract.roles.get(pack.location_role) if pack and pack.location_role else None
    if not (store and date and store in df.columns and date in df.columns):
        return ""
    t = _local(pd.to_datetime(df[date], errors="coerce"))
    span = t.groupby(df[store]).agg(["min", "max"])
    lo, hi = t.min(), t.max()
    if compared:   # from the earlier window's start to the later one's end
        lo = _local(pd.to_datetime(compared[0][date], errors="coerce")).min()
        hi = _local(pd.to_datetime(compared[1][date], errors="coerce")).max()
    month = pd.Timedelta(days=28)
    active = span[(span["max"] >= lo) & (span["min"] <= hi)]   # stores trading in the span
    changed = int(((active["min"] > lo + month) | (active["max"] < hi - month)).sum())
    if not changed:
        return ""
    return (f" {changed} of {len(active)} stores opened or closed between the periods compared "
            "(opening dates inferred from first sales): this is total growth; like-for-like "
            "growth leaves them out.")


def _basis(prior_period: str) -> str:
    """'same days', or 'same months' when a leap February makes the day counts differ."""
    return "same months" if "days against" in prior_period else "same days"


def _window_days(df: pd.DataFrame, date: str, start: str | None, end: str | None) -> int:
    """Days of the asked window the data covers (0 when none)."""
    lo, hi, first, last, why = period_window(df, date, start, end)
    if why:
        return 0
    return max(0, (min(hi, last).normalize() - max(lo, first.normalize())).days + 1)


def _period_name(stamp: pd.Timestamp, freq: str) -> str:
    """2025-12-31 at Y → '2025', at Q → '2025Q4', at M → '2025-12'; a week by its ending date;
    a day by itself."""
    if freq in ("M", "Q", "Y"):
        return str(stamp.to_period(freq))
    return f"week ending {stamp.date()}" if freq == "W" else str(stamp.date())




def _date_of(contract: DataContract, params: TargetedParams, df: pd.DataFrame) -> str | None:
    pack = pack_for(contract.domain)
    return (params.date_column or (contract.roles.get(pack.date_role) if pack else None)
            or time_axis(contract, params.metric_column, df))


_KPI_INTENTS = {QuestionIntent.TOP_N, QuestionIntent.SEGMENT_COMPARE, QuestionIntent.TIME_SERIES}


def handle_kpi(df: pd.DataFrame, params: TargetedParams, contract: DataContract,
               ctx: VizContext | None = None, full: pd.DataFrame | None = None,
               intent: QuestionIntent = QuestionIntent.TOP_N) -> TargetedResponse:
    """A domain-pack KPI (net sales, AOV, return rate …), overall, by a group or by period, in
    the period asked (already filtered), against the same period a year earlier when asked.
    Computed by domain_pack.compute_kpi: the same rule as the tiles."""
    pack = pack_for(contract.domain)
    kpi = pack.kpi(params.kpi)
    roles = {r: c for r, c in contract.roles.items() if c in df.columns}
    full = df if full is None else full
    _, currency, other = main_currency(df, roles)
    money = currency or next((c.unit for c in contract.columns if c.unit and c.name in
                              money_columns(pack, roles)), None)
    kind = {"money": "currency", "percent": "percent", "count": "count"}.get(kpi.unit, "number")

    def fmt(v: float) -> str:
        return _format_value(v, kind, money if kind == "currency" else None)

    def change(now: float, then: float) -> str:   # a rate moves in points, an amount in percent
        return (f"{(now - then) * 100:+.1f} pts" if kind == "percent" else f"{(now / then - 1) * 100:+.1f}%")

    date = _date_of(contract, params, df)
    weekly = bool(date) and weekly_cadence(full[date])   # YoY: 52 weeks back, same week-endings
    period = filter_period(full, date, params.period_start, params.period_end)[1]
    where = f" ({period})" if period else ""
    present = set(row_kinds(df, roles, pack).unique())
    # every KPI answer on a weak match says how the file was read
    lines = pack_caveats(pack, None, roles, "weak", always=True) \
        if contract.domain_confidence == "weak" else []
    read = " " + lines[0] if lines else ""   # (a pack without that line says nothing)
    how = describe_kpi(pack, kpi.name, roles, present) + read + (
        f" {currency} only: {other:,} rows in other currencies left out." if other else "")   # counts too
    group = params.group_by or params.segment_column
    if group and group in df.columns:
        by = compute_kpi(df, roles, pack, kpi.name, by=group, full=full)
        if by is None or by.empty:
            return _unanswerable(intent, f"{kpi.label.lower()} can't be computed from these rows",
                                 ["Ask without the group", "Ask about a longer period"])
        asc = (params.order or "desc").lower() == "asc"
        by = ranked(by, ascending=asc).head(params.n or 20)
        lead, tail = by.index[0], by.index[-1]
        answer = (f"{kpi.label} by {group}{where}: {lead} {'lowest' if asc else 'leads'} at "
                  f"{fmt(by.iloc[0])}" + (f", {tail} at {fmt(by.iloc[-1])}." if len(by) > 1 else "."))
        if params.compare == "yoy" and date:
            prior_df, prior_period, why = year_earlier(full, date, params.period_start, params.period_end,
                                                       weekly)
            prior = None if why else compute_kpi(prior_df, roles, pack, kpi.name, by=group, full=full)
            rows, days, cut = current_window(full, date, params.period_start, params.period_end, weekly)
            now = compute_kpi(rows, roles, pack, kpi.name, by=group, full=full)
            if prior is None or now is None:
                answer += f" No year-earlier comparison: {why or 'nothing to compare'}."
            elif lead in prior.index and lead in now.index and (kind == "percent" or prior[lead]):
                same = f" On the same days ({days}) {lead} was {fmt(now[lead])};" if cut else ""
                answer += (f"{same} A year earlier ({prior_period}) {lead} was {fmt(prior[lead])} "
                           f"({change(now[lead], prior[lead])}, {_basis(prior_period)})."
                           + stores_note(contract, full, date, (prior_df, rows)))
        chart = _chart(ChartType.HORIZONTAL_BAR, f"{kpi.label} by {group}{where}",
                       [{group: str(k), kpi.label: float(v)} for k, v in by.items()],
                       x_key=group, y_keys=[kpi.label], x_label=group, y_label=kpi.label,
                       source="kpi")
        _why(chart, ctx, "targeted:kpi→ranked bars", f"{kpi.label} per {group}, ranked. {how}",
             group, kpi.label, "sum" if kpi.den is None else "none", len(df))
        return TargetedResponse(intent=intent, answer_text=answer + f" How: {how}",
                                chart_configs=[chart],   # every group's value, not only the ends
                                table_data=[{group: str(k), kpi.label: fmt(v)} for k, v in by.items()])
    carry = ""   # a note from the trend path when it hands one period to the value path below
    asked = bool(params.period_start or params.period_end)
    # a period with yoy, or with no frequency ("how were net sales in Q4 2025?"): its total,
    # against the same period a year earlier, below; a frequency asked: the trend in that window
    one_period = asked and (params.compare == "yoy" or not params.frequency)
    if intent == QuestionIntent.TIME_SERIES and date and not one_period:
        flow = kpi.den is None and kind != "percent"
        default = _default_freq(df[date], "sum" if flow else "ratio") if defaults_pack(contract) else "M"
        freq = (params.frequency or default).upper()
        # a KPI is reported by week at the finest (a day's AOV or return rate is noise); a daily
        # ask is shown by month and says so ("net sales in January" asked as days is January)
        by_month = "" if freq in {"W", "M", "Q", "Y"} else " (shown by month)"
        freq = freq if freq in {"W", "M", "Q", "Y"} else "M"
        # the whole data's buckets, so the period before and a year earlier exist for the window
        t = _local(pd.to_datetime(full[date], errors="coerce"))
        rule = _week_anchor(t, contract) if freq == "W" else freq
        frame = full.assign(_period=t.dt.to_period(rule))
        series = compute_kpi(frame, roles, pack, kpi.name, by="_period", full=full)
        if series is None or series.empty:
            return _unanswerable(intent, f"{kpi.label.lower()} can't be computed over time here",
                                 [f"{kpi.label} overall"])
        series = series.sort_index()
        unit = ""
        if freq == "M" and flow and weekly and additive(pack, kpi.name):   # weekly sums by month:
            # each month's average week (a distinct count, orders or customers, is the month's)
            weeks = t.groupby(t.dt.to_period("M")).nunique()
            series, unit = series / weeks.reindex(series.index).to_numpy(), " (average week)"
        whole, note = _cut_partial(series, t, freq, rule if freq == "W" else None)
        series = whole
        if asked:   # the buckets inside the asked window; comparisons still read `whole`
            lo, hi = period_window(full, date, params.period_start, params.period_end)[:2]
            series = whole[[p.start_time >= lo.normalize() and p.end_time.normalize() <= hi.normalize()
                            for p in whole.index]]
            # a bucket cut outside the window is not this answer's business
            buckets = {str(p): p.end_time >= lo and p.start_time <= hi for p in frame["_period"].dropna().unique()}
            note = "".join(f" {x}" for x in re.findall(r"[^.]+\.", note)
                           if buckets.get(m.group(1) if (m := re.search(r"\(([^)]*)\)", x)) else "", True))
        if series.empty or (len(series) == 1 and asked):
            # one period: answered (and compared) below; an asked window's total leaves nothing out
            carry = note if series.empty and not asked else ""
        else:
            latest = series.index[-1]

            def name(p) -> str:   # a week is named by its week-ending date
                return f"week ending {p.end_time:%Y-%m-%d}" if freq == "W" else str(p)

            answer = f"{kpi.label}{unit} by {'week' if freq == 'W' else 'period'}{where}{by_month}: latest " \
                     f"{name(latest)} {fmt(series.iloc[-1])}"
            def base(v) -> bool:   # a value to compare against (0% is one; $0 has no % change)
                return v is not None and not pd.isna(v) and (kind == "percent" or v != 0)

            back = {"W": 52, "M": 12, "Q": 4, "Y": 1}[freq]
            before = whole.get(latest - 1)   # by label: a missing period is no "period before"
            if base(before):
                answer += f", vs {fmt(before)} the period before ({change(series.iloc[-1], before)})"
            prior = whole.get(latest - back) if freq != "Y" else None
            if base(prior):   # (by year, "a year earlier" is the period before: said once)
                answer += (f"; a year earlier {fmt(prior)} ({change(series.iloc[-1], prior)} year over year)")
            compared = latest - back if base(prior) else latest - 1 if base(before) else None
            if compared is None:
                answer += "; there is no earlier complete period to compare with"
            answer += "." + note + (stores_note(contract, full, date, (
                frame[frame["_period"] == compared], frame[frame["_period"] == latest]))
                if compared is not None else "")
            chart = _chart(ChartType.LINE, f"{kpi.label} over time{where}",
                           [{"period": f"{k.end_time:%Y-%m-%d}" if freq == "W" else str(k),
                             kpi.label: float(v)} for k, v in series.items()],
                           x_key="period", y_keys=[kpi.label], x_label="Period", y_label=kpi.label,
                           source="kpi")
            _why(chart, ctx, "targeted:kpi→line", f"{kpi.label} per {freq} period. {how}", date,
                 kpi.label, "sum" if kpi.den is None else "none", len(df))
            return TargetedResponse(intent=intent, answer_text=answer + f" How: {how}",
                                    chart_configs=[chart])
    value = compute_kpi(df, roles, pack, kpi.name, full=full)
    if value is None:
        return _unanswerable(intent, f"{kpi.label.lower()} can't be computed from these rows",
                             ["Ask about a longer period"])
    answer = f"{kpi.label}{where}: {fmt(value)}." + carry
    # one period asked on strong pack data (a month or longer): against the same period a year
    # earlier when the data has it (seasonal sales: last January, never December); unasked, so
    # silent when it can't, and never a single day (a Monday against a Sunday)
    auto = params.compare is None and bool(params.period_start or params.period_end) \
        and defaults_pack(contract) is not None and date is not None \
        and _window_days(full, date, params.period_start, params.period_end) >= 28
    if (params.compare == "yoy" or auto) and date:
        prior_df, prior_period, why = year_earlier(full, date, params.period_start, params.period_end,
                                                   weekly)
        prior = None if why else compute_kpi(prior_df, roles, pack, kpi.name, full=full)
        rows, days, cut = current_window(full, date, params.period_start, params.period_end, weekly)
        now = compute_kpi(rows, roles, pack, kpi.name, full=full)   # the same days as the year-earlier window
        both = prior is not None and now is not None and (kind == "percent" or prior != 0)
        same = f" On the same days ({days}): {fmt(now)}." if cut and both else ""
        answer += same + (f" A year earlier ({prior_period}): {fmt(prior)} "
                   f"({change(now, prior)} year over year, {_basis(prior_period)})."
                   + stores_note(contract, full, date, (prior_df, rows)) if both
                   else "" if auto else f" No year-earlier comparison: {why or 'nothing to compare'}.")
    return TargetedResponse(intent=intent, answer_text=answer + f" How: {how}")


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
