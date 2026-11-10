"""scripts/mutation.py turns mutmut's per-file .meta results into a score per function."""
import pytest

from scripts.mutation import functions_touching, mutant_line, on_changed_lines, planted, scores


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


def test_a_mutant_is_placed_on_its_source_line_from_its_diff():
    """mutmut show's diff is of the function alone (its def = line 1): the first changed line,
    offset from the def's line in the file, is the line the gate checks against the change."""
    diff = """--- domain_pack/spell.py
+++ domain_pack/spell.py
@@ -1,5 +1,5 @@
 def cohort_start(as_of, known):
     \"\"\"doc\"\"\"
-    return max(as_of - pd.DateOffset(months=WINDOW_MONTHS), known)
+    return min(as_of - pd.DateOffset(months=WINDOW_MONTHS), known)
"""
    assert mutant_line(diff, 323) == 325
    later = "--- f\n+++ f\n@@ -7,3 +7,3 @@\n     a = 1\n-    b = 2\n+    b = 3\n"
    assert mutant_line(later, 10) == 17       # hunk at the function's 7th line, one context line


def test_only_survivors_on_changed_lines_fail_the_gate():
    survivors = {"domain_pack.spell.x_cohort_start__mutmut_1": ("domain_pack/spell.py", 325),
                 "domain_pack.spell.x_cohort_start__mutmut_2": ("domain_pack/spell.py", 330)}
    on, off = on_changed_lines(survivors, {"domain_pack/spell.py": {325, 326}})
    assert on == ["domain_pack.spell.x_cohort_start__mutmut_1"]
    assert off == ["domain_pack.spell.x_cohort_start__mutmut_2"]


def test_a_plant_must_match_exactly_once():
    assert planted("a = 1\nb = 2\n", "b = 2", "b = 3") == "a = 1\nb = 3\n"
    for text in ("a = 1\n", "b = 2\nb = 2\n"):
        with pytest.raises(SystemExit, match="exactly 1"):
            planted(text, "b = 2", "b = 3")
