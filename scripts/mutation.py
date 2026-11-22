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
diff REF...HEAD touches (and names the changed product files outside source_paths: not mutated); `--lines REF` then runs only their mutants on lines that diff changed
and lists each survivor by file:line (the 5.7b-2 gate: 0 survivors on changed lines but the
equivalents gate.json names, in any source file). `--plant` replaces the one exact occurrence of OLD
with NEW in a worktree and runs the tests: caught = they pass on HEAD and one fails with the
plant (a source guard is refused: the gate never runs it).

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
TOTAL = ".mutation-total"        # in the worktree: the mutants a --lines run will run
SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
ENV = {"ANALYSTAI_TODAY": "2026-11-01", "HYPOTHESIS_PROFILE": "mutation"}
SOURCE_GUARDS = {"tests/test_one_definition.py"}   # grep the sources: mutmut rewrites them
# the pre-push gate's files: spell, ledger, pack-column and scorer functions (kpi's _spell and
# rows' with_spell_events are spell functions: the 5.7b-1 baseline scored them); 5.7b-3: the
# typed claims and the bridge (story's `bridge`, read by the story and the chart)
GATED = ("domain_pack/spell.py", "domain_pack/ledger.py", "domain_pack/columns.py",
         "domain_pack/kpi.py", "domain_pack/rows.py", "evals/scoring.py", "agents/claims.py",
         "domain_pack/story.py",
         # retro Domain 7: the review's 7 recurrences all lived outside the gate (the time and
         # epoch rules, the PII check, the targeted answers), so "checks green" never read them
         "connectors/base.py", "guardrails.py", "agents/targeted_analysis_agent.py",
         # 5.8a: the dialect table, the connection-string guard, the result-to-frame rule
         "connectors/sql_connector.py",
         # 5.8b: the share-link download (its hops, cap and refusals)
         "connectors/link.py")
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
    # a file outside GATED may hold named equivalents only (the --lines run reads them)
    for f in sorted({*GATED, *(k.split("::", 1)[0] for k in gate)}):
        now = function_hashes(_at("HEAD", f, root))
        exist |= {f"{f}::{name}" for name in now}
        if f not in GATED:
            continue
        before = function_hashes(_at(ref, f, root))
        for name, h in now.items():
            if before.get(name) != h and gate.get(key := f"{f}::{name}", {}).get("hash") != h:
                problems.append(f"{key}: changed since {ref[:12]} with no clean mutation gate "
                                f"recorded")
    problems += [f"{k}: in {GATE}, but no such function (renamed or deleted)"
                 for k in gate if k not in exist]
    return problems


def _grep(pattern: str, *paths: str, root: Path = ROOT) -> list[str]:
    got = subprocess.run(["git", "-C", str(root), "grep", "-l", *pattern.split("\0"), "--", *paths],
                         capture_output=True, text=True)
    return got.stdout.split()


def caller_tests(names: list[str], root: Path = ROOT) -> list[str]:
    """The test files that name a function, and those that import a module calling it (retro
    Domain 8: a hand-picked list missed one), so the gate's rerun runs as printed."""
    # ponytail: module-level importers over-include (35 files for story's tie-break); narrow to the
    # calling functions' names if a gate run gets slow
    tests: set[str] = set()
    for name in {n.rsplit(".", 1)[-1] for n in names}:
        for f in _grep(f"-w\0{name}", "*.py", root=root):
            if f.startswith("tests/"):
                tests.add(f)
            else:
                tests.update(_grep(f"-F\0{f[:-3].replace('/', '.')}", "tests/*.py", root=root))
    return sorted(t for t in tests if t.startswith("tests/test_"))


def record_tests(names: list[str], given: list[str] | None, root: Path = ROOT) -> list[str]:
    """The tests a --record runs: every caller's test file (runnable under mutmut) and any extra
    given. A given list that leaves a caller's out is refused, naming it: a shorter list hides
    survivors the full one shows, so each hand-picked re-record found a different one (PR9-02
    recurred twice: the hint printed the callers, main still took any --tests)."""
    callers = runnable(caller_tests(names, root))
    if given is None:
        return callers
    if missing := [t for t in callers if t not in given]:
        raise ValueError(f"--record leaves out callers' tests: {' '.join(missing)} "
                         "(omit --tests to run them all)")
    return [*callers, *(t for t in given if t not in callers)]


def rerun(problems: list[str], ref: str) -> str | None:
    """The run that clears check's refusals: only the refused functions, never --changed-since
    (it mutates every changed function, gated or not), over their callers' tests; None when only
    orphans are left."""
    names = [p.split("::", 1)[1].split(":", 1)[0] for p in problems if "no clean mutation" in p]
    return (f"run: uv run python scripts/mutation.py {' '.join(names)} --lines {ref} --record"
            if names else None)   # --record runs every caller's tests itself (record_tests)


def unnamed(entry: dict, mutants: list[tuple[str, str, str]]) -> list[tuple[str, str, str]]:
    """The left mutants ((kind, old line, new line)) a GATE entry's equivalents don't explain:
    any that isn't a survivor, and a survivor seen more times than it is named."""
    named = Counter()
    for e in entry.get("equivalents", []):
        named[e["old"], e["new"]] += e["n"]
    seen = Counter(m[1:] for m in mutants if m[0] == "survived")
    return [m for m in mutants if m[0] != "survived" or seen[m[1:]] > named[m[1:]]]


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
        if bad := unnamed(entry, left.get(key, [])):
            unexplained[key] = bad
            entry.pop("hash", None)
        else:
            entry["hash"] = h
    return new, unexplained


def source_paths(root: Path = ROOT) -> list[str]:
    return tomllib.loads((root / "pyproject.toml").read_text())["tool"]["mutmut"]["source_paths"]


def outside_source_paths(files: list[str], paths: list[str]) -> list[str]:
    """The product .py files mutmut never mutates (tests, scripts, the frontend and the oracle,
    checked against the generators, are never product files)."""
    def product(f):
        return (f.endswith(".py") and not f.startswith(("tests/", "scripts/", "frontend/"))
                and f != "evals/reference_kpis.py")
    return sorted(f for f in files if product(f)
                  and not any(f == p or (p.endswith("/") and f.startswith(p)) for p in paths))


def undefined(functions: list[str], paths: list[str], root: Path = ROOT) -> list[str]:
    """The named functions no source_paths file defines (mutmut would have no mutant of them)."""
    files = [g for p in paths for g in ((root / p).rglob("*.py") if p.endswith("/")
                                        else [root / p])]
    defined = {n for g in files for n, _ in _defs(g.read_text(), every=True)}
    return [f for f in functions if f not in defined]


def changed_lines(ref: str) -> dict[str, set[int]]:
    """Per source_paths file, the HEAD lines the diff ref...HEAD adds or changes."""
    paths = source_paths()
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


def progress(got: dict[str, dict], total: int | None) -> str:
    """One line for --status: mutants run so far (of the run's total on changed lines, when it
    wrote one) and how many no test killed."""
    done = sum(c[k] for c in got.values() for k in ("killed", "survived", "no_tests", "timeout"))
    left = sum(c["survived"] + c["no_tests"] for c in got.values())
    return f"done {done}" + (f" of {total}" if total else "") + f", {left} not killed"


def without_spinner(text: str) -> str:
    """mutmut's output without its progress frames (each a '\\r'-separated spinner line)."""
    return "\n".join(line for line in text.splitlines() if line.strip() and line[0] not in SPINNER)


def status() -> int:
    """Scores so far of every mutation run still going (read from its worktree's .meta files)."""
    runs = sorted(Path(tempfile.gettempdir()).glob(f"{PREFIX}*/mut/mutants"))
    if not runs:
        print("no mutation run is going")
    for m in runs:
        got = scores([json.loads(p.read_text()) for p in m.rglob("*.meta")])
        total = m.parent / TOTAL
        print(f"{m.parent}: {progress(got, int(total.read_text()) if total.is_file() else None)}")
        for f, c in got.items():
            print(f"  {f:28} {c['score']}  " + " ".join(f"{k}={v}" for k, v in c.items()
                                                        if k != "score"))
    return 0


def plant_verdict(clean: int, bugged: int) -> tuple[str, int]:
    """(word, exit) from pytest's exit on HEAD and with the plant. Caught only when the tests
    pass without the plant: a test already red (a cold cache, an ordering bug) proves nothing
    (retro 5.7b-2c: a plant said 'caught' on a test that failed on its own)."""
    if clean:
        return f"ERROR: the tests fail without the plant (pytest exit {clean})", 2
    return {1: ("caught", 0), 0: ("MISSED: every selected test passed", 1)}.get(
        bugged, (f"ERROR: pytest exit {bugged}", 2))


def plant(file: str, old: str, new: str, tests: list[str], keyword: str | None) -> int:
    """0 when the selected tests pass on HEAD and one fails on the planted bug, 1 when every
    test passes with it, 2 otherwise (an error is not a catch)."""
    if dropped := [t for t in tests if t not in runnable(tests)]:
        print("left out (the gate never runs them: they read the sources mutmut rewrites): "
              + " ".join(dropped))
    if not (tests := runnable(tests)):
        print(f"plant in {file}: ERROR: no --tests the gate runs; put the check in another file")
        return 2
    cmd = [str(ROOT / ".venv/bin/python"), "-m", "pytest", "-q", "-x", "-p", "no:warnings",
           *tests, *(["-k", keyword] if keyword else [])]
    with worktree() as wt:
        clean = subprocess.run(cmd, cwd=wt, env={**os.environ, **ENV}, capture_output=True,
                               text=True)
        src = wt / file
        src.write_text(planted(src.read_text(), old, new))
        run = subprocess.run(cmd, cwd=wt, env={**os.environ, **ENV}, capture_output=True,
                             text=True) if not clean.returncode else clean
    print("\n".join(run.stdout.strip().splitlines()[-3:]))
    word, code = plant_verdict(clean.returncode, run.returncode)
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
    print(f"recorded in {GATE}: {' '.join(k for k in hashes if k not in unexplained) or '(none)'}"
          f" (commit {GATE} before --check: it reads HEAD's)")
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
        if hint := rerun(problems, args.check):
            print(hint, file=sys.stderr)
        return 1 if problems else 0
    if args.record and not args.lines:
        ap.error("--record needs --lines REF (it records a gate on changed lines)")
    if args.record:
        try:
            names = [*args.functions, *(changed_functions(args.changed_since)
                                        if args.changed_since else [])]
            args.tests = record_tests(names, args.tests)
        except ValueError as e:
            ap.error(str(e))
    if not args.tests:
        ap.error("--tests is required (a --record finds its own: the callers' tests)")
    if args.plant:
        _refuse_uncommitted([args.plant[0], *args.tests])
        return plant(*args.plant, args.tests, args.keyword)
    _refuse_uncommitted([*source_paths(), *args.tests])
    if dropped := [t for t in args.tests if t not in runnable(args.tests)]:
        print("left out (they read the sources mutmut rewrites): " + " ".join(dropped))
        args.tests = runnable(args.tests) or ap.error("no --tests left to run")
    if args.changed_since:
        args.functions = sorted({*args.functions, *changed_functions(args.changed_since)})
        print(f"changed since {args.changed_since}: {' '.join(args.functions) or '(none)'}")
        if skipped := sorted(set(changed_functions(args.changed_since, every=True)) - set(args.functions)):
            print(f"not mutated (decorated: mutmut skips them; their tests are their check): "
                  f"{' '.join(skipped)}")
        diff = subprocess.run(["git", "-C", str(ROOT), "diff", "--name-only", "--diff-filter=d",
                               f"{args.changed_since}...HEAD"], capture_output=True, text=True,
                              check=True).stdout.split()
        if outside := outside_source_paths(diff, source_paths()):
            print(f"NOT MUTATED, changed but outside [tool.mutmut] source_paths (move a file "
                  f"there from also_copy to gate it): {' '.join(outside)}")
        if not args.functions:
            return 0
    if not args.functions:
        ap.error("name a function, or pass --changed-since")
    if missing := undefined(args.functions, source_paths()):
        ap.error(f"no [tool.mutmut] source_paths file defines {' '.join(missing)} (move its file "
                 f"there from also_copy)")
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
            (wt / TOTAL).write_text(str(len(placed)))   # --status: done N of this
        # into a log file, mutmut's spinner frames ('\r') are megabytes: keep its other lines
        log = not sys.stdout.isatty()
        run = subprocess.run([str(ROOT / ".venv/bin/mutmut"), "run", *globs], cwd=wt,
                             env={**os.environ, **ENV}, capture_output=log, text=True)
        if log:
            print(without_spinner(run.stdout + run.stderr), flush=True)
        metas = [json.loads(p.read_text()) for p in (wt / "mutants").rglob("*.meta")]
        got = scores(metas)
        if args.lines and run.returncode:   # never '0 survivors' from a run that never ran
            print(f"MUTMUT FAILED (exit {run.returncode}): no survivor count; see its output above")
        elif args.lines:   # every mutant run is on a changed line: each survivor fails the gate
            codes = {k: c for m in metas for k, c in m.get("exit_code_by_key", {}).items()
                     if c in SURVIVED | NO_TESTS and k in placed}
            on = sorted(codes)
            left = {}
            for k in on:
                f, _, old, new = placed[k]
                kind = "survived" if codes[k] in SURVIVED else "no_tests"
                left.setdefault(f"{f}::{_function(k)}", []).append((kind, old, new))
            gate = json.loads(path.read_text()) if (path := ROOT / GATE).is_file() else {}
            bad = {key: unnamed(gate.get(key, {}), ms) for key, ms in left.items()}
            unexplained = sum(map(len, bad.values()))
            print(f"survivors on lines changed since {args.lines}: {len(on)}, "
                  f"{len(on) - unexplained} of them named equivalents in {GATE}")
            for k in on:   # the changed line itself: no `mutmut show` needed to judge it
                f, n, old, new = placed[k]
                kind = "survived" if codes[k] in SURVIVED else "no_tests"
                named = (kind, old, new) not in bad[f"{f}::{_function(k)}"]
                print(f"  {f}:{n}  {k}{'  (named equivalent)' if named else ''}"
                      f"\n    - {old}\n    + {new}")
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
        return 1 if run.returncode or unexplained else 0
    return 1 if run.returncode or any(c.get("survived") or c.get("no_tests")
                                      for c in got.values()) else 0


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    from scripts.live import live
    with live("mutation", minutes=120):   # survivors kept in .live/ (a cut output cost a rerun)
        code = main()
    sys.exit(code)
