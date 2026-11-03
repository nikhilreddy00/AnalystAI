"""$0 replay of stored model replies through every deterministic step after the model (5.7
lesson: intake's fill-in regressed and no free gate saw it). A paid run stores each reply; this
feeds them back through RecordedClient and must reproduce the stored output. Every later paid
run becomes a free regression case."""
import json
from pathlib import Path

import pytest

from evals import run as harness
from llm.client import LLMResult, RecordedClient, set_client

REPLAYABLE = ("intake", "insight")   # offline runners (no sandbox, no database)
REPORTS = Path(harness.__file__).parent / "reports"


def _stored_runs():
    out = []
    for suite in REPLAYABLE:
        for path in sorted(REPORTS.glob(f"{suite}-*.json"), reverse=True)[:3]:
            report = json.loads(path.read_text())
            for cid, runs in report.get("runs", {}).items():
                out += [(suite, path.name, cid, r) for r in runs if r.get("replies")]
    return out


def _same(a, b):
    """Equal outputs, a report's own timestamp aside (generated_at is when, not what), floats at
    display precision (utils.display_values: a scipy statistic's last digits differ between the
    macOS that recorded a run and CI's Linux), and an agent's prompt version named in the
    methodology ('analysis v8' → 'v9': which prompt, not what the step computed)."""
    import re

    from agents.utils import display_values

    def plain(x):
        if isinstance(x, dict):
            return {k: plain(v) for k, v in x.items() if k != "generated_at"}
        if isinstance(x, str):
            x = re.sub(r"\((analysis|intake|insight|quality) v\d+\)", r"(\1 vN)", x)
        return [plain(v) for v in x] if isinstance(x, list) else display_values(x)
    return plain(json.loads(json.dumps(a, default=str))) == plain(json.loads(json.dumps(b, default=str)))


async def _replay(suite: str, case: dict, replies: list[dict]) -> dict:
    set_client(RecordedClient([LLMResult(**r) for r in replies]))
    try:
        return await harness.run_once(suite, case, __import__("asyncio").Semaphore(1))
    finally:
        set_client(None)


async def test_a_recorded_run_replays_to_the_same_output(recorded_llm, monkeypatch):
    """The mechanism: a run through the harness keeps its replies; replaying them is the same."""
    monkeypatch.setitem(harness._budget, "max", 1.0)
    case = harness.load_cases("intake", None)[0]
    recorded = RecordedClient([json.dumps({"question_intent": "overview", "target_metrics": [],
                                           "segments": [], "analysis_types": ["eda"]})])
    set_client(harness.RecordingClient(recorded))
    try:
        first = await harness.run_once("intake", case, __import__("asyncio").Semaphore(1))
    finally:
        set_client(None)
    assert first["replies"] and first["replies"][0]["text"].startswith("{")
    again = await _replay("intake", case, first["replies"])
    assert _same(again["output"], first["output"]) and again["passed"] == first["passed"]


# A post-model step changed on purpose: (report, case) → why. Every other stored run must replay
# to its stored output (that is the $0 regression check over every old case).
CHANGED = {
    ("intake-20261102T204144Z.json", "intake-bank-01"):
        "Domain 2 intake gate: an overview intent naming a KPI after the overview is its time series",
    ("intake-20261102T204144Z.json", "intake-bank-09"):
        "Domain 2 intake gate: a default prediction is an unsupported concept: asked",
    ("intake-20261102T204144Z.json", "intake-bank-11"):
        "Domain 2 intake gate: 'average account balance' is the avg_balance KPI's word",
    **{(r, "intake-bank-08"): "Domain 2 intake: case reworded to one KPI ('money coming in' is "
                               "the inflows KPI's word); old replies replay against the new question"
       for r in ("intake-20261102T204144Z.json", "intake-20261102T205448Z.json")},
    **{(r, c): "Domain 2 E2E gate: a concept's name in a clarification is its glossary word "
               "(Pack.name_of: 'Net interest margin', never 'Nim')"
       for r in ("intake-20261102T204144Z.json", "intake-20261102T205448Z.json")
       for c in ("intake-bank-05", "intake-bank-09")},
    **{("intake-20261103T175225Z.json", c): "Domain 3 intake gate: 'we pay' is the paid KPI's "
                                             "word (the verb was missing from the glossary)"
       for c in ("intake-health-10", "intake-health-12")},
    **{("insight-20261103T180921Z.json", c): "Domain 3: the time axis is the pack's date role "
                                            "(discharge, service date), never Admit/Paid Date"
       for c in ("ins-43-inpatient-overview", "ins-44-claims-overview")},
    ("insight-20261103T180311Z.json", "ins-43-inpatient-overview"):
        "Domain 3 insight gate: the harness builds a patient-data bundle on the PHI-protected copy "
        "(names withheld, a 7-stay department pooled), as production does",
    ("insight-20261103T180311Z.json", "ins-44-claims-overview"):
        "Domain 3 insight gate: the harness's MoM fixture leaves claims' run-out months out "
        "(as analysis_agent's built-in does): Jul vs Jun, never Sep vs Aug",
    ("insight-20261102T210024Z.json", "ins-41-loan-tape-overview"):
        "Domain 2 browser check: a pack overview adds its first state ratio (NPL) by product",
    ("insight-20261102T210024Z.json", "ins-42-ledger-overview"):
        "Domain 2 CI: card_ledger's salaries in cents (an unrounded exp() differed on Linux); "
        "inflow totals move by cents",
}


@pytest.mark.parametrize("suite, report, cid, run", _stored_runs() or
                         [pytest.param(None, None, None, None, marks=pytest.mark.skip(
                             "no stored replies yet: the next paid intake/insight run adds them"))])
async def test_stored_replies_reproduce_their_stored_output(suite, report, cid, run, monkeypatch):
    monkeypatch.setitem(harness._budget, "max", 1.0)
    case = next(c for c in harness.load_cases(suite, None) if c["id"] == cid)
    again = await _replay(suite, case, run["replies"])
    if (report, cid) in CHANGED:   # changed as stated: it must now differ, and pass its checks
        assert not _same(again["output"], run["output"]), f"{report} {cid}: listed but unchanged"
        assert again["passed"], f"{report} {cid}: changed, yet still failing: {again['checks']}"
        return
    assert _same(again["output"], run["output"]), f"{report} {cid}: a post-model step changed"
