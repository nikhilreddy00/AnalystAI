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
    out = filtered_dashboard(clean, contract, charts, kpis, CARDIO)
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
    out = filtered_dashboard(clean, contract, charts, kpis,
                             {"Payer": {"kind": "in", "values": ["Northwind Health"]}})
    assert out["tiles"]["Paid"] == f"${truth['paid_by_payer']['Northwind Health']:,.2f}"


def test_retail_and_banking_dashboards_filter_too():
    for name, col in (("retail_lines", None), ("loan_tape", "Product")):
        clean, contract, charts, kpis, _ = _dashboard(name)
        col = col or next(c for c in clean.columns if not pd.api.types.is_numeric_dtype(clean[c])
                          and not pd.api.types.is_datetime64_any_dtype(clean[c])
                          and 2 <= clean[c].nunique() <= 20)
        value = clean[col].dropna().iloc[0]
        out = filtered_dashboard(clean, contract, charts, kpis, {col: {"kind": "equality", "value": value}})
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

    clean, contract, charts, kpis, _ = _dashboard("inpatient")
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
    out = filtered_dashboard(clean, contract, charts, kpis,
                             {"Product": {"kind": "equality", "value": "Mortgage"}})
    assert out["tiles"]["NPL ratio"] == "0.0%"
    npl = next(c for c in charts if c.title.startswith("NPL ratio by"))
    assert npl.id in out["charts"]
