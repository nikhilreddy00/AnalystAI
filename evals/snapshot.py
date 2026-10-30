"""What each paid suite's agent is handed, built by the suites' own harness helpers: column roles
plus a hash of the rendered context. Phase 5 Ingestion changes `build_data_contract`, which every
suite calls; a suite whose snapshot is unchanged needs no paid re-run (eval cost playbook).

    uv run python -m evals.snapshot <out.json>      # write
    uv run python -m evals.snapshot diff <a> <b>    # which cases changed, per suite
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from pathlib import Path

from evals import run as harness
from orchestrator.state import AnalysisSession, AnalysisTask, ConnectorConfig, ConnectorType


def _roles(contract) -> dict:
    return {c.name: {"dtype": c.dtype, "date": c.is_date, "id": c.is_id, "metric": c.is_metric}
            for c in contract.columns}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


async def snapshot(suites: tuple[str, ...] = ("analysis", "intake", "insight", "e2e")) -> dict:
    from agents import analysis_agent, insight_agent, intake_agent
    from connectors.csv_connector import CSVConnector

    build = CSVConnector(ConnectorConfig(type=ConnectorType.CSV)).build_data_contract
    out: dict[str, dict] = {s: {} for s in suites}
    for case in harness.load_cases("analysis", None) if "analysis" in suites else []:
        df = harness.dataset(case)[0]
        session = AnalysisSession(session_id="snap", data_contract=build(df),
                                  task_plan=AnalysisTask(raw_query=case["question"], **case["task"]))
        out["analysis"][case["id"]] = {"roles": _roles(session.data_contract),
                                       "context": _sha(analysis_agent.build_context(session).render())}
    for case in harness.load_cases("intake", None) if "intake" in suites else []:
        session = harness.intake_session(case)
        out["intake"][case["id"]] = {
            "roles": _roles(session.data_contract),
            "context": _sha(intake_agent.build_context(session, case["question"]).render())}
    for case in harness.load_cases("insight", None) if "insight" in suites else []:
        session, _, _ = await harness.insight_bundle(case)
        out["insight"][case["id"]] = {"roles": _roles(session.data_contract),
                                      "context": _sha(insight_agent.build_context(session).render())}
    # E2E loads through the connector (file → load → contract), not read_csv.
    harness.UPLOADS.mkdir(parents=True, exist_ok=True)
    for case in harness.load_cases("e2e", None) if "e2e" in suites else []:
        _, _, csv = harness.dataset(case)
        path = harness.UPLOADS / f"{case['dataset']['name']}-{case['dataset']['seed']}.csv"
        if not path.exists() or path.read_text() != csv:
            path.write_text(csv)
        conn = CSVConnector(ConnectorConfig(type=ConnectorType.CSV, file_path=str(path)))
        out["e2e"][case["id"]] = {"roles": _roles(conn.build_data_contract(await conn.load()))}
    return out


def diff(a: dict, b: dict) -> dict:
    return {suite: sorted(cid for cid in a[suite] if a[suite][cid] != b[suite].get(cid))
            for suite in a}


if __name__ == "__main__":
    if sys.argv[1] == "diff":
        load = lambda p: json.loads(Path(p).read_text())   # noqa: E731
        print(json.dumps(diff(load(sys.argv[2]), load(sys.argv[3])), indent=1))
    else:
        Path(sys.argv[1]).write_text(json.dumps(asyncio.run(snapshot()), indent=1, sort_keys=True))
        print(f"→ {sys.argv[1]}")
