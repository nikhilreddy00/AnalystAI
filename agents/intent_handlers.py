"""Deterministic handlers for the three lightweight intents introduced in Gap 1
Spec 1: META_QUESTION, EXPORT_REQUEST, CHART_REQUEST. No LLM calls; all three
return a TargetedResponse synchronously.
"""
from __future__ import annotations

from typing import Callable

import pandas as pd

from orchestrator.state import (
    AnalysisSession,
    QuestionIntent,
    TargetedResponse,
)


# Emit signature mirrors graph._emit: the caller passes a one-arg callable that
# pushes onto the SSE event queue. Handlers that need to emit an extra event
# (only EXPORT_REQUEST today) take this callable as a parameter.
Emit = Callable[[dict], None]


def handle_meta_question(session: AnalysisSession) -> TargetedResponse:
    """Answer questions ABOUT the dataset (shape, schema, dtypes, preview)."""
    raw = (session.task_plan.raw_query if session.task_plan else "").lower()
    contract = session.data_contract

    if contract is None:
        return TargetedResponse(
            intent=QuestionIntent.META_QUESTION,
            answer_text="Connect a data source first — I don't know what data you're asking about.",
        )

    if any(kw in raw for kw in ("preview", "first rows", "first 5", "sample row", "head")):
        findings = session.analysis_findings
        if findings and findings.head_preview:
            return TargetedResponse(
                intent=QuestionIntent.META_QUESTION,
                answer_text=f"Here are the first {len(findings.head_preview)} rows.",
                table_data=findings.head_preview,
                table_columns=list(findings.head_preview[0].keys()) if findings.head_preview else [],
            )
        return TargetedResponse(
            intent=QuestionIntent.META_QUESTION,
            answer_text="Run an analysis first to see a preview of the rows.",
        )

    if any(kw in raw for kw in ("dtype", "data type", "data types")):
        rows = [
            {"name": c.name, "dtype": c.dtype, "nullable": c.nullable}
            for c in contract.columns
        ]
        return TargetedResponse(
            intent=QuestionIntent.META_QUESTION,
            answer_text=f"Column types for the {len(rows)} columns:",
            table_columns=["name", "dtype", "nullable"],
            table_data=rows,
        )

    if any(kw in raw for kw in ("column name", "what columns", "schema", "fields")):
        rows = [
            {"name": c.name, "dtype": c.dtype, "nullable": c.nullable}
            for c in contract.columns
        ]
        return TargetedResponse(
            intent=QuestionIntent.META_QUESTION,
            answer_text=f"The dataset has {len(rows)} columns.",
            table_columns=["name", "dtype", "nullable"],
            table_data=rows,
        )

    # Default / "how many rows" / "shape" / "size"
    return TargetedResponse(
        intent=QuestionIntent.META_QUESTION,
        answer_text=(
            f"This dataset has {contract.row_count:,} rows × "
            f"{contract.column_count} columns."
        ),
    )


def handle_export_request(session: AnalysisSession, emit: Emit) -> TargetedResponse:
    """Trigger PPTX export, or explain why we can't."""
    raw = (session.task_plan.raw_query if session.task_plan else "").lower()
    is_pptx = any(kw in raw for kw in ("pptx", "powerpoint", "ppt", "slide", "deck"))

    if not is_pptx:
        return TargetedResponse(
            intent=QuestionIntent.EXPORT_REQUEST,
            answer_text="Only PowerPoint (PPTX) export is supported right now.",
        )

    if not session.insight_report:
        return TargetedResponse(
            intent=QuestionIntent.EXPORT_REQUEST,
            answer_text="Nothing to export — run an analysis first.",
        )

    emit({
        "type": "export",
        "format": "pptx",
        "url": "/dashboard/latest/report.pptx",
    })
    return TargetedResponse(
        intent=QuestionIntent.EXPORT_REQUEST,
        answer_text="Generating the PPTX report. The download will start in a moment.",
    )


def handle_chart_request(session: AnalysisSession, df: pd.DataFrame) -> TargetedResponse:
    """Build a single chart from CHART_REQUEST params."""
    from agents.visualization_agent import build_single_chart

    if session.task_plan is None:
        return TargetedResponse(
            intent=QuestionIntent.CHART_REQUEST,
            answer_text="I couldn't tell which chart to make.",
        )

    p = session.task_plan.targeted_params
    if not p.chart_type or not (p.target_columns or p.metric_column):
        return TargetedResponse(
            intent=QuestionIntent.CHART_REQUEST,
            answer_text=(
                'I couldn\'t tell which columns to chart. '
                'Try: "bar chart of revenue by region".'
            ),
        )

    chart = build_single_chart(df, p)
    return TargetedResponse(
        intent=QuestionIntent.CHART_REQUEST,
        answer_text=f"Here's the {chart.chart_type} you asked for.",
        chart_configs=[chart],
    )
