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
# Re-pinned at Domain 8's end (2026-11-18); its diff is named in
# docs/baselines/phase5_7_logistics.md.
# D7-19 PR (2026-11-19, docs/baselines/phase5_7_d7_19.md): a count by group reads the counted
# records (domain_pack.count_rows) and a KPI-named model segment takes the product's value. Every
# pack dataset's model_segments probe moves (its dummy 0.0 values become the KPI's); the
# datasets with left-out rows also move their generic count charts and count answers, each
# total now the tiles' (truth from evals/datagen, seed 1).
_PROBE = "model_segments: a segment labelled with a pack KPI's exact name takes measure(by=)"
_COUNTS = _PROBE + "; the count charts and a category's value counts read the counted records: "
CHANGES: dict[str, dict[str, str]] = {"deterministic": {
    **{d: _PROBE for d in (
        "claims-1", "claims-2", "claims-3", "employees-1", "employees-2", "employees-3", "encounters-1",
        "encounters-2", "encounters-3", "hr_roster-1", "hr_roster_onerow-1", "hr_snapshots-1", "inpatient-1",
        "machine_readings-1", "policies-1", "saas_accounts-1", "saas_accounts-2", "saas_accounts-3",
        "saas_mrr_snapshots-1", "saas_subscriptions-1", "sensor_readings-1", "sensor_readings-2",
        "sensor_readings-3", "shift_production-1", "shift_production-2", "shift_production-3", "shipments-1",
        "shipments-2", "shipments-3")},
    "b2b_invoices-1": _COUNTS + "266 non-product lines and 119 credit lines out (truth non_product_lines, "
                      "credit_lines); POSTAGE no longer tops the Description answer",
    "card_ledger-1": _COUNTS + "1,490 EUR rows (truth eur_rows), reversed rows and reversal entries out",
    "dock_appointments-1": _COUNTS + "38 cancelled appointments out (truth cancelled 38; visits 1,462)",
    "health_claims-1": _COUNTS + "433 earlier versions and 230 voided lines out: line 1 counts 2,668 = "
                       "truth claims_final",
    "loan_tape-1": _COUNTS + "106 closed or pending rows out (truth closed_rows 106)",
    "loss_run-1": _COUNTS + "34,280 earlier valuations out: 3,975 claimant rows = truth claim_rows_latest",
    "order_lines-1": _COUNTS + "110 cancelled lines out (truth cancelled_lines 110)",
    "order_lines-2": _COUNTS + "108 cancelled lines out",
    "retail_lines-1": _COUNTS + "refunded, cancelled, test and 111 CAD lines out (truth cad_lines 111, test_lines 26)",
    "saas_invoices-1": _COUNTS + "76 void or draft invoices out",
    "work_orders-1": _COUNTS + "39 cancelled work orders out (truth cancelled_wos 39; CM + EM = 311 = "
                     "corrective_wos, was 333)",
    **{f"tms_shipments-{s}": _COUNTS + "58 cancelled and 102 other-currency rows out of seed 1's charts "
       "(2,840 = truth shipments); Weight, summed by cost per lb, reads the KPI's rows in its trend and "
       "answers (seed 1: 160 rows left out, said)" for s in (1, 2, 3)}}}


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
