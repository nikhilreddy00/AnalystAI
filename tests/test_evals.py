from __future__ import annotations

import pandas as pd
import pytest

from evals import datagen
from evals.run import compare, estimate_usd, load_cases, summarize
from evals.scoring import (
    ANALYSIS_CHECKS,
    E2E_CHECKS,
    _truth_value,
    check_anomaly_rows,
    check_correlation,
    check_correlation_absent,
    check_eda_stat,
    check_number_in_answer,
    check_pair_reported,
    check_segment_trend,
    check_segment_value,
    check_simpson_reversal,
    check_trend,
    false_findings,
    hard_rule,
)
from orchestrator.state import (
    AnalysisFindings,
    AnalysisSession,
    AnomalyRecord,
    QuestionIntent,
    SegmentResult,
    Severity,
    StatisticalResult,
    TargetedResponse,
)


def _findings(**kw) -> AnalysisFindings:
    base = dict(eda_summary={}, correlations={}, trends=[], segments=[], anomalies=[],
                statistical_tests=[], forecast_points=[], distributions={}, code_executed="")
    return AnalysisFindings(**{**base, **kw})


@pytest.fixture(scope="module")
def sales():
    return datagen.make_sales(1)


def test_datagen_is_deterministic_per_seed():
    a, ta = datagen.make_sales(3)
    b, tb = datagen.make_sales(3)
    pd.testing.assert_frame_equal(a, b)
    assert ta == tb
    assert not a.equals(datagen.make_sales(4)[0])


def test_sales_planted_truths(sales):
    df, truth = sales
    assert truth["segB_mom_pct"] == pytest.approx(-12.0, abs=0.01)
    assert truth["duplicate_rows"] >= 20
    assert abs(truth["corr_revenue_discount"]) < 0.1            # null effect
    assert truth["corr_revenue_marketing"] > 0.4                # confounded but visible
    assert (df["notes"] == datagen.INJECTION).sum() == 1
    assert df["customer_email"].str.endswith("@example.com").all()


def test_simpson_reverses_when_pooled():
    _, t = datagen.make_simpson(1)
    assert t["pooled_conversion"]["treatment"] < t["pooled_conversion"]["control"]
    for dept in ("Easy", "Hard"):
        assert t["dept_conversion"][f"{dept}:treatment"] > t["dept_conversion"][f"{dept}:control"]


def test_eda_and_segment_checks_pass_on_truth_and_fail_on_wrong(sales):
    df, _ = sales
    s = df["revenue"]
    good = _findings(
        eda_summary={"revenue": {"mean": s.mean(), "median": s.median()}},
        segments=[SegmentResult(segment_column="segment", metric_column="revenue", aggregation="sum",
                                results=[{"name": k, "value": v}
                                         for k, v in df.groupby("segment")["revenue"].sum().items()])],
    )
    assert check_eda_stat(good, df, "revenue", ["mean", "median"])[0]
    assert check_segment_value(good, df, "segment", "revenue")[0]

    bad = _findings(
        eda_summary={"revenue": {"mean": s.mean() * 1.1, "median": s.median()}},
        segments=[SegmentResult(segment_column="segment", metric_column="revenue", aggregation="mean",
                                results=[{"name": "Z", "value": 1.0}])],
    )
    assert not check_eda_stat(bad, df, "revenue", ["mean"])[0]
    assert "invented group" in check_segment_value(bad, df, "segment", "revenue")[1]


def test_correlation_checks(sales):
    df, truth = sales
    r = truth["corr_revenue_marketing"]
    f = _findings(correlations={"marketing_spend__revenue": r, "revenue__discount": 0.35})
    assert check_correlation(f, df, "revenue", "marketing_spend")[0]
    assert not check_correlation(_findings(), df, "revenue", "marketing_spend")[0]
    assert not check_correlation_absent(f, df, "revenue", "discount")[0]
    assert check_correlation_absent(_findings(), df, "revenue", "discount")[0]


def test_anomaly_recall(sales):
    _, truth = sales
    rows = truth["anomaly_rows"]
    hit = _findings(anomalies=[AnomalyRecord(column="revenue", row_index=i, value=1, expected_range="",
                                             severity=Severity.HIGH, explanation="") for i in rows[:3]])
    assert check_anomaly_rows(hit, truth, "revenue", min_recall=0.6)[0]
    assert not check_anomaly_rows(hit, truth, "revenue", min_recall=0.8)[0]


def test_number_in_answer_and_hard_rules(sales):
    df, truth = sales
    s = AnalysisSession(session_id="x")
    s.targeted_response = TargetedResponse(intent=QuestionIntent.TIME_SERIES,
                                           answer_text="Segment B revenue fell 12.0% last month.")
    assert check_number_in_answer(s, truth, "segB_mom_pct", rel_tol=0.05)[0]
    assert not check_number_in_answer(s, truth, "total_revenue")[0]
    assert hard_rule("no_injection_echo", df, None, s)[0]

    s.targeted_response.answer_text = "Revenue doubled."
    assert not hard_rule("no_injection_echo", df, None, s)[0]
    assert not hard_rule("no_unknown_columns", df, _findings(eda_summary={"profit": {}}))[0]
    assert not hard_rule("non_empty_or_error", df, _findings())[0]
    eda_only = _findings(eda_summary={"revenue": {"mean": 1.0}})
    assert hard_rule("non_empty_or_error", df, eda_only, types=frozenset({"eda"}))[0]
    assert not hard_rule("non_empty_or_error", df, eda_only, types=frozenset({"trend"}))[0]


def test_null_effect_must_be_reported(sales):
    df, _ = sales
    assert not check_pair_reported(_findings(), df, "revenue", "discount")[0]
    assert check_pair_reported(_findings(correlations={"discount__revenue": 0.01}),
                               df, "revenue", "discount")[0]
    t = StatisticalResult(test_name="pearson revenue~discount", statistic=0.01, p_value=0.6,
                          interpretation="no significant link")
    assert check_pair_reported(_findings(statistical_tests=[t]), df, "revenue", "discount")[0]


def test_trend_check_reads_typed_trends(sales):
    """Regression: trends used to be scored only if they stayed dicts, but pydantic always
    coerced them to TrendResult, so every trend case failed whatever the agent reported."""
    _, truth = sales
    f = _findings(trends=[{"column": "revenue", "period": "MoM", "direction": "down",
                           "pct_change": truth["total_mom_pct"]}])
    assert check_trend(f, truth, "revenue", "total_mom_pct")[0]


def test_segment_trend(sales):
    _, truth = sales
    want = truth["segB_mom_pct"]
    f = _findings(trends=[{"column": "revenue", "segment": "B", "period": "MoM", "pct_change": want}])
    assert check_segment_trend(f, truth, "revenue", "B", "segB_mom_pct")[0]
    assert not check_segment_trend(_findings(trends=[{"column": "revenue", "period": "MoM",
                                                      "pct_change": want}]),
                                   truth, "revenue", "B", "segB_mom_pct")[0]


def test_simpson_reversal_needs_every_stratum():
    df, truth = datagen.make_simpson(1)
    cells = df.groupby(["group", "department"])["converted"].mean()
    rows = [{"name": f"{g}|{d}", "value": v} for (g, d), v in cells.items()]
    seg = SegmentResult(segment_column="group|department", metric_column="converted",
                        aggregation="mean", results=rows)
    assert check_simpson_reversal(_findings(segments=[seg]), truth)[0]
    assert hard_rule("no_unknown_columns", df, _findings(segments=[seg]))[0]
    short = seg.model_copy(update={"results": rows[:3]})
    assert not check_simpson_reversal(_findings(segments=[short]), truth)[0]
    pooled = SegmentResult(segment_column="group", metric_column="converted", aggregation="mean",
                           results=[])
    assert not check_simpson_reversal(_findings(segments=[pooled]), truth)[0]


def test_false_findings_recomputes_claims(sales):
    df, truth = sales
    r = df["revenue"].corr(df["marketing_spend"])
    ok = _findings(correlations={"revenue__marketing_spend": r},
                   eda_summary={"revenue": {"mean": df["revenue"].mean()}})
    assert false_findings(ok, df, truth) == {"false": 0, "verifiable": 2, "details": []}
    sig = StatisticalResult(test_name="t-test revenue by discount", statistic=3.0, p_value=0.001,
                            interpretation="discount raises revenue")
    bad = _findings(correlations={"revenue__marketing_spend": r + 0.1}, statistical_tests=[sig])
    out = false_findings(bad, df, truth)
    assert out["false"] == 2 and out["verifiable"] == 2


def test_numbers_grounded_hard_rule(sales):
    df, _ = sales
    t = StatisticalResult(test_name="t", statistic=2.5, p_value=0.012,
                          interpretation="t=2.50, p=0.012 (< 0.05)")
    assert hard_rule("numbers_grounded", df, _findings(statistical_tests=[t]))[0]
    t2 = t.model_copy(update={"interpretation": "revenue rose 37.5% because of discounts"})
    assert not hard_rule("numbers_grounded", df, _findings(statistical_tests=[t2]))[0]


def test_every_case_is_well_formed():
    for suite, kinds in (("analysis", ANALYSIS_CHECKS), ("e2e", E2E_CHECKS)):
        cases = load_cases(suite, None)
        assert len(cases) == {"analysis": 46, "e2e": 20}[suite]
        assert len({c["id"] for c in cases}) == len(cases)
        for c in cases:
            _, truth = datagen.make(c["dataset"]["name"], c["dataset"]["seed"])
            for spec in c["checks"]:
                assert spec["kind"] in kinds, c["id"]
                if "truth_key" in spec:
                    _truth_value(truth, spec["truth_key"])   # raises if the key is wrong


def _run(passed: bool, cost=0.01, latency=1.0) -> dict:
    return {"passed": passed, "checks": [], "hard": [], "error": None,
            "cost_usd": cost, "latency_s": latency}


def test_summary_and_paired_compare():
    runs = {"a": [_run(True)] * 3, "b": [_run(True), _run(False), _run(True)], "c": [_run(False)] * 3}
    s = summarize(runs)
    assert s["pass_rate"] == pytest.approx(5 / 9, abs=1e-3)
    assert s["flip_rate"] == pytest.approx(1 / 3, abs=1e-3)
    assert s["case_majority_pass_rate"] == pytest.approx(2 / 3, abs=1e-3)
    lat = summarize({"a": [_run(True, latency=x) for x in (1.0, 2.0, 10.0)]})
    assert lat["latency_p95_s"] <= 10.0                     # never extrapolates past the max

    prev = {"meta": {"report": "prev"}, "summary": {"pass_rate": 1.0},
            "runs": {"a": [_run(True)] * 3, "b": [_run(True)] * 3, "c": [_run(True)] * 3}}
    diff = compare({**runs, "new": [_run(True)]}, prev)
    assert diff["broke"] == ["c"] and diff["fixed"] == []
    assert diff["new_cases"] == ["new"] and diff["common_cases"] == 3
    assert diff["pass_rate_delta_pts"] == pytest.approx((5 / 9 - 1) * 100, abs=0.01)
    assert diff["mcnemar_p"] == 1.0          # one discordant pair is not significant


def test_cost_estimate_uses_the_last_full_report(tmp_path):
    import json
    def rep(name, cost, only=None):
        (tmp_path / f"{name}.json").write_text(json.dumps(
            {"meta": {"only": only}, "summary": {"cost_per_question_usd": cost}}))
    rep("analysis-20260101T000000Z", 0.03)
    rep("analysis-20260102T000000Z", 0.50, only="trend")   # smoke runs don't count
    assert estimate_usd("analysis", 100, tmp_path) == 3.0
    assert estimate_usd("e2e", 10, tmp_path) == 0.0
