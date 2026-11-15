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
# The evidence the judge sees: v2 lifts a 12k-character cut that hid half of a real overview's
# 47 evidence items (bundles and the calibration set stay far below either cap).
INPUT_VERSION = 2
_MAX_EVIDENCE_CHARS = 40_000
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
             "Evidence:\n" + delimit_untrusted(json.dumps(facts, default=str)[:_MAX_EVIDENCE_CHARS])]
    if past:
        lines.append("Earlier analyses (not evidence):\n" + delimit_untrusted("\n".join(past)))
    lines.append("Claims:\n" + delimit_untrusted("\n".join(
        f"{i}. {c['text']}" + (f" [cites: {', '.join(c['ids'])}]" if c["ids"] else "")
        for i, c in enumerate(claims))))
    return "\n\n".join(lines)


async def judge_claims(bundle: dict, claims: list[dict], llm: LLMClient) -> dict:
    if not claims:
        return {"labels": [], "why": [], "prompt_version": PROMPT_VERSION, "input_version": INPUT_VERSION}
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
    return {"labels": labels, "why": why, "prompt_version": PROMPT_VERSION,
            "input_version": INPUT_VERSION}


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
    acts = [r for r in rows if r["source"] == "action"]
    rows_st = [r for r in rows if r["source"] != "action"]

    def agreement(rs):
        return round(sum((r["want"] == "supported") == (r["got"] == "supported") for r in rs)
                     / len(rs), 4) if rs else None
    out = {"prompt_version": PROMPT_VERSION, "model": settings.llm_fast_model,
           "claims": len(rows), "agreement": round(agree / len(rows), 4),
           "statement_agreement": agreement(rows_st), "action_agreement": agreement(acts),
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


PROSE = EVALS_DIR / "cases" / "prose_judge"
PROSE_LOG = PROSE / "held_out_log.json"   # one entry per held-out run, appended, never edited
_PROSE_BATCH = 25                          # sentences per call: about one report's free text
_PROSE_USD_PER_CALL = 0.003                # Haiku 4.5: ~1.6k tokens in, ~50 out, plus margin


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """The 95% Wilson interval of k successes in n (a rate on 50 sentences is not exact)."""
    if not n:
        return 0.0, 0.0
    p, d = k / n, 1 + z * z / n
    mid, half = (p + z * z / (2 * n)) / d, z * ((p * (1 - p) + z * z / (4 * n)) / n) ** 0.5 / d
    return round(mid - half, 4), round(mid + half, 4)


def prose_shas() -> dict:
    """What a held-out run measured: the judge's prompt (with the untrusted note), the set, the
    model. A held-out entry counts only while all three are today's."""
    from agents import prose_judge
    sha = lambda b: __import__("hashlib").sha256(b).hexdigest()   # noqa: E731
    return {"prompt_version": prose_judge.PROMPT_VERSION, "prompt_sha": sha(prose_judge._SYSTEM.encode()),
            "set_sha": sha((PROSE / "held_out.yaml").read_bytes()), "model": settings.llm_fast_model}


def prose_rates(rows: list[dict]) -> dict:
    """TPR (overreach caught) and TNR (a sound sentence kept), overall and per kind, with their
    Wilson intervals."""
    def rate(rs, want):
        k, n = sum(r["got"] == want for r in rs), len(rs)
        return {"rate": round(k / n, 4) if n else None, "k": k, "n": n, "ci95": wilson(k, n)}
    pos, neg = [r for r in rows if r["label"]], [r for r in rows if not r["label"]]
    out = {"tpr": rate(pos, True), "tnr": rate(neg, False)}
    for kind in ("causal", "benchmark"):
        out[f"tpr_{kind}"] = rate([r for r in pos if r.get("kind") == kind], True)
        out[f"tnr_near_{kind}"] = rate([r for r in neg if r.get("near") == kind], False)
    return out


async def prose(which: str, max_usd: float) -> dict:
    """The prose judge on its tuning set (any number of times) or its held-out set (once per
    prompt: the log refuses a second run of the same prompt, set and model)."""
    from agents.prose_judge import flagged
    items = yaml.safe_load((PROSE / ("tuning.yaml" if which == "tune" else "held_out.yaml")).read_text())
    shas = prose_shas()
    log = json.loads(PROSE_LOG.read_text()) if PROSE_LOG.exists() else []
    if which == "held-out" and any({k: e.get(k) for k in shas} == shas for e in log):
        raise SystemExit("this prompt, set and model already had their held-out run (one per prompt)")
    batches = [items[i:i + _PROSE_BATCH] for i in range(0, len(items), _PROSE_BATCH)]
    estimate = len(batches) * _PROSE_USD_PER_CALL
    print(f"estimate ${estimate:.3f} for {len(items)} sentences in {len(batches)} calls")
    if estimate > max_usd:
        raise SystemExit(f"estimate ${estimate:.3f} is over --max-usd {max_usd}")
    meter: list[dict] = []
    trace_meter.set(meter)
    rows = []
    for batch in batches:
        got = await flagged([x["text"] for x in batch], get_client())
        if got is None:
            raise SystemExit("the judge failed (no JSON reply): nothing is scored")
        rows += [{**x, "got": i in got} for i, x in enumerate(batch)]
    out = {"set": which, **shas, "sentences": len(rows), **prose_rates(rows),
           "cost_usd": round(sum(m.get("cost_usd", 0) for m in meter), 4),
           "stamp": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")}
    dest = EVALS_DIR / "reports" / f"prose_judge-{which}-{out['stamp']}.json"
    print(json.dumps(out, indent=1))
    if which == "held-out":   # its sentences are never printed (no tuning on them)
        PROSE_LOG.write_text(json.dumps(log + [out], indent=1) + "\n")
        dest.write_text(json.dumps(out, indent=1))
    else:
        dest.write_text(json.dumps({**out, "rows": rows}, indent=1))
        for r in rows:
            if r["got"] != r["label"]:
                print(f"  want {'yes' if r['label'] else 'no ':<3} got {'yes' if r['got'] else 'no ':<3} {r['text']}")
    print(f"→ {dest}")
    return out


if __name__ == "__main__":
    if sys.argv[1:2] == ["prose"] and sys.argv[2:3] in (["tune"], ["held-out"]):
        usd = float(sys.argv[sys.argv.index("--max-usd") + 1]) if "--max-usd" in sys.argv else 0.05
        asyncio.run(prose(sys.argv[2], usd))
    elif sys.argv[1:2] == ["calibrate"]:
        asyncio.run(calibrate(Path(sys.argv[2]) if len(sys.argv) > 2 else
                              EVALS_DIR / "cases" / "insight_judge" / "calibration.yaml"))
    else:
        raise SystemExit("usage: python -m evals.judge calibrate [calibration.yaml] | "
                         "prose tune|held-out [--max-usd N]")
