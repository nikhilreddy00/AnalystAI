"""A delivered report, read back from its run's events (Phase 5.6b): the thread view, provenance,
the Markdown export and the per-report PowerPoint. The report is the run's own report events
(memory/run_store.REPORT_EVENTS), exactly what the user was shown; nothing here reads the
session's latest fields, which other turns overwrite.
"""
from __future__ import annotations

import re

from memory.run_store import Run, report_events, report_hash
from orchestrator.state import (KPI, AnalysisFindings, AnalysisSession, ChartConfig,
                                InsightReport)


def report_of(events: list[dict]) -> list[dict]:
    return report_events(events)


def fold(report: list[dict]) -> dict:
    """The last of each report event, as the UI's reducer shows it (later ones replace)."""
    out: dict = {}
    for e in report:
        out[e["type"]] = e.get("data")
    return out


def provenance(run: Run, events: list[dict]) -> dict:
    """What this report ran under: the run's start record + every model call's usage."""
    start = next((e for e in events if e.get("type") == "provenance"), {})
    calls = [c for e in events if e.get("type") == "usage" for c in e.get("calls", [])]
    prompts = sorted({(c["agent"], c["prompt_version"], c["model"]) for c in calls})
    return {
        "dataset_version_id": run.input.get("dataset_version_id"),
        "model": start.get("model"), "fast_model": start.get("fast_model"),
        "cards": start.get("cards", {}),
        "prompts": [{"agent": a, "prompt_version": p, "model": m} for a, p, m in prompts],
        "llm_calls": len(calls),
        "cost_usd": round(sum(c.get("cost_usd", 0.0) for c in calls), 6),
        "input_tokens": sum(c.get("input_tokens", 0) for c in calls),
        "output_tokens": sum(c.get("output_tokens", 0) for c in calls),
        "started_at": run.created_at.isoformat() if run.created_at else None,
        "finished_at": run.updated_at.isoformat() if run.updated_at else None,
    }


def turn(run: Run, events: list[dict], latest_version_id: str | None) -> dict:
    """One question and its answer, as the thread view and the exports read it."""
    report = report_of(events)
    sha = report_hash(events)
    prov = provenance(run, events)
    vid = prov["dataset_version_id"]
    return {
        "run_id": run.id, "question": run.input.get("message", ""), "status": run.status,
        "error": run.error, "report": report, "report_sha256": sha,
        # A stored seal that doesn't match the events means something rewrote them.
        "intact": run.report_sha256 is None or run.report_sha256 == sha,
        "stale": bool(vid and latest_version_id and vid != latest_version_id),
        "provenance": prov,
    }


# ── Markdown ───────────────────────────────────────────────────────────────────

_MD_SPECIAL = re.compile(r"([\\`*_{}\[\]<>()#+!|~-])")


def md(text: str | None) -> str:
    """Escape Markdown, so a question or model text can't add links, images (which many viewers
    load automatically) or HTML to the export."""
    return _MD_SPECIAL.sub(r"\\\1", " ".join(str(text or "").split()))


def _code(text: str) -> str:
    fence = "`" * max(3, max((len(m) for m in re.findall(r"`+", text)), default=0) + 1)
    return f"{fence}\n{text}\n{fence}"


def _claims(items) -> list[str]:
    out = []
    for c in items or []:
        text, ids = (c.get("text"), c.get("evidence_ids")) if isinstance(c, dict) else (c, [])
        cite = f" \\[{', '.join(md(i) for i in ids)}\\]" if ids else ""
        out.append(f"- {md(text)}{cite}")
    return out


def to_markdown(title: str, dataset_name: str | None, turns: list[dict]) -> str:
    lines = [f"# {md(title)}", ""]
    if dataset_name:
        lines += [f"Dataset: {md(dataset_name)}", ""]
    for i, t in enumerate(turns, 1):
        lines += [f"## {i}. {md(t['question'])}", ""]
        r = fold(t["report"])
        if not r:
            lines += [f"_{md(t['error'] or 'No report: ' + t['status'])}_", ""]
            continue
        if ins := r.get("insight"):
            view = ins.get("executive_view") or {}
            if view.get("headline"):
                lines += [f"**{md(view['headline'])}**", ""]
            lines += [md(ins.get("executive_summary")), ""]
            if view.get("findings"):
                lines += ["### Findings", "", *_claims(view["findings"]), ""]
            if view.get("action_items"):
                lines += ["### Actions", ""] + [f"- {md(a.get('action'))}"
                                                for a in view["action_items"]] + [""]
        if tr := r.get("targeted_response"):
            lines += [md(tr.get("answer_text")), ""]
        if flags := (r.get("expert_review") or {}).get("flags"):
            lines += ["### Review", ""] + [
                f"- {md(f['label'])}: {md(f['claim'])} ({md(f['reason'])})" for f in flags] + [""]
        if ev := r.get("evidence"):
            lines += ["### Evidence", ""]
            for e in ev:
                lines += [f"#### {md(e['id'])} ({md(e['kind'])}, {md(e['confidence'])} "
                          f"confidence, {e['row_count']} rows)", "", md(e["claim"]), ""]
                if e.get("code"):
                    lines += [_code(e["code"]), ""]
                if e.get("result_excerpt"):
                    lines += [_code(e["result_excerpt"]), ""]
        p = t["provenance"]
        lines += [f"_Model {md(p['model'])}, {p['llm_calls']} model calls, "
                  f"${p['cost_usd']:.4f}, finished {md(p['finished_at'])}. "
                  f"Report sha256 {t['report_sha256']}._", ""]
    return "\n".join(lines)


# ── PowerPoint ─────────────────────────────────────────────────────────────────

def as_session(base: AnalysisSession, report: list[dict]) -> AnalysisSession:
    """A session holding only this report, for api/lib/report_pptx.build_pptx (charts as
    delivered, KPIs from the report's briefing)."""
    r = fold(report)
    kpis = [KPI.model_validate(k) for k in (r.get("briefing") or {}).get("kpis", [])]
    return AnalysisSession(
        session_id=base.session_id, user_id=base.user_id, dataset_name=base.dataset_name,
        insight_report=InsightReport.model_validate(r["insight"]) if r.get("insight") else None,
        chart_configs=[ChartConfig.model_validate(c) for c in r.get("charts") or []],
        analysis_findings=AnalysisFindings(eda_summary={}, correlations={}, trends=[],
                                           segments=[], anomalies=[], statistical_tests=[],
                                           code_executed="", kpis=kpis) if kpis else None)
