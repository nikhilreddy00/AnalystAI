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

from evals.snapshot import GOLDEN, snapshot

# Insight contexts quoting test statistics whose last float digits differ between macOS (where
# the golden is pinned) and CI's Linux.
PLATFORM_FLOATS = {"ins-15-gap-not-significant", "ins-39-region-null", "ins-40-minutes-null-holdout"}
# The domain being built: {suite: {case or dataset: why}}. Empty right after a pin.
_BANK = "Domain 2: bank_balances is now strong banking (pack KPIs, level hints, caveats)"
_LAYOUT = "Domain 2 R4: the schema summary says first-line-only blanks are layout, not missing"
_OUTLIERS = "Domain 2: the cleaning summary names outlier columns in full ('Lineitem price', not 'Lineitem')"
_MEAN = "Domain 2 audit B1: a pack's additive: false role (a unit price) is averaged, never summed"
_SAAS = ("Domain 2: a level's cadence is stated; MRR by plan is built in from each account's "
         "latest snapshot (the semantic layer's level, as the pack's)")
CHANGES: dict[str, dict[str, str]] = {
    "deterministic": {"bank_balances-1": _BANK, "bank_balances-2": _BANK, "bank_balances-3": _BANK,
                      "retail_lines-1": f"{_LAYOUT}; {_OUTLIERS}; {_MEAN}", "store_weeks-1": _OUTLIERS,
                      "b2b_invoices-1": _MEAN,
                      **{f"saas_accounts-{i}": _SAAS for i in (1, 2, 3)}},
    "analysis": {"bank-1": _BANK, "bank-2": _BANK, "bank-3": _BANK,
                 **{f"retail-{i}": _MEAN for i in (1, 2, 5, 6, 7)},
                 **{f"saas-{i}": _SAAS for i in (1, 2, 4)},
                 "saas-3": "Domain 2: a correlation on snapshot data (MRR, a level) uses one row "
                           "per account, its latest snapshot (pseudo-replication)"},
    "intake": {"intake-clear-sql-03": "Domain 2: account_id + balance is a weak banking match",
               **{f"intake-retail-0{i}": _LAYOUT for i in (1, 2, 3, 5, 6, 7, 8)}},
}


async def test_every_finished_domain_gets_the_same_inputs_and_outputs():
    golden = json.loads(GOLDEN.read_text())
    now = await snapshot()
    broken = {}
    for suite, cases in golden.items():
        for cid, pinned in cases.items():
            if cid in PLATFORM_FLOATS or cid in CHANGES.get(suite, {}):
                continue
            got = now.get(suite, {}).get(cid)
            # the pinned stages only: a probe added since the pin is checked from the next pin on
            if got is None or (any(got.get(k) != pinned[k] for k in pinned) if isinstance(pinned, dict)
                               else got != pinned):   # deterministic entries are {stage: hash}
                broken.setdefault(suite, {})[cid] = sorted(
                    k for k in pinned if got is None or got.get(k) != pinned[k]) \
                    if isinstance(pinned, dict) else "changed"
    assert not broken, (f"changed against the golden (a bug, or list it in CHANGES with why): {broken}")
    stale = {s: sorted(set(c) - set(golden.get(s, {}))) for s, c in CHANGES.items()}
    assert not any(stale.values()), f"CHANGES names cases the golden doesn't have: {stale}"
