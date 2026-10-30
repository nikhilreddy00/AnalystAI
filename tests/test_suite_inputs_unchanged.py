"""Eval cost playbook: a suite whose inputs didn't change needs no paid re-run. Phase 5 Ingestion
rewrote build_data_contract (every suite calls it); the analysis agent's 46 rendered contexts and
the E2E loads' column roles are byte-identical to the snapshot taken before it
(evals/snapshots/pre_ingestion.json). A change that breaks this must re-run those suites."""
import json

from evals.snapshot import snapshot
from evals.run import EVALS_DIR


async def test_analysis_contexts_and_e2e_roles_match_the_pre_ingestion_snapshot():
    before = json.loads((EVALS_DIR / "snapshots" / "pre_ingestion.json").read_text())
    now = await snapshot(("analysis", "e2e"))
    assert now["analysis"] == before["analysis"]
    assert {c: v["roles"] for c, v in now["e2e"].items()} == \
        {c: v["roles"] for c, v in before["e2e"].items()}
