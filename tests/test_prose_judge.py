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
                                   "no json", '{"yes": [0]}'])
def test_a_reply_that_skips_a_sentence_or_isnt_the_json_fails_closed(reply):
    """G3: a sentence with no verdict is unjudged, never passed as sound: the judge returns None
    and its caller says so."""
    assert _run(["Spend drove sales.", "Sales rose 4%."], reply)[0] is None
    assert _run(["Spend drove sales."])[0] is None   # no reply at all (an outage)


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


def test_the_held_out_set_is_balanced_and_shares_no_sentence_with_tuning():
    tune = yaml.safe_load((SETS / "tuning.yaml").read_text())
    held = yaml.safe_load((SETS / "held_out.yaml").read_text())
    assert sum(x["label"] for x in held) == sum(not x["label"] for x in held) == 50
    assert not {x["text"] for x in tune} & {x["text"] for x in held}
    assert all(x.get("kind") in ("causal", "benchmark") for x in held + tune if x["label"])
