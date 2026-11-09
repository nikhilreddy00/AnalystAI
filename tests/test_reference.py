"""The independent oracle (evals/reference_kpis.py) agrees with the generators' truth.

Each dataset maps the truth keys it must reproduce (truth key -> reference key) and lists every
other truth key with the reason it is left out, so a new truth key can't slip by unnoticed."""
import ast
from pathlib import Path

import pytest

from evals import datagen
from evals.reference_kpis import reference

NAIVE = "naive twin (a trap's wrong answer)"
FILE = "file fact, not a spell or ledger KPI"
PAY = "pay, tenure or correlation: not a spell or ledger KPI"

LEDGER = {"as_of": "as_of", "cohort_date": "window_start", "new_mrr": "new_mrr",
          "expansion_mrr": "expansion_mrr", "contraction_mrr": "contraction_mrr",
          "churn_mrr": "churned_mrr", "reactivation_mrr": "reactivation_mrr",
          "new_n": "new_customers", "expansion_n": "expansion_customers",
          "contraction_n": "contraction_customers", "churn_n": "churned_customers",
          "reactivation_n": "reactivated_customers", "opening_mrr": "opening_mrr",
          "opening_customers": "opening_customers", "nrr": "nrr", "grr": "grr",
          "logo_churn_rate": "logo_churn_rate", "mrr": "mrr",
          "paying_customers": "paying_customers", "arr": "arr"}
HR = {"as_of": "as_of", "headcount_asof": "headcount", "avg_headcount_12m": "avg_headcount",
      "separations_12m": "separations", "attrition_12m": "attrition_rate"}

CASES = {
    "saas_subscriptions": (1, {**LEDGER, "arpa": "arpa", "mrr_by_segment": "mrr_by_segment",
                               "mrr_by_plan": "mrr_by_plan",
                               "logo_churn_by_segment": "logo_churn_rate_by_segment"}, {
        "row_count": FILE, "pii_columns": FILE, "customers_in_file": FILE,
        "test_customers": FILE, "eur_customers_paying": "the excluded currency's own count",
        "scheduled_cancels": FILE, "partial_month": "a label, not a number",
        **{k: NAIVE for k in ("mrr_annual_not_normalised", "mrr_no_quantity", "mrr_no_discount",
                              "mrr_with_trials_and_paused", "mrr_without_past_due",
                              "churned_by_canceled_at", "new_counting_reactivations",
                              "mrr_one_subscription_each", "churn_counting_cancel_at",
                              "mrr_with_test_and_eur", "customers_as_rows")}}),
    "saas_mrr_snapshots": (1, {**LEDGER, "mrr_by_segment_now": "mrr_by_segment",
                               "nrr_by_segment_at_cohort": "nrr_by_segment"}, {
        "row_count": FILE, "pii_columns": FILE,
        "churned_mrr_by_month": "per-month breakdown over all history: a period-asked figure",
        "churned_mrr_by_month_window": "per-month breakdown: a period-asked figure",
        "biggest_churn_month": "derived from the per-month breakdown",
        "northwind_share_of_big_month": "a planted-story ratio, not a KPI",
        "monthly_logo_churn_by_month": "a monthly rate, not a pack KPI",
        "monthly_logo_churn_avg": "a monthly rate, not a pack KPI",
        **{k: NAIVE for k in ("mrr_summed_all_months", "nrr_uncapped_as_grr",
                              "nrr_period_with_new", "annual_churn_monthly_x12",
                              "as_of_from_label")}}),
    "hr_roster": (1, {**HR, "window_start": "window_start", "contingent_asof": "contingent_workers",
                      "fte_asof": "fte", "headcount_by_month_end": "headcount_by_month_end",
                      "attrition_12m_by_dept": "attrition_rate_by_department",
                      "voluntary_attrition_12m": "voluntary_attrition",
                      "involuntary_attrition_12m": "involuntary_attrition",
                      "retirement_rate_12m": "retirement_rate",
                      "voluntary_attrition_12m_by_dept": "voluntary_attrition_by_department",
                      "hires_12m": "hires", "headcount_by_dept_asof": "headcount_by_department"}, {
        "row_count": FILE, "pii_columns": FILE, "people": FILE, "rows": FILE,
        "legal_people_asof": "same number as headcount_by_department['Legal']",
        "attrition_12m_by_category": "the voluntary/involuntary/retirement keys again",
        "headcount_by_dept_2025_12_31": "a headcount at a date asked for, not the as-of",
        "attrition_by_year": "per-year windows: a period-asked figure",
        "attrition_by_year_months": "per-year windows: a period-asked figure",
        "attrition_partial_year": "per-year windows: a period-asked figure",
        "attrition_2025_vs_2026": "per-year windows: a period-asked figure",
        "attrition_2026_partial": "per-year windows: a period-asked figure",
        "future_exits": FILE, "future_hires": FILE, "terminated_without_date": FILE,
        **{k: PAY for k in ("avg_salary_asof", "avg_salary_by_dept_asof",
                            "pay_gap_mean_fte_by_level", "pay_gap_median_fte_by_level",
                            "tenure_median_current_months", "tenure_median_at_exit_months",
                            "corr_salary_left", "corr_performance_involuntary",
                            "involuntary_share_by_rating")},
        **{k: NAIVE for k in ("headcount_asof_future_exits_out",
                              "headcount_asof_with_terminated_no_date", "attrition_12m_end_hc",
                              "attrition_12m_by_dept_end_hc", "attrition_all_rows",
                              "attrition_12m_with_contingent", "voluntary_incl_retirement",
                              "avg_headcount_start_end", "attrition_2026_annualised",
                              "pay_gap_mean_annual_by_level", "pay_gap_median_annual_by_level")}}),
    "hr_roster_onerow": (1, HR, {
        "row_count": FILE, "pii_columns": FILE, "people": FILE, "rows": FILE,
        "separations_12m_spells": "the spell file's count, not this file's",
        "first_exits_not_in_file": FILE, "avg_headcount_12m_original_hire": NAIVE}),
    # seed 4: a person hired and terminated inside October, only seen on a Terminated row
    "hr_snapshots": (4, {**HR, "headcount_by_month_end": "headcount_by_month_end",
                         "attrition_12m_by_dept_at_exit": "attrition_rate_by_department"}, {
        "row_count": FILE, "pii_columns": FILE, "rows": FILE,
        "avg_salary_by_dept_asof": PAY, "total_salary_asof": PAY, "transfers": FILE,
        "hires_after_first_snapshot": "whole-file count, not windowed",
        **{k: NAIVE for k in ("separations_12m_with_termination_date", "headcount_summed_rows",
                              "salary_summed_all_months", "attrition_by_dept_current_dept",
                              "headcount_asof_with_terminated_rows")}}),
    "employees": (1, {"as_of": "as_of", "avg_headcount_12m": "avg_headcount",
                      "separations_12m": "separations",
                      "attrition_12m_by_dept": "attrition_rate_by_department"}, {
        "row_count": FILE, "pii_columns": FILE, "null_pairs": FILE, "corr_salary_left": PAY,
        "attrition_by_dept": "share of the file who ever left (a column mean), over no period"}),
}


def test_reference_is_independent():
    src = Path(__file__).parents[1] / "evals" / "reference_kpis.py"
    names = set()
    for node in ast.walk(ast.parse(src.read_text())):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            names |= {node.module or ""} | {f"{node.module}.{a.name}" for a in node.names}
    bad = {n for n in names if n.split(".")[0] in ("domain_pack", "agents")
           or n.startswith("evals.datagen") or n.endswith(".datagen") or n == "evals"}
    assert not bad, bad


def _same(got, want) -> bool:
    if isinstance(want, dict):
        return isinstance(got, dict) and set(got) == set(want) and all(
            _same(got[k], want[k]) for k in want)
    if isinstance(want, str):
        return got == want
    return got == pytest.approx(want, rel=1e-9, abs=1e-12)


@pytest.mark.parametrize("name", list(CASES))
def test_reference_matches_truth(name):
    seed, keys, excluded = CASES[name]
    df, truth = datagen.make(name, seed)
    assert set(keys) | set(excluded) == set(truth), "every truth key is required or excluded"
    got = reference(name, df)
    assert set(got) >= set(keys.values())
    wrong = {k: (got[r], truth[k]) for k, r in keys.items() if not _same(got[r], truth[k])}
    assert not wrong, wrong


@pytest.mark.parametrize("name", list(CASES))
def test_the_reference_equals_the_product_on_every_spell_dataset(name):
    """The differential check: the product's KPI engine, through the production reader and
    cleaning, gives the reference's number for every KPI both compute."""
    import domain_pack as dp
    from tests.test_oracle import _built
    ref = reference(name, datagen.make(name, 1)[0])
    _, df, contract, _ = _built(name)
    pack = dp.pack_for(contract.domain)
    roles = {r: c for r, c in contract.roles.items() if c in df.columns}
    both = [k.name for k in pack.kpis if k.name in ref and dp.computable(pack, k.name, roles)]
    assert len(both) >= 5, both
    for k in both:
        assert dp.compute_kpi(df, roles, pack, k) == pytest.approx(ref[k], rel=1e-9), k
