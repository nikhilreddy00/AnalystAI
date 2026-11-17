"""scripts/mutation.py turns mutmut's per-file .meta results into a score per function."""
import json
import subprocess
from pathlib import Path

import pytest

from scripts.mutation import (GATED, ROOT, check, function_hashes, functions_touching,
                              mutant_lines, naming, outside_source_paths, plant, plant_verdict,
                              planted, progress, recorded, rerun, runnable, scores, uncommitted,
                              undefined, unnamed, unreached, without_spinner)


def test_mutants_are_scored_per_function_and_an_untested_mutant_is_not_killed():
    meta = {"exit_code_by_key": {
        "domain_pack.x_spell_window__mutmut_1": 1,      # killed
        "domain_pack.x_spell_window__mutmut_2": 3,      # killed
        "domain_pack.x_spell_window__mutmut_3": 0,      # survived
        "domain_pack.x_spell_window__mutmut_4": 33,     # no test reached it: not killed
        "domain_pack.x_spell_window__mutmut_5": 36,     # timeout: reported apart
        "domain_pack.spell.x_cohort_start__mutmut_1": 1,
        "domain_pack.xǁPackǁkpi__mutmut_1": 0,
        "domain_pack.x_other__mutmut_1": None,          # not checked (another function's run)
    }}
    got = scores([meta])
    assert got["spell_window"] == {"killed": 2, "survived": 1, "no_tests": 1, "timeout": 1,
                                   "score": 0.5}
    assert got["cohort_start"]["score"] == 1.0
    assert got["Pack.kpi"]["score"] == 0.0
    assert "other" not in got


def test_a_change_names_the_function_or_method_it_is_in_never_its_neighbours():
    src = '''import x

def a():
    return 1


@decorator
def b():
    def inner():
        return 2
    return inner


class Pack:
    def kpi(self):
        return 3
'''
    assert functions_touching(src, {4}) == ["a"]
    assert functions_touching(src, {7}, every=True) == ["b"]    # the decorator line is b's
    assert functions_touching(src, {10}, every=True) == ["b"]   # a nested def belongs to b
    assert functions_touching(src, {10}) == []   # decorated: mutmut has no mutant of it
    assert functions_touching(src, {16}) == ["Pack.kpi"]
    assert functions_touching(src, {1, 5, 6}) == []       # imports and blank lines: nothing


def test_only_mutants_on_changed_lines_are_run():
    """The gate judges changed lines only (ROADMAP 5.7b-2 step 9), so --lines runs only the
    mutants whose copy differs from the function's unmutated copy on a changed line: a
    mutant's line is the def's line in the source plus its first differing line."""
    source = "import x\n\n\ndef f(a):\n    b = a + 1\n    return b * 2\n\n\nclass P:\n    def k(self):\n        return 3\n"
    mutated = ("def x_f__mutmut_orig(a):\n    b = a + 1\n    return b * 2\n"
               "def x_f__mutmut_1(a):\n    b = a - 1\n    return b * 2\n"
               "def x_f__mutmut_2(a):\n    b = a + 1\n    return b / 2\n"
               "class P:\n    def xǁPǁk__mutmut_orig(self):\n        return 3\n"
               "    def xǁPǁk__mutmut_1(self):\n        return 4\n")
    assert mutant_lines(mutated, source) == {
        "x_f__mutmut_1": (5, "b = a + 1", "b = a - 1"),
        "x_f__mutmut_2": (6, "return b * 2", "return b / 2"),
        "xǁPǁk__mutmut_1": (11, "return 3", "return 4")}


def test_a_run_refuses_files_that_differ_from_head(tmp_path):
    """The worktree is HEAD (retro 5.7b-2a: two plants ran without their new tests), so a
    changed or untracked file the run reads is refused, never silently left out."""
    def git(*a):
        subprocess.run(["git", "-C", str(tmp_path), *a], check=True, capture_output=True)
    git("init", "-q")
    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / "c.py").write_text("y = 1\n")
    git("add", ".")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "c")
    assert uncommitted(["a.py", "c.py::test_x"], tmp_path) == []
    (tmp_path / "a.py").write_text("x = 2\n")
    (tmp_path / "b.py").write_text("")
    assert uncommitted(["a.py", "b.py", "c.py::test_x"], tmp_path) == ["a.py", "b.py"]


def test_a_function_no_selected_test_reaches_is_found_before_any_mutant_runs(tmp_path):
    """Retro 5.7b-2b: a narrow --tests list showed check_segment_value as 35 no_tests only after
    hours of mutants. mutmut's stats (which tests reach each function) exist before any mutant
    runs; a function with none is refused, with the test files that name it."""
    stats = {"tests_by_mangled_function_name": {
        "evals.scoring.x_false_findings": ["tests/test_eval_scorer.py::test_a"],
        "evals.scoring.x_check_segment_value": [],
        "domain_pack.xǁPackǁkpi": ["tests/test_pack.py::test_k"]}}
    assert unreached(stats, ["false_findings", "check_segment_value", "Pack.kpi", "handle_kpi"]) \
        == ["check_segment_value", "handle_kpi"]
    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text("from x import check_segment_value\n")
    (tmp_path / "tests" / "test_b.py").write_text("check_segment_value_truth\n")   # another word
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    assert naming("check_segment_value", tmp_path) == ["tests/test_a.py"]
    assert naming("Pack.kpi", tmp_path) == []


def test_the_source_guards_are_left_out_of_a_mutation_run():
    """Retro 5.7b-2b: tests/test_one_definition.py greps the sources, so mutmut's rewritten copies
    fail it before any mutant runs (gate 1 died on it). The run drops it, file or node id."""
    assert runnable(["tests/test_spell.py", "tests/test_one_definition.py",
                     "tests/test_one_definition.py::test_x", "tests/test_saas.py::test_y"]) \
        == ["tests/test_spell.py", "tests/test_saas.py::test_y"]


def test_a_plant_is_caught_only_by_tests_that_pass_without_it():
    """Retro 5.7b-2c: a test red on its own (asyncio.run on a cold cache) read as 'caught'."""
    assert plant_verdict(0, 1) == ("caught", 0)
    assert plant_verdict(0, 0)[1] == 1
    assert plant_verdict(1, 1)[1] == 2 and "without the plant" in plant_verdict(1, 1)[0]
    assert plant_verdict(0, 5)[1] == 2   # pytest selected nothing


def test_a_plant_refuses_tests_the_gate_never_runs(capsys):
    """Retro 5.7b-2c: a kill planted against test_one_definition.py said 'caught', and the gate,
    which leaves that file out, still found the survivor."""
    assert plant("domain_pack/story.py", "x", "y", ["tests/test_one_definition.py"], None) == 2
    assert "no --tests the gate runs" in capsys.readouterr().out


def test_status_says_how_many_of_the_runs_mutants_are_done():
    """Retro 5.7b-2c: 'how long is left?' took five calls; mutmut's own count is every mutant."""
    got = {"f": {"killed": 3, "survived": 1, "no_tests": 0, "timeout": 1, "score": 0.75},
           "g": {"killed": 2, "survived": 0, "no_tests": 1, "timeout": 0, "score": 0.67}}
    assert progress(got, 40) == "done 8 of 40, 2 not killed"
    assert progress(got, None) == "done 8, 2 not killed"


def test_a_logged_run_keeps_mutmuts_lines_but_not_its_spinner():
    raw = "⠋ Generating mutants⠙ Generating mutants\r    done in 69s\r⠦ 0/31562  🎉 0\r⠧ 1/31562\n🎉 a.x_f__mutmut_1\n"
    assert without_spinner(raw) == "    done in 69s\n🎉 a.x_f__mutmut_1"


def test_a_plant_must_match_exactly_once():
    assert planted("a = 1\nb = 2\n", "b = 2", "b = 3") == "a = 1\nb = 3\n"
    for text in ("a = 1\n", "b = 2\nb = 2\n"):
        with pytest.raises(SystemExit, match="exactly 1"):
            planted(text, "b = 2", "b = 3")


def _repo(tmp_path):
    """A git repo with the gate's layout: commit(files) writes, commits and returns the sha."""
    def git(*a):
        return subprocess.run(["git", "-C", str(tmp_path), *a], check=True, capture_output=True,
                              text=True).stdout.strip()

    def commit(files: dict[str, str]):
        for name, text in files.items():
            (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / name).write_text(text)
        git("add", ".")
        git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "c")
        return git("rev-parse", "HEAD")
    git("init", "-q")
    return commit


SPELL = "domain_pack/spell.py"
BASE = '''def f(a):
    return a + 1


def g():
    """Two."""
    return 2


class W:
    def m(self):
        return 3
'''


def _gate(entries: dict) -> dict[str, str]:
    return {"docs/mutation/gate.json": json.dumps(entries)}


def _keys(problems: list[str]) -> list[str]:
    return [p.split(": ", 1)[0] for p in problems]


def test_the_hash_is_the_functions_code_never_its_comments_docstring_or_place():
    """2a plan decision 15: a comment, a docstring or a function moved down the file needs no new
    mutation run; a changed expression or decorator does."""
    h = function_hashes(BASE)
    assert set(h) == {"f", "g", "W.m"}
    same = "# a comment\n\n\n" + BASE.replace('"""Two."""', '"""Two, said again."""') \
        .replace("return a + 1", "return a + 1   # why")
    assert function_hashes(same) == h
    assert function_hashes(BASE.replace("a + 1", "a + 2"))["f"] != h["f"]
    assert function_hashes(BASE.replace("return 3", "return 4"))["W.m"] != h["W.m"]
    # mutmut mutates no decorated function (a lone @staticmethod / @classmethod aside): it is
    # never in the gate, whose record a mutation run must be able to write
    assert "f" not in function_hashes(BASE.replace("def f", "@cache\ndef f"))
    assert "W.m" not in function_hashes(BASE.replace("    def m", "    @property\n    def m"))
    assert "W.m" in function_hashes(BASE.replace("    def m(self)", "    @staticmethod\n    def m()"))


def test_the_gate_refuses_an_unrecorded_change_to_a_gated_function_and_passes_a_recorded_one(tmp_path):
    """ROADMAP 5.7b-2 step 9: the hook can't run mutmut (~13 s a mutant), so it refuses a push
    that changes a spell / ledger / pack-column / scorer function unless docs/mutation/gate.json
    holds that function's hash (written by --record after a clean gate)."""
    commit = _repo(tmp_path)
    base = commit({SPELL: BASE, "agents/x.py": "def y():\n    return 1\n"})
    assert check(base, tmp_path) == []
    changed = BASE.replace("a + 1", "a + 2")
    commit({SPELL: changed, "agents/x.py": "def y():\n    return 2\n"})   # agents: not gated
    assert _keys(check(base, tmp_path)) == [f"{SPELL}::f"]
    commit(_gate({f"{SPELL}::f": {"hash": function_hashes(changed)["f"], "equivalents": []}}))
    assert check(base, tmp_path) == []
    commit({SPELL: changed.replace('"""Two."""', '"""Two again."""') + "\n# end\n"})
    assert check(base, tmp_path) == []            # a docstring or a comment: no new run
    commit({SPELL: changed + "\n\ndef h():\n    return 5\n"})
    assert _keys(check(base, tmp_path)) == [f"{SPELL}::h"]   # a new function is a change
    commit({SPELL: changed.replace("a + 2", "a + 3")})
    assert _keys(check(base, tmp_path)) == [f"{SPELL}::f"]   # changed after its record


def test_the_gate_refuses_a_recorded_function_that_no_longer_exists(tmp_path):
    """A renamed or deleted function leaves its key behind: an orphan is refused, so gate.json
    names only functions that exist."""
    commit = _repo(tmp_path)
    base = commit({SPELL: BASE})
    renamed = BASE.replace("def f", "def f2")
    commit({SPELL: renamed,
            **_gate({f"{SPELL}::f2": {"hash": function_hashes(renamed)["f2"], "equivalents": []},
                     f"{SPELL}::f": {"hash": function_hashes(BASE)["f"], "equivalents": []}})})
    assert _keys(check(base, tmp_path)) == [f"{SPELL}::f"]


def test_a_file_outside_the_gate_holds_named_equivalents_but_no_orphan(tmp_path):
    """Retro 5.7b-2c: an equivalent in an agent file lived in prose only, so the --lines run
    failed on it. gate.json may name it; its function must still exist."""
    commit = _repo(tmp_path)
    eq = {"equivalents": [{"old": "a", "new": "b", "n": 1, "reason": "r"}]}
    base = commit({SPELL: BASE, "agents/x.py": "def y():\n    return 1\n",
                   **_gate({"agents/x.py::y": eq})})
    assert check(base, tmp_path) == []
    commit({"agents/x.py": "def z():\n    return 1\n"})
    assert _keys(check(base, tmp_path)) == ["agents/x.py::y"]


def test_a_survivor_is_unexplained_unless_named_as_often_as_it_is_left():
    """The one rule --record and the --lines exit share."""
    entry = {"equivalents": [{"old": "a", "new": "b", "n": 1, "reason": "r"}]}
    assert unnamed(entry, [("survived", "a", "b")]) == []
    assert len(unnamed(entry, [("survived", "a", "b")] * 2)) == 2
    assert unnamed(entry, [("no_tests", "a", "b")]) == [("no_tests", "a", "b")]
    assert unnamed({}, [("survived", "a", "b")]) == [("survived", "a", "b")]


def test_a_function_is_recorded_only_when_every_mutant_left_on_its_changed_lines_is_a_named_equivalent():
    """--record writes a hash only after a clean gate: each surviving mutant matches a named
    equivalent (its old and new line, as many times as it is named). A mutant no test reached,
    or one that timed out, is never an equivalent."""
    eq = [{"old": "x = a or b", "new": "x = a or None", "n": 1, "reason": "b is never read"}]
    gate = {"s.py::f": {"equivalents": eq}, "s.py::old": {"hash": "h0", "equivalents": []}}
    left = {"s.py::f": [("survived", "x = a or b", "x = a or None")], "s.py::g": []}
    new, unexplained = recorded(gate, {"s.py::f": "hf", "s.py::g": "hg"}, left)
    assert unexplained == {}
    assert new == {"s.py::f": {"hash": "hf", "equivalents": eq},
                   "s.py::g": {"hash": "hg", "equivalents": []},
                   "s.py::old": {"hash": "h0", "equivalents": []}}
    for bad in ([("survived", "x = a or b", "x = a or None")] * 2,
                [("no_tests", "x = a or b", "x = a or None")],
                [("timeout", "x = a or b", "x = a or None")],
                [("survived", "x = a or b", "x = b or b")]):
        new, unexplained = recorded(gate, {"s.py::f": "hf"}, {"s.py::f": bad})
        assert list(unexplained) == ["s.py::f"] and "hash" not in new["s.py::f"], bad


def test_the_gates_rerun_command_names_only_the_functions_it_refused():
    """Retro Domain 7: the hint said --changed-since main, which mutated 14 changed functions over
    16 test files (~75 min) where the gate refused 2 (~7 min when named)."""
    problems = [f"{SPELL}::f: changed since abc with no clean mutation gate recorded",
                "domain_pack/story.py::P.k: changed since abc with no clean mutation gate recorded",
                f"{SPELL}::g: in docs/mutation/gate.json, but no such function (renamed or deleted)"]
    assert rerun(problems, "abc") == ("run: uv run python scripts/mutation.py f P.k --lines abc "
                                      "--record --tests <each function's callers' test files>")
    assert rerun(problems[2:], "abc") is None     # an orphan is fixed in gate.json, not by a run


def test_the_hook_and_ci_both_run_the_gate():
    """The hook is opt-in (core.hooksPath) and skippable (--no-verify), so CI runs the same
    check; CI's checkout needs the history for the merge-base."""
    hook = (ROOT / ".githooks/pre-push").read_text()
    ci = (ROOT / ".github/workflows/ci.yml").read_text()
    call = 'scripts/mutation.py --check "$(git merge-base HEAD origin/main)"'
    assert call in hook
    job = ci.split("\n  zero-suites:", 1)[1].split("\n  frontend:", 1)[0]
    assert call in job and "fetch-depth: 0" in job


def test_a_changed_product_file_outside_source_paths_is_named_never_silently_skipped():
    """Retro 5.7b-3: guardrails.py sat in also_copy only, so --changed-since never mutated its
    changed lines and said nothing; the gate found it at the end, a run late."""
    paths = ["domain_pack/", "agents/claims.py"]
    changed = ["domain_pack/kpi.py", "agents/claims.py", "guardrails.py", "agents/x.py",
               "tests/test_x.py", "scripts/y.py", "evals/reference_kpis.py", "docs/a.md",
               "frontend/e2e/s.ts"]
    assert outside_source_paths(changed, paths) == ["agents/x.py", "guardrails.py"]


def test_every_gated_file_is_one_mutmut_mutates():
    import tomllib
    paths = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["mutmut"]["source_paths"]
    assert outside_source_paths(list(GATED), paths) == []


def test_a_named_function_no_source_path_defines_is_refused_before_any_worktree(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text("def f():\n    return 1\n\nclass P:\n    def k(self):\n"
                                           "        return 2\n")
    (tmp_path / "g.py").write_text("def h():\n    return 3\n")
    assert undefined(["f", "P.k", "h"], ["pkg/"], tmp_path) == ["h"]
    assert undefined(["f", "h"], ["pkg/", "g.py"], tmp_path) == []


def test_the_committed_gate_is_well_formed():
    """Every key names a file that exists; every equivalent says why."""
    gate = json.loads((ROOT / "docs/mutation/gate.json").read_text())
    for key, entry in gate.items():
        assert "::" in key and Path(ROOT / key.split("::")[0]).is_file(), key
        assert all(e.keys() == {"old", "new", "n", "reason"} and e["reason"].strip()
                   for e in entry["equivalents"]), key
