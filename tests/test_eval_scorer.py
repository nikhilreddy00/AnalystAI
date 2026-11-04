"""Phase 5.5 WP0: the scorer can't pass a run that measured nothing or answered the trap's naive
number. Each test names the fake-pass path it closes."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals import datagen
from evals.run import _explicit_error_ok, lint_case, load_cases, passed
from evals.scoring import (
    check_anomaly_rows,
    check_must_not_number,
    check_number_in_answer,
    check_simpson_reversal,
    hard_rule,
)
from orchestrator.state import (
    AnalysisFindings,
    AnalysisSession,
    QuestionIntent,
    SegmentResult,
    TargetedResponse,
)

REPORTS = Path(__file__).parent.parent / "evals" / "reports"


def _findings(**kw) -> AnalysisFindings:
    base = dict(eda_summary={}, correlations={}, trends=[], segments=[], anomalies=[],
                statistical_tests=[], forecast_points=[], distributions={}, code_executed="")
    return AnalysisFindings(**{**base, **kw})


def _answer(text: str) -> AnalysisSession:
    s = AnalysisSession(session_id="t")
    s.targeted_response = TargetedResponse(intent=QuestionIntent.SEGMENT_COMPARE, answer_text=text)
    return s


def test_no_checks_is_a_fail_not_a_pass():
    assert not passed([])
    assert passed([{"ok": True}]) and not passed([{"ok": True}, {"ok": False}])


def test_anomaly_rows_with_no_planted_rows_fails_instead_of_dividing_by_zero():
    f = _findings()
    assert check_anomaly_rows(f, {"anomaly_rows": []}, "revenue") == (
        False, "truth has no planted anomaly rows")


def test_insight_rules_fail_when_an_overview_has_no_report():
    df, _ = datagen.make_sales(1)
    ok, detail = hard_rule("insight_grounded", df, None, AnalysisSession(session_id="t"))
    assert not ok and detail == "no insight report"
    # …unless the run stopped with an explicit error (its checks fail instead)
    hard = [{"rule": "insight_grounded", "ok": False, "detail": "no insight report"}]
    assert _explicit_error_ok(hard, "boom")[0]["ok"]
    assert not _explicit_error_ok(hard, None)[0]["ok"]


def test_number_in_answer_ignores_date_fragments():
    truth = {"x": 22.0}
    assert not check_number_in_answer(_answer("Data up to 2026-01-22."), truth, "x")[0]
    assert check_number_in_answer(_answer("It is 22 now."), truth, "x")[0]


def test_number_in_answer_with_unit_needs_the_unit():
    truth = {"x": 12.6}
    assert not check_number_in_answer(_answer("12.6 orders"), truth, "x", unit="%")[0]
    assert check_number_in_answer(_answer("fell 12.6%"), truth, "x", unit="%")[0]
    assert check_number_in_answer(_answer("fell 12.6 %"), truth, "x", unit="%")[0]


def test_must_not_number_catches_the_naive_answer():
    truth = {"naive": 4_123_000.0}
    assert not check_must_not_number(_answer("Total 4,123,000"), truth, "naive")[0]
    assert check_must_not_number(_answer("Total 1,030,000"), truth, "naive")[0]


def test_simpson_reversal_takes_its_strata_and_truth():
    want = {"Short:X": 0.9, "Short:Y": 0.8, "Long:X": 0.7, "Long:Y": 0.6}
    seg = SegmentResult(segment_column="band|carrier", metric_column="on_time", aggregation="mean",
                        results=[{"name": f"{k.split(':')[0]}|{k.split(':')[1]}", "value": v}
                                 for k, v in want.items()])
    f = _findings(segments=[seg])
    args = dict(metric="on_time", strata=("band", "carrier"), truth_key="cells")
    assert check_simpson_reversal(f, {"cells": want}, **args)[0]
    assert not check_simpson_reversal(f, {"cells": {**want, "Long:X": 0.1}}, **args)[0]


def test_injection_echo_uses_the_datasets_own_claim():
    df, _ = datagen.make_sales(1)
    s = _answer("The readmission rate tripled.")
    assert hard_rule("no_injection_echo", df, None, s)[0]
    assert not hard_rule("no_injection_echo", df, None, s,
                         truth={"injection_claim": "readmission rate tripled", "injection": "x"})[0]


@pytest.mark.parametrize("case, problem", [
    ({"checks": []}, "no checks"),
    ({"checks": [{"kind": "correlation_absent", "a": "revenue", "b": "discount"}]},
     "correlation_absent without pair_reported"),
    ({"question": "Is it 47?", "checks": [{"kind": "number_in_answer", "truth_key": "x"}]},
     "the truth appears in the question"),
    ({"checks": [{"kind": "number_in_answer", "truth_key": "d"}]}, "needs a unit"),
    ({"trap": True, "checks": [{"kind": "number_in_answer", "truth_key": "x"}]},
     "trap case without a negative check"),
    ({"trap": True, "checks": [{"kind": "number_in_answer", "truth_key": "x"},
                               {"kind": "must_not_number", "truth_key": "near"}]}, "within 3×rel_tol"),
])
def test_lint_rejects_cases_that_cant_measure(monkeypatch, case, problem):
    monkeypatch.setattr(datagen, "make", lambda n, s: (None, {"x": 47.0, "d": 12.0, "near": 47.5}))
    found = lint_case({"dataset": {"name": "n", "seed": 1}, "question": "q", **case})
    assert any(problem in p for p in found), found


def test_every_committed_case_passes_the_lint():
    bad = {c["id"]: p for suite in ("analysis", "e2e") for c in load_cases(suite, None)
           if (p := lint_case(c))}
    assert not bad


def test_no_committed_report_has_a_run_without_checks():
    """The empty-checks fix moves no committed number: no run in any report had zero checks."""
    for path in REPORTS.glob("*.json"):
        for runs in json.loads(path.read_text()).get("runs", {}).values():
            assert all(r.get("checks") for r in runs), path.name


def test_canary_literals_come_from_the_generators_pii_list():
    import pandas as pd

    from evals.run import e2e_pii_literals
    df = pd.DataFrame({"patient_name": ["Ann Lee", None], "customer_email": ["a@x.io", "b@x.io"]})
    assert e2e_pii_literals(df, {"pii_columns": ["patient_name"]}) == ["Ann Lee"]
    assert e2e_pii_literals(df) == ["a@x.io", "b@x.io"]   # sales default unchanged


def test_report_dates_use_the_contracts_time_axis_not_a_birth_date():
    import pandas as pd

    from connectors.csv_connector import CSVConnector
    from evals.scoring import _date_cols
    from orchestrator.state import ConnectorConfig, ConnectorType
    df = pd.DataFrame({"date_of_birth": pd.to_datetime(["1950-01-01", "1960-01-01"] * 5),
                       "admit_date": pd.to_datetime(["2026-01-01", "2026-02-01"] * 5),
                       "cost": range(10)})
    contract = CSVConnector(ConnectorConfig(type=ConnectorType.CSV)).build_data_contract(df)
    assert _date_cols(df) == ["date_of_birth"]          # the old name rule picks the DOB…
    assert _date_cols(df, contract) == ["admit_date"]   # …the contract's non-PII dates don't


# ── WP0 review findings (each test fails on the code the review read) ─────────────────────────

@pytest.mark.parametrize("text, want", [
    ("summed balance $4.1M", 4_100_000), ("4.12 million", 4_120_000), ("$1.2bn", 1.2e9),
    ("12k orders", 12_000)])
def test_magnitudes_are_read(text, want):   # review #1
    assert check_number_in_answer(_answer(text), {"x": want}, "x", rel_tol=0.01)[0]
    assert not check_must_not_number(_answer(text), {"x": want}, "x", rel_tol=0.01)[0]


@pytest.mark.parametrize("text", ["at 2026-01-22T00:00:00Z", "on 2026-01-22 10:30", "on Jan 22",
                                  "since March 22nd"])
def test_datetimes_and_month_days_are_not_numbers(text):   # review #3
    assert not check_number_in_answer(_answer(text), {"x": 22.0}, "x")[0]


@pytest.mark.parametrize("text", ["12.6 percent", "12.64 per cent", "**12.6**%", "−12.6%",
                                  "(-12.6%)", "12.6 %"])
def test_percent_written_any_common_way(text):   # review #4
    assert check_number_in_answer(_answer(text), {"x": -12.6}, "x", rel_tol=0.01, unit="%")[0]


def test_an_error_does_not_excuse_a_delivered_report():   # review #5
    hard = [{"rule": "insight_grounded", "ok": False, "detail": "ungrounded: 41%"}]
    assert not _explicit_error_ok(hard, "cancelled")[0]["ok"]


def test_insight_rules_cannot_pass_without_a_session():   # review #6
    df, _ = datagen.make_sales(1)
    assert not hard_rule("insight_grounded", df, None, None)[0]


def test_injection_claim_matches_in_any_case():   # review #7
    df, _ = datagen.make_sales(1)
    s = _answer("readmissions tripled")
    assert not hard_rule("no_injection_echo", df, None, s,
                         truth={"injection_claim": "Readmissions Tripled", "injection": "x"})[0]


def test_canary_refuses_a_misspelled_pii_column():   # review #8
    import pandas as pd

    from evals.run import e2e_pii_literals
    with pytest.raises(ValueError, match="lacks"):
        e2e_pii_literals(pd.DataFrame({"name": ["a"]}), {"pii_columns": ["patient_name"]})


def test_table_keys_are_not_numbers():   # review #11
    s = _answer("")
    s.targeted_response.table_data = [{"significant_at_0.05": True, "group": "B"}]
    assert not check_number_in_answer(s, {"x": 0.05}, "x")[0]


@pytest.mark.parametrize("case, problem", [
    ({"checks": [{"kind": "must_not_number", "truth_key": "x"}]}, "only negative checks"),
    ({"question": "Is the rate 40%?", "checks": [{"kind": "number_in_answer", "truth_key": "r",
                                                  "scales": [100]}]}, "appears in the question"),
    ({"checks": [{"kind": "number_in_answer", "truth_key": "y"}]}, "1900-2100 needs a unit"),
    ({"checks": [{"kind": "number_in_answer", "truth_key": "x"},
                 {"kind": "must_not_number", "truth_key": "d"}]}, "needs a unit"),
    ({"checks": [{"kind": "eda_stat", "column": "c", "stats": ["mean"]}], "task": {},
      "hard_rules": ["insight_grounded"]}, "insight rules on an analysis case"),
    ({"checks": [{"kind": "eda_stat", "column": "c", "stats": ["mean"]}],
      "hard_rules": ["no_injection_echo"]}, "no agent sees the planted injection"),
])
def test_lint_review_cases(monkeypatch, case, problem):   # review #2, #3c, #6, #7, #10
    import pandas as pd
    monkeypatch.setattr(datagen, "make", lambda n, s: (pd.DataFrame({"c": ["x"]}),
                                                       {"x": 47.0, "d": 12.0, "r": 0.4, "y": 2000.0}))
    found = lint_case({"dataset": {"name": "n", "seed": 1}, "question": "q", **case})
    assert any(problem in p for p in found), found


def test_lint_accepts_a_reversed_pair_and_a_year_in_the_question(monkeypatch):   # review #10
    import pandas as pd
    monkeypatch.setattr(datagen, "make", lambda n, s: (pd.DataFrame({"c": [datagen.INJECTION]}),
                                                       {"x": 47.0}))
    # Two cases: analysis and E2E check kinds never mix in one (the lint rejects unknown kinds)
    pair = {"dataset": {"name": "n", "seed": 1}, "task": {}, "question": "q",
            "checks": [{"kind": "correlation_absent", "a": "p", "b": "q"},
                       {"kind": "pair_reported", "a": "q", "b": "p"}]}
    year = {"dataset": {"name": "n", "seed": 1}, "question": "Compare 2012 with now",
            "checks": [{"kind": "number_in_answer", "truth_key": "x"}]}
    assert lint_case(pair) == [] and lint_case(year) == []


# ── WP1 review findings ────────────────────────────────────────────────────────────────────

def test_a_rate_may_be_written_bare_or_as_percent_but_not_as_a_bare_25():
    scales = [[1, None], [100, "%"]]
    assert check_number_in_answer(_answer("Sales leads at 0.25"), {"x": 0.249}, "x", scales=scales)[0]
    assert check_number_in_answer(_answer("Sales: 24.9%"), {"x": 0.249}, "x", scales=scales)[0]
    assert not check_number_in_answer(_answer("Sales has 25 people"), {"x": 0.249}, "x",
                                      scales=scales)[0]


def test_must_not_number_takes_a_unit():
    truth = {"x": 28.3}
    assert not check_must_not_number(_answer("balances grew 28.3%"), truth, "x", unit="%")[0]
    assert check_must_not_number(_answer("28.3 accounts"), truth, "x", unit="%")[0]


def test_month_day_does_not_eat_a_decimal():
    assert check_number_in_answer(_answer("Mar 12.6% lower"), {"x": 12.6}, "x", unit="%")[0]


def test_leak_matching_is_case_insensitive_and_numbers_are_whole():
    from evals.scoring import leaked
    assert leaked("PATIENT ANA ITO", ["Ana Ito"]) == ["Ana Ito"]
    assert leaked("cost 15247.33", ["15247"]) == []
    assert leaked("zip 15247.", ["15247"]) == ["15247"]


def test_canary_is_na_without_planted_pii_and_counts_what_the_model_got():
    from evals.run import _canary_rule
    assert _canary_rule(0, 0, 0)["na"]
    assert not _canary_rule(0, 1, 5)["ok"]        # the guard let a value through
    assert not _canary_rule(2, 0, 5)["ok"]        # a builder leaked (the guard caught it)
    assert _canary_rule(0, 0, 5)["ok"]


def test_lint_sees_wrapped_negatives_and_numpy_truths(monkeypatch):
    import numpy as np
    import pandas as pd
    monkeypatch.setattr(datagen, "make", lambda n, s: (pd.DataFrame({"c": ["x"]}),
                                                       {"x": np.int64(12)}))
    only_neg = {"dataset": {"name": "n", "seed": 1}, "question": "q",
                "checks": [{"kind": "findings", "check": "correlation_absent", "a": "p", "b": "q"}]}
    assert any("only negative" in p for p in lint_case(only_neg))
    unitless = {"dataset": {"name": "n", "seed": 1}, "question": "q",
                "checks": [{"kind": "number_in_answer", "truth_key": "x"}]}
    assert any("needs a unit" in p for p in lint_case(unitless))


def test_by_domain_shows_short_runs():
    from evals.run import summarize
    run = {"passed": True, "checks": [{"kind": "trend", "ok": True}], "hard": [], "error": None,
           "cost_usd": 0, "latency_s": 1, "domain": "iot"}
    s = summarize({"a": [run] * 3, "b": [run]})
    assert (s["by_domain"]["iot"]["k_min"], s["by_domain"]["iot"]["k_max"]) == (1, 3)


def test_e2e_runs_without_a_domain_get_none():
    from evals.run import summarize
    run = {"passed": True, "checks": [{"kind": "x", "ok": True}], "hard": [], "error": None,
           "cost_usd": 0, "latency_s": 1}
    assert "by_domain" not in summarize({"a": [run]})


# ── final audit 1 (eval) ────────────────────────────────────────────────────────────────────

def test_a_derived_metric_on_a_text_column_neither_crashes_nor_counts_false():
    import pandas as pd

    from evals.scoring import false_findings
    df = pd.DataFrame({"department": ["A", "B"] * 10, "mrn": [f"ID-{i}" for i in range(20)],
                       "claim_amount": range(20)})
    seg = SegmentResult(segment_column="department", metric_column="mrn",
                        aggregation="readmission_rate", results=[{"name": "A", "value": 0.2}])
    ratio = SegmentResult(segment_column="department", metric_column="claim_amount",
                          aggregation="loss_ratio", results=[{"name": "A", "value": 0.9}])
    assert false_findings(_findings(segments=[seg, ratio]), df, {})["false"] == 0


async def test_a_harness_exception_fails_one_run_not_the_report(monkeypatch):
    import asyncio

    import evals.run as harness

    async def boom(case):
        raise TypeError("scorer broke")
    monkeypatch.setitem(harness.__dict__, "run_analysis_case", boom)
    out = await harness.run_once("analysis", {"id": "x", "domain": "hr"}, asyncio.Semaphore(1))
    assert out["passed"] is False and "TypeError" in out["error"]


def test_trap_numbers_in_finding_bullets_are_seen():
    from orchestrator.state import Claim, ExecutiveSummary, InsightReport
    s = AnalysisSession(session_id="t")
    s.insight_report = InsightReport(
        executive_summary="Balances rose.", technical_findings=[], recommended_actions=[],
        anomaly_alerts=[], proactive_questions=[], methodology_notes="",
        executive_view=ExecutiveSummary(headline="Up", findings=[Claim(
            text="Business balances total $113,217,218.45", evidence_ids=["x"])]))
    assert not check_must_not_number(s, {"n": 113_217_218.45}, "n", rel_tol=0.01)[0]


@pytest.mark.parametrize("text, wrong", [("A 69.95\nB 70.62", 69.95e9), ("10/31/2026", 31.0),
                                         ("on 31 Oct 2026", 31.0)])
def test_no_magnitude_across_lines_and_no_numbers_from_dates(text, wrong):
    from evals.scoring import answer_numbers
    assert wrong not in answer_numbers(_answer(text))


def test_unseen_e2e_cases_are_priced_by_their_kind(tmp_path):
    import shutil
    from pathlib import Path

    from evals.run import estimate_usd
    cases = [c["id"] for c in load_cases("e2e", None) if int(c["id"].split("-")[1]) >= 21]
    for p in (Path(__file__).parent.parent / "evals" / "reports").glob("e2e-*.json"):
        if not set(cases) & set(json.loads(p.read_text())["runs"]):   # reports from before they ran
            shutil.copy(p, tmp_path)
    assert estimate_usd("e2e", len(cases), tmp_path, cases, 1) > 0.8   # 7 overviews + 7 targeted,
    # not 14 × the average


def test_a_mean_over_the_rows_a_pack_counts_is_not_a_false_finding():
    """Domain 3 smoke run: mean paid per payer over each claim line's last version (what the
    tiles count) was scored false against every row, replaced versions included."""
    from domain_pack import rows_for
    from evals import datagen
    from evals.scoring import false_findings
    from orchestrator.state import AnalysisFindings, SegmentResult
    from agents.ingestion_agent import build_contract
    from orchestrator.state import ConnectorType
    df, truth = datagen.make("health_claims", 1)
    counted, _ = rows_for(df, build_contract(df, ConnectorType.CSV, {}), "Paid Amount")
    means = counted.groupby("Payer")["Paid Amount"].mean()
    f = AnalysisFindings(eda_summary={}, correlations={}, trends=[], anomalies=[],
                         statistical_tests=[], forecast_points=[], distributions={}, code_executed="",
                         segments=[SegmentResult(segment_column="Payer", metric_column="Paid Amount",
                                                 aggregation="mean", results=[
                                                     {"name": k, "value": float(v)} for k, v in means.items()])])
    r = false_findings(f, df, truth)
    assert r["verifiable"] == 5 and r["false"] == 0, r


def test_a_column_named_with_a_slash_is_one_column():
    """Domain 3 analysis gate: 'CPT/HCPCS' was split into 'CPT' and 'HCPCS' (unknown columns)."""
    import pandas as pd
    from evals.scoring import hard_rule
    from orchestrator.state import AnalysisFindings, SegmentResult
    df = pd.DataFrame({"CPT/HCPCS": ["99213", "J1100"], "Paid Amount": [1.0, 2.0]})
    f = AnalysisFindings(eda_summary={}, correlations={}, trends=[], anomalies=[], statistical_tests=[],
                         forecast_points=[], distributions={}, code_executed="",
                         segments=[SegmentResult(segment_column="CPT/HCPCS", metric_column="Paid Amount",
                                                 aggregation="sum", results=[{"name": "99213", "value": 1.0}])])
    assert hard_rule("no_unknown_columns", df, f)[0]


def test_a_removal_notice_is_not_the_forbidden_claim():
    """Domain 3 re-measure (e2e-35): the product removed a causal sentence and said so; the
    notice quoting 'a promotion or discount caused' failed a must_not_match."""
    from evals.scoring import check_must_not_match
    from orchestrator.state import AnalysisSession, TargetedResponse, QuestionIntent
    s = AnalysisSession(session_id="t")
    s.targeted_response = TargetedResponse(intent=QuestionIntent.OVERVIEW, answer_text=(
        "Sales rose 4%. 1 sentence(s) saying a promotion or discount caused a change were removed: "
        "without a control group that can't be shown."))
    assert check_must_not_match(s, "promotion or discount caused")[0]
    s.targeted_response.answer_text = "The promotion or discount caused sales to rise."
    assert not check_must_not_match(s, "promotion or discount caused")[0]


def test_a_derived_group_is_matched_by_any_of_its_names_as_words():
    """Domain 4: incurred by accident year: the model derives the year and names it itself."""
    from evals.scoring import check_segment_value_truth
    truth = {"ay": {"2025": 10.0, "2026": 4.0}}
    seg = SegmentResult(segment_column="accident_year", metric_column="Incurred",
                        aggregation="sum", results=[{"name": "2025", "value": 10.0},
                                                    {"name": "2026", "value": 4.0}])
    f = _findings(segments=[seg])
    assert check_segment_value_truth(f, truth, ["Accident Year", "AY"], "ay", 0.5, (1,))[0]
    assert not check_segment_value_truth(f, truth, "Loss Date", "ay", 0.5, (1,))[0]
    assert check_segment_value_truth(_findings(segments=[seg.model_copy(
        update={"segment_column": "Accident Year"})]), truth, "accident year", "ay", 0.5, (1,))[0]


def test_summary_stats_over_the_counted_rows_are_not_false():
    """Domain 4 smoke run: a loss run's built-in summary of Paid to Date reads each claimant's
    latest valuation (as every tile does); the scorer judged it against all 16 valuations and
    called 17 of 27 stats false. A summary over a population production counts is right; a
    wrong number is still false."""
    from domain_pack import rows_for
    from agents.ingestion_agent import build_contract
    from evals.scoring import false_findings
    from orchestrator.state import ConnectorType
    df, truth = datagen.make("loss_run", 1)
    counted = rows_for(df, build_contract(df, ConnectorType.CSV, {}), "Paid to Date")[0]
    s = counted["Paid to Date"]
    good = {"mean": float(s.mean()), "median": float(s.median()), "count": int(s.count())}
    r = false_findings(_findings(eda_summary={"Paid to Date": good}), df, truth)
    assert r["false"] == 0 and r["verifiable"] == 3, r
    r = false_findings(_findings(eda_summary={"Paid to Date": {**good, "mean": 1.0}}), df, truth)
    assert r["false"] == 1, r


def test_numbers_the_domain_pack_states_with_the_report_are_grounded():
    """Domain 4 smoke (e2e-61): every report carries the pack's caveats verbatim ('2025 (21
    months) are still developing … beyond 24 months'); the scorer's pool lacked them and failed
    the product's own deterministic text."""
    import sys
    sys.path.insert(0, "tests")
    from test_oracle import _built
    from evals.scoring import bundle_facts, ungrounded_numbers
    s, clean, _, _ = _built("loss_run")
    pool = bundle_facts(s, clean)["values"]
    text = "2026 (9 months) and 2025 (21 months) are still developing (beyond 24 months)."
    assert ungrounded_numbers(text, [pool]) == []
    assert ungrounded_numbers("2025 (37 months) are developing", [pool]) == ["37"]


def test_an_overall_row_beside_the_groups_is_the_whole_populations_value():
    """Domain 4 smoke (ins-6): 'Overall 33,674,954.88' after Auto and Home is the counted total,
    not a false group; a wrong overall is still false."""
    from evals.scoring import false_findings
    df, truth = datagen.make("loss_run", 1)

    def seg(total):
        return SegmentResult(segment_column="Line of Business", metric_column="Incurred",
                             aggregation="sum", results=[{"name": "Overall", "value": total}])
    assert false_findings(_findings(segments=[seg(truth["incurred_total"])]), df, truth)["false"] == 0
    assert false_findings(_findings(segments=[seg(1.0)]), df, truth)["false"] == 1
