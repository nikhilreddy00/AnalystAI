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

# The domain being built: {suite: {case or dataset: why}}. Empty right after a pin.
CHANGES: dict[str, dict[str, str]] = {}


async def test_every_finished_domain_gets_the_same_inputs_and_outputs():
    golden = json.loads(GOLDEN.read_text())
    now = await snapshot()
    broken = {}
    for suite, cases in golden.items():
        for cid, pinned in cases.items():
            if cid in CHANGES.get(suite, {}):
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
