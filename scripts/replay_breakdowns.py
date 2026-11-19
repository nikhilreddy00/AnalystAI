"""$0 replay of a post-model breakdown step over every stored analysis output (D7-19).

The analysis suite isn't in tests/test_replay.py (intake and insight only), so a change to what
standard_stats does with the model's segments is measured here: each case's latest stored runs
go through agents.analysis_agent.product_breakdowns, are rescored with the current scorer, and
every check that flips is listed. A flip to fail is a case to re-measure (paid); a flip to pass
is the fix showing up on a stored reply.

    uv run python scripts/replay_breakdowns.py [--cases logi-4 iot-5 ...]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

REPORTS = Path(__file__).resolve().parents[1] / "evals" / "reports"


def latest_runs() -> dict[str, tuple[str, list[dict]]]:
    """{case: (report name, its stored runs)} from the newest analysis report holding the case."""
    out: dict[str, tuple[str, list[dict]]] = {}
    for p in sorted(REPORTS.glob("analysis-*.json")):   # names sort by time: later wins
        if "rescored" in p.name:
            continue
        runs = json.loads(p.read_text()).get("runs", {})
        for cid, rs in runs.items():
            if rs and all("output" in r for r in rs):
                out[cid] = (p.name, rs)
    return out


def main(argv: list[str] | None = None) -> None:
    from agents.analysis_agent import product_breakdowns
    from evals.run import cleaned_for_analysis, load_cases, score_analysis, working_frame
    from orchestrator.state import AnalysisFindings
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", nargs="*")
    args = ap.parse_args(argv)
    cases = {c["id"]: c for c in load_cases("analysis", None)}
    flips, seen = [], 0
    for cid, (report, runs) in sorted(latest_runs().items()):
        if cid not in cases or (args.cases and cid not in args.cases):
            continue
        case = cases[cid]
        df, contract = working_frame(case)
        df = cleaned_for_analysis(case, df, contract)[0]
        for i, r in enumerate(runs):
            out = r.get("output")
            if not out or not out.get("segments"):
                continue
            seen += 1
            raw = {"segments": json.loads(json.dumps(out["segments"])), "assumptions": []}
            product_breakdowns(raw, df, contract)
            before = {c["kind"] + ":" + c["detail"][:60]: c["ok"] for c in r.get("checks", [])}
            new = AnalysisFindings.model_validate({**out, "segments": raw["segments"]})
            after = score_analysis(case, new, r.get("error"), baseline=False)["checks"]
            was, now = all(before.values()) if before else None, all(c["ok"] for c in after)
            if raw["assumptions"] or was != now:
                flips.append((cid, i, report, was, now, raw["assumptions"]))
    for cid, i, report, was, now, notes in flips:
        tag = "FLIP→PASS" if now and not was else "FLIP→FAIL" if was and not now else "changed"
        print(f"{tag:10} {cid} run {i} ({report}): checks {was} → {now}; {'; '.join(notes)[:200]}")
    print(f"{seen} stored runs with segments; {sum(1 for f in flips if f[3] and not f[4])} flip to fail")


if __name__ == "__main__":
    main()
