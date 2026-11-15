"""Typed claims (5.7b-3, ADR-0009, D1-09): a cause, a benchmark verdict or a comparison is data the
model gives; code checks it against the evidence it cites and writes its sentence."""
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
    assert render(up, FINDINGS) == "Mean revenue: South (94.03) is lower than North (113.32)."
    tie = up.model_copy(update={"a": "east", "b": "South"})
    assert render(tie, FINDINGS) == "Mean revenue: east (94.03) is the same as South (94.03)."
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
