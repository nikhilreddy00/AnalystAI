"""Domain regression gate (Phase 5.7): building one domain must not break another. Every eval
dataset's no-LLM outputs (contract, cleaning audit, KPI tiles, briefing extras, cleaned frame,
built-in analysis code, trend chart, targeted answers) and every paid case's rendered context
(analysis, intake, insight; E2E column roles) must equal evals/snapshots/golden.json, pinned at
the end of the last finished domain.

A difference is a bug, or a change made on purpose: then it goes in CHANGES with its reason and
is re-measured at that domain's paid gate. At the domain's end the golden is re-pinned
(`uv run python -m evals.snapshot pin`), the diff named in its review note, and CHANGES emptied.
A suite whose cases all still match needs no paid re-run (eval cost playbook)."""
import json

import pytest

from evals.snapshot import GOLDEN, snapshot

# The domain being built: {suite: {case or dataset: why}}. Empty right after a pin.
CHANGES: dict[str, dict[str, str]] = {
    "deterministic": {"saas_subscriptions-1": "5.7b-2a C5 (user decision 2026-11-10): the file's "
                      "story says its 10 paused or unpaid rows are out of every month-end (no "
                      "date they began); no number moved"},
    "insight": {"ins-49-saas-subscriptions-overview": "5.7b-2a C5: the same today-only sentence "
                "in the story the insight reads; no number moved"},
}


GOLDEN_NOW = json.loads(GOLDEN.read_text())


def _broken(suite: str, pinned_cases: dict, now: dict) -> dict:
    broken = {}
    for cid, pinned in pinned_cases.items():
        if cid in CHANGES.get(suite, {}):
            continue
        got = now.get(cid)
        # the pinned stages only: a probe added since the pin is checked from the next pin on
        if got is None or (any(got.get(k) != pinned[k] for k in pinned) if isinstance(pinned, dict)
                           else got != pinned):   # deterministic entries are {stage: hash}
            broken[cid] = sorted(k for k in pinned if got is None or got.get(k) != pinned[k]) \
                if isinstance(pinned, dict) else "changed"
    return broken


# One test per paid suite and one per dataset (parallel under `pytest -n`; `-k <dataset>` checks
# only it while iterating): the same comparison as one big test, ~1 min less per targeted run.
@pytest.mark.parametrize("suite", [s for s in GOLDEN_NOW if s != "deterministic"])
async def test_every_finished_domain_gets_the_same_inputs(suite):
    now = (await snapshot((suite,)))[suite]
    broken = _broken(suite, GOLDEN_NOW[suite], now)
    assert not broken, f"changed against the golden (a bug, or list it in CHANGES with why): {broken}"


@pytest.mark.parametrize("dataset", sorted(GOLDEN_NOW.get("deterministic", {})))
async def test_every_finished_domain_gets_the_same_outputs(dataset):
    name, seed = dataset.rsplit("-", 1)
    now = (await snapshot(("deterministic",), {(name, int(seed))}))["deterministic"]
    broken = _broken("deterministic", {dataset: GOLDEN_NOW["deterministic"][dataset]}, now)
    assert not broken, f"changed against the golden (a bug, or list it in CHANGES with why): {broken}"


def test_changes_name_only_pinned_cases():
    stale = {s: sorted(set(c) - set(GOLDEN_NOW.get(s, {}))) for s, c in CHANGES.items()}
    assert not any(stale.values()), f"CHANGES names cases the golden doesn't have: {stale}"
