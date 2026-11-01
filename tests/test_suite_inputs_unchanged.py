"""Eval cost playbook: a suite whose inputs didn't change needs no paid re-run. The agents'
rendered contexts (analysis 46, insight 40, intake 60) and the E2E loads' column roles must stay
byte-identical to the snapshot of `main` before Phase 5.5 (evals/snapshots/pre_5_5.json, taken at
5eca99a; pre_ingestion.json was Phase 5 Ingestion's, before 5.6 changed intake and insight).
Phase 5.5 adds cases; every snapshotted case must still match. A change that breaks this must
re-run those suites: Phase 5.5 changed the analysis context on purpose (the user's question is
in it, prompt v8: derived rates), so analysis is pinned on column roles only and its 46 old
cases are re-run at the Phase 5.5 gate."""
import json

from evals.run import EVALS_DIR
from evals.snapshot import snapshot


# These insight contexts quote test statistics whose last float digits differ between macOS
# (where the snapshot was taken) and CI's Linux; on the snapshot's own platform they are pinned
# like the rest (checked for Phase 5.5 on macOS).
PLATFORM_FLOATS = {"ins-15-gap-not-significant", "ins-39-region-null", "ins-40-minutes-null-holdout"}


async def test_every_snapshotted_case_still_gets_the_same_inputs():
    before = json.loads((EVALS_DIR / "snapshots" / "pre_5_5.json").read_text())
    now = await snapshot(("analysis", "intake", "insight", "e2e"))
    assert {c: now["analysis"][c]["roles"] for c in before["analysis"]} == \
        {c: v["roles"] for c, v in before["analysis"].items()}
    for suite in ("intake", "insight"):
        changed = sorted(c for c in before[suite] if now[suite].get(c) != before[suite][c]
                         and c not in PLATFORM_FLOATS)
        assert not changed, (suite, changed, [(now[suite].get(c), before[suite][c]) for c in changed[:2]])
    assert {c: now["e2e"][c]["roles"] for c in before["e2e"]} == \
        {c: v["roles"] for c, v in before["e2e"].items()}
    assert len(now["analysis"]) == 68 and len(now["e2e"]) == 34   # the 5.5 cases are in it


# Phase 5.7: the no-LLM stages' outputs per eval dataset (contract, cleaning audit, KPI tiles,
# briefing extras, cleaned frame), from evals/snapshots/pre_5_7.json taken at ab05639. A domain
# pack must not change another domain's data; a dataset listed here changed on purpose, and its
# paid cases are re-measured at the domain's gate (named in its review note).
CHANGED_ON_PURPOSE: set[str] = set()


async def test_no_llm_outputs_unchanged_outside_the_domain_being_built():
    before = json.loads((EVALS_DIR / "snapshots" / "pre_5_7.json").read_text())["deterministic"]
    now = (await snapshot(("deterministic",)))["deterministic"]
    changed = sorted(d for d in before if now.get(d) != before[d] and d not in CHANGED_ON_PURPOSE)
    assert not changed, [(d, {k for k in before[d] if now.get(d, {}).get(k) != before[d][k]})
                         for d in changed]
