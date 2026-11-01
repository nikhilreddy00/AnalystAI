"""Phase 5.6b: a delivered report never changes. A run's report is its own report events; reopening
the thread returns them byte-identical to what the live stream delivered (the sha256 the run store
sealed), with provenance; a newer dataset version marks it stale and a re-run adds a report beside
it. Offline on the recorded e2e-01 run, at $0."""
import json

from llm.client import LLMResult
from memory.run_store import report_hash
from tests.test_durable_runs import FIXTURE, WANT_COST, _sandbox, replay  # noqa: F401
from tests.test_runs_api import _events, _worker
from tests.test_workspace_api import _ask, _changed, _upload, ws  # noqa: F401

ANSWER = {"message": "Continue with imputation", "is_approval": True,
          "approval_context": "high_null"}


async def _deliver(client, deps, **body) -> tuple[str, str, list[dict]]:
    """One overview, answered at its gate, read as the browser reads it: (run, thread, events)."""
    run = (await _ask(client, **body)).json()
    stop, task = await _worker(deps)
    try:
        live = await _events(client, run["run_id"])
        if live[-1][1]["type"] == "approval_required":
            await client.post("/chat", json={**ANSWER, "session_id": run["session_id"]})
            live += await _events(client, run["run_id"], last=live[-1][0])
    finally:
        stop.set()
        await task
    assert live[-1][1] == {"type": "done"}
    return run["run_id"], run["session_id"], [e for _, e in live]


def _refill(recorded_llm, monkeypatch):
    from agents import analysis_agent
    recorded_llm.responses[:] = [LLMResult(**r) for r in FIXTURE["replies"]]
    monkeypatch.setattr(analysis_agent, "run_code", _sandbox())


async def test_reopening_returns_exactly_what_was_delivered(ws):  # noqa: F811
    client, other, deps, csv = ws
    up = (await _upload(client, csv)).json()
    run_id, sid, live = await _deliver(client, deps, dataset_version_id=up["dataset_version_id"])

    thread = (await client.get(f"/api/sessions/{sid}/thread")).json()
    [t] = thread["turns"]
    delivered = [e for e in live if e["type"] in {"briefing", "evidence", "charts", "insight",
                                                  "expert_review", "targeted_response"}]
    assert {e["type"] for e in delivered} >= {"briefing", "evidence", "charts", "insight",
                                              "expert_review"}
    # Byte-identical: same canonical bytes, and the hash the run store sealed when it ended.
    canon = json.dumps(t["report"], sort_keys=True, separators=(",", ":"))
    assert canon == json.dumps(delivered, sort_keys=True, separators=(",", ":"))
    assert t["report_sha256"] == report_hash(live) == (await deps.run_store.get(run_id)
                                                        ).report_sha256
    assert t["intact"] and not t["stale"] and t["status"] == "completed"
    assert t["question"] == "Give me an overview of this dataset."
    p = t["provenance"]
    assert p["dataset_version_id"] == up["dataset_version_id"]
    assert p["cost_usd"] == WANT_COST and p["llm_calls"] == len(FIXTURE["replies"])
    assert p["cards"] and p["model"]

    for path in (f"/api/sessions/{sid}/thread", f"/api/sessions/{sid}/export.md",
                 f"/runs/{run_id}/report.pptx"):
        assert (await other.get(path)).status_code == 404, path


async def test_the_session_changing_never_changes_a_delivered_report(ws, recorded_llm, monkeypatch):  # noqa: F811
    """A newer version marks the old report stale; re-running on it adds a report beside it and
    the old one's bytes stay the same."""
    client, _, deps, csv = ws
    up = (await _upload(client, csv)).json()
    run1, sid, _ = await _deliver(client, deps, dataset_version_id=up["dataset_version_id"])
    before = (await client.get(f"/api/sessions/{sid}/thread")).json()["turns"][0]

    v2 = (await _upload(client, _changed(csv))).json()
    assert v2["status"] == "new_version"
    stale = (await client.get(f"/api/sessions/{sid}/thread")).json()["turns"][0]
    assert stale["stale"] is True and stale["report"] == before["report"]

    _refill(recorded_llm, monkeypatch)
    run2, _, _ = await _deliver(client, deps, session_id=sid,
                                dataset_version_id=v2["dataset_version_id"])
    t1, t2 = (await client.get(f"/api/sessions/{sid}/thread")).json()["turns"]
    assert (t1["run_id"], t2["run_id"]) == (run1, run2)
    assert t1["report"] == before["report"] and t1["report_sha256"] == before["report_sha256"]
    assert t1["stale"] and not t2["stale"]
    assert t2["provenance"]["dataset_version_id"] == v2["dataset_version_id"]


async def test_a_report_cancelled_after_it_arrived_is_kept(ws):  # noqa: F811
    """The user saw the insight before pressing Stop: that is the report, sealed at cancel."""
    client, _, deps, csv = ws
    up = (await _upload(client, csv)).json()
    run = (await _ask(client, dataset_version_id=up["dataset_version_id"])).json()
    insight = {"type": "insight", "data": {"executive_summary": "x"}}
    await deps.run_store.append(run["run_id"], [insight])
    await client.post(f"/runs/{run['run_id']}/cancel")
    [t] = (await client.get(f"/api/sessions/{run['session_id']}/thread")).json()["turns"]
    assert t["status"] == "cancelled" and t["report"] == [insight]
    assert (await deps.run_store.get(run["run_id"])).report_sha256 == t["report_sha256"]


async def test_a_rewritten_report_is_not_intact(ws):  # noqa: F811
    client, _, deps, csv = ws
    up = (await _upload(client, csv)).json()
    run = (await _ask(client, dataset_version_id=up["dataset_version_id"])).json()
    await deps.run_store.append(run["run_id"], [{"type": "insight", "data": {"a": 1}}])
    await client.post(f"/runs/{run['run_id']}/cancel")
    deps.run_store._runs[run["run_id"]].events[-3]["data"]["a"] = 2   # tampered at rest
    [t] = (await client.get(f"/api/sessions/{run['session_id']}/thread")).json()["turns"]
    assert t["intact"] is False


async def test_exports(ws):  # noqa: F811
    client, _, deps, csv = ws
    up = (await _upload(client, csv)).json()
    run_id, sid, live = await _deliver(client, deps, dataset_version_id=up["dataset_version_id"])
    r = await client.get(f"/api/sessions/{sid}/export.md")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/markdown")
    md = r.text
    insight = next(e["data"] for e in live if e["type"] == "insight")
    evidence = next(e["data"] for e in live if e["type"] == "evidence")
    assert md.startswith("# orders")
    assert "Give me an overview of this dataset." in md
    assert all(e["id"].split(":")[0] in md for e in evidence)
    assert insight["executive_view"]["headline"].split()[0] in md
    assert "Report sha256 " + report_hash(live) in md
    pptx = await client.get(f"/runs/{run_id}/report.pptx")
    assert pptx.status_code == 200 and pptx.content[:2] == b"PK"


def test_markdown_escapes_model_and_user_text():
    from api.lib.report import md, to_markdown
    assert md("![x](http://evil/p.png) <img src=x>") == \
        "\\!\\[x\\]\\(http://evil/p.png\\) \\<img src=x\\>"
    out = to_markdown("t", None, [{
        "question": "[click](http://evil)", "status": "completed", "error": None,
        "report_sha256": "h",
        "report": [{"type": "evidence", "data": [{
            "id": "e1", "kind": "eda", "confidence": "high", "row_count": 3, "claim": "c",
            "code": "print('```')", "result_excerpt": ""}]}],
        "provenance": {"model": "m", "llm_calls": 0, "cost_usd": 0.0, "finished_at": None}}])
    assert "](http" not in out.replace("\\]\\(", "")
    assert "````\nprint('```')\n````" in out   # a code fence the code can't close
