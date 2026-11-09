"""Is this function's logic pinned by tests? Runs mutmut on the named functions, in a throwaway
worktree of HEAD (never in the repo: mutmut writes `mutants/` and rewrites sources there), with
the given tests, and prints each function's score: killed ÷ (killed + survived + no test).
A mutant no selected test reaches counts as not killed; timeouts are shown apart.

    uv run python scripts/mutation.py pack_columns --tests tests/test_pack_columns.py
    uv run python scripts/mutation.py spell_window cohort_start --tests tests/test_spell.py \\
        tests/test_ledger.py --json /tmp/m.json

Commit first: the worktree is HEAD. $0: no model call. Config: `[tool.mutmut]` in pyproject.toml
(this script adds the test selection). Exit 1 when a function has a mutant that survived or
that no selected test reached, or when mutmut itself fails."""
import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KILLED, SURVIVED, NO_TESTS = {1, 3}, {0}, {5, 33}
TIMEOUT = {36, 24, -24, 152, 255}


def _function(key: str) -> str:
    """'domain_pack.spell.x_cohort_start__mutmut_3' → 'cohort_start'; a method 'xǁPackǁkpi' →
    'Pack.kpi'."""
    name = key.rsplit("__mutmut_", 1)[0].rsplit(".", 1)[-1]
    return ".".join(p for p in name.split("ǁ")[1:]) if "ǁ" in name else name[2:]


def scores(metas: list[dict]) -> dict[str, dict]:
    """Per function: killed, survived, no_tests, timeout and the score, from mutmut's .meta
    files (exit codes per mutant; None = not run in this session)."""
    out: dict[str, dict] = {}
    for meta in metas:
        for key, code in meta.get("exit_code_by_key", {}).items():
            if code is None:
                continue
            c = out.setdefault(_function(key), {"killed": 0, "survived": 0, "no_tests": 0,
                                                 "timeout": 0})
            kind = ("killed" if code in KILLED else "survived" if code in SURVIVED
                    else "no_tests" if code in NO_TESTS else "timeout" if code in TIMEOUT else None)
            if kind is None:
                raise ValueError(f"{key}: exit code {code} (suspicious, segfault or interrupted)")
            c[kind] += 1
    for c in out.values():
        judged = c["killed"] + c["survived"] + c["no_tests"]
        c["score"] = round(c["killed"] / judged, 4) if judged else None
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("functions", nargs="+", help="function names (any module)")
    ap.add_argument("--tests", nargs="+", required=True, help="test files or node ids to run")
    ap.add_argument("-k", dest="keyword", help="pytest -k over the tests (leave slow ones out)")
    ap.add_argument("--json", help="also write the scores here")
    args = ap.parse_args()
    head = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()
    with tempfile.TemporaryDirectory() as tmp:
        wt = Path(tmp) / "mut"
        subprocess.run(["git", "-C", str(ROOT), "worktree", "add", "--detach", str(wt), "HEAD"],
                       check=True, capture_output=True)
        try:
            cfg = wt / "pyproject.toml"
            text = cfg.read_text()
            if "[tool.mutmut]\n" not in text:
                raise SystemExit("pyproject.toml has no [tool.mutmut] table")
            tests = [*args.tests, *(["-k", args.keyword] if args.keyword else [])]
            selection = f"pytest_add_cli_args_test_selection = {json.dumps(tests)}\n"
            cfg.write_text(text.replace("[tool.mutmut]\n", "[tool.mutmut]\n" + selection))
            env = {**os.environ, "ANALYSTAI_TODAY": "2026-11-01", "HYPOTHESIS_PROFILE": "mutation"}
            globs = [f"*.x_{f}__mutmut_*" if "." not in f else
                     f"*.xǁ{f.replace('.', 'ǁ')}__mutmut_*" for f in args.functions]
            run = subprocess.run([str(ROOT / ".venv/bin/mutmut"), "run", *globs], cwd=wt, env=env)
            got = scores([json.loads(p.read_text()) for p in (wt / "mutants").rglob("*.meta")])
        finally:
            subprocess.run(["git", "-C", str(ROOT), "worktree", "remove", "--force", str(wt)],
                           check=False)
    got = {f: got.get(f, {"score": None}) for f in args.functions}
    print(f"mutation scores at {head} (tests: {' '.join(args.tests)})")
    for f, c in got.items():
        counts = " ".join(f"{k}={v}" for k, v in c.items() if k != "score")
        print(f"  {f:28} {c.get('score')}  {counts}")
    if args.json:
        Path(args.json).write_text(json.dumps({"head": head, "tests": args.tests, "scores": got},
                                              indent=1))
    return 1 if run.returncode or any(c.get("survived") or c.get("no_tests")
                                      for c in got.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
