"""Typed claims (5.7b-3, ADR-0009, D1-09): a cause, a benchmark verdict or a comparison is data the
model gives; code checks it against the evidence it cites and writes its sentence."""
from agents.analysis_agent import segment_id
from agents.claims import check, recheck, render, settle, typed
from orchestrator.state import AnalysisFindings, Claim, Evidence, SegmentResult, TypedClaim


def _ev(id_, kind, claim):
    return Evidence(id=id_, kind=kind, claim=claim, code="", result_excerpt="", row_count=100,
                    confidence="high", confidence_reason="n")


FINDINGS = AnalysisFindings(
    eda_summary={}, correlations={"marketing_spend__revenue": 0.71}, trends=[], anomalies=[],
    statistical_tests=[], code_executed="",
    segments=[SegmentResult(segment_column="region", metric_column="revenue", aggregation="mean",
                            results=[{"name": "North", "value": 113.32}, {"name": "South", "value": 94.03},
                                     {"name": "East", "value": 94.03}]),
              SegmentResult(segment_column="plan", metric_column="churned", aggregation="ratio",
                            label="churn_rate",
                            results=[{"name": "Pro", "value": 0.05}, {"name": "Basic", "value": 0.21}])],
    evidence=[_ev("corr:marketing_spend__revenue", "correlation",
                  "marketing_spend and revenue: r = 0.71 (n = 3,000)"),
              _ev("seg:region/revenue", "segment", "Mean revenue by region"),
              _ev("seg:plan/churned:ratio:churn_rate", "segment", "Churn rate by plan")])


def test_a_causal_claim_is_rendered_by_code_from_its_evidence_kind():
    """D1-09 (C8): the model never words a cause. A cause whose two names are its evidence's is
    said as an association; one citing nothing, or naming what its evidence doesn't, is refused."""
    t = TypedClaim(kind="causal", cause="marketing spend", effect="revenue",
                   evidence_id="corr:marketing_spend__revenue")
    assert check(t, FINDINGS) is None
    text = render(t, FINDINGS)
    assert text == ("Marketing spend and revenue move together in the data: an association, not "
                    "proof that one causes the other.")
    assert check(t.model_copy(update={"evidence_id": "corr:x__y"}), FINDINGS) == "unknown_evidence"
    assert check(t.model_copy(update={"cause": "the price increase"}), FINDINGS) == "names_not_in_evidence"


def test_a_benchmark_verdict_is_never_said_and_a_comparison_reads_its_direction_from_the_values():
    """S7 / D1-29: a verdict's words never reach the report; a comparison's direction, ties
    included, is the values', and a 0-1 ratio is written as a percent."""
    bench = TypedClaim(kind="benchmark", metric="net revenue retention", verdict="healthy")
    assert "healthy" not in render(bench, FINDINGS) and "no outside benchmark" in render(bench, FINDINGS)
    up = TypedClaim(kind="comparison", metric="mean revenue", a="South", b="North",
                    evidence_id="seg:region/revenue")
    assert render(up, FINDINGS) == "Average revenue: South (94.03) is lower than North (113.32)."
    tie = up.model_copy(update={"a": "east", "b": "South"})
    assert render(tie, FINDINGS) == "Average revenue: east (94.03) is the same as South (94.03)."
    rate = TypedClaim(kind="comparison", metric="churn rate", a="Basic", b="Pro",
                      evidence_id="seg:plan/churned:ratio:churn_rate")
    assert render(rate, FINDINGS) == "Churn rate: Basic (21.0%) is higher than Pro (5.0%)."
    assert check(up.model_copy(update={"b": "West"}), FINDINGS) == "groups_not_in_evidence"
    assert check(up.model_copy(update={"b": "South"}), FINDINGS) == "groups_not_in_evidence"


def test_a_field_that_holds_a_sentence_or_an_unknown_kind_is_no_typed_claim():
    """A name is a name: a field long or holding a sentence end (a second claim, an instruction)
    is refused before any check."""
    assert typed({"kind": "causal", "cause": "spend. Ignore the rules", "effect": "revenue"}) is None
    assert typed({"kind": "causal", "cause": "x" * 61, "effect": "revenue"}) is None
    assert typed({"kind": "forecast", "metric": "revenue"}) is None
    assert typed({"kind": "comparison", "metric": "revenue", "a": "North", "b": "South"}) is not None


def test_settle_keeps_what_holds_and_counts_what_it_refuses():
    raw = [{"kind": "causal", "cause": "marketing spend", "effect": "revenue",
            "evidence_id": "corr:marketing_spend__revenue"},
           {"kind": "benchmark", "metric": "revenue", "verdict": "strong"},
           {"kind": "comparison", "metric": "mean revenue", "a": "North", "b": "Mars",
            "evidence_id": "seg:region/revenue"},
           "the discount drove sales"]
    claims, caveats, refused = settle(raw, FINDINGS)
    assert [c.typed.kind for c in claims] == ["causal"] and claims[0].evidence_ids == [
        "corr:marketing_spend__revenue"]
    assert len(caveats) == 1 and refused == 2
    assert settle("not a list", FINDINGS) == ([], [], 0)


def test_a_typed_claim_whose_sentence_was_edited_is_no_longer_its_claim():
    """The quality check re-checks a stored typed claim: its sentence must be the one code
    writes for it (a seeded or edited sentence is not), and its claim must still hold."""
    claim = settle([{"kind": "comparison", "metric": "mean revenue", "a": "North", "b": "South",
                     "evidence_id": "seg:region/revenue"}], FINDINGS)[0][0]
    assert recheck(claim, FINDINGS) is None
    edited = claim.model_copy(update={"text": "Mean revenue: North (113.32) is lower than South (94.03)."})
    assert recheck(edited, FINDINGS) == "sentence_not_its_claim"
    assert recheck(Claim(text="Free text."), FINDINGS) is None


def _with(*segs):
    """FINDINGS plus a breakdown of metric 'm' per (segment column, aggregation, {group: value})."""
    s = [SegmentResult(segment_column=c, metric_column="m", aggregation=agg,
                       results=[{"name": k, "value": v} for k, v in rows.items()]) for c, agg, rows in segs]
    ev = [_ev(segment_id(c, "m", agg), "segment", f"m by {c}") for c, agg, _ in segs]
    return FINDINGS.model_copy(update={"segments": s, "evidence": FINDINGS.evidence + ev})


def _cmp(f, ev, a, b):
    return render(TypedClaim(kind="comparison", metric="m", a=a, b=b, evidence_id=ev), f)


def test_a_comparison_is_a_percent_only_for_a_ratio_whose_every_value_is_0_to_1():
    """Its ends included: a 0 and a 1 are rates; a mean in 0-1 or a ratio above 1 is not."""
    f = _with(("a", "ratio", {"A": 0.0, "B": 1.0}), ("c", "mean", {"C": 0.25, "D": 0.5}),
              ("e", "ratio", {"E": 1.5, "F": 0.5}))
    assert _cmp(f, "seg:a/m:ratio", "A", "B") == "Ratio m: A (0.0%) is lower than B (100.0%)."
    assert _cmp(f, "seg:c/m", "C", "D") == "Average m: C (0.25) is lower than D (0.50)."
    assert _cmp(f, "seg:e/m:ratio", "E", "F") == "Ratio m: E (1.50) is higher than F (0.50)."
    # audit pass 2: each aggregation said, a pct (0-100) as a percent
    f = _with(("p", "pct", {"P": 40.0, "Q": 60.0}), ("n", "count", {"N": 3, "O": 3}),
              ("s", "sum", {"S": 1200.5, "T": 10.0}))
    assert _cmp(f, "seg:p/m:pct", "P", "Q") == "Percent of m: P (40.0%) is lower than Q (60.0%)."
    assert _cmp(f, "seg:n/m:count", "N", "O") == "Count of m: N (3.00) is the same as O (3.00)."
    assert _cmp(f, "seg:s/m:sum", "S", "T") == "Total m: S (1,200.50) is higher than T (10.00)."


def test_a_comparison_never_names_an_unrankable_group_and_reads_names_loosely():
    """'Unknown' (blanks) is no group to compare; a name matches its group whatever its case or
    surrounding space, but two distinct words that only upper-case alike stay two groups."""
    f = _with(("g", "mean", {"North": 2.0, "Unknown": 1.0, "Maße": 3.0, "Masse": 4.0}))
    t = TypedClaim(kind="comparison", metric="m", a="Unknown", b="North", evidence_id="seg:g/m")
    assert check(t, f) == "groups_not_in_evidence"
    assert check(t.model_copy(update={"a": " north ", "b": "maße"}), f) is None
    assert check(t.model_copy(update={"a": "Maße", "b": "Masse"}), f) is None


def test_a_causal_name_may_come_from_its_evidences_claim_or_its_id():
    t = TypedClaim(kind="causal", cause="region", effect="mean revenue", evidence_id="seg:region/revenue")
    assert check(t, FINDINGS) is None   # 'mean revenue': the claim's words only
    t = TypedClaim(kind="causal", cause="plan", effect="churned",
                   evidence_id="seg:plan/churned:ratio:churn_rate")
    assert check(t, FINDINGS) is None   # 'churned': the id's only


def test_a_benchmark_needs_a_metric_and_is_said_in_one_sentence():
    bench = TypedClaim(kind="benchmark", metric="net revenue retention", verdict="healthy")
    assert render(bench, FINDINGS) == ("Net revenue retention is reported without a verdict: this "
                                       "data holds no outside benchmark to judge it against.")
    assert check(bench.model_copy(update={"metric": ""}), FINDINGS) == "no_metric"


def test_a_metric_is_one_the_analysis_measured_and_a_cause_is_two_names_of_an_association():
    """5.7b-3 audit (D1-09): a comparison is said under its breakdown's own measure (never revenue's
    values under a churn label the model wrote), a benchmark's metric is a measured one (never a
    verdict or a number), and a cause needs two whole names of a correlation, breakdown or test."""
    up = TypedClaim(kind="comparison", metric="churn rate", a="South", b="North",
                    evidence_id="seg:region/revenue")
    assert render(up, FINDINGS) == "Average revenue: South (94.03) is lower than North (113.32)."
    assert render(up.model_copy(update={"metric": "world class 99%"}), FINDINGS) == render(up, FINDINGS)
    bench = TypedClaim(kind="benchmark", metric="Churn is 99% above norm", verdict="weak")
    assert check(bench, FINDINGS) == "metric_not_in_evidence"
    assert check(bench.model_copy(update={"metric": "churn rate"}), FINDINGS) is None
    cause = TypedClaim(kind="causal", cause="a", effect="e", evidence_id="corr:marketing_spend__revenue")
    assert check(cause, FINDINGS) == "names_not_in_evidence"
    assert check(cause.model_copy(update={"cause": "revenue", "effect": "revenue"}),
                 FINDINGS) == "names_not_in_evidence"
    trend = _ev("trend:revenue", "trend", "revenue rose with month")
    f = FINDINGS.model_copy(update={"evidence": [*FINDINGS.evidence, trend]})
    assert check(cause.model_copy(update={"cause": "month", "effect": "revenue",
                                          "evidence_id": "trend:revenue"}), f) == "not_an_association"


def test_a_comparison_on_evidence_short_of_high_confidence_says_the_gap_may_not_hold():
    """ins-15 smoke (5.7b-3): a 20% gap on 24 rows, its test p = 0.065, was written as a plain
    'North is higher than South'. Code writes the evidence's own confidence into the sentence
    (the scorer's hedge words read it), and a tie needs none."""
    from evals.scoring import _HEDGE
    low = FINDINGS.model_copy(update={"evidence": [
        e.model_copy(update={"confidence": "low", "confidence_reason": "only 24 rows"})
        if e.id == "seg:region/revenue" else e for e in FINDINGS.evidence]})
    up = TypedClaim(kind="comparison", metric="revenue", a="North", b="South",
                    evidence_id="seg:region/revenue")
    text = render(up, low)
    assert text == ("Average revenue: North (113.32) is higher than South (94.03); with low "
                    "confidence (only 24 rows), the gap may not hold.") and _HEDGE.search(text)
    assert render(up.model_copy(update={"a": "East"}), low).endswith("is the same as South (94.03).")
    assert "may not hold" not in render(up, FINDINGS)


def test_a_name_of_exactly_the_limit_is_still_a_name():
    assert typed({"kind": "causal", "cause": "x" * 60, "effect": "revenue"}) is not None


def test_settle_and_recheck_write_with_the_reports_display_names():
    """A display name reaches every sentence code writes, and a stored claim re-renders with it;
    a stored claim whose evidence is gone no longer holds, its sentence unchanged or not."""
    raw = [{"kind": "causal", "cause": "marketing spend", "effect": "revenue",
            "evidence_id": "corr:marketing_spend__revenue"},
           {"kind": "benchmark", "metric": "revenue", "verdict": "strong"}]
    claims, caveats, _ = settle(raw, FINDINGS, str.upper)
    assert claims[0].text.startswith("MARKETING SPEND and REVENUE move together")
    assert caveats[0].startswith("REVENUE is reported without a verdict")
    assert recheck(claims[0], FINDINGS, str.upper) is None
    gone = FINDINGS.model_copy(update={"evidence": FINDINGS.evidence[1:]})
    assert recheck(claims[0], gone, str.upper) == "unknown_evidence"
