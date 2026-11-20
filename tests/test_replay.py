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


# fields added to stored structures since runs were recorded (evals/snapshot.py's _set_only)
NEW_EMPTY_FIELDS = {"spell_story", "window_span"}


def _same(a, b):
    """Equal outputs, a report's own timestamp aside (generated_at is when, not what), floats at
    display precision (utils.display_values: a scipy statistic's last digits differ between the
    macOS that recorded a run and CI's Linux), and an agent's prompt version named in the
    methodology ('analysis v8' → 'v9': which prompt, not what the step computed)."""
    import re

    from agents.utils import display_values

    def plain(x):
        if isinstance(x, dict):   # a field added since the run, still empty, is no change
            return {k: plain(v) for k, v in x.items() if k != "generated_at"
                    and not (k in NEW_EMPTY_FIELDS and not v)}
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
_SAAS_AUDIT1 = ("Domain 6 audit 1: the SaaS pack's caveat (a one-month customer is in no month-end "
                "figure), the ledger story in generic words and the cohort window words changed the "
                "deterministic text after the model")
CHANGED = {
    # Domain 8: on-time's numerator no longer reads 'Status has a value' (the no-status guard moved
    # to the flag denominator after the model copied it into broken code, logi-4); its How changed
    ("insight-20261118T174137Z.json", "ins-54-logi-shipments-overview"):
        "Domain 8: on-time's How drops 'Status has a value'",
    # Domain 7 quality run: 'shifts' left the iot pack's small-cell nouns (they are categories,
    # never people) and its operator caveat counts operators; the stored ins-51 run predates it
    **{(r, "ins-51-iot-shift-overview"): "Domain 7: small cells count operators, never shifts"
       for r in ("insight-20261116T220202Z.json", "insight-20261116T220202Z-rescored.json")},
    # Domain 7 review: the over-100% performance flag is filed under Ideal Cycle Time (s), not
    # Changeover (min); the final re-measure's ins-51 predates it
    ("insight-20261117T102814Z.json", "ins-51-iot-shift-overview"):
        "Domain 7 review: the over-max flag names the ideal cycle's column",
    # Domain 7 audit: the iot pack's invalid-reading words name historian digital states
    # (I/O Timeout, Shutdown, Comm Fail …); the cleaning note lists them
    **{(r, "ins-52-iot-readings-overview"): "Domain 7 audit: the invalid-reading note lists the "
       "historian's digital states" for r in ("insight-20261116T220202Z.json",
                                              "insight-20261116T220202Z-rescored.json")},
    # Domain 7 spec review: the read-as caveat says open repairs make MTTR read low and the zone
    **{(r, "ins-53-iot-sensor-drift"): "Domain 7 spec review: the read-as caveat says MTTR's open "
       "repairs and the time zone" for r in ("insight-20261116T220202Z.json",
                                             "insight-20261116T220202Z-rescored.json")},
    ("insight-20261116T035805Z.json", "ins-15-gap-not-significant"):
        "5.7b-3 smoke: a typed comparison on evidence short of high confidence says the gap may "
        "not hold (the plain one failed the hedge check)",
    **{("insight-20261116T040022Z.json", c): "5.7b-3 insight k=1: a typed cause on evidence short of "
       "high confidence says even the association may not hold"
       for c in ("ins-17-low-n-correlation", "ins-37-discount-null")},
    # PR14-03: a sentence naming a day-first date ("covers 885 employees at 31 Oct 2026") was
    # removed as a one-month scope claim; it is kept now
    **{(r, c): "5.7b-3 E2E: a day-first date is a day, never a one-month scope claim"
       for r, c in (("insight-20261116T042427Z.json", "ins-47-hr-roster-overview"),
                    ("insight-20261116T042427Z.json", "ins-48-hr-snapshots-overview"),
                    ("insight-20261116T042427Z.json", "ins-49-saas-subscriptions-overview"),
                    ("insight-20261116T042427Z.json", "ins-50-saas-mrr-overview"),
                    ("insight-20261116T042657Z.json", "ins-49-saas-subscriptions-overview"))},
    # 5.7b-2a (C5, PR1-05): a 20-row ticket list read as SaaS has no end-date column, so churned
    # MRR and logo churn can't be counted: no longer named as KPIs it reads (they read 0 before)
    ("insight-20261106T181700Z.json", "ins-16-tickets-not-significant-holdout"):
        "5.7b-2a C5: a spell list without its end date names no churn KPI (unavailable, not 0)",
    ("insight-20261106T012454Z.json", "ins-04-subs-overview"): _SAAS_AUDIT1,
    ("insight-20261106T012454Z.json", "ins-08-plan-revenue"): _SAAS_AUDIT1,
    ("insight-20261106T012454Z.json", "ins-13-mom-vs-yoy"): _SAAS_AUDIT1,
    ("insight-20261106T012454Z.json", "ins-16-tickets-not-significant-holdout"): _SAAS_AUDIT1,
    ("insight-20261106T012454Z.json", "ins-19-stale-memory-holdout"): _SAAS_AUDIT1,
    ("insight-20261106T012454Z.json", "ins-29-multi-year-holdout"): _SAAS_AUDIT1,
    ("insight-20261106T012454Z.json", "ins-38-tickets-null"): _SAAS_AUDIT1,
    ("insight-20261106T012454Z.json", "ins-49-saas-subscriptions-overview"): _SAAS_AUDIT1,
    ("insight-20261106T012454Z.json", "ins-50-saas-mrr-overview"): _SAAS_AUDIT1,
    ("insight-20261106T012659Z.json", "ins-16-tickets-not-significant-holdout"): _SAAS_AUDIT1,
    ("insight-20261106T012659Z.json", "ins-49-saas-subscriptions-overview"): _SAAS_AUDIT1,
    ("insight-20261106T012659Z-rescored.json", "ins-16-tickets-not-significant-holdout"): _SAAS_AUDIT1,
    ("insight-20261106T012659Z-rescored.json", "ins-49-saas-subscriptions-overview"): _SAAS_AUDIT1,
    ("insight-20261104T214949Z.json", "ins-48-hr-snapshots-overview"):
        "Domain 5 audit 1: the cleaning audit carries the file's story (domain_pack.spell_story: "
        "year-earlier rates, peak to now, people vs rows), the insight's computed facts",
    ("insight-20261104T214949Z.json", "ins-47-hr-roster-overview"):
        "Domain 5 insight gate: pay of a group under 11 people (Legal, 6) is dropped from every "
        "segment (analysis standard_stats, protected_groups), so the bundle's evidence changed",
    **{(r, "ins-45-policies-overview"): "Domain 4 audit pass 1: a policy table's transaction type "
       "is its status (renewal status blank by design on cancellation rows), earned-premium and "
       "protected-attribute cautions, lapse rate"
       for r in ("insight-20261104T015642Z.json", "insight-20261104T015458Z.json")},
    ("insight-20261104T015458Z.json", "ins-46-loss-run-overview"):
        "Domain 4 live run: a column blank by the file's layout (Case Reserve on closed claims) is "
        "no longer a 'missing' evidence note or CRITICAL alert (one LAYOUT_STRATEGIES rule)",
    **{("insight-20261104T014955Z.json", c): "Domain 4: the insurance pack's KPI order (tiles: "
       "policies on premium tables, severity on loss runs) reorders the stated caveat's KPI list"
       for c in ("ins-45-policies-overview", "ins-46-loss-run-overview")},
    ("intake-20261104T012924Z.json", "intake-ins-09"):
        "Domain 4 intake gate: 'premium did we write' is the written_premium KPI's word",
    ("intake-20261104T012924Z.json", "intake-ins-11"):
        "Domain 4 intake gate: 'whole life policies' is life insurance: unsupported, asked",
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
    **{("insight-20261103T191036Z.json", c): "Domain 3 re-gate: the read-as-healthcare caveat writes "
                                            "'thirty days' (a digit there is ungrounded on a claims file)"
       for c in ("ins-43-inpatient-overview", "ins-44-claims-overview")},
    ("insight-20261103T182036Z.json", "ins-43-inpatient-overview"):
        "Domain 3 live run: the pooled small-groups bucket is never ranked lowest (guardrails.rankable)",
    ("insight-20261103T182036Z.json", "ins-44-claims-overview"):
        "Domain 3: the trend evidence names the months it compares (Jul vs Jun 2026)",
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
    if (report, cid) in CHANGED:   # changed as stated: it differs, and fails no check it passed
        if _same(again["output"], run["output"]):   # a k=3 report: a reply the change never met
            runs = json.loads((REPORTS / report).read_text())["runs"][cid]
            assert len(runs) > 1, f"{report} {cid}: listed but unchanged"
            return
        failing = {c["detail"] for c in again["checks"] if not c["ok"]}
        assert failing <= {c["detail"] for c in run["checks"] if not c["ok"]}, (
            f"{report} {cid}: changed, and newly failing: {again['checks']}")
        return
    assert _same(again["output"], run["output"]), f"{report} {cid}: a post-model step changed"


# Suites that store replies but replay elsewhere or not at all, and why (D1-15 said "every suite
# stores replies; test_replay replays them" while two were replayed: the claim is this list now).
NOT_REPLAYED = {
    "e2e": "a whole run (database, every agent); its analysis and insight steps replay here",
    "quality": "the prose judge is a model call by design (evals.run --replay keeps it live)",
}


def test_every_suite_that_stores_replies_replays_or_says_why():
    suites = {p.name.split("-")[0] for p in REPORTS.glob("*.json")
              if any(r.get("replies") for rs in json.loads(p.read_text()).get("runs", {}).values() for r in rs)}
    assert suites - {*REPLAYABLE, "analysis", *NOT_REPLAYED} == set()


def _analysis_runs():
    """Every analysis case, once: its first stored run in the newest report that has it (the whole
    suite, as the retired scripts/replay_breakdowns.py covered; the three newest reports held one
    domain, and every run of every case took 4 min a push)."""
    latest: dict[str, tuple[str, dict]] = {}
    for path in sorted(REPORTS.glob("analysis-2*Z.json")):   # names sort by time: later wins
        for cid, runs in json.loads(path.read_text()).get("runs", {}).items():
            if run := next((r for r in runs if r.get("replies") and r.get("checks")), None):
                latest[cid] = (path.name, run)
    return [(report, cid, run) for cid, (report, run) in sorted(latest.items())]


# A run that loses a check it passed: a later change moved it on purpose, or its code fails only
# under this machine's pandas, never E2B's (health-6's second run: KeyError 'Claim ID' here):
# (report, case) → why. A listed run that loses nothing fails (the list never rots).
CHANGED_ANALYSIS: dict[tuple[str, str], str] = {}


@pytest.mark.parametrize("report, cid, run", _analysis_runs())
async def test_a_stored_analysis_run_keeps_every_check_it_passed(report, cid, run, monkeypatch):
    """D1-15 / D3-10 (PR24: two paid fix rounds a free replay would have caught): the analysis
    agent's stored replies through the real run_analysis_agent, its model code run by a local
    stand-in for E2B (evals.run.local_sandbox) on the upload a run sends. The sandbox's pandas is
    not this one, so outputs differ in dtypes; the verdicts may not: every check that passed
    still passes."""
    from agents import analysis_agent
    monkeypatch.setitem(harness._budget, "max", 1.0)
    monkeypatch.setattr(analysis_agent, "run_code", harness.local_sandbox)
    case = next(c for c in harness.load_cases("analysis", None) if c["id"] == cid)
    again = await _replay("analysis", case, run["replies"])
    from collections import Counter
    passed = lambda checks: Counter(c["kind"] for c in checks if c["ok"])  # noqa: E731  by kind, any order
    lost = passed(run["checks"]) - passed(again["checks"])
    lost_run = run["passed"] and not again["passed"]
    if (report, cid) in CHANGED_ANALYSIS:
        assert lost or lost_run, f"{report} {cid}: listed in CHANGED_ANALYSIS but loses nothing: drop it"
        return
    assert not lost and not lost_run, f"{report} {cid}: lost {dict(lost)}: {again['checks']}"


async def test_replay_mode_replays_the_models_and_calls_only_the_judge():
    """evals.run --replay (5.7b-3): a stored run's model replies come back in order, its stored
    judge replies are dropped, and only the prose judge reaches the live client."""
    stored = {"runs": {"c1": [{"replies": [{"text": '{"answer": 1}'}, {"text": '```json\n{"0": "cause"}'},
                                           {"text": '{"answer": 2}'}]}]}}
    live = RecordedClient(['{"0": "neither"}'])
    client = harness.ReplayClient(live, stored)
    harness.REPLAY_CASE.set("c1")
    ask = {"messages": [{"role": "user", "content": "q"}], "max_tokens": 9, "prompt_version": "v"}
    assert (await client.complete(agent="insight", **ask)).text == '{"answer": 1}'
    assert (await client.complete(agent="prose_judge", **ask)).text == '{"0": "neither"}'
    assert (await client.complete(agent="quality", **ask)).text == '{"answer": 2}'
    assert [c["agent"] for c in live.calls] == ["prose_judge"]
    from agents.errors import LLMError
    with pytest.raises(LLMError, match="no stored reply left"):
        await client.complete(agent="quality", **ask)
