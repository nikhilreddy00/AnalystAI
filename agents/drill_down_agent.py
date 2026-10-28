"""Drill-down agent — compact follow-up using prior analysis context.

Used by drill_down_node when QuestionIntent == DRILL_DOWN. Calls Claude once
with the prior insight_report + data summary + the new question, and parses a
small JSON response into a TargetedResponse.
"""
from __future__ import annotations

import io
import json
import uuid

import pandas as pd

from agents.errors import LLMOutputError
from agents.utils import strip_markdown_fences
from agents.visualization_agent import _why, build_context, finalize_charts
from llm.client import get_client
from orchestrator.state import (
    AnalysisSession,
    ChartConfig,
    ChartType,
    QuestionIntent,
    TargetedResponse,
)


_SYSTEM = """\
You are a senior data analyst answering a follow-up question.

The user has already received an analysis with executive summary, technical
findings, and anomaly alerts (provided to you below). They are now asking a
specific drill-down question. Your job is to answer it directly in 2-3 sentences,
referencing the prior findings when relevant.

If a chart would help, suggest ONE chart spec. Otherwise, leave chart_spec null.

Respond with ONLY a JSON object (no markdown fences, no extra text):

{
  "answer_text": "<2-3 sentence answer>",
  "chart_spec": null OR {
    "chart_type": "BarChart" | "LineChart" | "ScatterChart" | "PieChart" | "HistogramChart",
    "x_column": "<column name>",
    "y_column": "<column name>"
  }
}
"""


async def run_drill_down_agent(
    session: AnalysisSession,
    df_parquet: bytes | None,
) -> TargetedResponse:
    """
    Answer a drill-down follow-up question using the prior analysis context.

    Makes a single Claude call (with one automatic retry on JSON parse failure)
    using the session's insight_report, data_contract, and analysis_findings as
    context. Returns a TargetedResponse containing a 2-3 sentence answer and an
    optional stub ChartConfig.

    Parameters
    ----------
    session:
        Must have ``insight_report`` set; the agent short-circuits to an error
        message if no prior analysis exists. ``task_plan.raw_query`` supplies the
        follow-up question text.
    df_parquet:
        The session DataFrame; the suggested chart's data is computed from it. Without it,
        no chart is attached.

    Returns
    -------
    TargetedResponse
        Raises ``LLMOutputError`` if the model's JSON is unparseable twice.
    """
    if not session.insight_report:
        return TargetedResponse(
            intent=QuestionIntent.DRILL_DOWN,
            answer_text="Drill-down requires a prior analysis. Ask for an overview first.",
        )

    report = session.insight_report
    query = session.task_plan.raw_query if session.task_plan else ""

    # Compact data summary — keep tokens bounded.
    contract = session.data_contract
    column_names = [c.name for c in contract.columns] if contract else []
    column_dtypes = (
        ", ".join(f"{c.name}:{c.dtype}" for c in contract.columns[:30])
        if contract else "<no contract>"
    )
    findings = session.analysis_findings
    eda_summary = json.dumps(findings.eda_summary, default=str)[:1500] if findings else ""

    user_prompt = f"""\
Prior analysis:
- Executive summary: {report.executive_summary}
- Technical findings: {report.technical_findings[:5]}
- Anomaly alerts: {[a.title for a in report.anomaly_alerts][:5]}

Dataset:
- Rows: {contract.row_count if contract else "?"}
- Columns ({len(column_names)}): {column_dtypes}
- EDA stats (truncated): {eda_summary}

Follow-up question: {query}

Answer in JSON as specified.
"""

    msg = await get_client().complete(
        agent="drill_down", prompt_version="v0",
        max_tokens=1024,
        system=_SYSTEM,
        messages=[{"role": "user", "content": user_prompt}],
    )

    raw_text = msg.text
    cleaned = strip_markdown_fences(raw_text)
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        # Retry once with an explicit "raw JSON only" reminder — mirrors the
        # pattern used in insight_agent.py (lines 351-367).
        retry_msg = await get_client().complete(
            agent="drill_down", prompt_version="v0",
            max_tokens=1024,
            system=_SYSTEM,
            messages=[
                {"role": "user", "content": user_prompt},
                {"role": "assistant", "content": raw_text},
                {"role": "user", "content": (
                    "Your previous response was not valid JSON. "
                    "Respond again with ONLY the JSON object — no markdown, no commentary."
                )},
            ],
        )
        retry_text = retry_msg.text
        try:
            payload = json.loads(strip_markdown_fences(retry_text))
        except json.JSONDecodeError as exc:
            raise LLMOutputError(
                "drill_down", f"malformed JSON twice: {exc}",
                "I had trouble formatting an answer. Try rephrasing your question.",
            ) from exc

    answer = payload.get("answer_text", "").strip()
    chart_configs: list[ChartConfig] = []
    spec = payload.get("chart_spec")
    if spec and isinstance(spec, dict):
        chart_type_str = spec.get("chart_type")
        x = spec.get("x_column")
        y = spec.get("y_column")
        # Only build the chart if both columns exist and there is data to plot.
        data = _chart_data(df_parquet, chart_type_str, x, y) \
            if chart_type_str and x in column_names and y in column_names else []
        if data:
            try:
                chart_type = ChartType(chart_type_str)
            except ValueError:
                chart_type = ChartType.BAR
            chart_configs.append(ChartConfig(
                id=str(uuid.uuid4()),
                chart_type=chart_type,
                title=f"{y} vs {x}",
                data=data,
                x_key=x,
                y_keys=[y],
                colors=[],
                x_label=x,
                y_label=y,
                show_legend=False,
                show_grid=True,
                source_finding="drill_down",
            ))
            # The model suggested this chart: it gets the same lint as any agent chart (repaired
            # or dropped), and its rationale says a model picked it.
            frame = pd.read_parquet(io.BytesIO(df_parquet))
            agg = "none" if chart_type == ChartType.SCATTER else (
                "mean" if pd.api.types.is_numeric_dtype(frame[y]) and x != y else "count")
            ctx = build_context(frame, session.task_plan, narrative=session.narrative)
            _why(chart_configs[-1], ctx, "drill_down:model_suggested",
                 f"Suggested by the drill-down model for this follow-up; {agg} {y} per {x}"
                 if agg != "none" else "Suggested by the drill-down model: raw points.",
                 x, y if agg != "count" else "count", agg, len(frame),
                 code=f"df.groupby({x!r})[{y!r}].{agg}()" if agg not in ("none", "count") else "")
            chart_configs = finalize_charts(chart_configs, ctx)

    return TargetedResponse(
        intent=QuestionIntent.DRILL_DOWN,
        answer_text=answer or "I couldn't formulate a direct answer to that.",
        chart_configs=chart_configs,
    )


def _chart_data(df_parquet: bytes | None, chart_type: str, x: str, y: str) -> list[dict]:
    """Rows for the suggested chart: raw points for a scatter, mean of y per x otherwise
    (top 20 by value for categorical charts, x-ordered for lines)."""
    if not df_parquet:
        return []
    df = pd.read_parquet(io.BytesIO(df_parquet), columns=list(dict.fromkeys([x, y]))).dropna()
    if df.empty:
        return []
    if chart_type == ChartType.SCATTER.value:
        pts = df.sample(min(len(df), 500), random_state=0)
        return json.loads(pts.to_json(orient="records", date_format="iso"))
    if x == y or not pd.api.types.is_numeric_dtype(df[y]):
        grouped = df.groupby(x).size()       # non-numeric y: count rows per x
    else:
        grouped = df.groupby(x)[y].mean()
    if chart_type == ChartType.LINE.value:
        grouped = grouped.sort_index().tail(200)
    else:
        grouped = grouped.sort_values(ascending=False).head(20)
    return [{x: str(k), y: round(float(v), 4)} for k, v in grouped.items()]
