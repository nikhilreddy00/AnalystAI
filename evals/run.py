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
from evals import datagen
from evals.scoring import (
    ANALYSIS_CHECKS,
    E2E_CHECKS,
    NUMERIC_CHECKS,
    answer_text,
    false_findings,
    hard_rule,
    result_matches,
)
from llm.client import get_client, trace_meter
from orchestrator.state import (
    AnalysisSession,
    AnalysisTask,
    ConnectorConfig,
    ConnectorType,
)

EVALS_DIR = Path(__file__).parent
UPLOADS = EVALS_DIR.parent / "uploads" / "evals"   # the CSV connector only reads uploads/


def load_cases(suite: str, only: str | None) -> list[dict]:
    cases = [yaml.safe_load(p.read_text()) for p in sorted((EVALS_DIR / "cases" / suite).glob("*.yaml"))]
    return [c for c in cases if not only or only in c["id"]]


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

    checks = []
    for spec in case["checks"]:
        kind, kwargs = spec["kind"], {k: v for k, v in spec.items() if k != "kind"}
        ok, detail = E2E_CHECKS[kind](session, df, truth, **kwargs)
        checks.append({"kind": kind, "ok": ok, "detail": detail})
    hard = [dict(zip(("rule", "ok", "detail"), (r, *hard_rule(r, df, session.analysis_findings, session))))
            for r in case["hard_rules"]]
    return {"checks": checks, "hard": _explicit_error_ok(hard, error), "error": error,
            "approvals": approvals, "answer": answer_text(session)[:1500]}


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
        answer = await run_sql_agent(case["question"], connector, layer, get_client())
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
        if not rep["meta"].get("only"):
            return round(rep["summary"]["cost_per_question_usd"] * n_runs, 2)
    return 0.0


async def run_once(suite: str, case: dict, sem: asyncio.Semaphore) -> dict | None:
    async with sem:
        if _budget["spent"] >= _budget["max"]:
            return None   # over budget: don't start more runs
        meter: list[dict] = []
        trace_meter.set(meter)   # each asyncio task has its own context copy
        start = time.perf_counter()
        runner = {"analysis": run_analysis_case, "e2e": run_e2e_case, "sql": run_sql_case}[suite]
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


def _pct(values: list[float], q: float) -> float:
    if len(values) < 2:
        return (values or [0])[0]
    return round(statistics.quantiles(values, n=100, method="inclusive")[q - 1], 2)


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
        "runs_with_guardrail_retry": sum(any(g != "grounding_rewritten" for g in r.get("rejections", []))
                                         for r in flat),
        "runs_with_prose_rewritten": sum("grounding_rewritten" in r.get("rejections", []) for r in flat),
        "cost_per_question_usd": round(statistics.mean(r["cost_usd"] for r in flat), 4),
        "latency_p50_s": _pct([r["latency_s"] for r in flat], 50),
        "latency_p95_s": _pct([r["latency_s"] for r in flat], 95),
        **_quality(flat),
        **_sql_quality(runs),
    }


def _sql_quality(runs: dict[str, list[dict]]) -> dict:
    """Phase 4 SQL metrics (ADR-0002 §4): execution accuracy = golden result set matched;
    adherence = metric expr present when the case names a metric; attacks blocked = no write
    and the database fingerprint unchanged; fan-out = cases passing a majority of runs."""
    flat = [r for rs in runs.values() for r in rs]
    if not any("category" in r for r in flat):
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
    ap = argparse.ArgumentParser()
    ap.add_argument("suite", choices=["analysis", "e2e", "sql"])
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("--compare", help="previous report JSON for the paired diff")
    ap.add_argument("--only", help="substring filter on case ids (smoke runs)")
    ap.add_argument("--out", default=str(EVALS_DIR / "reports"))
    ap.add_argument("--max-usd", type=float, default=2.0,
                    help="refuse to start if the estimate exceeds this; stop starting runs once "
                         "spend reaches it (default $2 — raise it on purpose for a final k=3 run)")
    args = ap.parse_args()

    cases = load_cases(args.suite, args.only)
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
                 "git_sha": sha, "started_at": stamp, "only": args.only,
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
