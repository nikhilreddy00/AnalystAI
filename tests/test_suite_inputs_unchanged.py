"""Eval cost playbook: a suite whose inputs didn't change needs no paid re-run. The agents'
rendered contexts (analysis 46, insight 40, intake 60) and the E2E loads' column roles must stay
byte-identical to the snapshot of `main` before Phase 5.5 (evals/snapshots/pre_5_5.json, taken at
5eca99a; pre_ingestion.json was Phase 5 Ingestion's, before 5.6 changed intake and insight).
Phase 5.5 adds cases; every snapshotted case must still match. A change that breaks this must
re-run those suites."""
import json

from evals.run import EVALS_DIR
from evals.snapshot import snapshot


async def test_every_snapshotted_case_still_gets_the_same_inputs():
    before = json.loads((EVALS_DIR / "snapshots" / "pre_5_5.json").read_text())
    now = await snapshot(("analysis", "intake", "insight", "e2e"))
    for suite in ("analysis", "intake", "insight"):
        changed = sorted(c for c in before[suite] if now[suite].get(c) != before[suite][c])
        assert not changed, (suite, changed, [(now[suite].get(c), before[suite][c]) for c in changed[:2]])
    assert {c: now["e2e"][c]["roles"] for c in before["e2e"]} == \
        {c: v["roles"] for c, v in before["e2e"].items()}
    assert len(now["analysis"]) == 67 and len(now["e2e"]) == 34   # the 5.5 cases are in it
