"""
Faithfulness judge for the insight eval (ADR-0002 §4): one Haiku call per report labels each
executive claim supported / unsupported / contradicted / overstated against the bundle's
evidence. It counts only once it agrees with labelled claims (`calibrate`, ≥ 85% agreement and
≥ 90% recall on seeded defects); it is frozen (prompt version) before the final run.

    uv run python -m evals.judge calibrate        # evals/cases/insight_judge/calibration.yaml
Judging a report's stored outputs: `python -m evals.run judge <report.json>`.
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

from agents.utils import strip_markdown_fences
from api.config import settings
from evals.scoring import insight_claims
from guardrails import UNTRUSTED_NOTE, delimit_untrusted
from llm.client import LLMClient, get_client, trace_meter

PROMPT_VERSION = "v1"   # v2 (every part must hold) calibrated worse: 87.5% vs 90%
_SYSTEM = ((Path(__file__).parent.parent / "prompts" / "insight_judge" / f"{PROMPT_VERSION}.md")
           .read_text() + "\n" + UNTRUSTED_NOTE)
LABELS = ("supported", "unsupported", "contradicted", "overstated")
EVALS_DIR = Path(__file__).parent


def render(bundle: dict, claims: list[dict]) -> str:
    """The evidence side is the bundle (what the agent was given), never the agent's context."""
    first, last = bundle.get("date_first"), bundle.get("date_last")
    span = (f"{first[0]}-{first[1]:02d} to {last[0]}-{last[1]:02d} ({bundle['n_months']} months)"
            if first else "the data has no date column")
    findings = bundle["values"][0]
    evidence = [{k: e[k] for k in ("id", "kind", "claim", "result_excerpt", "row_count",
                                   "confidence", "confidence_reason")}
                | {"assumptions": e["assumptions"][:5]} for e in bundle["evidence"]]
    facts = {"date_range": span, "evidence": evidence, "eda": findings.get("eda_summary", {}),
             "cleaning": bundle["audit_summary"], "charts": bundle["chart_titles"]}
    past = bundle.get("past_insights") or []
    lines = [f"Question: {bundle['question']}",
             "Evidence:\n" + delimit_untrusted(json.dumps(facts, default=str)[:12000])]
    if past:
        lines.append("Earlier analyses (not evidence):\n" + delimit_untrusted("\n".join(past)))
    lines.append("Claims:\n" + delimit_untrusted("\n".join(
        f"{i}. {c['text']}" + (f" [cites: {', '.join(c['ids'])}]" if c["ids"] else "")
        for i, c in enumerate(claims))))
    return "\n\n".join(lines)


async def judge_claims(bundle: dict, claims: list[dict], llm: LLMClient) -> dict:
    if not claims:
        return {"labels": [], "why": [], "prompt_version": PROMPT_VERSION}
    result = await llm.complete(agent="insight_judge", prompt_version=PROMPT_VERSION,
                                model=settings.llm_fast_model, max_tokens=1500, system=_SYSTEM,
                                messages=[{"role": "user", "content": render(bundle, claims)}])
    try:
        rows = json.loads(strip_markdown_fences(result.text.strip()))["labels"]
        by_i = {int(r["i"]): r for r in rows}
        labels = [by_i[i]["label"] if by_i.get(i, {}).get("label") in LABELS else "unparsed"
                  for i in range(len(claims))]
        why = [str(by_i.get(i, {}).get("why", "")) for i in range(len(claims))]
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        labels, why = ["unparsed"] * len(claims), [""] * len(claims)
    return {"labels": labels, "why": why, "prompt_version": PROMPT_VERSION}


async def judge_report(output: dict, llm: LLMClient) -> dict:
    claims = insight_claims(output["report"]) if output and output.get("report") else []
    return {**await judge_claims(output["bundle"], claims, llm),
            "claims": [c["text"] for c in claims]}


async def calibrate(path: Path) -> dict:
    """Agreement with the labelled claims, and recall on the seeded (by-construction) defects."""
    from evals.run import insight_bundle, load_cases
    spec = yaml.safe_load(path.read_text())
    cases = {c["id"]: c for c in load_cases("insight", None)}
    meter: list[dict] = []
    trace_meter.set(meter)
    rows = []
    for group in spec["groups"]:
        bundle = (await insight_bundle(cases[group["case"]]))[2]
        claims = [{"text": c["text"], "ids": c.get("ids", [])} for c in group["claims"]]
        got = await judge_claims(bundle, claims, get_client())
        for c, label, why in zip(group["claims"], got["labels"], got["why"]):
            rows.append({"case": group["case"], "text": c["text"], "want": c["label"],
                         "got": label, "why": why, "source": c["source"]})
    agree = sum((r["want"] == "supported") == (r["got"] == "supported") for r in rows)
    seeded = [r for r in rows if r["source"] == "seeded"]
    caught = sum(r["got"] != "supported" for r in seeded)
    out = {"prompt_version": PROMPT_VERSION, "model": settings.llm_fast_model,
           "claims": len(rows), "agreement": round(agree / len(rows), 4),
           "exact_label_agreement": round(sum(r["want"] == r["got"] for r in rows) / len(rows), 4),
           "seeded_defect_recall": round(caught / len(seeded), 4) if seeded else None,
           "cost_usd": round(sum(m.get("cost_usd", 0) for m in meter), 4),
           "disagreements": [r for r in rows if (r["want"] == "supported") != (r["got"] == "supported")]}
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = EVALS_DIR / "reports" / f"insight_judge-{stamp}.json"
    dest.write_text(json.dumps({**out, "rows": rows}, indent=1))
    print(json.dumps({k: v for k, v in out.items() if k != "disagreements"}, indent=1))
    for r in out["disagreements"]:
        print(f"  want {r['want']:<12} got {r['got']:<12} {r['text'][:90]}  ({r['why']})")
    print(f"→ {dest}")
    return out


if __name__ == "__main__":
    if sys.argv[1:2] != ["calibrate"]:
        raise SystemExit("usage: python -m evals.judge calibrate [calibration.yaml]")
    asyncio.run(calibrate(Path(sys.argv[2]) if len(sys.argv) > 2 else
                          EVALS_DIR / "cases" / "insight_judge" / "calibration.yaml"))
