"""What each paid suite's agent is handed, built by the suites' own harness helpers: column roles
plus a hash of the rendered context. Phase 5 Ingestion changes `build_data_contract`, which every
suite calls; a suite whose snapshot is unchanged needs no paid re-run (eval cost playbook).

    uv run python -m evals.snapshot <out.json>      # write
    uv run python -m evals.snapshot diff <a> <b>    # which cases changed, per suite
    uv run python -m evals.snapshot pin              # re-pin evals/snapshots/golden.json (a domain's end)
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


async def snapshot(suites: tuple[str, ...] = ("analysis", "intake", "insight", "e2e", "deterministic")) -> dict:
    from agents import analysis_agent, insight_agent, intake_agent
    from api.config import settings
    from connectors.csv_connector import CSVConnector

    # PHI pseudonyms follow the key: one key, so pytest, CI and a shell snapshot agree
    key, settings.encryption_key = settings.encryption_key, harness.EVAL_ENCRYPTION_KEY
    try:
        return await _snapshot(suites, analysis_agent, insight_agent, intake_agent, CSVConnector)
    finally:
        settings.encryption_key = key


async def _snapshot(suites, analysis_agent, insight_agent, intake_agent, CSVConnector) -> dict:  # noqa: N803

    out: dict[str, dict] = {s: {} for s in suites}
    for case in harness.load_cases("analysis", None) if "analysis" in suites else []:
        session = AnalysisSession(session_id="snap", data_contract=harness.working_frame(case)[1],
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
    if "deterministic" in suites:   # every analysis + E2E dataset
        names = {(c["dataset"]["name"], c["dataset"]["seed"])
                 for s in ("analysis", "e2e") for c in harness.load_cases(s, None)}
        out["deterministic"] = await deterministic(names)
    return out


def _rounded(v):
    """Floats to 9 significant digits: the last digits of a threshold differ between macOS and
    CI's Linux (numpy builds); a real change moves far more than that."""
    if isinstance(v, float):
        return float(f"{v:.9g}")
    if isinstance(v, dict):
        return {k: _rounded(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_rounded(x) for x in v]
    return v


def _run_standard(code: str, df) -> dict:
    """The built-in code's output (run here as the sandbox runs it: header, then the code), not
    its text: an equivalent rewrite that changes nothing for this data keeps the pin."""
    import tempfile

    from agents.analysis_agent import _CODE_HEADER
    with tempfile.NamedTemporaryFile(suffix=".csv") as f:
        df.to_csv(f.name, index=False)
        scope: dict = {}
        exec(_CODE_HEADER.replace("/data/df.csv", f.name) + code, scope)   # noqa: S102 (our code)
    return scope["result"]


def _behaviour(df, contract) -> dict:
    """Phase 5.7 (audit pass 1: a domain's conventions changed banking's charts and answers
    unseen): the built-in analysis code, the trend chart and three targeted answers, so a change
    to another domain's charts or answers fails the pin too."""
    import io

    from agents.analysis_agent import _build_standard_code
    from agents.targeted_analysis_agent import run_targeted_analysis
    from agents.visualization_agent import build_context, month_line
    from orchestrator.state import QuestionIntent, TargetedParams

    metric = next(iter(contract.metric_columns), None)
    date = next(iter(contract.date_columns), None)
    group = next((c.name for c in contract.columns if not (c.is_metric or c.is_date or c.is_id)
                  and not c.pii), None)
    task = AnalysisTask(raw_query="Give me an overview of this dataset.",
                        analysis_types=["eda", "trend", "segmentation"],
                        target_metrics=contract.metric_columns[:3], segments=[group] if group else [])
    out = {"standard_code": _run_standard(_build_standard_code(contract, task), df)}
    if metric and date:
        line = month_line(df, date, metric, build_context(df, contract=contract), monthly=True)
        out["chart"] = line.model_dump(mode="json", include={"data", "y_label", "title"}) if line else None
    buf = io.BytesIO()
    df.to_parquet(buf, index=False)
    answers = []
    for intent, params in ((QuestionIntent.TIME_SERIES, {"metric_column": metric, "frequency": "M"}),
                           (QuestionIntent.TIME_SERIES, {"metric_column": metric, "frequency": "W"}),
                           (QuestionIntent.SEGMENT_COMPARE, {"metric_column": metric, "segment_column": group})):
        if not metric or (intent == QuestionIntent.SEGMENT_COMPARE and not group):
            continue
        s = AnalysisSession(session_id="snap", data_contract=contract, task_plan=AnalysisTask(
            raw_query="q", analysis_types=["eda"], target_metrics=[], question_intent=intent,
            targeted_params=TargetedParams(**params)))
        s.__dict__["_df_parquet"] = buf.getvalue()
        r = run_targeted_analysis(s)
        answers.append([r.answer_text, [c.data for c in r.chart_configs]])
    out["answers"] = answers
    return out


def _set_only(d: dict, new_fields: tuple[str, ...]) -> dict:
    return {k: v for k, v in d.items() if not (k in new_fields and not v)}


async def deterministic(datasets: set[tuple[str, int]]) -> dict:
    """Phase 5.7: what the no-LLM stages make of each eval dataset, loaded as production loads it
    (connector → contract → cleaning → KPI tiles + briefing extras) under an overview plan. A
    dataset whose hash is unchanged gets the same deterministic outputs, so a pack that must not
    touch it is proven not to, for $0."""
    import io

    import pandas as pd

    from agents.cleaning_agent import run_cleaning_agent
    from agents.visualization_agent import classify_columns, compute_briefing_extras, generate_smart_kpis
    from connectors.csv_connector import CSVConnector, _read_bytes
    from evals import datagen

    out = {}
    for name, seed in sorted(datasets):
        df = _read_bytes(datagen.make(name, seed)[0].to_csv(index=False).encode(), ".csv")[0]
        contract = CSVConnector(ConnectorConfig(type=ConnectorType.CSV)).build_data_contract(df)
        session = AnalysisSession(session_id="snap", data_contract=contract, task_plan=AnalysisTask(
            raw_query="Give me an overview of this dataset.", analysis_types=["eda"],
            target_metrics=contract.metric_columns,
            segments=[c.name for c in contract.columns
                      if not (c.is_metric or c.is_date or c.is_id)][:3]))
        buf = io.BytesIO()
        df.to_parquet(buf, index=False)
        session.__dict__["_df_parquet"] = buf.getvalue()
        clean_df, audit = await run_cleaning_agent(session)
        roles = classify_columns(clean_df)
        kpis = generate_smart_kpis(clean_df, roles, session.task_plan.raw_query, contract=contract)
        extras = compute_briefing_extras(clean_df, roles, contract)
        behaviour = _behaviour(clean_df, contract)
        out[f"{name}-{seed}"] = {k: _sha(json.dumps(_rounded(v), sort_keys=True, default=str)) for k, v in {
            # fields added since pre_5_7 count only when set (a new empty field is no change)
            "contract": _set_only(contract.model_dump(mode="json"), ("domain", "roles", "domain_confidence")),
            "audit": _set_only(audit.model_dump(mode="json"), ("grain", "partial_months", "main_currency")),
            "kpis": [{**k.model_dump(mode="json"), "recipe": _set_only(k.recipe.model_dump(
                mode="json"), ("kpi",)) if k.recipe else None} for k in kpis],
            "extras": extras,
            "frame": pd.util.hash_pandas_object(clean_df, index=True).tolist(),
            **behaviour}.items()}
    return out


def diff(a: dict, b: dict) -> dict:
    return {suite: sorted(cid for cid in a[suite] if a[suite][cid] != b.get(suite, {}).get(cid))
            for suite in a}


GOLDEN = Path(__file__).parent / "snapshots" / "golden.json"

if __name__ == "__main__":
    if sys.argv[1] == "pin":   # only at a domain's end, its changes listed in its review note
        GOLDEN.write_text(json.dumps(asyncio.run(snapshot()), indent=1, sort_keys=True))
        print(f"→ {GOLDEN}")
    elif sys.argv[1] == "diff":
        load = lambda p: json.loads(Path(p).read_text())   # noqa: E731
        print(json.dumps(diff(load(sys.argv[2]), load(sys.argv[3])), indent=1))
    else:
        Path(sys.argv[1]).write_text(json.dumps(asyncio.run(snapshot()), indent=1, sort_keys=True))
        print(f"→ {sys.argv[1]}")
