"""Drill-down agent — compact follow-up using prior analysis context.

Used by drill_down_node when QuestionIntent == DRILL_DOWN. Calls Claude once
with the prior insight_report + data summary + the new question, and parses a
small JSON response into a TargetedResponse.
"""
from __future__ import annotations

import json
import uuid

from agents.utils import strip_markdown_fences
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
        Reserved for future use. The agent currently produces empty-data chart
        specs (``ChartConfig.data == []``) so no DataFrame loading is needed.
        Richer chart data would require deserialising this parquet buffer, which
        is deferred to a later iteration.

    Returns
    -------
    TargetedResponse
        Always returned — never raises for normal LLM or JSON parsing errors.
        On persistent parse failure the ``answer_text`` carries a user-readable
        fallback message.
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
        except json.JSONDecodeError:
            return TargetedResponse(
                intent=QuestionIntent.DRILL_DOWN,
                answer_text="I had trouble formatting an answer. Try rephrasing your question.",
            )

    answer = payload.get("answer_text", "").strip()
    chart_configs: list[ChartConfig] = []
    spec = payload.get("chart_spec")
    if spec and isinstance(spec, dict):
        chart_type_str = spec.get("chart_type")
        x = spec.get("x_column")
        y = spec.get("y_column")
        # Only build the chart if both columns exist in the contract.
        if chart_type_str and x in column_names and y in column_names:
            try:
                chart_type = ChartType(chart_type_str)
            except ValueError:
                chart_type = ChartType.BAR
            # Lightweight chart from EDA — no pandas join here, we hand a stub
            # ChartConfig that the frontend renderer can display. If a richer
            # plot is needed, the user can switch to CHART_REQUEST.
            chart_configs.append(ChartConfig(
                id=str(uuid.uuid4()),
                chart_type=chart_type,
                title=f"{y} vs {x}",
                data=[],   # frontend renders an empty-state placeholder for now
                x_key=x,
                y_keys=[y],
                colors=["#6366f1"],
                show_legend=True,
                show_grid=True,
                source_finding="drill_down",
            ))

    return TargetedResponse(
        intent=QuestionIntent.DRILL_DOWN,
        answer_text=answer or "I couldn't formulate a direct answer to that.",
        chart_configs=chart_configs,
    )
