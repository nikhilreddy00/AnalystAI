"""scripts/mutation.py turns mutmut's per-file .meta results into a score per function."""
import subprocess

import pytest

from scripts.mutation import functions_touching, mutant_lines, planted, scores, uncommitted


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
    assert functions_touching(src, {7}) == ["b"]          # the decorator line is b's
    assert functions_touching(src, {10}) == ["b"]         # a nested def belongs to b
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


def test_a_plant_must_match_exactly_once():
    assert planted("a = 1\nb = 2\n", "b = 2", "b = 3") == "a = 1\nb = 3\n"
    for text in ("a = 1\n", "b = 2\nb = 2\n"):
        with pytest.raises(SystemExit, match="exactly 1"):
            planted(text, "b = 2", "b = 3")
