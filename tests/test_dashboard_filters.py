"""Dashboard filters (user report, 2026-11-03): filtering a dashboard changed nothing. Only row-count
charts were recounted in the browser; every sum, KPI chart and domain-pack tile stayed as the run
computed it (the browser lacks the domain rules). The server recomputes them now, with the same
functions that built them (one definition)."""
import re

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
    assert out["rows"] is None   # fix-diff review: 'Showing 7 of …' was the small cell itself


def test_a_time_zone_aware_date_range_keeps_whole_days():
    """Final review: an aware column crashed the endpoint; rows later on the last day were cut."""
    df = pd.DataFrame({"d": pd.to_datetime(["2026-02-28T18:00:00Z", "2026-03-01T01:00:00Z"])})
    assert len(apply_filters(df, {"d": {"kind": "date_range", "from": "2026-02-01", "to": "2026-02-28"}})) == 1


def test_the_endpoint_refilters_only_this_sessions_pins():
    """Final review M2: pins from another dataset were refiltered with the latest rows."""
    import inspect

    from api.routes import dashboard
    assert "session_ids={session.session_id}" in inspect.getsource(dashboard.get_latest_dashboard_filtered)


async def test_the_dashboard_sends_filter_options_never_rows_and_has_no_browser_cap(tmp_path, monkeypatch):
    """The 25,000-row 'too large' 422 came back in Domains 3 and 4 (a 38,255-row loss run): it
    existed only because every row was shipped to the browser, which also recounted charts and
    tiles itself (a second copy of the rules, without row kinds, versions or currency: the
    'sometimes wrong under filters'). /latest/data now sends the filter options (values, ranges,
    dates from the rows that count) and the counted total; every number comes from the server.
    A server compute guard stays (far above any upload the 50 MB cap allows)."""
    import asyncio
    from types import SimpleNamespace

    from fastapi import HTTPException
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
    app = SimpleNamespace(state=SimpleNamespace(session_store=store, limiter=limiter))
    req = Request({"type": "http", "method": "GET", "path": "/", "headers": [], "app": app,
                   "client": ("127.0.0.1", 1)})
    data = await get_latest_dashboard_data(req, UserProfile("owner"))
    assert "rows" not in data and data["row_count"] == truth["claim_rows_latest"]
    lob = next(o for o in data["options"] if o["column"] == "Line of Business")
    assert lob["control"] in ("binary", "pills") and lob["values"] == ["Auto", "Home"]
    status = next(o for o in data["options"] if o["column"] == "Claim Status")
    assert "Reopened" not in status["values"]   # no claim is Reopened at its latest valuation
    home = await get_latest_dashboard_filtered(req, FilterRequest(
        filters={"Line of Business": {"kind": "equality", "value": "Home"}}), UserProfile("owner"))
    assert home["tiles"]["Open case reserves"] == f"${truth['open_reserve_by_line']['Home']:,.2f}"
    assert home["rows"] == truth["claimants_by_line"]["Home"]   # 'Showing N of M': the same rows
    # the server's guard: every row it reads (earlier versions included)
    monkeypatch.setattr(dashboard, "MAX_ROWS", 30_000)
    with pytest.raises(HTTPException) as e:
        await get_latest_dashboard_data(req, UserProfile("owner"))
    assert e.value.status_code == 422


# ── Every filter on every domain (user request 2026-11-04: 'no matter what the filters, the
# right answers'): relations that need no truth table, so every new domain is covered at once.

def _number(text: str) -> float:
    return float(re.sub(r"[^0-9.\-]", "", text.split(" vs ")[0]))


def _filter_columns(clean, contract, k: int = 8) -> list[tuple[str, list[str]]]:
    """The first k category filters the dashboard offers whose counted rows all have a value
    (a blank row is in no filter value), never the currency (filtering by it changes which
    currency counts)."""
    from domain_pack import as_text, superseded
    from agents.visualization_agent import filter_options
    shown = clean[~superseded(clean, contract)]
    cur = contract.roles.get("currency")
    out = []
    for o in filter_options(clean, contract)["options"]:
        if o["control"] in ("binary", "pills") and o["column"] != cur and 2 <= len(o["values"]) <= 8 \
                and shown[o["column"]].notna().all():
            out.append((o["column"], o["values"]))
    return [(c, v) for c, v in out if as_text(shown[c]).isin(v).all()][:k]


PACK_DATASETS = ["retail_lines", "store_weeks", "b2b_invoices", "loan_tape", "loan_tape_messy",
                 "card_ledger", "bank_balances", "bank_statement", "ledger_drcr", "deposits_daily",
                 "inpatient", "encounters", "health_claims", "policies", "loss_run", "claims",
                 "hr_roster", "hr_roster_onerow", "hr_snapshots", "employees"]


@pytest.mark.parametrize("name", PACK_DATASETS)
def test_every_filter_partitions_and_matches_the_group_answer(name):
    """Every category filter the dashboard offers, every value, every tile and chart, on every
    domain dataset, through filtered_dashboard (the production path). Truth-free relations:
    counts add up; a filtered tile = the KPI by that column (kinds judged on all rows); an
    additive tile's values add up to the whole; a chart grouped by the filtered column shows
    only that value, with its unfiltered number."""
    import domain_pack
    clean, contract, charts, kpis, _ = _dashboard(name)
    pack = domain_pack.pack_for(contract.domain)
    roles = {r: c for r, c in contract.roles.items() if c in clean.columns}
    tiles = {k.label: pack.kpi(k.recipe.kpi) for k in kpis if k.recipe and k.recipe.kind == "pack"}
    assert tiles, f"{name}: no pack tiles"
    whole = filtered_dashboard(clean, contract, charts, {})
    columns = _filter_columns(clean, contract)
    assert columns, f"{name}: no category filter to test"
    for col, values in columns:
        outs = {v: filtered_dashboard(clean, contract, charts, {col: {"kind": "equality", "value": v}})
                for v in values}
        for v, o in outs.items():   # a small cell on patient data: no number at all, not even a count
            assert not o["suppressed"] or (o["rows"] is None and not o["tiles"] and not o["charts"]), v
        if not any(o["suppressed"] for o in outs.values()):
            assert sum(o["rows"] for o in outs.values()) == whole["rows"], (name, col, "rows")
        for label, kpi in tiles.items():
            by = domain_pack.compute_kpi(clean, roles, pack, kpi.name, by=col, full=clean)
            got = {v: _number(o["tiles"][label]) for v, o in outs.items() if label in o["tiles"]}
            scale = 100 if kpi.unit == "percent" else 1
            tol = 0.051 if kpi.unit == "percent" else 0.0051
            for v, x in got.items():   # the filtered tile = the KPI computed by that column
                if by is None or v not in by.index.astype(str):
                    assert x == 0, (name, col, label, v, x)   # rows that never count (voided): 0
                    continue
                want = float(by[by.index.astype(str) == v].iloc[0]) * scale
                assert abs(x - want) <= tol * max(1, abs(want)) if kpi.unit == "count" else \
                    abs(x - want) <= tol + 1e-9 * abs(want), (name, col, label, v, x, want)
            if domain_pack.additive(pack, kpi.name) and label in whole["tiles"] and len(got) == len(values) \
                    and not any(o["suppressed"] for o in outs.values()):
                total = _number(whole["tiles"][label])   # a partition adds up to the whole
                assert abs(sum(got.values()) - total) <= 0.01 * len(values) + 1e-9 * abs(total), \
                    (name, col, label, sum(got.values()), total)
        for c in charts:   # a chart grouped by the filtered column
            if c.x_key != col or c.id not in whole["charts"]:
                continue
            ys = [y for y in c.y_keys if not y.endswith(("__n", "__ci"))]
            before = {str(r[col]): r for r in whole["charts"][c.id]}
            for v, o in outs.items():
                for r in o["charts"].get(c.id, []):
                    assert str(r[col]) == v, (name, col, c.title, v, r)
                    for y in ys:
                        if y in r and v in before and y in before[v]:
                            b = float(before[v][y])
                            assert abs(float(r[y]) - b) <= 1e-6 * max(1, abs(b)), \
                                (name, col, c.title, v, y, r[y], b)

async def test_dashboard_recomputations_are_bounded_server_wide(monkeypatch):
    """Security review: a timed-out recomputation keeps running in its thread; the dashboard pool's
    workers bound what runs at once (a semaphore released on cancel did not)."""
    import asyncio
    import time
    from concurrent.futures import ThreadPoolExecutor

    from api.routes import dashboard
    monkeypatch.setattr(dashboard, "_POOL", ThreadPoolExecutor(max_workers=1))
    running, peak = [0], [0]

    def slow():
        running[0] += 1
        peak[0] = max(peak[0], running[0])
        time.sleep(0.05)
        running[0] -= 1
        return "ok"
    assert await asyncio.gather(*(dashboard._bounded(slow) for _ in range(4))) == ["ok"] * 4
    assert peak[0] == 1


@pytest.mark.parametrize("name", PACK_DATASETS)
def test_in_range_and_date_filters_agree_with_their_parts(name):
    """Fix-diff review: the property test used equality only. 'in' of two values = the sum of each
    value's additive tiles; a range and a date span count exactly the counted rows inside them."""
    import domain_pack
    from agents.visualization_agent import filter_options
    clean, contract, charts, kpis, _ = _dashboard(name)
    pack = domain_pack.pack_for(contract.domain)
    shown = clean[~domain_pack.superseded(clean, contract)]
    adds = {k.label for k in kpis if k.recipe and k.recipe.kind == "pack"
            and domain_pack.additive(pack, k.recipe.kpi)}
    for col, values in _filter_columns(clean, contract, k=1):
        a, b = values[:2]
        both = filtered_dashboard(clean, contract, charts, {col: {"kind": "in", "values": [a, b]}})
        one = [filtered_dashboard(clean, contract, charts, {col: {"kind": "equality", "value": v}}) for v in (a, b)]
        if both["suppressed"] or any(o["suppressed"] for o in one):
            continue
        assert both["rows"] == one[0]["rows"] + one[1]["rows"], (name, col)
        for label in adds & set(both["tiles"]):
            total = sum(_number(o["tiles"][label]) for o in one)
            assert abs(_number(both["tiles"][label]) - total) <= 0.02 + 1e-9 * abs(total), (name, label)
    for o in filter_options(clean, contract)["options"]:
        if o["control"] == "range":
            lo, hi = o["min"], (o["min"] + o["max"]) / 2
            out = filtered_dashboard(clean, contract, charts, {o["column"]: {"kind": "range", "min": lo, "max": hi}})
            want = int(pd.to_numeric(shown[o["column"]], errors="coerce").between(lo, hi).sum())
        elif o["control"] == "dates":
            out = filtered_dashboard(clean, contract, charts, {o["column"]: {"kind": "date_range",
                                                                               "from": o["from"], "to": o["to"]}})
            t = pd.to_datetime(shown[o["column"]], format="mixed", errors="coerce")
            want = int(t.notna().sum())
        else:
            continue
        assert out["suppressed"] or out["rows"] == want, (name, o["column"], out["rows"], want)


def test_every_domain_dataset_is_in_the_every_filter_check():
    """User, 2026-11-04: a new domain must never skip the every-filter check. Any eval dataset that
    reads as a domain (a strong pack match) is in PACK_DATASETS: enforced here, not remembered."""
    from evals import datagen
    from tests.test_oracle import _built
    domains = [n for n in datagen.DATASETS if _built(n)[2].domain_confidence == "strong"]
    missing = sorted(set(domains) - set(PACK_DATASETS))
    assert not missing, f"add to PACK_DATASETS (every filter on every domain): {missing}"
