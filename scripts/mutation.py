"""Is this function's logic pinned by tests? Runs mutmut on the named functions, in a throwaway
worktree of HEAD (never in the repo: mutmut writes `mutants/` and rewrites sources there), with
the given tests, and prints each function's score: killed ÷ (killed + survived + no test).
A mutant no selected test reaches counts as not killed; timeouts are shown apart.

    uv run python scripts/mutation.py pack_columns --tests tests/test_pack_columns.py
    uv run python scripts/mutation.py window first_known --tests tests/test_spell.py \\
        tests/test_ledger.py --json /tmp/m.json
    uv run python scripts/mutation.py --changed-since main --tests tests/test_spell.py
    uv run python scripts/mutation.py --status            # scores so far of a run still going
    uv run python scripts/mutation.py --plant domain_pack/kpi.py OLD NEW --tests \\
        tests/test_properties.py -k p2   # one hand-planted bug: exit 0 when a test catches it

`--changed-since REF` mutates every function in `[tool.mutmut] source_paths` whose lines the
diff REF...HEAD touches; `--lines REF` then runs only their mutants on lines that diff changed
and lists each survivor by file:line (the 5.7b-2 gate: 0 survivors on changed lines). `--plant` replaces the one exact occurrence of OLD
with NEW in a worktree and runs the tests: caught = a test failed.

The pre-push gate (ROADMAP 5.7b-2 step 9): the hook can't run mutmut, so it runs
`--check "$(git merge-base HEAD origin/main)"` (< 1 s), which refuses a GATED function whose
code (its AST, docstring out: a comment is no change) differs from REF's unless
docs/mutation/gate.json holds its hash. `--record` (with `--lines REF`) writes those hashes after
a run whose every surviving mutant on a function's changed lines is one of its named
`equivalents` ({old, new, n, reason}, written into gate.json by hand first). Known limits: a module
constant or a pack's YAML changes behaviour without changing any function's hash; mutmut mutates
no decorated function (a @property, a pydantic validator), so neither run nor gate covers one:
its tests and plants are its check, and --changed-since lists it apart.

--tests drops the guards that read source files (SOURCE_GUARDS): mutmut's rewritten sources
fail them before any mutant runs. The worktree is HEAD, so a run refuses an
uncommitted source or test file it reads. $0: no model call. Config: `[tool.mutmut]` in pyproject.toml
(this script adds the test selection). Exit 2, before any mutant runs, when no selected test
reaches a named function (it prints the test files that name it). Exit 1 when a mutant survived
or no test reached it, or when mutmut itself fails."""
import argparse
import ast
import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import tomllib
from collections import Counter
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KILLED, SURVIVED, NO_TESTS = {1, 3}, {0}, {5, 33}
TIMEOUT = {36, 24, -24, 152, 255}
PREFIX = "analystai-mutation-"   # --status finds running worktrees by it
ENV = {"ANALYSTAI_TODAY": "2026-11-01", "HYPOTHESIS_PROFILE": "mutation"}
SOURCE_GUARDS = {"tests/test_one_definition.py"}   # grep the sources: mutmut rewrites them
# the pre-push gate's files: spell, ledger, pack-column and scorer functions (kpi's _spell and
# rows' with_spell_events are spell functions: the 5.7b-1 baseline scored them)
GATED = ("domain_pack/spell.py", "domain_pack/ledger.py", "domain_pack/columns.py",
         "domain_pack/kpi.py", "domain_pack/rows.py", "evals/scoring.py")
GATE = "docs/mutation/gate.json"


def runnable(tests: list[str]) -> list[str]:
    """The --tests without the source guards (a node id reads as its file)."""
    return [t for t in tests if t.split("::", 1)[0] not in SOURCE_GUARDS]


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


def mutated(d: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """mutmut mutates a function unless it is decorated (a lone @staticmethod or @classmethod
    aside): a @property, a pydantic validator, an @cache function has no mutants, so its tests
    and plants are its only check (mutmut/mutation/file_mutation.py)."""
    decs = d.decorator_list
    return not decs or (len(decs) == 1 and isinstance(decs[0], ast.Name)
                        and decs[0].id in ("staticmethod", "classmethod"))


def _defs(source: str, every: bool = False):
    """(name, node) per top-level function and method ('Pack.kpi') mutmut mutates (every: all of
    them), named as mutmut names them (a nested function belongs to the one it is in)."""
    for node in ast.parse(source).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            found = [(node.name, node)]
        elif isinstance(node, ast.ClassDef):
            found = [(f"{node.name}.{m.name}", m) for m in node.body
                     if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))]
        else:
            continue
        yield from ((n, d) for n, d in found if every or mutated(d))


def functions_touching(source: str, lines: set[int], every: bool = False) -> list[str]:
    """The top-level functions and methods whose span (decorators included) holds any line."""
    return [name for name, d in _defs(source, every)
            if any(min([d.lineno, *(x.lineno for x in d.decorator_list)]) <= n <= d.end_lineno
                   for n in lines)]


def function_hashes(source: str) -> dict[str, str]:
    """Each function mutmut mutates (the gate's), its code as a hash: its AST with the docstring
    out, so a comment, a docstring or a move down the file is the same code, and a changed
    expression is not."""
    out = {}
    for name, d in _defs(source):
        node = copy.copy(d)
        if ast.get_docstring(d, clean=False) is not None:
            node.body = d.body[1:] or [ast.Pass()]
        out[name] = hashlib.sha256(ast.dump(node).encode()).hexdigest()[:16]
    return out


def _at(ref: str, file: str, root: Path) -> str:
    """The file's text at ref ("" when it isn't there)."""
    got = subprocess.run(["git", "-C", str(root), "show", f"{ref}:{file}"], capture_output=True,
                         text=True)
    return got.stdout if got.returncode == 0 else ""


def check(ref: str, root: Path = ROOT) -> list[str]:
    """The pre-push gate, on HEAD's committed files: each GATED function whose code differs from
    ref's must have its hash in GATE, and every GATE key must name a function that exists."""
    text = _at("HEAD", GATE, root)
    gate = json.loads(text) if text else {}
    problems, exist = [], set()
    for f in GATED:
        before, now = function_hashes(_at(ref, f, root)), function_hashes(_at("HEAD", f, root))
        for name, h in now.items():
            exist.add(key := f"{f}::{name}")
            if before.get(name) != h and gate.get(key, {}).get("hash") != h:
                problems.append(f"{key}: changed since {ref[:12]} with no clean mutation gate "
                                f"recorded")
    problems += [f"{k}: in {GATE}, but no such function (renamed or deleted)"
                 for k in gate if k not in exist]
    return problems


def recorded(gate: dict, hashes: dict[str, str],
             left: dict[str, list[tuple[str, str, str]]]) -> tuple[dict, dict]:
    """GATE with each run function's hash written when every mutant left on its changed lines
    ((kind, old line, new line), not killed) is a survivor named in its equivalents, as many
    times as named; and the functions with any other left mutant (no test, timeout), unrecorded."""
    new = {k: dict(v) for k, v in gate.items()}
    unexplained = {}
    for key, h in hashes.items():
        entry = new.setdefault(key, {})
        entry.setdefault("equivalents", [])
        named = Counter()
        for e in entry["equivalents"]:
            named[e["old"], e["new"]] += e["n"]
        mutants = left.get(key, [])
        seen = Counter((old, nu) for kind, old, nu in mutants if kind == "survived")
        if bad := [m for m in mutants if m[0] != "survived" or seen[m[1:]] > named[m[1:]]]:
            unexplained[key] = bad
            entry.pop("hash", None)
        else:
            entry["hash"] = h
    return new, unexplained


def changed_lines(ref: str) -> dict[str, set[int]]:
    """Per source_paths file, the HEAD lines the diff ref...HEAD adds or changes."""
    paths = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["mutmut"]["source_paths"]
    diff = subprocess.run(["git", "-C", str(ROOT), "diff", "-U0", f"{ref}...HEAD", "--", *paths],
                          capture_output=True, text=True, check=True).stdout
    touched: dict[str, set[int]] = {}
    f = None
    for line in diff.splitlines():
        if line.startswith("+++ "):
            f = line[6:] if line.startswith("+++ b/") else None
        elif line.startswith("@@") and f and f.endswith(".py"):
            start, _, n = line.split("+", 1)[1].split(" ", 1)[0].partition(",")
            # a pure deletion (",0") names the line before it: still a change to that function
            touched.setdefault(f, set()).update(range(int(start), int(start) + max(int(n or 1), 1)))
    return touched


def changed_functions(ref: str, every: bool = False) -> list[str]:
    """Every source_paths function mutmut mutates (every: all) the diff ref...HEAD adds or
    changes lines in."""
    return sorted({name for f, lines in changed_lines(ref).items()
                   for name in functions_touching((ROOT / f).read_text(), lines, every)})


def mutant_lines(mutated: str, source: str) -> dict[str, tuple[int, str, str]]:
    """Each mutant in mutmut's generated file → (the source line it changes, that line before,
    after): its function's def line in `source` plus the first line where it differs from the
    unmutated copy (`…__mutmut_orig`). One parse per file, never a `mutmut show` per mutant."""
    lines = mutated.splitlines()
    spans = {}
    for node in ast.parse(mutated).body:
        for d in ([node] if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                  else node.body if isinstance(node, ast.ClassDef) else []):
            if isinstance(d, (ast.FunctionDef, ast.AsyncFunctionDef)):
                spans[d.name] = lines[d.lineno - 1:d.end_lineno]
    defs = {}
    for node in ast.parse(source).body:   # the def line (a mutant copy has no decorators)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            defs[node.name] = node.lineno
        elif isinstance(node, ast.ClassDef):
            defs |= {f"{node.name}.{m.name}": m.lineno for m in node.body
                     if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))}
    out = {}
    for name, body in spans.items():
        base, _, n = name.rpartition("__mutmut_")
        orig = spans.get(f"{base}__mutmut_orig")
        if not base or n == "orig" or orig is None or _function(name) not in defs:
            continue
        a, b = [orig[0].replace(f"{base}__mutmut_orig", base), *orig[1:]], \
            [body[0].replace(name, base), *body[1:]]
        i = next((k for k, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
        out[name] = (defs[_function(name)] + i, *((x[i].strip() if i < len(x) else "")
                                                   for x in (a, b)))
    return out


def unreached(stats: dict, functions: list[str]) -> list[str]:
    """The functions no selected test calls, from mutmut's stats (each of their mutants would
    be no_tests): known before any mutant runs, so a narrow --tests list fails in minutes."""
    hit = {_function(k) for k, t in stats.get("tests_by_mangled_function_name", {}).items() if t}
    return [f for f in functions if f not in hit]


def naming(function: str, root: Path = ROOT) -> list[str]:
    """The test files that name the function: where to look for the tests that reach it."""
    word = function.rsplit(".", 1)[-1]
    out = subprocess.run(["git", "-C", str(root), "grep", "-lw", word, "--", "tests/*.py"],
                         capture_output=True, text=True).stdout
    return out.split()


def uncommitted(paths: list[str], root: Path = ROOT) -> list[str]:
    """The paths (a test node id reads as its file) that differ from HEAD or are untracked: the
    worktree is HEAD, so a run would silently read their committed copy, or none."""
    files = [p.split("::", 1)[0] for p in paths]
    out = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "--", *files],
                         capture_output=True, text=True, check=True).stdout
    return sorted(line[3:] for line in out.splitlines())


def _refuse_uncommitted(paths: list[str]) -> None:
    if dirty := uncommitted(paths):
        print("uncommitted (the run reads HEAD): commit first: " + " ".join(dirty), file=sys.stderr)
        raise SystemExit(2)   # 1 means a missed plant or a survivor


def planted(text: str, old: str, new: str) -> str:
    """text with its one occurrence of old replaced; a plant that matches 0 or 2+ times is an
    error, never a silent no-op."""
    if text.count(old) != 1:
        raise SystemExit(f"--plant: OLD occurs {text.count(old)} times (needs exactly 1)")
    return text.replace(old, new)


@contextmanager
def worktree():
    """A detached worktree of HEAD under the system temp dir, removed on exit."""
    with tempfile.TemporaryDirectory(prefix=PREFIX) as tmp:
        wt = Path(tmp) / "mut"
        subprocess.run(["git", "-C", str(ROOT), "worktree", "add", "--detach", str(wt), "HEAD"],
                       check=True, capture_output=True)
        try:
            yield wt
        finally:
            subprocess.run(["git", "-C", str(ROOT), "worktree", "remove", "--force", str(wt)],
                           check=False)


def status() -> int:
    """Scores so far of every mutation run still going (read from its worktree's .meta files)."""
    runs = sorted(Path(tempfile.gettempdir()).glob(f"{PREFIX}*/mut/mutants"))
    if not runs:
        print("no mutation run is going")
    for m in runs:
        print(m.parent)
        for f, c in scores([json.loads(p.read_text()) for p in m.rglob("*.meta")]).items():
            print(f"  {f:28} {c['score']}  " + " ".join(f"{k}={v}" for k, v in c.items()
                                                        if k != "score"))
    return 0


def plant(file: str, old: str, new: str, tests: list[str], keyword: str | None) -> int:
    """0 when a selected test fails on the planted bug, 1 when every test passes, 2 when pytest
    errors or selects nothing (an error is not a catch)."""
    with worktree() as wt:
        src = wt / file
        src.write_text(planted(src.read_text(), old, new))
        run = subprocess.run([str(ROOT / ".venv/bin/python"), "-m", "pytest", "-q", "-x",
                              "-p", "no:warnings", *tests, *(["-k", keyword] if keyword else [])],
                             cwd=wt, env={**os.environ, **ENV}, capture_output=True, text=True)
    print("\n".join(run.stdout.strip().splitlines()[-3:]))
    verdict = {1: ("caught", 0), 0: ("MISSED: every selected test passed", 1)}
    word, code = verdict.get(run.returncode, (f"ERROR: pytest exit {run.returncode}", 2))
    print(f"plant in {file}: {word}")
    return code


def record(changed: dict[str, set[int]], metas: list[dict], placed: dict, functions: list[str]):
    """Writes GATE for the run's GATED functions (HEAD's code; the run is a worktree of HEAD):
    each one's hash when every mutant left on its changed lines is a named equivalent."""
    kinds = {c: kind for kind, codes in (("survived", SURVIVED), ("no_tests", NO_TESTS),
                                         ("timeout", TIMEOUT)) for c in codes}
    hashes, left = {}, {}
    for f in (f for f in GATED if f in changed):
        source = _at("HEAD", f, ROOT)
        run = set(functions_touching(source, changed[f])) & set(functions)
        hashes |= {f"{f}::{n}": h for n, h in function_hashes(source).items() if n in run}
    for m in metas:
        for k, c in m.get("exit_code_by_key", {}).items():
            if k in placed and c not in KILLED:
                f, _, old, new = placed[k]
                left.setdefault(f"{f}::{_function(k)}", []).append((kinds.get(c, "not run"), old, new))
    path = ROOT / GATE
    gate, unexplained = recorded(json.loads(path.read_text()) if path.is_file() else {}, hashes, left)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(gate, indent=1, sort_keys=True, ensure_ascii=False) + "\n")
    print(f"recorded in {GATE}: {' '.join(k for k in hashes if k not in unexplained) or '(none)'}")
    for k, bad in unexplained.items():
        print(f"NOT recorded {k}: {len(bad)} left mutants are no named equivalent")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("functions", nargs="*", help="function names (any module)")
    ap.add_argument("--tests", nargs="+", help="test files or node ids to run")
    ap.add_argument("-k", dest="keyword", help="pytest -k over the tests (leave slow ones out)")
    ap.add_argument("--json", help="also write the scores here")
    ap.add_argument("--changed-since", metavar="REF", help="add the functions REF...HEAD changed")
    ap.add_argument("--lines", metavar="REF", help="fail only on survivors on lines REF...HEAD "
                                                   "changed (the 5.7b-2 gate)")
    ap.add_argument("--status", action="store_true", help="scores so far of a running run")
    ap.add_argument("--plant", nargs=3, metavar=("FILE", "OLD", "NEW"), help="one planted bug")
    ap.add_argument("--check", metavar="REF", help="the pre-push gate: GATED functions changed "
                                                   "since REF have a recorded clean gate")
    ap.add_argument("--record", action="store_true", help=f"with --lines: write {GATE} for each "
                    "GATED function whose left mutants are all named equivalents")
    args = ap.parse_args()
    if args.status:
        return status()
    if args.check:
        for p in (problems := check(args.check)):
            print(f"pre-push: {p}", file=sys.stderr)
        if problems:
            print("run: uv run python scripts/mutation.py --changed-since main --lines main "
                  "--record --tests <each function's callers' test files>", file=sys.stderr)
        return 1 if problems else 0
    if args.record and not args.lines:
        ap.error("--record needs --lines REF (it records a gate on changed lines)")
    if not args.tests:
        ap.error("--tests is required")
    if args.plant:
        _refuse_uncommitted([args.plant[0], *args.tests])
        return plant(*args.plant, args.tests, args.keyword)
    _refuse_uncommitted([*tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["mutmut"]
                         ["source_paths"], *args.tests])
    if dropped := [t for t in args.tests if t not in runnable(args.tests)]:
        print("left out (they read the sources mutmut rewrites): " + " ".join(dropped))
        args.tests = runnable(args.tests) or ap.error("no --tests left to run")
    if args.changed_since:
        args.functions = sorted({*args.functions, *changed_functions(args.changed_since)})
        print(f"changed since {args.changed_since}: {' '.join(args.functions) or '(none)'}")
        if skipped := sorted(set(changed_functions(args.changed_since, every=True)) - set(args.functions)):
            print(f"not mutated (decorated: mutmut skips them; their tests are their check): "
                  f"{' '.join(skipped)}")
        if not args.functions:
            return 0
    if not args.functions:
        ap.error("name a function, or pass --changed-since")
    head = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()
    with worktree() as wt:
        cfg = wt / "pyproject.toml"
        text = cfg.read_text()
        if "[tool.mutmut]\n" not in text:
            raise SystemExit("pyproject.toml has no [tool.mutmut] table")
        tests = [*args.tests, *(["-k", args.keyword] if args.keyword else [])]
        selection = f"pytest_add_cli_args_test_selection = {json.dumps(tests)}\n"
        cfg.write_text(text.replace("[tool.mutmut]\n", "[tool.mutmut]\n" + selection))
        globs = [f"*.x_{f}__mutmut_*" if "." not in f else
                 f"*.xǁ{f.replace('.', 'ǁ')}__mutmut_*" for f in args.functions]
        placed = {}
        # generate the mutants and mutmut's stats (which tests reach each function); the run
        # below loads the same stats
        gen = subprocess.run([str(ROOT / ".venv/bin/mutmut"), "run", "no_such_mutant"], cwd=wt,
                             env={**os.environ, **ENV}, capture_output=True, text=True)
        stats = wt / "mutants" / "mutmut-stats.json"
        if stats.is_file():
            lost = unreached(json.loads(stats.read_text()), args.functions)
        elif "could not find any test case for any mutant" in gen.stdout:   # it saves none then
            lost = args.functions
        else:
            print(gen.stdout[-3000:] + gen.stderr[-3000:], file=sys.stderr)
            print("mutmut wrote no stats (its output is above)", file=sys.stderr)
            return 2
        if lost:
            for f in lost:
                print(f"no selected test reaches {f}; tests that name it: "
                      f"{' '.join(naming(f)) or '(none)'}", file=sys.stderr)
            return 2   # 1 means a survivor
        if args.lines:   # the gate judges changed lines: run only the mutants on them
            changed = changed_lines(args.lines)
            for f, lines in changed.items():
                if (mutated := wt / "mutants" / f).is_file():
                    module = f.removesuffix(".py").replace("/", ".")
                    placed |= {f"{module}.{k}": (f, *m) for k, m in
                               mutant_lines(mutated.read_text(), (wt / f).read_text()).items()
                               if m[0] in lines and _function(k) in args.functions}
            globs = sorted(placed) or ["no_such_mutant"]
            print(f"{len(placed)} mutants on lines changed since {args.lines}")
        run = subprocess.run([str(ROOT / ".venv/bin/mutmut"), "run", *globs], cwd=wt,
                             env={**os.environ, **ENV})
        metas = [json.loads(p.read_text()) for p in (wt / "mutants").rglob("*.meta")]
        got = scores(metas)
        if args.lines and run.returncode:   # never '0 survivors' from a run that never ran
            print(f"MUTMUT FAILED (exit {run.returncode}): no survivor count; see its output above")
        elif args.lines:   # every mutant run is on a changed line: each survivor fails the gate
            on = sorted(k for m in metas for k, c in m.get("exit_code_by_key", {}).items()
                        if c in SURVIVED | NO_TESTS and k in placed)
            print(f"survivors on lines changed since {args.lines}: {len(on)}")
            for k in on:   # the changed line itself: no `mutmut show` needed to judge it
                f, n, old, new = placed[k]
                print(f"  {f}:{n}  {k}\n    - {old}\n    + {new}")
            if args.record:
                record(changed, metas, placed, args.functions)
    got = {f: got.get(f, {"score": None}) for f in args.functions}
    print(f"mutation scores at {head} (tests: {' '.join(args.tests)})")
    for f, c in got.items():
        counts = " ".join(f"{k}={v}" for k, v in c.items() if k != "score")
        print(f"  {f:28} {c.get('score')}  {counts}")
    if args.json:
        Path(args.json).write_text(json.dumps({"head": head, "tests": args.tests, "scores": got},
                                              indent=1))
    if args.lines:
        return 1 if run.returncode or on else 0
    return 1 if run.returncode or any(c.get("survived") or c.get("no_tests")
                                      for c in got.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
