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
# Re-pinned at 5.7b-2's end (2026-11-13); its diff is named in docs/baselines/phase5_7b_2.md.
CHANGES: dict[str, dict[str, str]] = {
    # Domain 7 (IoT, planned at B0 per lesson 33): sensor_readings now reads as the iot pack
    # (contract domain, roles, a failures tile, cleaning flags), so its outputs and every case on
    # it change. Re-measured at Domain 7's analysis and E2E gates.
    "deterministic": {f"sensor_readings-{s}": "Domain 7: read as the iot pack" for s in (1, 2, 3)} | {
        # E2E gate e2e-86: a column's group answer of 5 groups or fewer names the ones between the
        # leader and the lowest too (only those words added; domain_diff --show, 2026-11-17)
        d: "Domain 7: a few groups each named" for d in (
            "claims-1", "claims-2", "claims-3", "employees-1", "employees-2", "employees-3",
            "health_claims-1", "hr_roster_onerow-1", "hr_roster-1", "loan_tape-1", "mixed_dates-1",
            "mixed_dates-2", "saas_accounts-1", "saas_accounts-2", "saas_accounts-3",
            "saas_mrr_snapshots-1", "saas_subscriptions-1", "sales-1", "sales-2", "sales-3", "sales-4",
            "sales-5", "store_weeks-1")},
    "analysis": {c: "Domain 7: sensor_readings read as the iot pack" for c in ("iot-1", "iot-2", "iot-3")},
    "e2e": {c: "Domain 7: sensor_readings read as the iot pack" for c in ("e2e-31", "e2e-32")},
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
