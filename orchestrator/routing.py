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
    # Phase A: skip cleaning + full analysis + insight for targeted Q&A.
    # Handlers in targeted_analysis_agent use .dropna() per-column so they're
    # safe on uncleaned data; trade-off is intentional to keep follow-ups fast.
    if _is_targeted(task):
        return "targeted_analysis"
    return "cleaning"


def route_after_cleaning(state: dict) -> str:
    session: AnalysisSession = state["session"]
    if session.error_message:
        return "error"
    audit = session.cleaning_audit
    # Skip approval gate when user already approved (is_approval=True)
    if audit and audit.high_null_columns and not state.get("is_approval"):
        return "human_approval"
    return "analysis"


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
    FEAT-02 + Phase 6: Route after quality_check_node.
    Three outcomes:
      - insight_report cleared → node decided to retry → "analysis"
      - committed AND first-run (overview_done still False) → "senior_expert"
      - committed AND follow-up (overview_done True) → "end"
    quality_check_node now leaves overview_done untouched — the flip happens
    in senior_expert_node so we can still detect first-run here.
    """
    session: AnalysisSession = state["session"]
    if session.insight_report is None:
        return "analysis"
    if not session.overview_done:
        return "senior_expert"
    return "end"
