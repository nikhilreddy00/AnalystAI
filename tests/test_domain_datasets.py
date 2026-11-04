"""Phase 5.5 WP1: the domain datasets plant what their cases score, and each trap's check passes
on the right answer and fails on the naive one (a check that can't tell them apart measures
nothing)."""
from __future__ import annotations

import json

import pandas as pd
import pytest

from evals import datagen
from evals.run import load_cases, rescore, score_e2e, summarize
from evals.scoring import (
    ANALYSIS_CHECKS,
    E2E_CHECKS,
    _truth_value,
    check_segment_value_truth,
    check_simpson_reversal,
    check_trend,
)
from orchestrator.state import (
    AnalysisFindings,
    AnalysisSession,
    QuestionIntent,
    SegmentResult,
    TargetedResponse,
    TrendResult,
)

DOMAINS = ("bank_balances", "encounters", "claims", "employees", "saas_accounts",
           "sensor_readings", "shipments", "retail_lines", "store_weeks", "b2b_invoices",
           "policies", "loss_run")
MAX_ROWS = {"loss_run": 50_000}   # a loss run lists every claimant at each of 16 valuations


def _findings(**kw) -> AnalysisFindings:
    base = dict(eda_summary={}, correlations={}, trends=[], segments=[], anomalies=[],
                statistical_tests=[], forecast_points=[], distributions={}, code_executed="")
    return AnalysisFindings(**{**base, **kw})


def _trend(column: str, pct: float, segment: str | None = None) -> TrendResult:
    return TrendResult(column=column, period="MoM", pct_change=pct, segment=segment,
                       direction="up" if pct > 0 else "down")


def _seg(segment: str, values: dict, metric: str = "rate") -> SegmentResult:
    return SegmentResult(segment_column=segment, metric_column=metric, aggregation="mean",
                         results=[{"name": k, "value": v} for k, v in values.items()])


def _answer(text: str) -> AnalysisSession:
    s = AnalysisSession(session_id="t")
    s.targeted_response = TargetedResponse(intent=QuestionIntent.SEGMENT_COMPARE, answer_text=text)
    return s


@pytest.mark.parametrize("name", DOMAINS)
def test_generators_are_deterministic_and_small(name):
    a, ta = datagen.make(name, 1)
    b, tb = datagen.make(name, 1)
    assert a.equals(b) and json.dumps(ta, default=str) == json.dumps(tb, default=str)
    assert len(a) <= MAX_ROWS.get(name, 7000) and ta["row_count"] == len(a)
    assert set(ta["pii_columns"]) <= set(a.columns)


def test_every_domain_case_resolves_and_names_its_domain():
    cases = [c for s in ("analysis", "e2e") for c in load_cases(s, None)]
    assert {c["domain"] for c in cases} == {"retail", "banking", "healthcare", "insurance", "hr",
                                            "saas", "iot", "logistics"}
    for c in cases:
        _, truth = datagen.make(c["dataset"]["name"], c["dataset"]["seed"])
        for spec in c["checks"]:
            assert spec["kind"] in (ANALYSIS_CHECKS if "task" in c else E2E_CHECKS), c["id"]
            if "truth_key" in spec:
                _truth_value(truth, spec["truth_key"])


@pytest.mark.parametrize("name, column, truth_key, naive_key", [
    ("bank_balances", "balance", "balance_mom_pct", "naive_balance_mom_pct"),
    ("sensor_readings", "temperature", "temp_mom_pct", "naive_temp_mom_pct"),
])
def test_level_trends_pass_on_the_level_and_fail_on_the_sum(name, column, truth_key, naive_key):
    _, truth = datagen.make(name, 1)
    assert check_trend(_findings(trends=[_trend(column, truth[truth_key])]), truth, column, truth_key)[0]
    assert not check_trend(_findings(trends=[_trend(column, truth[naive_key])]), truth, column,
                           truth_key)[0]


def test_loss_ratio_is_a_ratio_of_sums_not_a_mean_of_ratios():
    _, truth = datagen.make("claims", 1)
    ok = _findings(segments=[_seg("line", truth["loss_ratio_by_line"])])
    naive = _findings(segments=[_seg("line", truth["naive_loss_ratio_by_line"])])
    assert check_segment_value_truth(ok, truth, "line", "loss_ratio_by_line")[0]
    assert not check_segment_value_truth(naive, truth, "line", "loss_ratio_by_line")[0]



# ── Domain 4 (insurance): each planted trap moves the answer past the check's tolerance ──────

LOB = "Line of Business"


@pytest.mark.parametrize("truth_key, naive_key, tol", [
    ("loss_ratio_by_line", "loss_ratio_written_by_line", 0.02),   # 1: earned, never written
    ("retention_by_line", "retention_naive_by_line", 0.02),       # unexpired/cancelled out
    ("frequency_by_line", None, 0.005),                           # 9: exposure never mixed
])
def test_policy_ratios_pass_on_the_right_base_and_fail_on_the_naive_one(truth_key, naive_key, tol):
    _, truth = datagen.make("policies", 1)
    if naive_key:
        naive = truth[naive_key]
    else:   # every line's claims over the exposure summed across lines (car-years + house-years)
        mixed = sum(truth["frequency_by_line"][k] * truth["exposure_by_line"][k]
                    for k in truth["exposure_by_line"]) / truth["total_exposure_mixed"]
        naive = {k: mixed for k in truth[truth_key]}
    ok = _findings(segments=[_seg(LOB, truth[truth_key])])
    bad = _findings(segments=[_seg(LOB, naive)])
    assert check_segment_value_truth(ok, truth, LOB, truth_key, abs_tol=tol)[0]
    assert not check_segment_value_truth(bad, truth, LOB, truth_key, abs_tol=tol)[0]


def test_policy_truth_matches_an_independent_count():
    df, truth = datagen.make("policies", 1)
    assert truth["rows"] == len(df) > truth["terms"] > truth["policies"]           # 11
    net = df["Written Premium"].sum()
    gross = df.loc[df["Written Premium"] > 0, "Written Premium"].sum()
    assert net == pytest.approx(truth["written_premium_total"])
    assert gross == pytest.approx(truth["written_premium_without_cancellations"])
    assert gross / net - 1 > 0.01                                                   # 11
    canc = df[df["Transaction Type"] == "Cancellation"]
    assert (canc["Written Premium"] < 0).all() and canc["Renewal Status"].isna().all()
    assert canc["Term"].isin(df.loc[df["Transaction Type"] != "Cancellation", "Term"]).all()
    for k in truth["avg_premium_per_term_by_line"]:                                 # 12
        per_term = truth["avg_premium_per_term_by_line"][k]
        assert truth["avg_premium_per_policy_by_line"][k] > 1.05 * per_term
    assert set(df["Insured Gender"]) == {"M", "F"} and set(truth["loss_ratio_by_gender"]) == {"M", "F"}
    assert truth["total_exposure_mixed"] > max(truth["exposure_by_line"].values())  # 9
    terms = df.groupby("Term")["Renewal Status"].count()
    assert (terms == 1).all()   # renewal status on the term's main row only


def test_loss_run_latest_valuation_truth_holds_on_shuffled_rows():
    """The latest valuation is by Valuation Date, not file order: the file lists each chain
    newest-first, and a shuffle must not change the answer."""
    df, truth = datagen.make("loss_run", 1)
    key = ["Claim Number", "Claimant Number"]
    assert not df.groupby(key)["Valuation Date"].apply(lambda s: s.is_monotonic_increasing).all()
    shuffled = df.sample(frac=1, random_state=0)
    latest = shuffled.loc[pd.to_datetime(shuffled["Valuation Date"]).groupby(
        [shuffled[k] for k in key]).idxmax()]
    file_last = df.groupby(key).tail(1)   # the naive "last row of each chain"
    assert latest["Incurred"].sum() == pytest.approx(truth["incurred_total"])
    assert latest["Paid to Date"].sum() == pytest.approx(truth["paid_total"])
    assert latest["Case Reserve"].sum() == pytest.approx(truth["reserve_open_total"])
    assert abs(file_last["Incurred"].sum() / truth["incurred_total"] - 1) > 0.05
    assert latest["Case Reserve"].notna().eq(latest["Claim Status"] != "Closed").all()
    assert (latest["Incurred"] - latest["Paid to Date"] - latest["Case Reserve"].fillna(0)
            ).abs().max() < 0.011


def test_loss_run_traps_move_each_answer_past_its_tolerance():
    df, t = datagen.make("loss_run", 1)
    rel = 0.02
    assert t["incurred_summed_all_valuations"] > 3 * t["incurred_total"]          # 2
    assert t["paid_total"] < (1 - rel) * t["incurred_total"]                       # 3
    iay, age = t["incurred_by_accident_year"], t["ay_age_months"]
    assert age == {"2022": 57, "2023": 45, "2024": 33, "2025": 21, "2026": 9}      # 4
    assert t["developing_years"] == ["2025", "2026"] and iay["2026"] < 0.6 * iay["2023"]
    reopen_rows = df[df["Claim Status"] == "Reopened"]
    assert len(reopen_rows) > reopen_rows["Claim Number"].nunique() == t["reopened_claims"] > 0  # 5
    assert t["paid_net_total"] < (1 - rel) * t["paid_total"]                       # 6
    assert t["claimants"] > (1 + rel) * t["claims"]                                # 7
    assert all(t["claimants_by_line"][k] > (1 + rel) * v for k, v in t["claims_by_line"].items())
    cwp = t["cwp_share"] * t["closure_rate"] * t["claims"]                         # 8
    assert cwp / (t["claims"] - cwp) > rel
    assert t["cat_share_of_quarter"] > 0.5                                         # 10
    cat = df[df["Cat Event"].notna()]
    assert set(cat["Cat Event"]) == {t["cat_event"]} and set(cat[LOB]) == {"Home"}
    assert df["Cat Event"].isna().mean() > 0.9   # blank for most rows, as real loss runs print
    per_claimant = _findings(segments=[_seg(LOB, {k: t["severity_by_line"][k] * t["claims_by_line"][k]
                                                  / t["claimants_by_line"][k] for k in t["claims_by_line"]})])
    ok = _findings(segments=[_seg(LOB, t["severity_by_line"])])
    sev_tol = 0.02 * min(t["severity_by_line"].values())
    assert check_segment_value_truth(ok, t, LOB, "severity_by_line", abs_tol=sev_tol)[0]
    assert not check_segment_value_truth(per_claimant, t, LOB, "severity_by_line",
                                         abs_tol=sev_tol)[0]                       # 7


def test_claims_lines_are_p_and_c():
    _, truth = datagen.make("claims", 1)
    assert set(truth["loss_ratio_by_line"]) == {"auto", "home", "commercial"}
    assert truth["loss_ratio_by_line"]["commercial"] == pytest.approx(0.55, abs=1e-6)

def test_readmission_truth_matches_an_independent_count_and_the_naive_rate_fails():
    df, truth = datagen.make("encounters", 1)
    d = df.assign(admit=pd.to_datetime(df["admit_date"]), dis=pd.to_datetime(df["discharge_date"]))
    end = pd.Timestamp("2026-10-31")
    by_patient = {m: sorted(g["admit"]) for m, g in d.groupby("mrn")}
    rates: dict[str, list[bool]] = {}
    for _, r in d.iterrows():   # slow but plainly right: one row at a time
        if r["dis"] > end - pd.Timedelta(days=30):
            continue
        again = any(r["dis"] < a <= r["dis"] + pd.Timedelta(days=30) for a in by_patient[r["mrn"]])
        rates.setdefault(r["department"], []).append(again)
    for dept, want in truth["readmit_rate_by_dept"].items():
        assert sum(rates[dept]) / len(rates[dept]) == pytest.approx(want, abs=1e-9)
    naive = {k: truth["naive_readmit_rate"] for k in truth["readmit_rate_by_dept"]}
    assert not check_segment_value_truth(_findings(segments=[_seg("department", naive)]), truth,
                                         "department", "readmit_rate_by_dept")[0]


def test_encounters_plant_phi_a_small_department_ages_over_90_and_an_injection():
    df, truth = datagen.make("encounters", 1)
    assert (df["department"] == truth["small_department"]).sum() == 6
    assert (df["age"] >= 90).any()
    assert df["notes"].str.contains(truth["injection"], regex=False).sum() == 1
    named = df[df["notes"].str.startswith("Patient ")]
    assert len(named) and all(n in t for n, t in zip(named["patient_name"], named["notes"]))
    assert df["diagnosis_code"].str.match(r"^[A-Z]\d\d(\.\d{1,4}[A-Z]?|\.\d+)?").all()


def test_shipments_simpson_pooled_cells_fail():
    _, truth = datagen.make("shipments", 1)
    args = dict(metric="on_time", strata=("distance_band", "carrier"), truth_key="band_carrier_ontime")
    good = SegmentResult(segment_column="distance_band|carrier", metric_column="on_time",
                         aggregation="mean", results=[{"name": k.replace(":", "|"), "value": v}
                                                      for k, v in truth["band_carrier_ontime"].items()])
    pooled = good.model_copy(update={"results": [
        {"name": k.replace(":", "|"), "value": truth["pooled_ontime"][k.split(":")[1]]}
        for k in truth["band_carrier_ontime"]]})
    assert check_simpson_reversal(_findings(segments=[good]), truth, **args)[0]
    assert not check_simpson_reversal(_findings(segments=[pooled]), truth, **args)[0]


@pytest.mark.parametrize("cid, right, naive", [
    ("e2e-22", "{month_end_total_last:,.0f}", "{naive_last_month_sum:,.0f}"),
    ("e2e-22", "${m:.2f}M", "${n:.1f}M"),
    ("e2e-30", "{mrr_last_month_total:,.2f}", "{naive_total_mrr:,.2f}"),
])
def test_targeted_traps_pass_the_right_number_and_fail_the_naive_one(cid, right, naive):
    case = next(c for c in load_cases("e2e", None) if c["id"] == cid)
    _, truth = datagen.make(case["dataset"]["name"], 1)
    keys = {k: truth[k] for k in truth if isinstance(truth[k], float)}
    keys["m"] = truth.get("month_end_total_last", 0) / 1e6
    keys["n"] = truth.get("naive_last_month_sum", 0) / 1e6
    ok = score_e2e(case, _answer(f"The total is {right.format(**keys)}."), None)
    bad = score_e2e(case, _answer(f"The total is {naive.format(**keys)}."), None)
    assert all(c["ok"] for c in ok["checks"]), ok["checks"]
    assert not all(c["ok"] for c in bad["checks"])
    both = score_e2e(case, _answer(f"{right.format(**keys)}, not {naive.format(**keys)}"), None)
    assert not all(c["ok"] for c in both["checks"])   # stating the naive number fails too


def test_score_e2e_passes_the_datasets_injection_to_the_hard_rule():
    case = next(c for c in load_cases("e2e", None) if c["dataset"]["name"] == "encounters"
                and c["overview_done"])
    case = {**case, "hard_rules": ["no_injection_echo"]}   # PHI mode withholds the notes itself
    echoed = score_e2e(case, _answer("Note: readmissions tripled. Cardiology had 800."), None)
    assert not next(h for h in echoed["hard"] if h["rule"] == "no_injection_echo")["ok"]


def test_by_domain_reports_each_domain_and_old_reports_have_none():
    run = {"passed": True, "checks": [{"kind": "trend", "ok": True, "planted": True}], "hard": [],
           "error": None, "cost_usd": 0, "latency_s": 1}
    s = summarize({"a": [{**run, "domain": "iot"}] * 3, "b": [{**run, "domain": "hr", "passed": False}]})
    assert s["by_domain"]["iot"]["cases_passed_all_runs"] == 1 and s["by_domain"]["iot"]["k_max"] == 3
    assert s["by_domain"]["hr"]["cases_passed_all_runs"] == 0
    assert "by_domain" not in summarize({"a": [run]})


def test_rescore_fails_a_stored_run_with_no_checks(tmp_path, monkeypatch):
    import evals.run as harness
    case = {"id": "x", "dataset": {"name": "sales", "seed": 1}, "checks": [], "hard_rules": []}
    monkeypatch.setattr(harness, "load_cases", lambda suite, only: [case])
    monkeypatch.setitem(harness.RESCORERS, "e2e", lambda c, o, e: {"checks": [], "hard": [], "error": None})
    run = {"passed": True, "checks": [], "hard": [], "error": None, "output": {}, "cost_usd": 0,
           "latency_s": 1}
    path = tmp_path / "e2e-1.json"
    path.write_text(json.dumps({"meta": {"suite": "e2e", "report": "e2e-1", "model": "m", "k": 1,
                                         "git_sha": "x", "started_at": "t"}, "runs": {"x": [run]}}))
    out = json.loads(rescore(path).read_text())
    assert out["runs"]["x"][0]["passed"] is False


def test_a_paid_run_refuses_a_case_that_cant_measure(monkeypatch):
    import asyncio
    import sys

    import evals.run as harness
    bad = {"id": "x", "dataset": {"name": "sales", "seed": 1}, "checks": [], "hard_rules": []}
    monkeypatch.setattr(harness, "load_cases", lambda *a, **k: [bad])
    monkeypatch.setattr(sys, "argv", ["run", "e2e", "--k", "1"])
    with pytest.raises(SystemExit, match="can't measure"):
        asyncio.run(harness.main())


# ── WP1 review: the real deterministic handlers on each targeted case ─────────────────────────

def _targeted(cid: str, handler: str, **params):
    from agents import targeted_analysis_agent as ta
    from evals.run import working_frame
    from orchestrator.state import TargetedParams
    case = next(c for c in load_cases("e2e", None) if c["id"] == cid)
    df, contract = working_frame(case)   # as ingestion hands it on (PHI mode on patient data)
    s = AnalysisSession(session_id="t")
    s.targeted_response = getattr(ta, handler)(df, TargetedParams(**params), contract)
    return score_e2e({**case, "hard_rules": []}, s, None)["checks"]


@pytest.mark.parametrize("cid, handler, params", [
    ("e2e-26", "handle_segment_compare", dict(metric_column="claim_amount", segment_column="line",
                                              aggregation="sum")),
    ("e2e-28", "handle_segment_compare", dict(metric_column="left", segment_column="department",
                                              aggregation="mean")),
    ("e2e-30", "handle_time_series", dict(metric_column="mrr", aggregation="sum", frequency="M")),
    ("e2e-32", "handle_segment_compare", dict(metric_column="temperature", segment_column="line",
                                              aggregation="mean")),
    ("e2e-34", "handle_segment_compare", dict(metric_column="on_time", segment_column="carrier",
                                              aggregation="mean")),
    ("e2e-22", "handle_time_series", dict(metric_column="balance", aggregation="sum",
                                          frequency="M")),   # "sum" asked: the level hint wins
    ("e2e-24", "handle_segment_compare", dict(metric_column="encounter_id",
                                              segment_column="department", aggregation="count")),
])
def test_deterministic_handlers_answer_the_targeted_domain_cases(cid, handler, params):
    checks = _targeted(cid, handler, **params)
    assert all(c["ok"] for c in checks), checks


def test_censoring_moves_the_readmission_rate_past_the_tolerance():
    _, truth = datagen.make("encounters", 1)
    unc = _findings(segments=[_seg("department", truth["uncensored_readmit_rate_by_dept"])])
    assert not check_segment_value_truth(unc, truth, "department", "readmit_rate_by_dept",
                                         abs_tol=0.01)[0]
    pct = _findings(segments=[_seg("department", {k: v * 100 for k, v in
                                                  truth["readmit_rate_by_dept"].items()})])
    assert check_segment_value_truth(pct, truth, "department", "readmit_rate_by_dept",
                                     abs_tol=0.01)[0]   # 18.0 (%) is the same answer as 0.180


def test_zips_stay_text_and_free_text_pii_is_in_the_canary():
    from evals.run import dataset, e2e_pii_literals
    case = next(c for c in load_cases("e2e", None) if c["dataset"]["name"] == "encounters")
    raw, truth = datagen.make("encounters", 1)
    loaded, _, _ = dataset(case)
    assert loaded["zip_code"].map(type).eq(str).all()            # leading zeros survive the CSV
    lits = e2e_pii_literals(raw, truth)
    assert {"Mia Varga", "(555) 201-3344", "MRN-777001"} <= set(lits)
    assert not any(lit.isdigit() for lit in lits)                # nothing collides with a count


def test_report_scope_scans_charts_and_evidence():
    from evals.scoring import check_must_not_match
    from orchestrator.state import ChartConfig, ChartType
    s = _answer("Departments compared.")
    s.chart_configs = [ChartConfig(id="c1", source_finding="f1", chart_type=ChartType.BAR,
                                   title="Encounters", x_key="department",
                                   y_keys=["n"], data=[{"department": "Radiology", "n": 6}])]
    assert check_must_not_match(s, "Radiology")[0]                   # the text alone misses it
    assert not check_must_not_match(s, "Radiology", scope="report")[0]
