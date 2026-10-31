"""
Dashboard endpoints — pin/unpin charts, load user dashboard.
Full implementation in Phase 5 (frontend). Stub for Phase 1.
"""

from fastapi import APIRouter, HTTPException, Request, Depends
from fastapi.responses import Response
from api.auth import get_current_user, UserProfile
from api.rate_limit import limiter
from pydantic import BaseModel, Field

from orchestrator.state import ChartType

router = APIRouter()


class PinChartRequest(BaseModel):
    session_id: str
    chart: dict


class ChartFeedbackRequest(BaseModel):
    session_id: str = Field(max_length=64)
    chart_id: str = Field(max_length=64)
    chart_type: ChartType      # the type the user switched to


@router.get("")
async def get_dashboard(
    request: Request,
    current_user: UserProfile = Depends(get_current_user)
):
    from agents.visualization_agent import _GENERIC_COLORS

    store = request.app.state.session_store
    charts = await store.get_dashboard(current_user.uid)
    # Charts pinned before Phase 5 carry the old generic palette; the theme decides instead.
    for c in charts:
        if isinstance(c, dict) and c.get("colors") and all(
                str(x).lower() in _GENERIC_COLORS for x in c["colors"]):
            c["colors"] = []
    return {"charts": charts}


@router.get("/latest")
async def get_latest_analysis(
    request: Request,
    current_user: UserProfile = Depends(get_current_user)
):
    """Phase 4: auto-generated dashboard for the most recent committed analysis."""
    store = request.app.state.session_store
    session = await store.get_latest_analysis(current_user.uid)
    if session is None:
        return {"latest": None}

    findings = session.analysis_findings
    briefing = None
    if findings and (
        findings.head_preview
        or findings.dtypes_summary
        or findings.missing_pct
        or findings.eda_summary
        or findings.kpis
    ):
        briefing = {
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
        }

    return {
        "latest": {
            "session_id": session.session_id,
            "updated_at": session.updated_at.isoformat(),
            "raw_query": session.task_plan.raw_query if session.task_plan else None,
            "charts": [c.model_dump() for c in session.chart_configs],
            "insight": session.insight_report.model_dump() if session.insight_report else None,
            "briefing": briefing,
            "expert_review": session.expert_review.model_dump() if session.expert_review else None,
        }
    }


@router.post("/pin")
async def pin_chart(
    request: Request,
    body: PinChartRequest,
    current_user: UserProfile = Depends(get_current_user)
):
    store = request.app.state.session_store
    # Only to a thread of your own: a pin on someone else's (or a deleted) thread would outlive
    # its deletion and show on the wrong dataset page.
    if not await store.get_summary(body.session_id, current_user.uid):
        raise HTTPException(status_code=404, detail="Session not found")
    await store.pin_chart(current_user.uid, body.session_id, body.chart)
    return {"status": "pinned"}


@router.post("/chart-feedback")
@limiter.limit("30/minute")   # each call loads and saves a whole session
async def chart_feedback(
    request: Request,
    body: ChartFeedbackRequest,
    current_user: UserProfile = Depends(get_current_user)
):
    """The user switched a chart's type (Phase 5 Visualization). Persist it on the stored chart, so
    pinning and the PowerPoint show the user's choice, and log the override (labels only)."""
    from agents.visualization_agent import _LINT_NOTE, VIZ_CARD, swap_options
    from guardrails import chart_lint
    from llm.client import trace_feedback

    store = request.app.state.session_store
    session = await store.load(body.session_id)
    if not session or session.user_id != current_user.uid:
        raise HTTPException(status_code=404, detail="Session not found")
    charts = list(session.chart_configs) + (
        list(session.targeted_response.chart_configs) if session.targeted_response else [])
    chart = next((c for c in charts if c.id == body.chart_id), None)
    if chart is None:
        raise HTTPException(status_code=404, detail="Chart not found")
    if body.chart_type not in swap_options(chart):
        raise HTTPException(status_code=422, detail="That chart type would misrepresent this data")
    before, chart.chart_type = chart.chart_type, body.chart_type
    chart.lint_warnings = [_LINT_NOTE.get(lb, lb) for lb in chart_lint(chart.model_dump(mode="json"))]
    await store.save(session)
    trace_feedback(VIZ_CARD.name, "chart_override",
                   chart.rationale.rule if chart.rationale else "none", before.value, body.chart_type.value)
    return {"chart": chart.model_dump(mode="json")}


@router.delete("/{chart_id}")
async def unpin_chart(
    request: Request,
    chart_id: str,
    current_user: UserProfile = Depends(get_current_user)
):
    store = request.app.state.session_store
    await store.unpin_chart(chart_id, current_user.uid)
    return {"status": "unpinned"}


@router.get("/latest/report.pptx")
async def download_latest_report(
    request: Request,
    current_user: UserProfile = Depends(get_current_user)
):
    """Phase 5: build a PowerPoint deck from the user's latest committed analysis."""
    store = request.app.state.session_store
    session = await store.get_latest_analysis(current_user.uid)
    if session is None:
        raise HTTPException(
            status_code=404,
            detail="No completed analysis found. Run an analysis in /chat first.",
        )

    # Lazy import — keeps python-pptx out of the cold-start path of every other route.
    from api.lib.report_pptx import build_pptx
    pptx_bytes = build_pptx(session)

    safe_query = (session.task_plan.raw_query if session.task_plan else "report") or "report"
    # ASCII only: headers are latin-1 (a question like "売上の推移" would be a 500).
    safe_query = "".join(c if c.isascii() and (c.isalnum() or c in "-_ ") else "_"
                         for c in safe_query)[:40].strip() or "report"
    filename = f"analystai_{safe_query.replace(' ', '_')}.pptx"

    return Response(
        content=pptx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/latest/data")
@limiter.limit("30/minute")
async def get_latest_dashboard_data(
    request: Request,
    current_user: UserProfile = Depends(get_current_user),
):
    """Return raw cleaned rows + column_roles for the user's latest completed session.
    Used by the dashboard page for client-side filtering + chart re-aggregation."""
    from pathlib import Path

    store = request.app.state.session_store
    if store is None:
        raise HTTPException(status_code=503, detail="Session store not available")

    session = await store.get_latest_analysis(current_user.uid)
    if not session:
        raise HTTPException(status_code=404, detail="No completed analysis yet")

    if not session.cleaned_data_path or not Path(session.cleaned_data_path).is_file():
        raise HTTPException(status_code=404, detail="Cleaned data is not cached for this session")

    import pandas as pd
    df = pd.read_parquet(session.cleaned_data_path)

    MAX_ROWS = 25_000
    if len(df) > MAX_ROWS:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Dataset too large for the interactive dashboard "
                f"({len(df):,} rows; limit is {MAX_ROWS:,}). "
                f"Run a targeted question via chat instead."
            ),
        )

    from agents.visualization_agent import classify_columns
    roles = classify_columns(df)

    # NaN → None so the JSON payload is valid
    rows = df.where(df.notna(), None).to_dict("records")

    return {
        "session_id": session.session_id,
        "row_count": len(rows),
        "column_roles": roles,
        "rows": rows,
    }
