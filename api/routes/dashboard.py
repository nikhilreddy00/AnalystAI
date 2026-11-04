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
            "column_units": findings.column_units,
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
    if not await store.pin_chart(current_user.uid, body.session_id, body.chart):
        raise HTTPException(status_code=404, detail="Chart not found")   # another user's pin
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


def json_rows(df):
    """Rows as JSON-safe records: NaN, ±inf and NaT become None (a float column keeps NaN
    under a plain `where(notna, None)`, and JSONResponse refuses it)."""
    import numpy as np

    df = df.replace([np.inf, -np.inf], np.nan).astype(object)
    return df.where(df.notna(), None).to_dict("records")


async def _latest_frame(request: Request, user: UserProfile):
    """(latest completed session, its cleaned frame): the frame every dashboard view reads."""
    from pathlib import Path

    import pandas as pd

    store = request.app.state.session_store
    if store is None:
        raise HTTPException(status_code=503, detail="Session store not available")
    session = await store.get_latest_analysis(user.uid)
    if not session:
        raise HTTPException(status_code=404, detail="No completed analysis yet")
    if not session.cleaned_data_path or not Path(session.cleaned_data_path).is_file():
        raise HTTPException(status_code=404, detail="Cleaned data is not cached for this session")
    df = pd.read_parquet(session.cleaned_data_path)
    import domain_pack   # a chain's earlier versions are never counted or shown: not in the cap
    # the server still reads every row (kinds are judged on all): a hard cap on the whole frame
    shown = len(df) if len(df) > MAX_RAW_ROWS else \
        int((~domain_pack.superseded(df, session.data_contract)).sum())
    if shown > MAX_ROWS:   # every dashboard view: the rows and their filtered recomputation
        raise HTTPException(
            status_code=422,
            detail=(f"Dataset too large for the interactive dashboard ({shown:,} rows; limit is "
                    f"{MAX_ROWS:,}). Run a targeted question via chat instead."))
    return session, df


MAX_ROWS = 25_000
MAX_RAW_ROWS = 4 * MAX_ROWS   # every row the server reads (earlier versions included)


class FilterRequest(BaseModel):
    filters: dict[str, dict] = Field(max_length=20)

    def model_post_init(self, _) -> None:   # a filter's list of values is a UI pick, never a dump
        if any(len(f.get("values") or []) > 500 for f in self.filters.values()):
            raise ValueError("a filter lists more than 500 values")


@router.post("/latest/filtered")
@limiter.limit("60/minute")
async def get_latest_dashboard_filtered(
    request: Request,
    body: FilterRequest,
    current_user: UserProfile = Depends(get_current_user),
):
    """The latest dashboard under filters, recomputed on the server by the functions that built
    it: {charts: {chart id: data}, tiles: {label: value}, rows}. A chart from the model's own
    findings can't be rebuilt and is not in `charts` (the page marks it unfiltered)."""
    import asyncio

    from agents.visualization_agent import filtered_dashboard
    from orchestrator.state import ChartConfig

    session, df = await _latest_frame(request, current_user)
    charts = list(session.chart_configs)
    # this session's pins only: another dataset's chart is never refiltered with these rows
    for c in await request.app.state.session_store.get_dashboard(current_user.uid,
                                                                 session_ids={session.session_id}):
        try:
            charts.append(c if isinstance(c, ChartConfig) else ChartConfig.model_validate(c))
        except ValueError:   # a pin saved before a schema change: shown as pinned, not refiltered
            continue
    try:
        return await asyncio.wait_for(asyncio.to_thread(
            filtered_dashboard, df, session.data_contract, charts, body.filters,
            session.task_plan.raw_query if session.task_plan else "", session.narrative), timeout=30)
    except (KeyError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=f"Invalid filter: {exc}") from exc
    except TimeoutError as exc:
        raise HTTPException(status_code=503, detail="The filtered dashboard took too long; "
                                                    "narrow the filters or ask in chat.") from exc


@router.get("/latest/data")
@limiter.limit("30/minute")
async def get_latest_dashboard_data(
    request: Request,
    current_user: UserProfile = Depends(get_current_user),
):
    """Return raw cleaned rows + column_roles for the user's latest completed session.
    Used by the dashboard page for its filter options; the numbers under filters come from
    POST /latest/filtered (the domain rules live on the server)."""
    session, df = await _latest_frame(request, current_user)

    from agents.visualization_agent import classify_columns
    roles = classify_columns(df)

    # The rows a re-count reads (plan v2 R1, the one row rule): on snapshot data each account's
    # latest row on the books (a loan is one loan, not 14 month-ends); otherwise every row
    import domain_pack
    df = df[~domain_pack.superseded(df, session.data_contract)]   # filter options: counted rows only
    if domain_pack.snapshot_entity(session.data_contract, df.columns):
        df = df.assign(__counted=df.index.isin(domain_pack.entity_rows(df, session.data_contract).index))
    rows = json_rows(df)

    return {
        "session_id": session.session_id,
        "row_count": len(rows),
        "column_roles": roles,
        "rows": rows,
    }
