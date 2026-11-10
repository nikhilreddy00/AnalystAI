"""scripts/mutation.py turns mutmut's per-file .meta results into a score per function."""
from scripts.mutation import scores


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
