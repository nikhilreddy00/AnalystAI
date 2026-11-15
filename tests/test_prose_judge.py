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


def test_the_held_out_set_is_balanced_and_shares_no_sentence_with_tuning():
    tune = yaml.safe_load((SETS / "tuning.yaml").read_text())
    held = yaml.safe_load((SETS / "held_out.yaml").read_text())
    assert sum(x["label"] for x in held) == sum(not x["label"] for x in held) == 50
    assert not {x["text"] for x in tune} & {x["text"] for x in held}
    assert all(x.get("kind") in ("causal", "benchmark") for x in held + tune if x["label"])
