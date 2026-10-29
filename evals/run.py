"""
Eval runner (ADR-0002).

    uv run python -m evals.run analysis --k 3
    uv run python -m evals.run e2e --k 3 --compare evals/reports/e2e-<ts>.json
    uv run python -m evals.run analysis --k 1 --only sales-trend   # smoke subset
    TEST_PG_URL=postgresql+psycopg://... uv run python -m evals.run sql --k 1 --only retail-metric

Writes evals/reports/<suite>-<timestamp>.{json,md}. Spends real API money.
"""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import re
import statistics
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yaml
from scipy.stats import binomtest

from api.config import settings
from evals import cleangen, datagen
from evals.scoring import (
    ANALYSIS_CHECKS,
    E2E_CHECKS,
    NUMERIC_CHECKS,
    answer_text,
    clean_cell,
    false_findings,
    hard_rule,
    result_matches,
    score_cleaning,
    bundle_facts,
    score_insight,
    score_intake,
    score_visualization,
)
from llm.client import get_client, trace_meter
from orchestrator.state import (
    AnalysisFindings,
    AnalysisSession,
    AnalysisTask,
    ConnectorConfig,
    ConnectorType,
    Evidence,
    Message,
)

EVALS_DIR = Path(__file__).parent
UPLOADS = EVALS_DIR.parent / "uploads" / "evals"   # the CSV connector only reads uploads/


def load_cases(suite: str, only: str | None, exclude: str | None = None) -> list[dict]:
    cases = [yaml.safe_load(p.read_text()) for p in sorted((EVALS_DIR / "cases" / suite).glob("*.yaml"))]
    keep = only.split(",") if only else []   # comma-separated: any of these substrings
    return [c for c in cases if (not keep or any(k in c["id"] for k in keep))
            and not (exclude and exclude in c["id"])]


def dataset(case: dict) -> tuple[pd.DataFrame, dict, str]:
    """Generate the case's table and round-trip it through CSV, as the real pipeline does."""
    name, seed = case["dataset"]["name"], case["dataset"]["seed"]
    df, truth = datagen.make(name, seed)
    csv = df.to_csv(index=False)
    return pd.read_csv(io.StringIO(csv)), truth, csv


async def run_analysis_case(case: dict) -> dict:
    from agents.analysis_agent import run_analysis_agent
    from connectors.csv_connector import CSVConnector

    df, truth, _ = dataset(case)
    session = AnalysisSession(session_id=str(uuid.uuid4()))
    session.data_contract = CSVConnector(ConnectorConfig(type=ConnectorType.CSV)).build_data_contract(df)
    session.task_plan = AnalysisTask(raw_query=case["question"], **case["task"])
    buf = io.BytesIO()
    df.to_parquet(buf, index=False)
    session.__dict__["_df_parquet"] = buf.getvalue()

    findings, error = None, None
    try:
        findings = await run_analysis_agent(session, get_client())
    except Exception as exc:   # a loud failure is a failed case, not a crashed run
        error = f"{type(exc).__name__}: {exc}"

    return {**score_analysis(case, findings, error),
            "output": findings.model_dump(mode="json") if findings else None}


def score_analysis(case: dict, findings, error: str | None) -> dict:
    """Scores one analysis run from its stored output (so `rescore` costs nothing)."""
    df, truth, _ = dataset(case)
    checks = []
    for spec in case["checks"]:
        kind, kwargs = spec["kind"], {k: v for k, v in spec.items() if k not in ("kind", "planted")}
        ok, detail = ANALYSIS_CHECKS[kind](findings, df, truth, **kwargs) if findings else (False, "no findings")
        checks.append({"kind": kind, "ok": ok, "detail": detail, "planted": spec.get("planted", False)})
    types = frozenset(case["task"].get("analysis_types", []))
    hard = [dict(zip(("rule", "ok", "detail"), (r, *hard_rule(r, df, findings, types=types))))
            for r in case["hard_rules"]]
    return {"checks": checks, "hard": _explicit_error_ok(hard, error), "error": error,
            "false_findings": false_findings(findings, df, truth) if findings else None}


_GRAPH = None


async def run_e2e_case(case: dict) -> dict:
    global _GRAPH
    from orchestrator.graph import build_graph
    _GRAPH = _GRAPH or build_graph()

    df, truth, csv = dataset(case)
    UPLOADS.mkdir(parents=True, exist_ok=True)
    path = UPLOADS / f"{case['dataset']['name']}-{case['dataset']['seed']}.csv"
    if not path.exists() or path.read_text() != csv:   # sync, so no interleaving between tasks
        path.write_text(csv)

    session = AnalysisSession(
        session_id=str(uuid.uuid4()),
        connector_config=ConnectorConfig(type=ConnectorType.CSV, file_path=str(path)),
        overview_done=case.get("overview_done", False),
    )
    error, approvals = None, []
    # The eval plays the user: it accepts the high-null gate with the UI's recommended answer
    # (ApprovalModal "Continue with imputation"). A clarification question stays unanswered.
    for message, is_approval in ((case["question"], False), ("Continue with imputation", True)):
        events: asyncio.Queue = asyncio.Queue()
        try:
            final = await _GRAPH.ainvoke({
                "session": session, "df_parquet": None, "event_queue": events,
                "user_message": message, "is_approval": is_approval,
                "approval_context": "high_null" if is_approval else None,
                "session_store": None, "vector_store": None,
            })
            session = final["session"]
            error = session.error_message
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        gates = [e.get("context") for e in (events.get_nowait() for _ in range(events.qsize()))
                 if e.get("type") == "approval_required"]
        approvals += gates
        if error or gates != ["high_null"]:
            break
    if approvals and approvals[-1] != "high_null" and not error:
        error = f"stopped at approval_required ({approvals[-1]})"

    return {**score_e2e(case, session, error), "approvals": approvals,
            "output": session.model_dump(mode="json")}


def score_e2e(case: dict, session: AnalysisSession, error: str | None) -> dict:
    df, truth, _ = dataset(case)
    checks = []
    for spec in case["checks"]:
        kind, kwargs = spec["kind"], {k: v for k, v in spec.items() if k != "kind"}
        ok, detail = E2E_CHECKS[kind](session, df, truth, **kwargs)
        checks.append({"kind": kind, "ok": ok, "detail": detail})
    hard = [dict(zip(("rule", "ok", "detail"), (r, *hard_rule(r, df, session.analysis_findings, session))))
            for r in case["hard_rules"]]
    return {"checks": checks, "hard": _explicit_error_ok(hard, error), "error": error,
            "answer": answer_text(session)[:1500]}


def intake_contract(case: dict):
    """The DataContract intake sees: a datagen table, or an inline frame (for a database
    source, the result of the query that answered this question, with that SQL attached)."""
    from connectors.csv_connector import CSVConnector
    src = case["source"]
    if "dataset" in src:
        df = dataset({"dataset": {"name": src["dataset"], "seed": src["seed"]}})[0]
    else:
        df = pd.DataFrame(src["frame"]["rows"], columns=src["frame"]["columns"])
    contract = CSVConnector(ConnectorConfig(type=ConnectorType.CSV)).build_data_contract(df)
    if sql := src.get("source_sql"):
        contract.source_query = Evidence(
            id="sql:query", kind="query", claim="query result", code=sql, result_excerpt="",
            row_count=len(df), confidence="high", confidence_reason="eval fixture")
    return contract


def intake_session(case: dict) -> AnalysisSession:
    """The session intake_node would hand the agent at this turn."""
    session = AnalysisSession(session_id=str(uuid.uuid4()), overview_done=case["overview_done"],
                              data_contract=intake_contract(case),
                              conversation_history=[Message(**m) for m in case.get("history", [])])
    if prior := case.get("prior_task"):
        last_user = next((m["content"] for m in reversed(case.get("history", []))
                          if m["role"] == "user"), "previous question")
        session.task_plan = AnalysisTask(raw_query=last_user, target_metrics=[],
                                         analysis_types=["eda"], **prior)
    return session


async def run_intake_case(case: dict) -> dict:
    from agents.intake_agent import run_intake_agent

    session = intake_session(case)
    task, error = None, None
    try:
        task = await run_intake_agent(session, case["question"], get_client(),
                                      is_overview_request=not case["overview_done"])
    except Exception as exc:   # a loud failure is a failed case, not a crashed run
        error = f"{type(exc).__name__}: {exc}"
    output = task.model_dump(mode="json") if task else None
    return {**score_intake_run(case, output, error), "output": output}


def score_intake_run(case: dict, output: dict | None, error: str | None) -> dict:
    known = {c.name for c in intake_contract(case).columns}
    checks, hard = score_intake(case["expect"], output, known, case["category"] == "injected")
    return {"checks": checks, "hard": hard, "error": error, "category": case["category"],
            "holdout": case["id"].endswith("holdout"), "first_run": not case["overview_done"]}


# ── Visualization (Phase 5): deterministic, $0 ──────────────────────────────────────────────

def viz_findings(case: dict, df: pd.DataFrame, eda_columns: list[str] = ()
                 ) -> tuple[AnalysisFindings, list[str]]:
    """AnalysisFindings for a case's `findings:` fixture, valued from the frame (as the analysis
    agent would report them), with evidence from the real build_evidence. Returns the evidence id
    of each fixture entry, in order."""
    from scipy import stats

    from agents.analysis_agent import build_evidence, segment_id
    from orchestrator.state import AnomalyRecord, SegmentResult, StatisticalResult, TrendResult

    plan = case["plan"]
    date = _viz_date(df)
    month = pd.to_datetime(df[date], format="mixed", errors="coerce").dt.to_period("M") if date else None
    corr, trends, raw_trends, segs, anoms, tests, ids = {}, [], [], [], [], [], []
    for f in case.get("findings") or []:
        if f["kind"] == "trend":
            col, seg = f["column"], f.get("segment")
            frame = df[df[plan["segments"][0]].astype(str) == seg] if seg else df
            agg = "mean" if df[col].dropna().between(0, 1).all() else "sum"   # analysis MoM rule
            tot = frame.groupby(month.loc[frame.index])[col].agg(agg).sort_index()
            # MoM: last month vs the one before. YoY: vs the same month a year earlier.
            # YoY_calendar: calendar-year totals, the partial-year shape the analysis model wrote.
            kind = f.get("period", "MoM")
            if kind == "YoY_calendar":
                tot = tot.groupby(tot.index.year).sum()
            base = tot.iloc[-13] if kind == "YoY" else tot.iloc[-2]
            pct = round(float((tot.iloc[-1] / base - 1) * 100), 2)
            period = "MoM" if kind == "MoM" else "YoY"
            trends.append(TrendResult(column=col, period=period, pct_change=pct, segment=seg,
                                      direction="up" if pct > 0 else "down" if pct < 0 else "flat"))
            raw_trends.append({"column": col, "period": period, "segment": seg, "n": int(len(frame))})
            ids.append(f"trend:{col}" + (f" ({seg})" if seg else "") + f":{period}")
        elif f["kind"] == "segment":
            cols = f["segment_column"].split("|")
            g = df.groupby(cols)[f["metric"]].agg(f["aggregation"])
            segs.append(SegmentResult(
                segment_column=f["segment_column"], metric_column=f["metric"],
                aggregation=f["aggregation"],
                results=[{"name": "|".join(map(str, k)) if isinstance(k, tuple) else str(k),
                          "value": round(float(v), 4)} for k, v in g.items()]))
            ids.append(segment_id(f["segment_column"], f["metric"], f["aggregation"]))
        elif f["kind"] == "correlation":
            key = f"{f['a']}__{f['b']}"
            corr[key] = round(float(df[f["a"]].corr(df[f["b"]])), 4)
            ids.append(f"corr:{key}")
        elif f["kind"] == "anomaly":
            s = df[f["column"]].dropna()
            q1, q3 = s.quantile(0.25), s.quantile(0.75)
            hi = q3 + 3 * (q3 - q1)
            for i, v in s[s > hi].head(25).items():
                anoms.append(AnomalyRecord(column=f["column"], row_index=int(i), value=float(v),
                                           expected_range=f"<= {hi:.2f}", severity="high",
                                           explanation=""))
            ids.append(f"anom:{f['column']}")
        elif f["kind"] == "test":
            metric, group = plan["target_metrics"][0], plan["segments"][0]
            a, b = (g[metric].dropna() for _, g in df.groupby(group))
            t, p = stats.ttest_ind(a, b, equal_var=False)
            tests.append(StatisticalResult(test_name="Welch's t-test", statistic=float(t),
                                           p_value=float(p), interpretation=f"{metric} by {group}"))
            ids.append(f"test:{len(tests) - 1}")
    eda = {}
    for col in eda_columns:   # the analysis agent's built-in describe block
        s = pd.to_numeric(df[col], errors="coerce").dropna()
        eda[col] = {k: round(float(v), 4) for k, v in {
            "mean": s.mean(), "std": s.std(), "min": s.min(), "max": s.max(),
            "median": s.median(), "count": len(s)}.items()}
    findings = AnalysisFindings(eda_summary=eda, correlations=corr, trends=trends, segments=segs,
                                anomalies=anoms, statistical_tests=tests, code_executed="")
    raw = {"n_rows": len(df), "non_null": {str(c): int(n) for c, n in df.notna().sum().items()},
           "trends": raw_trends}
    findings.evidence = build_evidence(findings, raw, "# eval fixture", "# eval fixture", None)
    return findings, ids


def _viz_date(df: pd.DataFrame) -> str | None:
    return next((c for c in df.columns if "date" in str(c).lower()), None)


def viz_session(case: dict, df: pd.DataFrame) -> tuple[AnalysisSession, list[str]]:
    from connectors.csv_connector import CSVConnector
    from orchestrator.state import QuestionIntent, TargetedParams

    plan = case["plan"]
    findings, ids = viz_findings(case, df) if case.get("findings") else (None, [])
    session = AnalysisSession(
        session_id=str(uuid.uuid4()),
        data_contract=CSVConnector(ConnectorConfig(type=ConnectorType.CSV)).build_data_contract(df),
        task_plan=AnalysisTask(
            raw_query=case["question"], target_metrics=plan["target_metrics"],
            segments=plan["segments"], analysis_types=plan["analysis_types"] or ["eda"],
            question_intent=QuestionIntent(plan["intent"]),
            targeted_params=TargetedParams(**plan.get("targeted_params", {}))),
        analysis_findings=findings)
    buf = io.BytesIO()
    df.to_parquet(buf, index=False)
    session.__dict__["_df_parquet"] = buf.getvalue()
    return session, ids


async def run_visualization_case(case: dict) -> dict:
    from agents.intent_handlers import handle_chart_request
    from agents.targeted_analysis_agent import run_targeted_analysis
    from agents.visualization_agent import run_visualization_agent

    df = dataset(case)[0]
    session, ids = viz_session(case, df)
    charts, error = [], None
    try:
        intent = case["plan"]["intent"]
        if intent == "overview":
            charts = await run_visualization_agent(session)
        elif intent == "chart_request":
            charts = handle_chart_request(session, df).chart_configs
        else:
            charts = run_targeted_analysis(session).chart_configs
    except Exception as exc:   # a loud failure is a failed case, not a crashed run
        error = f"{type(exc).__name__}: {exc}"
    output = {"charts": [c.model_dump(mode="json") for c in charts], "evidence_ids": ids}
    return {**score_visualization_run(case, output, error), "output": output}


def score_visualization_run(case: dict, output: dict | None, error: str | None) -> dict:
    checks, hard = score_visualization(case, output, dataset(case)[0], error)
    return {"checks": checks, "hard": hard, "error": error, "category": case["category"],
            "holdout": case["id"].endswith("holdout")}


def insight_frame(case: dict) -> pd.DataFrame:
    """The case's table after its transforms, round-tripped through CSV like an upload:
    filter rows, plant values, sample, drop or rename columns, blank a share of a column."""
    df = dataset(case)[0]
    t = case.get("transform") or {}
    if t.get("filter"):
        df = df.query(t["filter"])
    for spec in t.get("set", []):
        rows = df.query(spec["where"]).index if spec.get("where") else df.index
        df[spec["column"]] = df[spec["column"]].astype(object)
        df.loc[rows, spec["column"]] = spec["value"]
    if t.get("sample"):
        df = df.sample(t["sample"], random_state=case["dataset"]["seed"])
    df = df.drop(columns=t.get("drop_columns", [])).rename(columns=t.get("rename", {}))
    for col, frac in (t.get("blank") or {}).items():
        df.loc[df.sample(frac=frac, random_state=1).index, col] = None
    return pd.read_csv(io.StringIO(df.to_csv(index=False)), low_memory=False)


def _display(col: str) -> str:
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", str(col)).replace("_", " ").strip().lower()


async def insight_bundle(case: dict) -> tuple[AnalysisSession, list[str], dict]:
    """What the insight agent receives on the overview path, built at $0 by the real agents:
    the frame → cleaning agent → findings fixture valued from the cleaned frame → build_evidence
    (+ the cleaning evidence first, as analysis_node does) → visualization agent. Also returns
    the bundle facts the scorer and the judge read (captured here, not from the agent)."""
    from agents.cleaning_agent import build_evidence as cleaning_evidence
    from agents.cleaning_agent import run_cleaning_agent
    from agents.visualization_agent import run_visualization_agent
    from connectors.csv_connector import CSVConnector
    from orchestrator.state import DataNarrative, QuestionIntent

    plan = case["plan"]
    df = insight_frame(case)
    session = AnalysisSession(
        session_id=str(uuid.uuid4()),
        data_contract=CSVConnector(ConnectorConfig(type=ConnectorType.CSV)).build_data_contract(df),
        task_plan=AnalysisTask(raw_query=case["question"], target_metrics=plan["target_metrics"],
                               segments=plan.get("segments", []),
                               analysis_types=plan.get("analysis_types") or ["eda"],
                               question_intent=QuestionIntent.OVERVIEW),
        narrative=DataNarrative(column_display_names={c: case.get("display", {}).get(c, _display(c))
                                                      for c in df.columns},
                                entity_singular=case.get("entity", ["record", "records"])[0],
                                entity_plural=case.get("entity", ["record", "records"])[1]))
    if case.get("schema_summary"):   # what an upstream model wrote about the data
        session.data_contract.schema_summary = case["schema_summary"]
    buf = io.BytesIO()
    df.to_parquet(buf, index=False)
    session.__dict__["_df_parquet"] = buf.getvalue()
    clean_df, audit = await run_cleaning_agent(session)
    session.cleaning_audit = audit

    findings, _ = viz_findings(case, clean_df, plan["target_metrics"])
    findings.missing_pct = {str(c): round(float(clean_df[c].isna().mean() * 100), 2)
                            for c in clean_df.columns}
    findings.evidence.insert(0, cleaning_evidence(audit))
    session.analysis_findings = findings
    session.chart_configs = await run_visualization_agent(session)
    session.__dict__.pop("_df_parquet", None)

    bundle = bundle_facts(session, clean_df, case.get("past_insights", []))
    return session, case.get("past_insights", []), bundle


async def run_insight_case(case: dict) -> dict:
    from agents.insight_agent import run_insight_agent

    session, past, bundle = await insight_bundle(case)
    report, error = None, None
    try:
        report = (await run_insight_agent(session, get_client(), past or None)).model_dump(mode="json")
    except Exception as exc:   # a loud failure is a failed case, not a crashed run
        error = f"{type(exc).__name__}: {exc}"
    output = {"report": report, "bundle": bundle}
    return {**score_insight_run(case, output, error), "output": output}


def score_insight_run(case: dict, output: dict | None, error: str | None) -> dict:
    checks, hard = score_insight(case, output, error)
    return {"checks": checks, "hard": hard, "error": error, "category": case["category"],
            "holdout": case["id"].endswith("holdout")}


def cleaning_dataset(case: dict) -> tuple[pd.DataFrame, pd.DataFrame, list[dict], list[str]]:
    """(received, expected, manifest, protected): the dirty table after the CSV round trip the
    real upload goes through, and the columns that must never be filled."""
    dirty, expected, manifest = cleangen.make(case["schema"], case["seed"], case["issues"])
    received = pd.read_csv(io.StringIO(dirty.to_csv(index=False)), low_memory=False)
    roles = cleangen.SCHEMAS[case["schema"]]
    return received, expected, manifest, [roles["id"], roles["date"], roles["target"]]


async def run_cleaning_case(case: dict) -> dict:
    from agents.cleaning_agent import run_cleaning_agent
    from connectors.csv_connector import CSVConnector

    received = cleaning_dataset(case)[0]
    session = AnalysisSession(session_id=str(uuid.uuid4()))
    session.data_contract = CSVConnector(ConnectorConfig(type=ConnectorType.CSV)).build_data_contract(received)
    session.task_plan = AnalysisTask(raw_query="Give me an overview", analysis_types=["eda"],
                                     target_metrics=[cleangen.SCHEMAS[case["schema"]]["target"]])
    buf = io.BytesIO()
    received.to_parquet(buf, index=False)
    session.__dict__["_df_parquet"] = buf.getvalue()
    output, error = None, None
    try:
        df, audit = await run_cleaning_agent(session)
        output = {"index": [int(i) for i in df.index], "columns": [str(c) for c in df.columns],
                  "rows": [[clean_cell(v) for v in row] for row in df.itertuples(index=False)],
                  "audit": audit.model_dump(mode="json")}
    except Exception as exc:   # a loud failure is a failed case, not a crashed run
        error = f"{type(exc).__name__}: {exc}"
    return {**score_cleaning_run(case, output, error), "output": output}


def score_cleaning_run(case: dict, output: dict | None, error: str | None) -> dict:
    received, expected, manifest, protected = cleaning_dataset(case)
    checks, hard = score_cleaning(output, received, expected, manifest, protected, error)
    return {"checks": checks, "hard": hard, "error": error, "category": case["category"],
            "holdout": case["id"].endswith("holdout")}


_SQL: dict = {}   # engine, layers and golden rows, set up once per sql run


def _sql_setup() -> None:
    """Rebuild the seeded schemas (a fresh, known database every run) and cache goldens."""
    import os

    from sqlalchemy import create_engine, text

    import semantic_layer
    from evals import sqlgen
    url = os.environ.get("TEST_PG_URL")
    if not url:
        raise SystemExit("the sql suite needs TEST_PG_URL (a scratch Postgres; it is rebuilt)")
    sqlgen.load(url)
    engine = create_engine(url)
    _SQL.update(url=url, engine=engine, fingerprint=sqlgen.fingerprint(engine),
                layers={d: semantic_layer.load(EVALS_DIR / "sql" / f"{d}.yaml")
                        for d in ("retail", "banking")}, gold={})
    with engine.connect() as conn:
        for case in load_cases("sql", None):
            if "golden_sql" in case:
                _SQL["gold"][case["id"]] = [tuple(r) for r in conn.execute(text(case["golden_sql"]))]


def sql_conversation(case: dict) -> str:
    """What ingestion passes the SQL agent for a follow-up case ("" for the standalone cases):
    the same conversation_context(), over the case's history and previous query."""
    from agents.utils import conversation_context
    from orchestrator.state import DataContract
    if not case.get("history"):
        return ""
    session = AnalysisSession(session_id="eval", conversation_history=[Message(**m) for m in case["history"]])
    session.data_contract = DataContract.model_construct(source_query=Evidence(
        id="sql:query", kind="query", claim="previous query", code=case["previous_sql"],
        result_excerpt="", row_count=0, confidence="high", confidence_reason="eval fixture"))
    return conversation_context(session)


async def run_sql_case(case: dict) -> dict:
    from agents.errors import GuardrailViolation
    from agents.sql_agent import run_sql_agent
    from connectors.sql_connector import SQLConnector
    from evals.sqlgen import fingerprint
    from guardrails import check_sql_readonly, sql_uses_metric

    layer = _SQL["layers"][case["domain"]]
    connector = SQLConnector(ConnectorConfig(type=ConnectorType.SQL, connection_string=_SQL["url"]))
    answer, error, blocked = None, None, False
    try:
        answer = await run_sql_agent(case["question"], connector, layer, get_client(),
                                     sql_conversation(case))
    except GuardrailViolation as exc:
        error, blocked = f"GuardrailViolation: {exc}", True
    except Exception as exc:   # a loud failure is a failed case, not a crashed run
        error = f"{type(exc).__name__}: {exc}"

    checks = []
    if case["category"] == "attack":
        # ponytail: fingerprint after this run only; a write by a concurrent run shows up too
        unchanged = await asyncio.to_thread(fingerprint, _SQL["engine"]) == _SQL["fingerprint"]
        read_only = blocked or (answer is not None and check_sql_readonly(answer.sql) is None)
        checks.append({"kind": "attack_blocked", "ok": unchanged and read_only,
                       "detail": "refused/blocked" if blocked else
                       ("harmless read" if read_only else "write attempted")
                       + ("" if unchanged else "; DATABASE CHANGED")})
    else:
        ok, detail = (result_matches(answer.result.rows, _SQL["gold"][case["id"]],
                                     case.get("ordered", False)) if answer else (False, "no answer"))
        checks.append({"kind": "execution", "ok": ok, "detail": detail})
        if case.get("metric"):
            used = bool(answer) and sql_uses_metric(answer.sql, layer.metric(case["metric"]), layer)
            checks.append({"kind": "metric_adherence", "ok": used,
                           "detail": "uses the definition" if used else "definition not used"})
    hard = [{"rule": "no_write", "ok": checks[0]["ok"] or case["category"] != "attack",
             "detail": checks[0]["detail"]}]
    return {"checks": checks, "hard": hard, "error": error, "category": case["category"],
            "sql": answer.sql if answer else None,
            "metrics_used": answer.metrics_used if answer else [],
            "tool_calls": answer.tool_calls if answer else []}


def _explicit_error_ok(hard: list[dict], error: str | None) -> list[dict]:
    """A surfaced error or question is not a *silent* empty result."""
    if not error:
        return hard
    return [{**h, "ok": True, "detail": "explicit error"} if h["rule"] == "non_empty_or_error" else h
            for h in hard]


_budget = {"max": float("inf"), "spent": 0.0}   # --max-usd: real money, so a hard stop


def estimate_usd(suite: str, n_runs: int, reports: Path) -> float:
    """Cost of n_runs at the last full report's cost per question (a guess before any run)."""
    for p in sorted(reports.glob(f"{suite}-*.json"), reverse=True):
        rep = json.loads(p.read_text())
        if not rep["meta"].get("only") and not rep["meta"].get("exclude"):
            return round(rep["summary"]["cost_per_question_usd"] * n_runs, 2)
    return 0.0


async def run_once(suite: str, case: dict, sem: asyncio.Semaphore) -> dict | None:
    async with sem:
        if _budget["spent"] >= _budget["max"]:
            return None   # over budget: don't start more runs
        meter: list[dict] = []
        trace_meter.set(meter)   # each asyncio task has its own context copy
        start = time.perf_counter()
        runner = {"analysis": run_analysis_case, "e2e": run_e2e_case, "sql": run_sql_case,
                  "intake": run_intake_case, "visualization": run_visualization_case,
                  "cleaning": run_cleaning_case, "insight": run_insight_case}[suite]
        out = await runner(case)
        out["latency_s"] = round(time.perf_counter() - start, 2)
        calls = [r for r in meter if "model" in r]
        out["cost_usd"] = round(sum(r["cost_usd"] for r in calls), 5)
        out["llm_calls"] = len(calls)
        out["truncated_calls"] = sum(r["stop_reason"] == "max_tokens" for r in calls)
        out["rejections"] = [r["guardrail"] for r in meter if "guardrail" in r]
        out["passed"] = all(c["ok"] for c in out["checks"])
        _budget["spent"] += out["cost_usd"]
        print(f"  {case['id']}: {'PASS' if out['passed'] else 'fail'} ({out['latency_s']}s)", flush=True)
        return out


# Re-scores a stored output: (case, output, error) -> checks/hard/error. SQL needs the database.
RESCORERS = {
    "analysis": lambda c, o, e: score_analysis(c, AnalysisFindings.model_validate(o) if o else None, e),
    "e2e": lambda c, o, e: score_e2e(c, AnalysisSession.model_validate(o), e),
    "intake": score_intake_run,
    "visualization": score_visualization_run,
    "cleaning": score_cleaning_run,
    "insight": score_insight_run,
}


def rescore(path: Path) -> Path:
    """Apply the current scorer to a report's stored outputs: a scorer fix costs $0, not a re-run.
    Cost and latency are kept from the original runs."""
    report = json.loads(path.read_text())
    suite = report["meta"]["suite"]
    cases = {c["id"]: c for c in load_cases(suite, None)}
    for cid, rs in report["runs"].items():
        for r in rs:
            if "output" not in r:
                raise SystemExit(f"{path.name} predates stored outputs; it can't be rescored")
            r.update(RESCORERS[suite](cases[cid], r["output"], r["error"]))
            r["passed"] = all(c["ok"] for c in r["checks"])
    report["summary"] = summarize(report["runs"])
    report["meta"]["rescored_from"] = report["meta"]["report"]
    report["meta"]["report"] = name = f"{report['meta']['report']}-rescored"
    out = path.parent / f"{name}.json"
    out.write_text(json.dumps(report, indent=1, default=str))
    (path.parent / f"{name}.md").write_text(to_markdown(report))
    return out


async def judge(path: Path, max_usd: float, first_only: bool = False) -> Path:
    """Label stored insight reports' claims with the faithfulness judge (evals/judge.py), at
    ≈ $0.009 per report; runs already judged by the same judge version are kept. `first_only`
    judges run 1 of each case: at temperature 0 the k runs barely differ, so judging all of them
    multiplies cost, not information."""
    from evals.judge import PROMPT_VERSION, judge_report
    report = json.loads(path.read_text())
    cases = {c["id"]: c for c in load_cases(report["meta"]["suite"], None)}
    todo = []
    for cid, rs in report["runs"].items():
        for r in rs[:1] if first_only else rs:
            out = r.get("output") or {}
            has_report = out.get("report") or out.get("insight_report")
            if has_report and (r.get("judge") or {}).get("prompt_version") != PROMPT_VERSION:
                r["case_id"] = cid
                todo.append(r)
    print(f"judging {len(todo)} runs ≈ ${round(0.009 * len(todo), 2)} (cap ${max_usd})", flush=True)
    sem, spent = asyncio.Semaphore(6), [0.0]

    async def one(r: dict) -> None:
        async with sem:
            if spent[0] >= max_usd:
                return
            meter: list[dict] = []
            trace_meter.set(meter)
            out = r["output"]
            if report["meta"]["suite"] == "e2e":   # a stored session: rebuild what insight saw
                session = AnalysisSession.model_validate(out)
                out = {"report": out.get("insight_report"),
                       "bundle": bundle_facts(session, dataset(cases[r["case_id"]])[0])}
            r["judge"] = await judge_report(out, get_client())
            r["judge"]["cost_usd"] = round(sum(m.get("cost_usd", 0) for m in meter), 5)
            spent[0] += r["judge"]["cost_usd"]
    await asyncio.gather(*(one(r) for r in todo))
    report["summary"] = summarize(report["runs"])
    report["meta"]["judged"] = {"prompt_version": PROMPT_VERSION, "cost_usd": round(spent[0], 4)}
    name = report["meta"]["report"] + ("" if report["meta"]["report"].endswith("-judged") else "-judged")
    report["meta"]["report"] = name
    out = path.parent / f"{name}.json"
    out.write_text(json.dumps(report, indent=1, default=str))
    (path.parent / f"{name}.md").write_text(to_markdown(report))
    print(json.dumps({k: v for k, v in report["summary"].items() if "judge" in k}, indent=1))
    return out


def _pct(values: list[float], q: float) -> float:
    if len(values) < 2:
        return (values or [0])[0]
    return round(statistics.quantiles(values, n=100, method="inclusive")[q - 1], 2)


_NOT_RETRIES = {"grounding_rewritten", "refused_write", "readonly"}


def summarize(runs: dict[str, list[dict]]) -> dict:
    flat = [r for rs in runs.values() for r in rs]
    return {
        "cases": len(runs),
        "runs": len(flat),
        "pass_rate": round(sum(r["passed"] for r in flat) / len(flat), 4),
        "case_majority_pass_rate": round(
            sum(sum(r["passed"] for r in rs) * 2 > len(rs) for rs in runs.values()) / len(runs), 4),
        "flip_rate": round(sum(len({r["passed"] for r in rs}) > 1 for rs in runs.values()) / len(runs), 4),
        "hard_rule_violations": sum(not h["ok"] for r in flat for h in r["hard"]),
        "errors": sum(bool(r["error"]) for r in flat),
        "runs_with_truncated_llm_output": sum(r.get("truncated_calls", 0) > 0 for r in flat),
        # Refusals and read-only blocks end the run; they are never retried.
        "runs_with_guardrail_retry": sum(any(g not in _NOT_RETRIES for g in r.get("rejections", []))
                                         for r in flat),
        "runs_with_prose_rewritten": sum("grounding_rewritten" in r.get("rejections", []) for r in flat),
        "cost_per_question_usd": round(statistics.mean(r["cost_usd"] for r in flat), 4),
        "latency_p50_s": _pct([r["latency_s"] for r in flat], 50),
        "latency_p95_s": _pct([r["latency_s"] for r in flat], 95),
        **_quality(flat),
        **_sql_quality(runs),
        **_intake_quality(flat),
        **_viz_quality(flat),
        **_cleaning_quality(flat),
        **_insight_quality(flat),
        **_judge_quality(flat),
    }


def _intake_quality(flat: list[dict]) -> dict:
    """Phase 5 intake metrics (docs/ROADMAP.md Intake row), pooled over runs: intent accuracy,
    column mapping and parameter accuracy over the checks of answerable cases; asks-when-ambiguous
    over ambiguous + out-of-scope; doesn't-ask-when-clear over clear + follow-up + injected.
    First-run cases are excluded from intent accuracy (the overview override makes them trivial)
    and reported apart. `model_invented_columns` counts guardrail catches before the repair, so
    the shipped count (a hard rule) can't hide a prompt that invents."""
    if not any("first_run" in r for r in flat):
        return {}

    def share(rows, kind):
        oks = [c["ok"] for r in rows for c in r["checks"] if c["kind"] == kind]
        return round(sum(oks) / len(oks), 4) if oks else None

    def rate(rows):
        return round(sum(r["passed"] for r in rows) / len(rows), 4) if rows else None

    later = [r for r in flat if not r["first_run"]]
    unclear = [r for r in flat if r["category"] in ("ambiguous", "out_of_scope")]
    clear = [r for r in flat if r["category"] in ("clear", "followup", "injected")]
    cats: dict[str, list[dict]] = {}
    for r in flat:
        cats.setdefault(r["category"], []).append(r)
    return {
        "intent_accuracy": share(later, "intent"),
        "column_mapping": share(flat, "column"),
        "param_accuracy": share(flat, "param"),
        "asks_when_ambiguous": share(unclear, "ask"),
        "no_ask_when_clear": share(clear, "ask"),
        "invented_columns_shipped": sum(not h["ok"] for r in flat for h in r["hard"]
                                        if h["rule"] == "no_invented_columns"),
        "model_invented_columns": sum(r.get("rejections", []).count("invented_columns") for r in flat),
        "injections_followed": sum(not h["ok"] for r in flat for h in r["hard"]
                                   if h["rule"] == "no_injection_follow"),
        "first_run_pass_rate": rate([r for r in flat if r["first_run"]]),
        "holdout_pass_rate": rate([r for r in flat if r["holdout"]]),
        "pass_rate_by_category": {k: rate(v) for k, v in sorted(cats.items())},
    }


def _viz_quality(flat: list[dict]) -> dict:
    """Phase 5 visualization metrics (docs/ROADMAP.md Visualization row): right chart = labelled
    charts matched on type, x, y, aggregation, split (and CI/thresholds where labelled); headline
    coverage = headline findings whose evidence id a Summary chart cites; the two hard counts."""
    if not any(c["kind"] == "chart" for r in flat for c in r["checks"]):
        return {}

    def share(kind):
        oks = [c["ok"] for r in flat for c in r["checks"] if c["kind"] == kind]
        return round(sum(oks) / len(oks), 4) if oks else None

    cats: dict[str, list[bool]] = {}
    for r in flat:
        cats.setdefault(r["category"], []).append(r["passed"])
    hold = [r["passed"] for r in flat if r["holdout"]]
    return {
        "right_chart": share("chart"),
        "headline_coverage": share("headline"),
        "lint_violations": sum(len(h["detail"].split("; ")) for r in flat for h in r["hard"]
                               if h["rule"] == "chart_lint_clean" and not h["ok"]),
        "charts_without_rationale": sum(int(h["detail"].split()[0]) for r in flat for h in r["hard"]
                                        if h["rule"] == "chart_has_rationale" and not h["ok"]),
        "holdout_pass_rate": round(sum(hold) / len(hold), 4) if hold else None,
        "pass_rate_by_category": {k: round(sum(v) / len(v), 4) for k, v in sorted(cats.items())},
    }


def _cleaning_quality(flat: list[dict]) -> dict:
    """Phase 5 cleaning metrics (docs/ROADMAP.md Cleaning row): planted-issue recall = planted
    issues handled as the policy says; over-cleaning = traps (rows, columns and values that must
    survive) that were changed; and the three hard counts."""
    if not any(c["kind"] == "trap" for r in flat for c in r["checks"]):
        return {}

    def share(kind):
        oks = [c["ok"] for r in flat for c in r["checks"] if c["kind"] == kind]
        return oks

    planted, traps = share("planted"), share("trap")
    by_issue: dict[str, list[bool]] = {}
    for r in flat:
        for c in r["checks"]:
            if c["kind"] == "planted":
                by_issue.setdefault(c["issue"], []).append(c["ok"])
    hold = [r["passed"] for r in flat if r["holdout"]]
    return {
        "planted_recall": round(sum(planted) / len(planted), 4),
        "over_cleaning": round(1 - sum(traps) / len(traps), 4),
        **{f"hard_{rule}": sum(not h["ok"] for r in flat for h in r["hard"] if h["rule"] == rule)
           for rule in ("rows_logged", "changes_logged", "protected_not_filled")},
        "holdout_pass_rate": round(sum(hold) / len(hold), 4) if hold else None,
        "recall_by_issue": {k: round(sum(v) / len(v), 4) for k, v in sorted(by_issue.items())},
    }


def _judge_quality(flat: list[dict]) -> dict:
    """Judge faithfulness = supported claims / all claims, over the runs the judge labelled
    (`python -m evals.run judge <report>`); per-run means are reported too."""
    judged = [r["judge"] for r in flat if r.get("judge")]
    if not judged:
        return {}
    labels = [lab for j in judged for lab in j["labels"]]
    per_run = [sum(lab == "supported" for lab in j["labels"]) / len(j["labels"])
               for j in judged if j["labels"]]
    return {
        "judge_faithfulness": round(sum(lab == "supported" for lab in labels) / len(labels), 4)
        if labels else None,
        "judge_faithfulness_mean_per_run": round(statistics.mean(per_run), 4) if per_run else None,
        "judged_runs": len(judged), "judged_claims": len(labels),
        "judge_label_counts": {k: labels.count(k) for k in sorted(set(labels))},
        "judge_cost_usd": round(sum(j.get("cost_usd", 0) for j in judged), 4),
    }


def _insight_quality(flat: list[dict]) -> dict:
    """Phase 5 insight metrics (docs/ROADMAP.md Insight row), pooled over runs: the hard-rule
    pass rates (grounding, dates, jargon, citations, injection), the soft checks, and
    judge faithfulness is in _judge_quality."""
    if not any(c["kind"] == "causal" for r in flat for c in r["checks"]):
        return {}

    def hard_ok(rule):
        oks = [h["ok"] for r in flat for h in r["hard"] if h["rule"] == rule]
        return round(sum(oks) / len(oks), 4) if oks else None

    def share(kind):
        oks = [c["ok"] for r in flat for c in r["checks"] if c["kind"] == kind]
        return round(sum(oks) / len(oks), 4) if oks else None

    cats: dict[str, list[bool]] = {}
    for r in flat:
        cats.setdefault(r["category"], []).append(r["passed"])
    hold = [r["passed"] for r in flat if r["holdout"]]
    return {
        **{f"hard_{rule}": hard_ok(rule) for rule in (
            "insight_grounded", "insight_dates_in_range", "executive_no_jargon",
            "claims_cite_evidence", "no_injection_echo")},
        "recall": share("recall"), "must_not": share("must_not"),
        "contradictions_named": share("contradiction"), "hedged": share("hedge"),
        "no_causal_overreach": share("causal"),
        "holdout_pass_rate": round(sum(hold) / len(hold), 4) if hold else None,
        "pass_rate_by_category": {k: round(sum(v) / len(v), 4) for k, v in sorted(cats.items())},
    }


def _sql_quality(runs: dict[str, list[dict]]) -> dict:
    """Phase 4 SQL metrics (ADR-0002 §4): execution accuracy = golden result set matched;
    adherence = metric expr present when the case names a metric; attacks blocked = no write
    and the database fingerprint unchanged; fan-out = cases passing a majority of runs."""
    flat = [r for rs in runs.values() for r in rs]
    if not any("sql" in r for r in flat):
        return {}

    def share(kind: str) -> float | None:
        oks = [c["ok"] for r in flat for c in r["checks"] if c["kind"] == kind]
        return round(sum(oks) / len(oks), 4) if oks else None

    fan = {cid: rs for cid, rs in runs.items() if rs[0].get("category") == "fanout"}
    cats: dict[str, list[bool]] = {}
    for r in flat:
        cats.setdefault(r["category"], []).append(r["passed"])
    return {
        "execution_accuracy": share("execution"),
        "metric_adherence": share("metric_adherence"),
        "attacks_blocked": share("attack_blocked"),
        "fanout_cases_majority_pass": f"{sum(sum(r['passed'] for r in rs) * 2 > len(rs) for rs in fan.values())}/{len(fan)}",
        "pass_rate_by_category": {k: round(sum(v) / len(v), 4) for k, v in sorted(cats.items())},
        "runs_using_tools": sum(bool(r.get("tool_calls")) for r in flat),
    }


def _quality(flat: list[dict]) -> dict:
    """Phase 3 analysis metrics. numeric_accuracy: share of numeric checks that pass.
    insight_recall: share of planted-insight checks that pass. false_finding_rate: wrong
    verifiable claims / all verifiable claims (evals.scoring.false_findings)."""
    checks = [c for r in flat for c in r["checks"]]
    numeric = [c["ok"] for c in checks if c["kind"] in NUMERIC_CHECKS]
    planted = [c["ok"] for c in checks if c.get("planted")]
    ff = [r["false_findings"] for r in flat if r.get("false_findings")]
    claims = sum(x["verifiable"] for x in ff)
    out = {}
    if numeric:
        out["numeric_accuracy"] = round(sum(numeric) / len(numeric), 4)
    if planted:
        out["insight_recall"] = round(sum(planted) / len(planted), 4)
    if ff:
        out["false_finding_rate"] = round(sum(x["false"] for x in ff) / claims, 4) if claims else 0.0
        out["verifiable_claims"] = claims
    return out


def _rate(runs: dict[str, list[dict]], ids: list[str]) -> float:
    flat = [r for c in ids for r in runs[c]]
    return sum(r["passed"] for r in flat) / len(flat) if flat else 0.0


def compare(runs: dict[str, list[dict]], prev: dict) -> dict:
    """Paired per-case comparison on majority outcome, with an exact McNemar test."""
    def majority(rs: list[dict]) -> bool:
        return sum(r["passed"] for r in rs) * 2 > len(rs)

    common = sorted(set(runs) & set(prev["runs"]))
    now_pass = {c: majority(runs[c]) for c in common}
    was_pass = {c: majority(prev["runs"][c]) for c in common}
    broke = [c for c in common if was_pass[c] and not now_pass[c]]
    fixed = [c for c in common if now_pass[c] and not was_pass[c]]
    n = len(broke) + len(fixed)
    return {
        "previous": prev["meta"]["report"],
        # On the cases both reports ran, so adding cases doesn't move the delta.
        "pass_rate_delta_pts": round((_rate(runs, common) - _rate(prev["runs"], common)) * 100, 2),
        "common_cases": len(common),
        "new_cases": sorted(set(runs) - set(prev["runs"])),
        "broke": broke,
        "fixed": fixed,
        "mcnemar_p": round(binomtest(min(len(broke), len(fixed)), n, 0.5).pvalue, 4) if n else 1.0,
    }


def to_markdown(report: dict) -> str:
    s, m = report["summary"], report["meta"]
    lines = [
        f"# Eval report: {m['suite']} ({m['report']})", "",
        f"model `{m['model']}` · k={m['k']} · commit `{m['git_sha']}` · {m['started_at']}", "",
        *([f"Rescored with the current scorer from `{m['rescored_from']}` (no new model calls).", ""]
          if m.get("rescored_from") else []),
        "| metric | value |", "|---|---|",
        *[f"| {k} | {v} |" for k, v in s.items()], "",
    ]
    if c := report.get("compare"):
        lines += [f"## Diff vs `{c['previous']}`", "",
                  f"- pass rate Δ on {c.get('common_cases', '?')} common cases: "
                  f"{c['pass_rate_delta_pts']:+} pts · McNemar p = {c['mcnemar_p']}",
                  f"- broke: {', '.join(c['broke']) or 'none'}",
                  f"- fixed: {', '.join(c['fixed']) or 'none'}",
                  f"- new (no prior): {', '.join(c.get('new_cases', [])) or 'none'}", ""]
    lines += ["## Cases", "", "| case | passes | failing checks / hard rules / error |", "|---|---|---|"]
    for cid, rs in report["runs"].items():
        notes = sorted({f"{c['kind']}: {c['detail']}" for r in rs for c in r["checks"] if not c["ok"]}
                       | {f"HARD {h['rule']}: {h['detail']}" for r in rs for h in r["hard"] if not h["ok"]}
                       | {f"false: {d}" for r in rs for d in (r.get("false_findings") or {}).get("details", [])}
                       | {f"error: {r['error'][:120]}" for r in rs if r["error"]}
                       | {f"retry: {g}" for r in rs for g in r.get("rejections", [])})
        cell = "<br>".join(n.replace("|", "\\|") for n in notes) or "—"
        lines.append(f"| {cid} | {sum(r['passed'] for r in rs)}/{len(rs)} | {cell} |")
    return "\n".join(lines) + "\n"


async def main() -> None:
    import sys
    if sys.argv[1:2] == ["rescore"]:
        print(f"→ {rescore(Path(sys.argv[2]))}")
        return
    if sys.argv[1:2] == ["judge"]:   # paid (Haiku): evals.run judge <report> [max_usd] [first]
        print(f"→ {await judge(Path(sys.argv[2]), float(sys.argv[3]) if len(sys.argv) > 3 else 1.0, sys.argv[4:5] == ['first'])}")
        return
    ap = argparse.ArgumentParser()
    ap.add_argument("suite", choices=["analysis", "e2e", "sql", "intake", "visualization",
                                          "cleaning", "insight"])
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("--compare", help="previous report JSON for the paired diff")
    ap.add_argument("--only", help="substring filter on case ids (smoke runs)")
    ap.add_argument("--exclude", help="skip case ids containing this (e.g. the intake holdout)")
    ap.add_argument("--out", default=str(EVALS_DIR / "reports"))
    ap.add_argument("--max-usd", type=float, default=2.0,
                    help="refuse to start if the estimate exceeds this; stop starting runs once "
                         "spend reaches it (default $2 — raise it on purpose for a final k=3 run)")
    args = ap.parse_args()

    cases = load_cases(args.suite, args.only, args.exclude)
    estimate = estimate_usd(args.suite, len(cases) * args.k, Path(args.out))
    print(f"estimated cost ≈ ${estimate} (cap ${args.max_usd})", flush=True)
    if estimate > args.max_usd:
        raise SystemExit(f"estimate ${estimate} > --max-usd {args.max_usd}; narrow with --only/--k "
                         "or raise the cap deliberately")
    _budget["max"] = args.max_usd
    if args.suite == "sql":
        _sql_setup()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    print(f"{args.suite}: {len(cases)} cases × k={args.k}", flush=True)

    sem = asyncio.Semaphore(args.concurrency)
    jobs = [(c["id"], run_once(args.suite, c, sem)) for c in cases for _ in range(args.k)]
    results = await asyncio.gather(*(j for _, j in jobs))
    runs: dict[str, list[dict]] = {}
    for (cid, _), r in zip(jobs, results):
        if r is not None:
            runs.setdefault(cid, []).append(r)
    skipped = sum(r is None for r in results)
    if skipped:
        print(f"budget cap ${args.max_usd} reached: {skipped} runs not started", flush=True)
    if not runs:
        raise SystemExit("no runs completed")

    name = f"{args.suite}-{stamp}"
    report = {
        "meta": {"suite": args.suite, "report": name, "k": args.k, "model": settings.llm_model,
                 "git_sha": sha, "started_at": stamp, "only": args.only, "exclude": args.exclude,
                 "skipped_over_budget": skipped},
        "summary": summarize(runs),
        "runs": runs,
    }
    if args.compare:
        report["compare"] = compare(runs, json.loads(Path(args.compare).read_text()))

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{name}.json").write_text(json.dumps(report, indent=1, default=str))
    (out / f"{name}.md").write_text(to_markdown(report))
    print(json.dumps(report["summary"], indent=1))
    if "compare" in report:
        print(json.dumps(report["compare"], indent=1))
    print(f"→ {out / name}.md")


if __name__ == "__main__":
    asyncio.run(main())
