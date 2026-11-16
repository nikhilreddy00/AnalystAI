"""The prose judge (5.7b-3, ADR-0009): a binary Haiku call for free text that states a cause or a
benchmark verdict, calibrated on a frozen labelled set and reported on a held-out one."""
import hashlib
from pathlib import Path

import pytest
import yaml

SETS = Path(__file__).resolve().parents[1] / "evals" / "cases" / "prose_judge"
# Frozen before any judge prompt (2026-11-15). A changed hash means a relabelled or re-cut set:
# a held-out set changed after its run is no held-out set (ledger D6-12, PR1-08).
FROZEN = {
    "guide.md": "c19db456f5b2fb1fe19fd288c4571fbf8968c4feef065aad373ec976de46ff2e",
    "tuning.yaml": "5d23c819a44e36cf6482d2b89086c1432d313c71e2ebe456ba167e6c36029027",
    "held_out.yaml": "1c772f34adbfacf159f6ce765d5cabdad77de2947a9737a37b429c7ca8e6c9ea",
}


@pytest.mark.parametrize("name", FROZEN)
def test_the_labelled_sets_and_their_guide_are_frozen(name):
    assert hashlib.sha256((SETS / name).read_bytes()).hexdigest() == FROZEN[name], name


def test_the_held_out_run_matches_the_current_prompt_set_and_model():
    """D6-12, PR1-08 (C8): the judge counts only on its held-out run of THIS prompt (with the
    untrusted note), set and model, at TPR and TNR ≥ 0.9. A prompt edit after the run fails here
    until it has its own held-out run (`python -m evals.judge prose held-out`)."""
    import json

    from evals.judge import PROSE_LOG, prose_shas
    last = json.loads(PROSE_LOG.read_text())[-1]
    assert {k: last[k] for k in prose_shas()} == prose_shas()
    assert last["tpr"]["rate"] >= 0.9 and last["tnr"]["rate"] >= 0.9, (last["tpr"], last["tnr"])
    assert last["sentences"] == 100


def _run(sentences, *replies):
    import asyncio

    from agents.prose_judge import flagged
    from llm.client import RecordedClient
    llm = RecordedClient(list(replies))
    return asyncio.run(flagged(sentences, llm)), llm


def test_the_judge_reads_one_verdict_per_sentence_by_its_number():
    """One call for the batch on the fast model, the sentences delimited as data under the
    untrusted note; a benchmark phrase is flagged without asking (the call never sees it)."""
    from api.config import settings
    from guardrails import UNTRUSTED_NOTE
    got, llm = _run(["Spend drove sales.", "A best-in-class rate.", "Sales rose 4%.", "The rate is healthy."],
                    'Here: {"0": "cause", "1": "neither", "2": "verdict"} (done)')
    assert got == {0, 1, 3}
    (call,) = llm.calls
    assert call["model"] == settings.llm_fast_model and call["system"].endswith(UNTRUSTED_NOTE)
    sent = call["messages"][0]["content"]
    assert sent.startswith("<untrusted_data>") and "best-in-class" not in sent and "2. The rate" in sent
    assert _run(["A world-class score."])[0] == {0}   # no call: nothing left to ask


@pytest.mark.parametrize("reply", ['{"0": "cause"}', '{"0": "cause", "1": "neither", "2": "x"}',
                                   "no json", '{"yes": [0]}',
                                   '{"0": "causal", "1": "neither"}'])   # an unknown label (audit)
def test_a_reply_that_skips_a_sentence_or_isnt_the_json_fails_closed(reply):
    """G3: a sentence with no verdict is unjudged, never passed as sound: the judge returns None
    and its caller says so."""
    assert _run(["Spend drove sales.", "Sales rose 4%."], reply)[0] is None
    assert _run(["Spend drove sales."])[0] is None   # no reply at all (an outage)


def test_the_judges_call_is_its_exact_request_and_a_failure_is_traced(monkeypatch):
    """The request as sent (no test double in between): its label and prompt version, its token
    budget, one user message of numbered lines; a failed judge traces its own rule label."""
    import asyncio

    import agents.prose_judge as pj
    from guardrails import delimit_untrusted
    from llm.client import LLMResult
    seen, traced = [], []

    class Spy:
        async def complete(self, **kw):
            seen.append(kw)
            return LLMResult(text="no json")
    monkeypatch.setattr(pj, "trace_verdict", lambda *a: traced.append(a))
    assert asyncio.run(pj.flagged(["Spend drove sales.", "Sales rose 4%."], Spy())) is None
    (kw,) = seen
    assert (kw["agent"], kw["prompt_version"], kw["max_tokens"]) == ("prose_judge", pj.PROMPT_VERSION, 1000)
    assert kw["messages"] == [{"role": "user", "content": delimit_untrusted(
        "0. Spend drove sales.\n1. Sales rose 4%.")}]
    assert traced == [("prose_judge", pj.PROMPT_VERSION, "judge_failed")]


def test_the_judges_prompt_and_phrases_say_no_domains_words():
    """D1-05 (C9): the shared judge reads every domain, so its prompt and phrase list name no
    pack's terms: no KPI label, no acronym from one (MRR, NPL, ALOS), no spell's nouns (built from
    every pack; everyday words a label also uses, 'revenue', 'total', stay English)."""
    import re

    import domain_pack
    from agents.prose_judge import BENCHMARK_PHRASES, _SYSTEM
    packs = domain_pack.load_packs()
    labels = {k.label.lower() for p in packs for k in p.kpis}
    acronyms = {a.lower() for p in packs for k in p.kpis for a in re.findall(r"\b[A-Z]{2,}\b", k.label)}
    nouns = {w for p in packs if p.spell for w in (p.spell.words.plural, p.spell.words.singular)}
    assert {"mrr", "npl"} <= acronyms and "customers" in nouns   # PR7-04: no vacuous pass
    text = (_SYSTEM.split("<untrusted_data>")[0] + " " + " ".join(BENCHMARK_PHRASES)).lower()
    words = set(re.findall(r"[a-z]+", text))
    assert not {lab for lab in labels if f" {lab} " in f" {text} "}, "a KPI label in the judge"
    assert not (acronyms | nouns) & words, sorted((acronyms | nouns) & words)


def test_with_a_judge_that_flags_nothing_every_overreach_sentence_passes_untouched():
    """D1-09, PR1-08 (C8), the behaviour guard: no regex judges a cause or a verdict anywhere.
    With the judge flagging nothing, every 'yes' sentence of the tuning set passes the insight
    report, the quality checks and a drill-down answer unchanged, but for the benchmark phrases;
    a regex re-added under any name fails here (sentences with a number are left out: grounding
    reads those)."""
    import asyncio

    from agents.drill_down_agent import checked_answer
    from agents.insight_agent import _assemble, build_context
    from agents.prose_judge import phrase_hit
    from agents.quality_agent import check_claims
    from evals.run import EVALS_DIR, insight_bundle
    from orchestrator.state import Claim, ExecutiveSummary, InsightReport
    from tests.test_retail_pack import _audited
    case = yaml.safe_load(next((EVALS_DIR / "cases" / "insight").glob("ins-01*.yaml")).read_text())
    session = asyncio.run(insight_bundle(case))[0]
    ctx, ev = build_context(session), "corr:revenue__marketing_spend"
    retail = _audited("retail_lines")   # a strong pack match: drill-down checks run
    said = [x["text"] for x in yaml.safe_load((SETS / "tuning.yaml").read_text())
            if x["label"] and not any(ch.isdigit() for ch in x["text"])]
    assert len(said) >= 15
    for s in said:
        hit = phrase_hit(s)
        # insight reads the phrases itself too, so they hold when the judge fails
        report = _assemble(session, ctx, {"headline": {"text": s, "evidence": [ev]}}, set())
        assert (report.executive_view.headline == s) is not hit, s
        session.insight_report = InsightReport(
            executive_summary=s, technical_findings=[], recommended_actions=[], anomaly_alerts=[],
            proactive_questions=[], methodology_notes="",
            executive_view=ExecutiveSummary(headline=s, headline_evidence_ids=[ev],
                                            findings=[Claim(text=s, evidence_ids=[ev])]))
        assert bool(check_claims(session, session.insight_report, ctx, set())) is hit, s
        assert (checked_answer(s, retail, [s], set()) == s) is not hit, s


def test_the_held_out_set_is_balanced_and_shares_no_sentence_with_tuning():
    tune = yaml.safe_load((SETS / "tuning.yaml").read_text())
    held = yaml.safe_load((SETS / "held_out.yaml").read_text())
    assert sum(x["label"] for x in held) == sum(not x["label"] for x in held) == 50
    assert not {x["text"] for x in tune} & {x["text"] for x in held}
    assert all(x.get("kind") in ("causal", "benchmark") for x in held + tune if x["label"])
