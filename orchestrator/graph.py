"""
LangGraph StateGraph — wires all 6 agents into a stateful pipeline.

State design:
- `session` carries all Pydantic agent outputs (persisted to PostgreSQL by insight_node)
- `df_parquet` carries the in-memory DataFrame bytes between ingestion → cleaning → analysis
  (not serialized to DB; restored by re-running ingestion on multi-turn resume)
- `event_queue` is an asyncio.Queue the SSE endpoint drains for streaming events

Node execution order (happy path):
  intake → ingestion → cleaning → analysis → visualization → insight → END

Conditional branches:
  intake       → human_approval  (clarification needed)
  cleaning     → human_approval  (>40% null columns found)
  any node     → error           (unhandled exception)
  ingestion    → analysis        (multi-turn resume: skip re-cleaning)
"""
from __future__ import annotations

import logging
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from agents.analysis_agent import run_analysis_agent
from agents.cleaning_agent import run_cleaning_agent
from agents.ingestion_agent import run_ingestion_agent
from agents.insight_agent import run_insight_agent
from agents.intake_agent import (
    generate_data_narrative,
    generate_proactive_questions,
    run_intake_agent,
)
from agents.senior_expert_agent import run_senior_expert_agent
from agents.targeted_analysis_agent import run_targeted_analysis
from agents.visualization_agent import (
    classify_columns,
    compute_briefing_extras,
    generate_smart_kpis,
    run_visualization_agent,
)
from orchestrator.routing import (
    route_after_analysis,
    route_after_cleaning,
    route_after_ingestion,
    route_after_intake,
    route_after_visualization,
    route_after_insight_node,
    route_after_quality_check,
)
from memory.session_data import write_cleaned_parquet
from orchestrator.state import AnalysisSession, MessageRole

logger = logging.getLogger(__name__)


class AgentState(TypedDict):
    session: AnalysisSession
    df_parquet: bytes | None           # in-memory only — not saved to DB
    event_queue: Any                   # asyncio.Queue[dict | None]
    user_message: str
    is_approval: bool
    session_store: Any                 # SessionStore | None
    vector_store: Any                  # VectorStore | None


# ── SSE helper ─────────────────────────────────────────────────────────────────

def _emit(state: AgentState, event: dict) -> None:
    q = state.get("event_queue")
    if q is not None:
        q.put_nowait(event)


# ── Nodes ──────────────────────────────────────────────────────────────────────

async def intake_node(state: AgentState) -> dict:
    _emit(state, {"type": "agent_start", "agent": "intake", "message": "Understanding your request..."})
    session: AnalysisSession = state["session"]

    try:
        # Phase 3: signal first-run so intake forces a comprehensive plan.
        is_overview = not session.overview_done
        task = await run_intake_agent(session, state["user_message"], is_overview_request=is_overview)
        session.task_plan = task
        session.add_message(MessageRole.USER, state["user_message"])

        if task.clarification_needed:
            _emit(state, {
                "type": "approval_required",
                "context": "clarification",
                "question": task.clarification_question,
            })
        else:
            _emit(state, {"type": "agent_complete", "agent": "intake", "message": "Analysis plan ready"})
    except Exception as exc:
        session.error_message = f"Intake agent failed: {exc}"

    return {"session": session}


async def ingestion_node(state: AgentState) -> dict:
    session: AnalysisSession = state["session"]
    # BUG-09: Clear error_message at the start of every graph run
    session.error_message = None
    # FEAT-02: reset quality-retry budget for each run (counts entries into quality_check)
    session.quality_check_attempts = 0
    session.status = "in_progress"
    is_resuming = session.task_plan and session.task_plan.resume_from == "analysis"

    if not is_resuming:
        _emit(state, {"type": "agent_start", "agent": "ingestion", "message": "Loading your data..."})

    try:
        contract = await run_ingestion_agent(session)

        if not is_resuming:
            # First-run: store the contract and emit progress
            session.data_contract = contract
            _emit(state, {
                "type": "agent_complete",
                "agent": "ingestion",
                "message": f"Loaded {contract.row_count:,} rows × {contract.column_count} columns",
            })

            # FEAT-01: Auto-suggest questions after data connect
            try:
                questions = await generate_proactive_questions(contract)
                if questions:
                    _emit(state, {
                        "type": "suggested_questions",
                        "questions": questions,
                    })
            except Exception:
                pass

            # PROMPT-2 follow-up: build display-name + entity narrative once per
            # dataset. Cached on session so downstream rendering agents (KPI,
            # charts, alerts) read humanized labels without further LLM calls.
            if session.narrative is None:
                try:
                    session.narrative = await generate_data_narrative(contract)
                except Exception:
                    pass
        # On resume: data_contract already set from prior session; we just restored df_parquet
    except Exception as exc:
        session.error_message = f"Data ingestion failed: {exc}"

    df_parquet: bytes | None = session.__dict__.pop("_df_parquet", None)
    # BUG-13: Validate that df_parquet is available on resume
    if df_parquet is None and session.error_message is None and is_resuming:
        session.error_message = (
            "Failed to restore DataFrame for multi-turn resume. "
            "The data source may have changed or become unavailable."
        )
    return {"session": session, "df_parquet": df_parquet}


async def cleaning_node(state: AgentState) -> dict:
    _emit(state, {"type": "agent_start", "agent": "cleaning", "message": "Cleaning the data..."})
    session: AnalysisSession = state["session"]

    if state.get("df_parquet"):
        session.__dict__["_df_parquet"] = state["df_parquet"]

    try:
        _, audit = await run_cleaning_agent(session)
        session.cleaning_audit = audit

        msg = audit.summary
        _emit(state, {"type": "agent_complete", "agent": "cleaning", "message": msg})

        # Only gate on high-null if this isn't already an approval response.
        # is_approval=True means the user already approved the data quality warning.
        if audit.high_null_columns and not state.get("is_approval"):
            _emit(state, {
                "type": "approval_required",
                "context": "high_null",
                "question": (
                    f"Columns {audit.high_null_columns} have >40% missing values. "
                    "How should I proceed?\n"
                    "A) Continue with imputation (recommended)\n"
                    "B) Drop these columns entirely\n"
                    "C) Cancel analysis"
                ),
                "high_null_columns": audit.high_null_columns,
            })
    except Exception as exc:
        session.error_message = f"Data cleaning failed: {exc}"

    # Best-effort cache of cleaned data for fast follow-ups. Never blocks the pipeline.
    cached = session.__dict__.get("_df_parquet") or state.get("df_parquet")
    if cached and not session.error_message:
        try:
            path = write_cleaned_parquet(session.session_id, cached)
            session.cleaned_data_path = path
        except Exception as exc:
            logger.warning("cached parquet write failed for %s: %s", session.session_id, exc)

    df_parquet: bytes | None = session.__dict__.pop("_df_parquet", state.get("df_parquet"))
    return {"session": session, "df_parquet": df_parquet}


async def analysis_node(state: AgentState) -> dict:
    _emit(state, {"type": "agent_start", "agent": "analysis", "message": "Running statistical analysis..."})
    session: AnalysisSession = state["session"]

    if state.get("df_parquet"):
        session.__dict__["_df_parquet"] = state["df_parquet"]

    try:
        findings = await run_analysis_agent(session)
        session.analysis_findings = findings

        # PROMPT-2: smart KPI selection. Compute from df + column roles before
        # df_parquet is dropped so the briefing event can carry typed KPI cards.
        # `session.narrative` is built in ingestion_node and supplies humanized
        # column display names + entity nouns.
        # PROMPT-10: same pass also computes briefing-table extras (unique
        # counts, sample values, skewness, pre-cleaning missing %, target
        # correlations, roles summary).
        df_bytes: bytes | None = state.get("df_parquet")
        if df_bytes:
            try:
                import io
                import pandas as pd
                df = pd.read_parquet(io.BytesIO(df_bytes))
                roles = classify_columns(df)
                query = session.task_plan.raw_query if session.task_plan else ""
                findings.kpis = generate_smart_kpis(
                    df, roles, query, narrative=session.narrative
                )
                extras = compute_briefing_extras(df, roles, session.data_contract)
                findings.unique_counts = extras.get("unique_counts", {})
                findings.sample_values_per_col = extras.get("sample_values_per_col", {})
                findings.skewness = extras.get("skewness", {})
                findings.pre_cleaning_missing_pct = extras.get("pre_cleaning_missing_pct", {})
                findings.target_correlations = extras.get("target_correlations", {})
                findings.target_col = extras.get("target_col")
                findings.column_roles_summary = extras.get("column_roles_summary", {})
            except Exception:
                findings.kpis = []

        n_anomalies = len(findings.anomalies)
        n_segs = len(findings.segments)
        msg = f"Analysis complete — {n_segs} segment breakdown(s)"
        if n_anomalies:
            msg += f", {n_anomalies} anomaly(ies) detected"
        _emit(state, {"type": "agent_complete", "agent": "analysis", "message": msg})

        # Phase 2: surface the briefing payload as tables in the UI.
        # Phase 3: only fire on the first-run auto-overview — follow-up Q&A doesn't
        # need to re-render the same dataset tour. session.overview_done is still
        # False at this point (gets flipped True in quality_check_node's commit).
        if not session.overview_done and (
            findings.head_preview
            or findings.dtypes_summary
            or findings.missing_pct
            or findings.eda_summary
            or findings.kpis
        ):
            _emit(state, {
                "type": "briefing",
                "data": {
                    "head_preview": findings.head_preview,
                    "dtypes_summary": findings.dtypes_summary,
                    "missing_pct": findings.missing_pct,
                    "describe_stats": findings.eda_summary,
                    "kpis": [k.model_dump() for k in findings.kpis],
                    # PROMPT-10: briefing-table extras
                    "pre_cleaning_missing_pct": findings.pre_cleaning_missing_pct,
                    "unique_counts": findings.unique_counts,
                    "sample_values_per_col": findings.sample_values_per_col,
                    "skewness": findings.skewness,
                    "target_correlations": findings.target_correlations,
                    "target_col": findings.target_col,
                    "column_roles_summary": findings.column_roles_summary,
                },
            })
    except Exception as exc:
        session.error_message = f"Analysis failed: {exc}"

    session.__dict__.pop("_df_parquet", None)
    return {"session": session}


async def visualization_node(state: AgentState) -> dict:
    _emit(state, {"type": "agent_start", "agent": "visualization", "message": "Building charts..."})
    session: AnalysisSession = state["session"]

    # PROMPT-1: viz now classifies columns + picks smart charts directly from
    # the DataFrame. cleaning_node passes df_parquet through state; analysis_node
    # returns only {session} so state.df_parquet survives unchanged.
    if state.get("df_parquet"):
        session.__dict__["_df_parquet"] = state["df_parquet"]

    try:
        charts = await run_visualization_agent(session)
        session.chart_configs = charts

        _emit(state, {
            "type": "agent_complete",
            "agent": "visualization",
            "message": f"Generated {len(charts)} chart(s)",
        })
        _emit(state, {
            "type": "charts",
            "data": [c.model_dump() for c in charts],
        })
    except Exception as exc:
        session.error_message = f"Visualization failed: {exc}"

    # Drop the parquet bytes once viz is done — insight/expert don't need the df.
    session.__dict__.pop("_df_parquet", None)
    return {"session": session}


async def insight_node(state: AgentState) -> dict:
    _emit(state, {"type": "agent_start", "agent": "insight", "message": "Synthesizing insights..."})
    session: AnalysisSession = state["session"]

    similar_insights: list[str] = []
    vs = state.get("vector_store")
    if vs and session.task_plan:
        try:
            past = await vs.retrieve_similar(session.task_plan.raw_query, session.user_id)
            similar_insights = [
                f"{p['task_summary']}: {p['executive_summary']}" for p in past
            ]
        except Exception:
            pass

    try:
        report = await run_insight_agent(session, similar_insights or None)
        session.insight_report = report
        session.status = "completed"

        _emit(state, {"type": "insight", "data": report.model_dump()})
        _emit(state, {"type": "agent_complete", "agent": "insight", "message": "Insight generated"})
    except Exception as exc:
        session.error_message = f"Insight generation failed: {exc}"

    # Commit side-effects (add_message, iteration++, vector_store, done)
    # live in quality_check_node so that a retry doesn't corrupt history,
    # double-increment iteration, persist a bad embedding, or emit done early.
    return {"session": session}


async def quality_check_node(state: AgentState) -> dict:
    """
    FEAT-02: Evaluate insight quality and decide whether to loop back.
    Checks that the insight report is substantive enough to deliver.
    """
    session: AnalysisSession = state["session"]
    # Count this entry first so the routing function reads the post-increment value
    # and bounds the loop regardless of whether insight_node ever succeeds.
    session.quality_check_attempts += 1

    report = session.insight_report

    # Quality checks
    issues: list[str] = []
    if not report:
        issues.append("No insight report generated")
    elif not report.executive_summary or len(report.executive_summary) < 20:
        issues.append("Executive summary is empty or too short")
    elif not report.technical_findings:
        issues.append("No technical findings produced")

    if not session.chart_configs:
        issues.append("No charts were generated")

    # max_attempts = initial check + allowed retries.
    # With max_attempts=2: first entry can trigger ONE retry, second entry cannot.
    max_attempts = 2
    if issues and session.quality_check_attempts < max_attempts:
        _emit(state, {
            "type": "quality_retry",
            "message": f"Quality check: {'; '.join(issues)}. Re-running analysis...",
        })
        session.error_message = None  # clear for retry
        session.analysis_findings = None
        session.chart_configs = []
        session.insight_report = None
        return {"session": session}

    # Non-retry path: commit the report to conversation + vector memory.
    # Runs whether quality is good OR retry budget is exhausted with a bad report
    # (in the latter case we still ship what we have).
    if report:
        session.add_message(MessageRole.ASSISTANT, report.executive_summary)
        session.iteration += 1

        vs = state.get("vector_store")
        if vs and session.task_plan:
            try:
                await vs.store_insight(
                    session_id=session.session_id,
                    user_id=session.user_id,
                    report=report,
                    task_summary=session.task_plan.raw_query,
                )
            except Exception:
                pass

    ss = state.get("session_store")
    if ss:
        try:
            await ss.save(session)
        except Exception:
            pass

    # Phase 6: done emission moved to terminal nodes (senior_expert on first-runs,
    # the follow-up branch below on subsequent turns). overview_done flip moved to
    # senior_expert_node so routing here can still detect first-run via !overview_done.
    if session.overview_done:
        # Follow-up turn — no senior_expert review will run, emit done now.
        _emit(state, {"type": "done"})
    return {"session": session}


async def targeted_analysis_node(state: AgentState) -> dict:
    """
    Phase A: handles every non-overview/non-deep_dive intent. Runs deterministic
    pandas/scipy via targeted_analysis_agent, emits a single compact card, commits
    to history + persistence, and ends the graph. No insight/expert pass.
    """
    intent_label = "targeted"
    session: AnalysisSession = state["session"]
    if session.task_plan and session.task_plan.question_intent:
        intent_label = session.task_plan.question_intent.value

    _emit(state, {
        "type": "agent_start",
        "agent": "targeted_analysis",
        "message": f"Answering {intent_label.replace('_', ' ')} question...",
    })

    if state.get("df_parquet"):
        session.__dict__["_df_parquet"] = state["df_parquet"]

    try:
        response = run_targeted_analysis(session)
        session.targeted_response = response

        _emit(state, {"type": "targeted_response", "data": response.model_dump()})
        msg = "Couldn't answer" if response.unanswerable_reason else "Answer ready"
        _emit(state, {"type": "agent_complete", "agent": "targeted_analysis", "message": msg})

        if response.answer_text:
            session.add_message(MessageRole.ASSISTANT, response.answer_text)
        session.iteration += 1
    except Exception as exc:
        session.error_message = f"Targeted analysis failed: {exc}"

    session.__dict__.pop("_df_parquet", None)

    ss = state.get("session_store")
    if ss:
        try:
            await ss.save(session)
        except Exception:
            pass

    _emit(state, {"type": "done"})
    return {"session": session}


async def senior_expert_node(state: AgentState) -> dict:
    """
    Phase 6: 7th agent — principal analyst critique of the first-run analysis.
    Only entered on first-run via route_after_quality_check (gated on !overview_done).
    """
    _emit(state, {"type": "agent_start", "agent": "senior_expert", "message": "Principal analyst reviewing..."})
    session: AnalysisSession = state["session"]

    try:
        review = await run_senior_expert_agent(session)
        session.expert_review = review
        _emit(state, {"type": "expert_review", "data": review.model_dump()})
        _emit(state, {"type": "agent_complete", "agent": "senior_expert", "message": "Expert review ready"})
    except Exception as exc:
        # Don't fail the run — the user already has the insight. Log and continue.
        _emit(state, {"type": "agent_complete", "agent": "senior_expert", "message": f"Expert review skipped: {exc}"})

    # Flip overview_done here (not in quality_check) so the next turn becomes targeted Q&A.
    session.overview_done = True

    ss = state.get("session_store")
    if ss:
        try:
            await ss.save(session)
        except Exception:
            pass

    _emit(state, {"type": "done"})
    return {"session": session}


async def human_approval_node(state: AgentState) -> dict:
    # The approval_required event was already emitted by the node that triggered routing here.
    # End graph — next user message re-enters at intake with approval context.
    ss = state.get("session_store")
    if ss:
        try:
            await ss.save(state["session"])
        except Exception:
            pass
    _emit(state, {"type": "done"})
    return {"session": state["session"]}


async def error_node(state: AgentState) -> dict:
    session: AnalysisSession = state["session"]
    session.status = "failed"
    msg = session.error_message or "An unexpected error occurred."
    _emit(state, {"type": "error", "message": msg})
    _emit(state, {"type": "done"})

    ss = state.get("session_store")
    if ss:
        try:
            await ss.save(session)
        except Exception:
            pass

    return {"session": session}


# ── Graph assembly ─────────────────────────────────────────────────────────────

def build_graph():
    graph: StateGraph = StateGraph(AgentState)

    graph.add_node("intake", intake_node)
    graph.add_node("ingestion", ingestion_node)
    graph.add_node("cleaning", cleaning_node)
    graph.add_node("analysis", analysis_node)
    graph.add_node("visualization", visualization_node)
    graph.add_node("insight", insight_node)
    graph.add_node("quality_check", quality_check_node)  # FEAT-02
    graph.add_node("senior_expert", senior_expert_node)  # Phase 6
    graph.add_node("targeted_analysis", targeted_analysis_node)  # Phase A — wired in A4
    graph.add_node("human_approval", human_approval_node)
    graph.add_node("error", error_node)

    # Ingestion runs first so intake has schema context (column names, row count)
    # before deciding what analysis to run and whether clarification is needed.
    graph.set_entry_point("ingestion")

    graph.add_conditional_edges("ingestion", route_after_ingestion, {
        "intake": "intake",
        "analysis": "analysis",                  # multi-turn resume skips intake re-run
        "targeted_analysis": "targeted_analysis",  # Phase A: resume + targeted intent
        "error": "error",
    })
    graph.add_conditional_edges("intake", route_after_intake, {
        "cleaning": "cleaning",
        "targeted_analysis": "targeted_analysis",  # Phase A: non-overview intents
        "human_approval": "human_approval",
        "error": "error",
    })
    graph.add_conditional_edges("cleaning", route_after_cleaning, {
        "analysis": "analysis",
        "human_approval": "human_approval",
        "error": "error",
    })
    graph.add_conditional_edges("analysis", route_after_analysis, {
        "visualization": "visualization",
        "error": "error",
    })
    graph.add_conditional_edges("visualization", route_after_visualization, {
        "insight": "insight",
        "error": "error",
    })

    # FEAT-02: Insight → quality check → END or retry loop.
    # If insight_node set error_message, bypass quality_check and route to error_node
    # so failures aren't silently retried on a broken state.
    graph.add_conditional_edges("insight", route_after_insight_node, {
        "quality_check": "quality_check",
        "error": "error",
    })
    graph.add_conditional_edges("quality_check", route_after_quality_check, {
        "end": END,
        "analysis": "analysis",       # FEAT-02 feedback loop
        "senior_expert": "senior_expert",  # Phase 6: first-run critique
    })
    graph.add_edge("senior_expert", END)
    graph.add_edge("targeted_analysis", END)  # Phase A: terminal node

    graph.add_edge("human_approval", END)
    graph.add_edge("error", END)

    return graph.compile()
