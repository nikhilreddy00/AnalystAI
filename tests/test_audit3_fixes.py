"""Phase 5.5 final audit pass 3: one test per finding, each failing without its fix."""
from __future__ import annotations

import asyncio
import io
import logging

import numpy as np
import pandas as pd
import pytest

from agents.ingestion_agent import build_contract
from evals import datagen
from orchestrator.state import ConnectorType, TargetedParams


def _contract(df):
    return build_contract(df, ConnectorType.CSV, {})


def _parquet(df) -> bytes:
    buf = io.BytesIO()
    df.to_parquet(buf, index=False)
    return buf.getvalue()


# ── analytics ───────────────────────────────────────────────────────────────────────────────

def _mrr_by_anniversary():
    rows = []
    for i, (plan, mrr) in enumerate([("Scale", 400.0)] * 100 + [("Pro", 100.0)] * 100
                                    + [("Starter", 25.0)] * 100):
        day = 1 + i % 28
        for m in pd.period_range("2026-01", "2026-11", freq="M"):
            t = m.to_timestamp() + pd.Timedelta(days=day - 1)
            if t <= pd.Timestamp("2026-11-04"):
                rows.append({"account_id": f"A{i}", "plan": plan, "month": t, "mrr": mrr,
                             "seats": 5, "active_users": 3, "churned": 0})
    return pd.DataFrame(rows)


def test_a_partial_newest_month_keeps_accounts_billed_later_in_the_month():
    from agents.utils import aggregate
    df = _mrr_by_anniversary()
    got = aggregate(df, "plan", "mrr", "last", _contract(df))
    assert got.to_dict() == pytest.approx({"Pro": 10_000, "Scale": 40_000, "Starter": 2_500})


def test_weekday_only_snapshots_on_a_saturday_export_still_count():
    from agents.utils import latest_rows
    days = pd.date_range("2026-03-02", "2026-03-28", freq="D")   # ends on a Saturday
    rows = [{"account_id": f"R{i}", "segment": "retail", "snapshot_date": d, "balance": 1000.0}
            for i in range(20) for d in days]
    rows += [{"account_id": f"B{i}", "segment": "business", "snapshot_date": d, "balance": 1000.0}
             for i in range(20) for d in days if d.weekday() < 5]
    df = pd.DataFrame(rows).assign(branch="Main", deposits=1.0)
    c = _contract(df)
    assert any(x.aggregation == "last" for x in c.columns if x.name == "balance")
    got = latest_rows(df, "balance", c).groupby("segment").balance.sum()
    assert got.to_dict() == {"business": 20_000.0, "retail": 20_000.0}


def test_an_intake_default_sum_of_a_reading_ranks_by_its_average():
    from agents import targeted_analysis_agent as ta
    df, truth = datagen.make("sensor_readings", 1)
    c = _contract(df)
    p = TargetedParams(metric_column="temperature", group_by="line", aggregation="sum")
    assert ta._asked_agg(p, c, "Which line runs hottest?").aggregation is None
    assert ta._asked_agg(p, c, "Total temperature by line").aggregation == "sum"
    r = ta.handle_top_n(df, ta._asked_agg(p, c, "Which line runs hottest?"), c)
    assert f"{truth['mean_temp_by_line']['B']:,.2f}" in r.answer_text


@pytest.mark.parametrize("name, values", [
    ("birthweight", [3300, 2950]), ("is_deceased", [0, 1]), ("deceased", ["yes", "no"]),
    ("cost_borne_by", ["patient", "insurer"]), ("country_of_birth", ["France", "Peru"]),
    ("total_deaths", [1950, 2004]), ("death_count", [1990, 2010])])
def test_measures_about_births_and_deaths_are_not_dates_of_birth(name, values):
    from guardrails import pii_kind
    assert pii_kind(name, values * 10) is None


def test_a_dollar_amount_that_also_names_usd_is_a_number():
    from agents.data_cleaning import try_numeric_conversion
    got = try_numeric_conversion(pd.Series(["$1,249.50 USD", "$12.00 USD", "$3.10 USD"]))
    assert got is not None and got[0].tolist() == [1249.5, 12.0, 3.1]


def test_a_padded_quantity_stays_a_number():
    from connectors.csv_connector import _read_bytes
    df, _ = _read_bytes(b"sku,units\nA,050\nB,047\nC,120\nD,009\n", ".csv")
    assert df["units"].tolist() == [50, 47, 120, 9]


def test_a_layer_measure_is_never_a_kpi_category():
    from agents.visualization_agent import classify_columns, generate_smart_kpis
    df, _ = datagen.make("shipments", 1)
    kpis = generate_smart_kpis(df, classify_columns(df), contract=_contract(df))
    assert not any("Delivery Days" in k.label for k in kpis if k.recipe and k.recipe.kind != "metric_avg")


def test_an_age_in_days_is_not_capped_at_90():
    from guardrails import is_age_column
    assert not is_age_column("age_at_admission_days") and is_age_column("age_at_admission")


# ── failures / parity ───────────────────────────────────────────────────────────────────────

def test_school_and_hotel_data_are_not_patient_data():
    rng = np.random.default_rng(0)
    school = pd.DataFrame({"enrollment_id": range(300), "subject_id": rng.integers(1, 9, 300),
                           "course": [f"C{i % 80}" for i in range(300)], "score": rng.random(300)})
    hotel = pd.DataFrame({"booking_id": range(300), "guest_name": ["Ana Ito", "Bo Lee"] * 150,
                          "hotel": [f"H{i % 78}" for i in range(300)],
                          "hospitality_tier": ["gold", "silver"] * 150, "revenue": rng.random(300)})
    assert not _contract(school).phi and not _contract(hotel).phi
    # next to health data the same header is a patient number
    school["dx_code"] = ["I10", "E11.9"] * 150
    assert _contract(school).phi


def test_the_access_log_keeps_the_client_address():
    from api.logging_scrub import ScrubFilter
    out = io.StringIO()
    h = logging.StreamHandler(out)
    h.addFilter(ScrubFilter())
    log = logging.getLogger("uvicorn.access")
    log.addHandler(h)
    log.setLevel(logging.INFO)
    try:
        log.info('%s - "%s %s HTTP/%s" %d', "203.0.113.5:54866", "GET", "/health", "1.1", 200)
    finally:
        log.removeHandler(h)
    assert "203.0.113.5:54866" in out.getvalue() and "200" in out.getvalue()


def test_a_missing_e2b_key_is_a_setup_error_not_a_retry(monkeypatch):
    import agents.analysis_agent as aa
    from agents.errors import SandboxError
    from api.config import settings
    from llm.client import RecordedClient
    from orchestrator.state import AnalysisSession, AnalysisTask
    import sandbox.executor as ex
    monkeypatch.delenv("E2B_API_KEY", raising=False)
    monkeypatch.setattr(settings, "e2b_api_key", "")
    monkeypatch.setattr(aa, "run_code", ex.run_code)
    df, _ = datagen.make("claims", 1)
    s = AnalysisSession(session_id="e", data_contract=_contract(df), task_plan=AnalysisTask(
        raw_query="q", analysis_types=["eda"], target_metrics=["claim_amount"]))
    s.__dict__["_df_parquet"] = _parquet(df)
    with pytest.raises(SandboxError) as err:
        asyncio.run(aa.run_analysis_agent(s, RecordedClient(["result['x'] = 1"])))
    assert err.value.retryable is False and "administrator" in err.value.user_message


# ── eval scorer ─────────────────────────────────────────────────────────────────────────────

def _seg(col, metric, agg, values):
    from orchestrator.state import AnalysisFindings
    return AnalysisFindings(eda_summary={}, correlations={}, trends=[], anomalies=[],
                            statistical_tests=[], code_executed="", segments=[{"segment_column": col, "metric_column": metric,
                                       "aggregation": agg,
                                       "results": [{"name": k, "value": v} for k, v in values.items()]}])


def test_a_level_answer_that_follows_the_layer_passes_saas_4():
    from agents.utils import aggregate
    from evals.scoring import check_segment_value, false_findings
    df, truth = datagen.make("saas_accounts", 1)
    want = aggregate(df, "plan", "seats", "last", _contract(df)).to_dict()
    f = _seg("plan", "seats", "last", want)
    assert check_segment_value(f, df, "plan", "seats")[0]
    assert false_findings(f, df, truth)["false"] == 0


def test_a_right_loss_ratio_filed_under_a_column_is_not_false():
    from evals.scoring import false_findings
    df, truth = datagen.make("claims", 1)
    f = _seg("line", "claim_amount", "sum", truth["loss_ratio_by_line"])
    assert false_findings(f, df, truth)["false"] == 0


def test_attrition_by_headcount_fails_hr_1():
    from evals.scoring import check_segment_value
    df, _ = datagen.make("employees", 1)
    f = _seg("department", "left", "count", df.groupby("department").left.count().to_dict())
    assert not check_segment_value(f, df, "department", "left", aggregation="mean")[0]


def test_a_domain_overview_with_a_broken_report_fails():
    from evals.run import run_passed
    out = {"checks": [{"ok": True}], "hard": [{"rule": "non_empty_or_error", "ok": False}]}
    assert not run_passed("e2e", {"domain": "insurance"}, out)
    assert run_passed("e2e", {"domain": "retail"}, out)   # old cases: unchanged meaning


def test_by_domain_reads_attribution_from_any_run():
    from evals.run import _by_domain
    base = {"domain": "hr", "checks": [{"kind": "x", "ok": True}], "hard": [], "passed": True}
    runs = {"hr-1": [{**base, "passed": False}, {**base, "builtin_only": False}]}
    assert _by_domain(runs)["by_domain"]["hr"]["model_dependent_cases"] == 1
