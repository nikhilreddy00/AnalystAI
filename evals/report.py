"""Read a stored eval report without a one-off script (retro 5.7b-3: 64 of them in one session).
The report's .md already lists each case's failing checks; this answers the rest.

    uv run python -m evals.report quality-20261116T041831Z q-31          # a case's runs
    uv run python -m evals.report quality-20261116T041831Z q-31 --field replies output
    uv run python -m evals.report --cost e2e-20261116T063034Z e2e-20261116T064809Z

A report is {meta, summary, runs: {case id: [run, ...]}}; a run has checks, hard, error,
passed, cost_usd, replies (the model's raw replies, what --replay reads), output, ..."""
import argparse
import json
import sys
from pathlib import Path

REPORTS = Path(__file__).parent / "reports"


def load(name: str) -> dict:
    path = Path(name) if name.endswith(".json") else REPORTS / f"{name}.json"
    return json.loads(path.read_text())


def case_lines(report: dict, case: str, fields: list[str]) -> list[str]:
    """Each run of the cases whose id contains `case`: verdict, cost and what failed, then each
    named field as JSON."""
    out = []
    for cid, runs in report["runs"].items():
        if case not in cid:
            continue
        for i, run in enumerate(runs):
            bad = [f"{c.get('kind') or c.get('rule')}: {c['detail']}"
                   for c in [*run.get("checks", []), *run.get("hard", [])] if not c["ok"]]
            out.append(f"{cid} run {i}: {'PASS' if run['passed'] else 'fail'} "
                       f"${run.get('cost_usd', 0):.4f}" + (f" error: {run['error']}"
                                                           if run.get("error") else ""))
            out += [f"  - {b}" for b in bad]
            out += [f"  {f}: {json.dumps(run.get(f), indent=2)}" for f in fields]
    return out or [f"no case id contains {case!r}; cases: {' '.join(report['runs'])}"]


def cost(reports: list[dict]) -> float:
    return round(sum(r.get("cost_usd", 0) for rep in reports for runs in rep["runs"].values()
                     for r in runs), 4)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("report", nargs="?", help="a report name (evals/reports/) or a .json path")
    ap.add_argument("case", nargs="?", help="a substring of the case ids to show")
    ap.add_argument("--field", nargs="+", default=[], help="run fields to print as JSON")
    ap.add_argument("--cost", nargs="+", metavar="REPORT", help="the summed cost of reports")
    args = ap.parse_args()
    if args.cost:
        print(f"${cost([load(r) for r in args.cost])}")
        return 0
    if not (args.report and args.case):
        ap.error("name a report and a case (its .md lists every case), or pass --cost")
    print("\n".join(case_lines(load(args.report), args.case, args.field)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
