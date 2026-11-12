"""$0 replay of the post-sandbox repair (plan v2 R2) over stored analysis outputs: a breakdown
that passed its truth checks in a paid run must come out of `repair_breakdowns` unchanged.
R6 found the first version recomputing a model's own quarter-of-spend breakdown over every row
(bank-6); this gate would have caught it at $0."""
import json
from functools import cache
from pathlib import Path

import pytest

from evals import run as harness

REPORTS = Path(harness.__file__).parent / "reports"
# a stored breakdown the repair corrects on purpose: {(report, case): why}
CHANGED = {
    ("analysis-20261102T002211Z.json", "retail-7"): "units by product summed cancelled and test "
    "lines (the case's check measured net sales, not units); now the units tile's rows",
}


@cache
def _cases() -> dict:
    return {c["id"]: c for c in harness.load_cases("analysis", None)}


def _stored():
    out = []
    for path in sorted(REPORTS.glob("analysis-*.json"), reverse=True)[:8]:
        for cid, runs in json.loads(path.read_text()).get("runs", {}).items():
            out += [(path.name, cid, i, r["output"]) for i, r in enumerate(runs)
                    if r.get("output") and r.get("output", {}).get("segments") and cid in _cases()
                    and all(c.get("ok") for c in r.get("checks", []))]
    return out


@cache
def _frame(cid: str):
    case = _cases()[cid]
    df, contract = harness.working_frame(case)
    return harness.cleaned_for_analysis(case, df, contract)[0], contract


@pytest.mark.parametrize("report, cid, i, output", _stored(), ids=lambda x: x if isinstance(x, str) else "")
def test_a_passing_breakdown_is_left_as_it_is(report, cid, i, output):
    from agents.analysis_agent import repair_breakdowns
    from orchestrator.state import AnalysisFindings, AnalysisTask
    f = AnalysisFindings.model_validate(output)
    before = [s.model_dump() for s in f.segments]
    df, contract = _frame(cid)
    case = _cases()[cid]   # the question the stored run answered (C4: a move asked is kept)
    repair_breakdowns(f, df, contract, AnalysisTask(raw_query=case["question"], **case["task"]))
    after = [s.model_dump() for s in f.segments]
    if (report, cid) not in CHANGED:   # a named one may be corrected; nothing else moves
        assert after == before
    assert [[r["name"] for r in s["results"]] for s in after] == \
        [[r["name"] for r in s["results"]] for s in before]   # its own groups, never more
