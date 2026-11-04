"""Dashboard filters (user report, 2026-11-03): filtering a dashboard changed nothing. Only row-count
charts were recounted in the browser; every sum, KPI chart and domain-pack tile stayed as the run
computed it (the browser lacks the domain rules). The server recomputes them now, with the same
functions that built them (one definition)."""
import pandas as pd
import pytest

from agents.visualization_agent import (
    apply_filters,
    build_context,
    filtered_dashboard,
    generate_dashboard_charts,
    generate_smart_kpis,
    pack_overview_charts,
)
from tests.test_oracle import _built

CARDIO = {"Department": {"kind": "equality", "value": "Cardiology"}}


def _dashboard(name: str):
    s, clean, contract, truth = _built(name)
    ctx = build_context(clean, contract=contract)
    charts = pack_overview_charts(clean, ctx) + generate_dashboard_charts(clean, ctx.roles, ctx=ctx)
    kpis = generate_smart_kpis(clean, ctx.roles, "overview", contract=contract)
    return clean, contract, charts, kpis, truth


def test_a_filter_changes_the_pack_tiles_and_kpi_charts():
    clean, contract, charts, kpis, truth = _dashboard("inpatient")
    out = filtered_dashboard(clean, contract, charts, CARDIO)
    cardio = clean[clean["Department"] == "Cardiology"]
    inpatient = cardio[cardio["Encounter Type"].eq("Inpatient") & cardio["Discharge Date"].notna()]
    assert out["tiles"]["Discharges"] == f"{inpatient['Encounter ID'].nunique():,}"
    assert out["tiles"]["Discharges"] != next(k.value for k in kpis if k.label == "Discharges")
    by_dept = next(c for c in charts if "readmission" in c.title.lower() and c.x_key == "Department")
    assert {r["Department"] for r in out["charts"][by_dept.id]} == {"Cardiology"}
    assert out["rows"] == len(cardio)


def test_claims_filters_keep_the_version_rule():
    """A payer filter on claims: paid is still each line's last version (never every row)."""
    clean, contract, charts, kpis, truth = _dashboard("health_claims")
    out = filtered_dashboard(clean, contract, charts,
                             {"Payer": {"kind": "in", "values": ["Northwind Health"]}})
    assert out["tiles"]["Paid"] == f"${truth['paid_by_payer']['Northwind Health']:,.2f}"


def test_retail_and_banking_dashboards_filter_too():
    for name, col in (("retail_lines", None), ("loan_tape", "Product")):
        clean, contract, charts, kpis, _ = _dashboard(name)
        col = col or next(c for c in clean.columns if not pd.api.types.is_numeric_dtype(clean[c])
                          and not pd.api.types.is_datetime64_any_dtype(clean[c])
                          and 2 <= clean[c].nunique() <= 20)
        value = clean[col].dropna().iloc[0]
        out = filtered_dashboard(clean, contract, charts, {col: {"kind": "equality", "value": value}})
        whole = {k.label: k.value for k in kpis}
        assert any(out["tiles"].get(k) not in (None, v) for k, v in whole.items()), (name, out["tiles"])


@pytest.mark.parametrize("f, n", [
    ({"kind": "equality", "value": "A"}, 2), ({"kind": "in", "values": ["A", "B"]}, 3),
    ({"kind": "range", "min": 2, "max": 3}, 2),
])
def test_apply_filters_matches_the_browser_semantics(f, n):
    df = pd.DataFrame({"k": ["A", "A", "B", None], "v": [1, 2, 3, 4]})
    col = "v" if f["kind"] == "range" else "k"
    assert len(apply_filters(df, {col: f})) == n


def test_a_date_range_filter():
    df = pd.DataFrame({"d": pd.to_datetime(["2026-01-05", "2026-02-10", "2026-03-01"])})
    f = {"d": {"kind": "date_range", "from": "2026-02-01", "to": "2026-02-28"}}
    assert len(apply_filters(df, f)) == 1


async def test_the_filtered_endpoint_recomputes_the_latest_dashboard(tmp_path, monkeypatch):
    """POST /dashboard/latest/filtered: the owner's latest dashboard, filtered on the server."""
    from types import SimpleNamespace

    from fastapi import HTTPException
    from starlette.requests import Request

    from api.auth import UserProfile
    from api.rate_limit import limiter
    from api.routes.dashboard import FilterRequest, get_latest_dashboard_filtered
    from memory.session_store import InMemorySessionStore
    from orchestrator.state import AnalysisFindings, AnalysisSession, InsightReport

    import asyncio
    # _built runs the cleaning agent with asyncio.run: never inside this test's own event loop
    # (it passed only when another test had filled the cache first)
    clean, contract, charts, kpis, _ = await asyncio.to_thread(_dashboard, "inpatient")
    path = tmp_path / "clean.parquet"
    clean.to_parquet(path, index=False)
    store = InMemorySessionStore()
    s = AnalysisSession(session_id="s1", user_id="owner", data_contract=contract, chart_configs=charts,
                        cleaned_data_path=str(path),
                        analysis_findings=AnalysisFindings(
                            eda_summary={}, correlations={}, trends=[], segments=[], anomalies=[],
                            statistical_tests=[], forecast_points=[], distributions={},
                            code_executed="", kpis=kpis),
                        insight_report=InsightReport(executive_summary="x", technical_findings=[],
                                                     recommended_actions=[], anomaly_alerts=[],
                                                     proactive_questions=[], methodology_notes=""))
    await store.create(s)
    monkeypatch.setattr(limiter, "enabled", False)
    app = SimpleNamespace(state=SimpleNamespace(session_store=store, limiter=limiter))
    req = Request({"type": "http", "method": "POST", "path": "/", "headers": [], "app": app,
                   "client": ("127.0.0.1", 1)})
    out = await get_latest_dashboard_filtered(req, FilterRequest(filters=CARDIO), UserProfile("owner"))
    assert out["rows"] == int((clean["Department"] == "Cardiology").sum()) and out["charts"]
    with pytest.raises(HTTPException) as e:
        await get_latest_dashboard_filtered(req, FilterRequest(filters={"Department": {"kind": "nope"}}),
                                            UserProfile("owner"))
    assert e.value.status_code == 422
    with pytest.raises(HTTPException) as e:
        await get_latest_dashboard_filtered(req, FilterRequest(filters=CARDIO), UserProfile("intruder"))
    assert e.value.status_code == 404


def test_a_filtered_group_with_none_of_a_ratios_rows_is_zero_not_unknown():
    """Live check: Product = Mortgage showed 'NPL ratio: not available' and no NPL chart; the
    whole data has non-performing loans, so mortgages' NPL ratio is 0.0% (as a month with no
    returns has a 0% return rate: compute_kpi's `full`)."""
    clean, contract, charts, kpis, truth = _dashboard("loan_tape")
    out = filtered_dashboard(clean, contract, charts,
                             {"Product": {"kind": "equality", "value": "Mortgage"}})
    assert out["tiles"]["NPL ratio"] == "0.0%"
    npl = next(c for c in charts if c.title.startswith("NPL ratio by"))
    assert npl.id in out["charts"]


def test_filter_requests_are_bounded():
    """Security review: the filtered endpoint keeps /latest/data's row cap (shared loader) and
    refuses a filter that lists more than 500 values."""
    from pydantic import ValidationError

    from api.routes import dashboard
    from api.routes.dashboard import FilterRequest
    with pytest.raises(ValidationError):
        FilterRequest(filters={"k": {"kind": "in", "values": list(range(501))}})
    import inspect
    assert "MAX_ROWS" in inspect.getsource(dashboard._latest_frame)


def test_a_filter_on_a_version_field_never_brings_old_versions_back():
    """Final review M1: 'Claim Frequency in [1, 7]' gave paid $263,285.08 > the $257,150.19
    total: version chains were resolved on the filtered rows only. They resolve on the whole data."""
    clean, contract, charts, kpis, truth = _dashboard("health_claims")
    out = filtered_dashboard(clean, contract, charts,
                             {"Claim Frequency": {"kind": "in", "values": ["1", "7"]}})
    paid = float(out["tiles"]["Paid"].replace("$", "").replace(",", ""))
    assert paid <= truth["paid_total_final"] + 0.01


def test_patient_data_filtered_to_a_small_cell_is_suppressed():
    """Final review M3: one stay showed 'readmission 100.0%'; on patient data a filter that
    leaves fewer than 11 records reports no numbers (CMS 1–10)."""
    clean, contract, charts, kpis, _ = _dashboard("inpatient")
    one = str(clean["Encounter ID"].iloc[0])
    out = filtered_dashboard(clean, contract, charts,
                             {"Encounter ID": {"kind": "equality", "value": one}})
    assert out["suppressed"] and out["tiles"] == {} and out["charts"] == {}


def test_a_time_zone_aware_date_range_keeps_whole_days():
    """Final review: an aware column crashed the endpoint; rows later on the last day were cut."""
    df = pd.DataFrame({"d": pd.to_datetime(["2026-02-28T18:00:00Z", "2026-03-01T01:00:00Z"])})
    assert len(apply_filters(df, {"d": {"kind": "date_range", "from": "2026-02-01", "to": "2026-02-28"}})) == 1


def test_the_endpoint_refilters_only_this_sessions_pins():
    """Final review M2: pins from another dataset were refiltered with the latest rows."""
    import inspect

    from api.routes import dashboard
    assert "session_ids={session.session_id}" in inspect.getsource(dashboard.get_latest_dashboard_filtered)


async def test_a_loss_runs_earlier_valuations_count_against_no_dashboard_cap(tmp_path, monkeypatch):
    """Domain 4 browser check: a 38,255-row loss run (3,975 claimant rows at their latest valuation)
    got 422 'too large' from /latest/data. Superseded versions are never counted or shown: the
    cap reads the rows that count, the browser gets those, and the server's filtered numbers
    still judge kinds on every row."""
    import asyncio
    from types import SimpleNamespace

    from starlette.requests import Request

    from api.auth import UserProfile
    from api.rate_limit import limiter
    from api.routes import dashboard
    from api.routes.dashboard import FilterRequest, get_latest_dashboard_data, get_latest_dashboard_filtered
    from memory.session_store import InMemorySessionStore
    from orchestrator.state import AnalysisFindings, AnalysisSession, InsightReport

    clean, contract, charts, kpis, truth = await asyncio.to_thread(_dashboard, "loss_run")
    path = tmp_path / "clean.parquet"
    clean.to_parquet(path, index=False)
    store = InMemorySessionStore()
    await store.create(AnalysisSession(
        session_id="s1", user_id="owner", data_contract=contract, chart_configs=charts,
        cleaned_data_path=str(path), analysis_findings=AnalysisFindings(
            eda_summary={}, correlations={}, trends=[], segments=[], anomalies=[], statistical_tests=[],
            forecast_points=[], distributions={}, code_executed="", kpis=kpis),
        insight_report=InsightReport(executive_summary="x", technical_findings=[], recommended_actions=[],
                                     anomaly_alerts=[], proactive_questions=[], methodology_notes="")))
    monkeypatch.setattr(limiter, "enabled", False)
    monkeypatch.setattr(dashboard, "MAX_ROWS", 10_000)   # below the file, above its counted rows
    app = SimpleNamespace(state=SimpleNamespace(session_store=store, limiter=limiter))
    req = Request({"type": "http", "method": "GET", "path": "/", "headers": [], "app": app,
                   "client": ("127.0.0.1", 1)})
    data = await get_latest_dashboard_data(req, UserProfile("owner"))
    assert data["row_count"] == truth["claim_rows_latest"]
    home = await get_latest_dashboard_filtered(req, FilterRequest(
        filters={"Line of Business": {"kind": "equality", "value": "Home"}}), UserProfile("owner"))
    assert home["tiles"]["Open case reserves"] == f"${truth['open_reserve_by_line']['Home']:,.2f}"
    # 'Showing N of M records': the filtered N counts the same rows as M (browser check: 15,780 of 3,975)
    assert home["rows"] == truth["claimants_by_line"]["Home"]
    # security review: the server still reads every row, so the whole frame has a hard cap too
    from fastapi import HTTPException
    monkeypatch.setattr(dashboard, "MAX_RAW_ROWS", 30_000)
    with pytest.raises(HTTPException) as e:
        await get_latest_dashboard_data(req, UserProfile("owner"))
    assert e.value.status_code == 422
