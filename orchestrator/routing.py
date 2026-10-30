"""
LangGraph conditional edge functions. Each function reads AgentState and returns
the name of the next node to route to.

Multi-turn resume logic:
- If task.resume_from == "analysis", ingestion still runs silently to restore
  the in-memory DataFrame from the connector, then routing skips cleaning.
- This avoids storing parquet bytes in the DB while keeping multi-turn fast.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from orchestrator.state import QuestionIntent

if TYPE_CHECKING:
    from orchestrator.state import AnalysisSession


# Phase A: Only OVERVIEW runs the full 7-agent pipeline.
# DRILL_DOWN follow-ups route to drill_down_node (added in Task 2.2).
# Every other intent shortcuts to `targeted_analysis_node`.
_FULL_PIPELINE_INTENTS = {QuestionIntent.OVERVIEW}


_NO_DATA_INTENTS = {QuestionIntent.META_QUESTION, QuestionIntent.EXPORT_REQUEST}


def _is_targeted(task) -> bool:
    return task is not None and task.question_intent not in _FULL_PIPELINE_INTENTS


def route_after_ingestion(state: dict) -> str:
    session: AnalysisSession = state["session"]
    if session.error_message:
        return "error"
    # Always run intake on every turn. Each turn carries a NEW user message that
    # must be re-classified, and intake is the only node that refreshes
    # task_plan.raw_query / intent and appends the user message to history.
    #
    # The multi-turn "resume" optimization (skip re-ingestion/cleaning) lives in
    # ingestion_node, keyed on task_plan.resume_from == "analysis" — it does NOT
    # need a routing shortcut here. An earlier version short-circuited straight to
    # drill_down/targeted/analysis when the PREVIOUS turn left resume_from set,
    # which skipped intake and left raw_query stale, so every follow-up after the
    # first repeated the first follow-up's answer. route_after_intake dispatches to
    # the correct terminal node from the freshly classified intent.
    return "intake"


def route_after_intake(state: dict) -> str:
    session: AnalysisSession = state["session"]
    if session.error_message:
        return "error"
    task = session.task_plan
    if task and task.clarification_needed:
        return "human_approval"
    if task and task.question_intent == QuestionIntent.DRILL_DOWN:
        return "drill_down"
    # Targeted answers read cleaned data too (Phase 5 Ingestion: on raw frames 10 of 15 stored
    # answers changed, duplicates counted twice). Cleaning is code (~0.2 s). Questions about the
    # app or an export don't read the data.
    if _is_targeted(task) and task.question_intent in _NO_DATA_INTENTS:
        return "targeted_analysis"
    return "cleaning"


def route_after_cleaning(state: dict) -> str:
    session: AnalysisSession = state["session"]
    if session.error_message:
        return "error"
    audit = session.cleaning_audit
    # The high-null gate is asked once per session: an answer (this run or earlier) skips it.
    if audit and audit.high_null_columns and not state.get("is_approval") \
            and session.high_null_answer is None:
        return "human_approval"
    return "targeted_analysis" if _is_targeted(session.task_plan) else "analysis"


def route_after_analysis(state: dict) -> str:
    session: AnalysisSession = state["session"]
    if session.error_message:
        return "error"
    return "visualization"


def route_after_visualization(state: dict) -> str:
    session: AnalysisSession = state["session"]
    if session.error_message:
        return "error"
    return "insight"


def route_after_insight_node(state: dict) -> str:
    """
    Route directly after insight_node. If insight raised, go to error_node;
    otherwise hand off to quality_check for the retry decision.
    """
    session: AnalysisSession = state["session"]
    if session.error_message:
        return "error"
    return "quality_check"


def route_after_quality_check(state: dict) -> str:
    """
    FEAT-02: quality_check_node is the single source of truth for the retry decision —
    it clears insight_report when it wants one more analysis pass. The attempts bound is a
    backstop so a node that ends with no report can't loop.
    """
    session: AnalysisSession = state["session"]
    if session.insight_report is None and session.quality_check_attempts < 2:
        return "analysis"
    return "end"


def route_after_terminal(state: dict) -> str:
    """targeted_analysis / drill_down: a failure must reach error_node (SSE error event)."""
    return "error" if state["session"].error_message else "end"


def route_after_approval(state: dict) -> str:
    """A gate's answer resumes the run: a clarification goes back to intake, the high-null
    choice to cleaning. A rerun_overview stop ends the run."""
    return {"clarification": "intake", "high_null": "cleaning"}.get(state.get("answered"), "end")
