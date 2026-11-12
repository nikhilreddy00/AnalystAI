"""The independent oracle (evals/reference_kpis.py) agrees with the generators' truth.

Each dataset maps the truth keys it must reproduce (truth key -> reference key) and lists every
other truth key with the reason it is left out, so a new truth key can't slip by unnoticed."""

import pandas as pd
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
                               "nrr_by_segment_at_cohort": "nrr_by_segment",
                               "churned_mrr_by_month_window": "churned_mrr_by_month"}, {
        "row_count": FILE, "pii_columns": FILE,
        "churned_mrr_by_month": "per-month breakdown over all history: a period-asked figure",
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
    seg = roles.get("segment")   # 5.7b-2b (C3): each segment's moves, transfers in and out included
    for k in (k for k in both if seg and f"{k}_by_segment" in ref and k.endswith("_mrr")):
        got = dp.compute_kpi(df, roles, pack, k, by=seg)
        assert {s: v for s, v in got.items() if v} == pytest.approx(
            {s: v for s, v in ref[f"{k}_by_segment"].items() if v}, rel=1e-9), k


def test_the_references_word_lists_are_the_packs():
    """The oracle copies the packs' words (it may not read the product, pyproject's contract):
    a pack edit its copy misses would make the two agree on the wrong thing, or disagree for no
    bug. Read here from the packs, in the test only."""
    import domain_pack as dp
    from evals import reference_kpis as R
    hr, saas = dp.pack_for("hr"), dp.pack_for("saas")

    def cond(pack, kpi, i=0):
        return pack.kpi(kpi).num[0][0].where[0][i]
    assert set(R.HR_ENDED) == set(hr.spell.ended) and set(R.SAAS_ENDED) == set(saas.spell.ended)
    assert R.CONTINGENT == set(cond(hr, "headcount").not_in)
    assert (R.VOLUNTARY, R.INVOLUNTARY, R.RETIREMENT) == (
        set(cond(hr, "voluntary_separations", 1).in_), set(cond(hr, "involuntary_separations", 1).in_),
        set(cond(hr, "retirements", 1).in_))
    assert set(R.SAAS_OUT) == set(cond(saas, "mrr").not_in)
    assert R.TEST_MARKER.pattern == saas.test_marker


# ── 5.7b-2b (C3): the ledger's lines and transfer moves, from GLOSSARY Move ──────────────────

def _book(*months) -> pd.DataFrame:
    """An MRR export over Sep and Oct 2026: each month's (customer, MRR, segment) rows."""
    return pd.DataFrame([(pd.Timestamp(f"2026-{9 + i}-01"), c, v, s)
                         for i, rows in enumerate(months) for c, v, s in rows],
                        columns=["month", "customer_id", "mrr", "segment"])


MOVED = ["new_mrr", "expansion_mrr", "reactivation_mrr", "transfer_in_mrr"]
LOST = ["contraction_mrr", "churned_mrr", "transfer_out_mrr"]


@pytest.mark.parametrize("sep,oct_,want", [
    # 1. SMB 100 → Ent 100: moved, no new, no churn
    ([("c0", 100.0, "SMB")], [("c0", 100.0, "Ent")],
     {"transfer_out_mrr": {"SMB": 100}, "transfer_in_mrr": {"Ent": 100}}),
    # 2. SMB 100 → Ent 60: 60 moved, the 40 lost in the part that fell
    ([("c0", 100.0, "SMB")], [("c0", 60.0, "Ent")],
     {"transfer_out_mrr": {"SMB": 60}, "transfer_in_mrr": {"Ent": 60}, "contraction_mrr": {"SMB": 40}}),
    # 3. SMB 100 → Ent 150: 100 moved, the 50 grown in the part that rose
    ([("c0", 100.0, "SMB")], [("c0", 150.0, "Ent")],
     {"transfer_out_mrr": {"SMB": 100}, "transfer_in_mrr": {"Ent": 100}, "expansion_mrr": {"Ent": 50}}),
    # 4. SMB 100 + Mid 100 → Ent 150: 150 moved, 75 out of each; the 50 lost, 25 in each
    ([("c0", 100.0, "SMB"), ("c0", 100.0, "Mid")], [("c0", 150.0, "Ent")],
     {"transfer_out_mrr": {"SMB": 75, "Mid": 75}, "transfer_in_mrr": {"Ent": 150},
      "contraction_mrr": {"SMB": 25, "Mid": 25}}),
    # 5. a churn where it was, a new one where it starts; nothing moved
    ([("c0", 100.0, "SMB")], [("c1", 50.0, "Ent")],
     {"churned_mrr": {"SMB": 100}, "new_mrr": {"Ent": 50}}),
])
def test_a_customer_who_moves_segment_moves_between_their_bridges(sep, oct_, want):
    got = reference("book_mrr_export", _book(sep, oct_))
    for k in MOVED + LOST:
        assert {s: v for s, v in got[f"{k}_by_segment"].items() if v} == pytest.approx(want.get(k, {})), k
    assert got["transfer_in_mrr"] == got["transfer_out_mrr"]   # 7. nothing moves in the whole


def test_a_mover_is_no_churned_or_new_customer():
    """6. a count: c0 moved segment, c1 added a second segment and kept the first. Neither is a
    new or a churned customer."""
    got = reference("book_mrr_export", _book([("c0", 100.0, "SMB"), ("c1", 50.0, "SMB")],
                                              [("c0", 100.0, "Ent"), ("c1", 50.0, "SMB"),
                                               ("c1", 50.0, "Ent")]))
    assert (got["new_customers"], got["churned_customers"]) == (0, 0)
    assert got["expansion_mrr"] == 50 and got["expansion_mrr_by_segment"] == {"Ent": 50}


def test_churned_mrr_by_month_and_its_mean_per_customer():
    got = reference("book_mrr_export", _book([("c0", 100.0, "SMB"), ("c1", 50.0, "SMB"),
                                              ("c2", 10.0, "SMB")], [("c2", 10.0, "SMB")]))
    assert got["churned_mrr_by_month"] == {"2026-10": 150} and got["churned_customers_by_month"] == {"2026-10": 2}
    assert got["churned_mrr_mean_by_month"] == {"2026-10": 75}


def test_the_references_segment_bridge_adds_up_on_every_book():
    """The reference against the generator first (P1's books, concurrent rows and movers
    included): each segment opens on its lines, and its moves, transfers in and out included,
    end at its MRR."""
    from hypothesis import given

    from tests.test_properties import mrr_books

    @given(mrr_books())
    def check(df):
        got = reference("book_mrr_export", df)
        if got["new_mrr"] is None:   # one month of data: no moves
            return
        segs = set(got["mrr_by_segment"]) | set(got["opening_mrr_by_segment"])
        for s in segs:
            built = got["opening_mrr_by_segment"].get(s, 0) + sum(
                got[f"{k}_by_segment"].get(s, 0) for k in MOVED) - sum(
                got[f"{k}_by_segment"].get(s, 0) for k in LOST)
            assert got["mrr_by_segment"].get(s, 0) == pytest.approx(built), s
    check()
