"""
Eval runner (ADR-0002).

    uv run python -m evals.run analysis --k 3
    uv run python -m evals.run e2e --k 3 --compare evals/reports/e2e-<ts>.json
    uv run python -m evals.run analysis --k 1 --only sales-trend   # smoke subset

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
from evals.scoring import ANALYSIS_CHECKS, E2E_CHECKS, answer_text, hard_rule
from llm.client import trace_meter
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
        findings = await run_analysis_agent(session)
    except Exception as exc:   # a loud failure is a failed case, not a crashed run
        error = f"{type(exc).__name__}: {exc}"

    checks = []
    for spec in case["checks"]:
        kind, kwargs = spec["kind"], {k: v for k, v in spec.items() if k != "kind"}
        ok, detail = ANALYSIS_CHECKS[kind](findings, df, truth, **kwargs) if findings else (False, "no findings")
        checks.append({"kind": kind, "ok": ok, "detail": detail})
    hard = [dict(zip(("rule", "ok", "detail"), (r, *hard_rule(r, df, findings)))) for r in case["hard_rules"]]
    return {"checks": checks, "hard": _explicit_error_ok(hard, error), "error": error}


_GRAPH = None


async def run_e2e_case(case: dict) -> dict:
    global _GRAPH
    from orchestrator.graph import build_graph
    _GRAPH = _GRAPH or build_graph()

    df, truth, csv = dataset(case)
    UPLOADS.mkdir(parents=True, exist_ok=True)
    path = UPLOADS / f"{case['dataset']['name']}-{case['dataset']['seed']}.csv"
    if not path.exists():
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


def _explicit_error_ok(hard: list[dict], error: str | None) -> list[dict]:
    """A surfaced error or question is not a *silent* empty result."""
    if not error:
        return hard
    return [{**h, "ok": True, "detail": "explicit error"} if h["rule"] == "non_empty_or_error" else h
            for h in hard]


async def run_once(suite: str, case: dict, sem: asyncio.Semaphore) -> dict:
    async with sem:
        meter: list[dict] = []
        trace_meter.set(meter)   # each asyncio task has its own context copy
        start = time.perf_counter()
        out = await (run_analysis_case if suite == "analysis" else run_e2e_case)(case)
        out["latency_s"] = round(time.perf_counter() - start, 2)
        out["cost_usd"] = round(sum(r["cost_usd"] for r in meter), 5)
        out["llm_calls"] = len(meter)
        out["truncated_calls"] = sum(r["stop_reason"] == "max_tokens" for r in meter)
        out["passed"] = all(c["ok"] for c in out["checks"])
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
        "cost_per_question_usd": round(statistics.mean(r["cost_usd"] for r in flat), 4),
        "latency_p50_s": _pct([r["latency_s"] for r in flat], 50),
        "latency_p95_s": _pct([r["latency_s"] for r in flat], 95),
    }


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
        "pass_rate_delta_pts": round((summarize(runs)["pass_rate"] - prev["summary"]["pass_rate"]) * 100, 2),
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
                  f"- pass rate Δ: {c['pass_rate_delta_pts']:+} pts · McNemar p = {c['mcnemar_p']}",
                  f"- broke: {', '.join(c['broke']) or 'none'}",
                  f"- fixed: {', '.join(c['fixed']) or 'none'}", ""]
    lines += ["## Cases", "", "| case | passes | failing checks / hard rules / error |", "|---|---|---|"]
    for cid, rs in report["runs"].items():
        notes = sorted({f"{c['kind']}: {c['detail']}" for r in rs for c in r["checks"] if not c["ok"]}
                       | {f"HARD {h['rule']}: {h['detail']}" for r in rs for h in r["hard"] if not h["ok"]}
                       | {f"error: {r['error'][:120]}" for r in rs if r["error"]})
        cell = "<br>".join(n.replace("|", "\\|") for n in notes) or "—"
        lines.append(f"| {cid} | {sum(r['passed'] for r in rs)}/{len(rs)} | {cell} |")
    return "\n".join(lines) + "\n"


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("suite", choices=["analysis", "e2e"])
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("--compare", help="previous report JSON for the paired diff")
    ap.add_argument("--only", help="substring filter on case ids (smoke runs)")
    ap.add_argument("--out", default=str(EVALS_DIR / "reports"))
    args = ap.parse_args()

    cases = load_cases(args.suite, args.only)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    print(f"{args.suite}: {len(cases)} cases × k={args.k}", flush=True)

    sem = asyncio.Semaphore(args.concurrency)
    jobs = [(c["id"], run_once(args.suite, c, sem)) for c in cases for _ in range(args.k)]
    results = await asyncio.gather(*(j for _, j in jobs))
    runs: dict[str, list[dict]] = {}
    for (cid, _), r in zip(jobs, results):
        runs.setdefault(cid, []).append(r)

    name = f"{args.suite}-{stamp}"
    report = {
        "meta": {"suite": args.suite, "report": name, "k": args.k, "model": settings.llm_model,
                 "git_sha": sha, "started_at": stamp, "only": args.only},
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
