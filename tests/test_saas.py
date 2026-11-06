"""Domain 6 (SaaS): every pack KPI on the three generated shapes equals the generator's truth
through the production path (reader, prepare_frame, cleaning), the shapes are detected as SaaS
and only SaaS, and a subscription list never reports what it can't hold (expansion, retention).
Truth: evals/datagen.py (make_saas_*), computed from the frame alone."""
import pandas as pd
import pytest

import domain_pack as dp
from evals import datagen
from tests.test_oracle import _built

ALIAS = {"churned_mrr": "churn_mrr", "new_customers": "new_n", "churned_customers": "churn_n",
         "reactivated_customers": "reactivation_n"}
SHAPES = {
    "saas_subscriptions": ["mrr", "arr", "paying_customers", "arpa", "new_mrr", "churned_mrr",
                           "reactivation_mrr", "new_customers", "churned_customers",
                           "reactivated_customers", "opening_customers", "opening_mrr",
                           "logo_churn_rate"],
    "saas_mrr_snapshots": ["mrr", "arr", "paying_customers", "new_mrr", "expansion_mrr",
                           "contraction_mrr", "churned_mrr", "reactivation_mrr", "new_customers",
                           "churned_customers", "reactivated_customers", "opening_customers",
                           "opening_mrr", "logo_churn_rate", "nrr", "grr"],
    "saas_invoices": ["billings", "recurring_share"],
}


def _setup(name):
    _, df, contract, truth = _built(name)
    pack = dp.pack_for(contract.domain)
    return df, contract, pack, {r: c for r, c in contract.roles.items() if c in df.columns}, truth


@pytest.mark.parametrize("name", list(SHAPES))
def test_every_kpi_equals_the_generators_truth(name):
    df, contract, pack, roles, truth = _setup(name)
    assert (contract.domain, contract.domain_confidence) == ("saas", "strong")
    for k in SHAPES[name]:
        assert dp.compute_kpi(df, roles, pack, k) == pytest.approx(truth[ALIAS.get(k, k)], rel=1e-9), k
    if pack.spell and dp.has_spell(pack, roles):
        assert f"{dp.spell_as_of(pack, df, roles):%Y-%m-%d}" == truth["as_of"]


def test_the_bridge_adds_up_on_the_mrr_export():
    df, _, pack, roles, t = _setup("saas_mrr_snapshots")
    k = {n: dp.compute_kpi(df, roles, pack, n) for n in
         ("mrr", "opening_mrr", "new_mrr", "expansion_mrr", "reactivation_mrr", "contraction_mrr",
          "churned_mrr")}
    assert k["mrr"] == pytest.approx(k["opening_mrr"] + k["new_mrr"] + k["expansion_mrr"]
                                     + k["reactivation_mrr"] - k["contraction_mrr"] - k["churned_mrr"])
    # traps that must not be the answer
    assert k["mrr"] != pytest.approx(t["mrr_summed_all_months"])
    assert dp.compute_kpi(df, roles, pack, "logo_churn_rate") != pytest.approx(t["annual_churn_monthly_x12"])


def test_a_subscription_list_never_reports_expansion_or_revenue_retention():
    """Red-team R4: today's price at both ends would be a confident $0 expansion."""
    df, _, pack, roles, _ = _setup("saas_subscriptions")
    for k in ("expansion_mrr", "contraction_mrr", "nrr", "grr", "quick_ratio"):
        assert not dp.computable(pack, k, roles) and dp.compute_kpi(df, roles, pack, k) is None, k


def test_mrr_is_added_from_the_price_with_a_source_note():
    """S1: the subscription list has no MRR column; prepare_frame adds one (the annual price ÷ 12,
    seats, discount) and the contract reads it as the MRR role, with a note saying so."""
    df, contract, pack, roles, t = _setup("saas_subscriptions")
    assert roles["mrr"] == "mrr" and "mrr" not in datagen.make("saas_subscriptions", 1)[0]
    assert any("formula: mrr" in a for a in contract.source_evidence.assumptions)
    annual = df[df["interval"].eq("year")].iloc[0]
    assert annual["mrr"] == pytest.approx(annual["unit_amount"] * annual["quantity"]
                                          * (1 - (annual["discount_pct"] or 0)) / 12)


@pytest.mark.parametrize("name", list(SHAPES))
def test_no_saas_shape_raises_the_high_null_gate(name):
    """S8: the end, cancel and trial dates are blank while a subscription runs; only marker
    fields may be over 40% blank."""
    df, _ = datagen.make(name, 1)
    null = df.replace("", None).isna().mean()
    over = {c for c, v in null.items() if v > 0.4}
    assert over <= {"trial_end", "ended_at", "canceled_at", "cancel_at", "discount_pct", "tax"}, over


def test_the_spell_columns_say_saas_words():
    from agents.analysis_agent import model_frame
    df, contract, *_ = _setup("saas_subscriptions")
    _, extra = model_frame(df, contract)
    assert {"start_event_date", "cancellation_event_date", "in_force_now", "end_date_unknown"} <= set(extra)
    assert not any(w in c for c in extra for w in ("hire", "exit", "left"))


def test_saas_has_no_protected_roles_so_no_small_cell_runs():
    """Lesson 26: the KPIs' population is customers (companies), not people."""
    df, _, pack, roles, _ = _setup("saas_mrr_snapshots")
    assert pack.protected_roles == []
    by = dp.compute_kpi(df, roles, pack, "mrr", by=roles["segment"])
    assert not by.attrs.get("hidden")


@pytest.mark.parametrize("name", ["retail_lines", "b2b_invoices", "loan_tape", "bank_balances",
                                  "inpatient", "policies", "hr_roster", "hr_snapshots", "employees"])
def test_other_domains_never_read_as_saas(name):
    _, _, contract, _ = _built(name)
    assert contract.domain != "saas", name


def test_a_segment_filter_moves_tiles_and_the_bridge_adds_up_over_segments():
    """F: a filter on segment changes the MRR tile and NRR; the bridge's moves add up over the
    segments (each move counted where it happened), the cohort by its segment 12 months earlier."""
    df, _, pack, roles, t = _setup("saas_mrr_snapshots")
    seg = roles["segment"]
    for k in ("new_mrr", "expansion_mrr", "contraction_mrr", "churned_mrr", "reactivation_mrr"):
        assert dp.compute_kpi(df, roles, pack, k, by=seg).sum() == pytest.approx(
            dp.compute_kpi(df, roles, pack, k)), k
    ent = df[df[seg] == "Enterprise"]
    assert dp.compute_kpi(ent, roles, pack, "mrr", full=df) < dp.compute_kpi(df, roles, pack, "mrr")
    for s, v in t["nrr_by_segment_at_cohort"].items():
        assert dp.compute_kpi(df[df[seg] == s], roles, pack, "nrr", full=df) == pytest.approx(v), s
    assert pd.Series(t["nrr_by_segment_at_cohort"]).nunique() == 3


def test_cohort_rates_by_group_equal_truth():
    """$0 rehearsal: NRR and logo churn by segment crashed (a by-group cohort is a Series, the
    empty-window check took it for a number)."""
    df, _, pack, roles, t = _setup("saas_mrr_snapshots")
    by = dp.compute_kpi(df, roles, pack, "nrr", by=roles["segment"])
    assert by.to_dict() == pytest.approx(t["nrr_by_segment_at_cohort"])
    df, _, pack, roles, t = _setup("saas_subscriptions")
    by = dp.compute_kpi(df, roles, pack, "logo_churn_rate", by=roles["segment"])
    assert by.to_dict() == pytest.approx(t["logo_churn_by_segment"])


def test_a_breakdown_of_part_of_a_level_is_never_replaced_by_the_level():
    """Smoke run saas-8 (lesson 13): the model's churned MRR by month (a part of MRR, filed under
    the mrr column) was replaced four times by MRR by month, a level_recomputed repair meant for a
    level summed over every snapshot. A summed level is at least the level in every group; a
    churned, new or changed part is below it, and is the model's own meaning."""
    from agents.analysis_agent import repair_breakdowns
    from orchestrator.state import AnalysisFindings, SegmentResult
    df, contract, pack, roles, t = _setup("saas_mrr_snapshots")
    churn = SegmentResult(segment_column="month", metric_column="mrr", aggregation="sum", results=[
        {"name": f"{k}-01", "value": v} for k, v in t["churned_mrr_by_month_window"].items()])
    f = AnalysisFindings(eda_summary={}, correlations={}, trends=[], segments=[churn], anomalies=[],
                         statistical_tests=[], code_executed="")
    repair_breakdowns(f, df, contract)
    assert {r["name"]: r["value"] for r in f.segments[0].results} == {
        f"{k}-01": v for k, v in t["churned_mrr_by_month_window"].items()}
    naive = df.groupby("segment")["mrr"].sum()   # the bug it is for: MRR summed over 24 months
    summed = SegmentResult(segment_column="segment", metric_column="mrr", aggregation="sum",
                           results=[{"name": k, "value": float(v)} for k, v in naive.items()])
    f = AnalysisFindings(eda_summary={}, correlations={}, trends=[], segments=[summed], anomalies=[],
                         statistical_tests=[], code_executed="")
    assert repair_breakdowns(f, df, contract)
    assert {r["name"]: r["value"] for r in f.segments[0].results} == pytest.approx(t["mrr_by_segment_now"])


def test_in_force_now_is_the_mrr_tiles_own_rows():
    """Smoke saas-6: the model summed mrr over every row. The brief points it at in_force_now,
    which must be exactly the tile's rows (test accounts and other currencies out too)."""
    from agents.analysis_agent import model_frame
    df, contract, pack, roles, t = _setup("saas_subscriptions")
    f, _ = model_frame(df, contract)
    by = f[f["in_force_now"] == "yes"].groupby("segment")["mrr"].sum()
    assert by.to_dict() == pytest.approx(t["mrr_by_segment"])


def test_a_column_and_its_formula_inputs_are_never_a_correlation_finding():
    """Live run: 'seats and MRR move closely together' (MRR is computed from seats)."""
    from agents.analysis_agent import drop_formula_correlations
    from orchestrator.state import AnalysisFindings
    _, contract, *_ = _setup("saas_subscriptions")
    f = AnalysisFindings(eda_summary={}, correlations={"quantity__mrr": 0.97, "mrr__unit_amount": 0.9,
                                                       "quantity__interval_count": 0.1},
                         trends=[], segments=[], anomalies=[], statistical_tests=[], code_executed="")
    assert drop_formula_correlations(f, contract) and f.correlations == {"quantity__interval_count": 0.1}
    _, own, *_ = _setup("saas_mrr_snapshots")   # a file's own MRR column: its correlations stand
    g = AnalysisFindings(eda_summary={}, correlations={"mrr__quantity": 0.5}, trends=[], segments=[],
                         anomalies=[], statistical_tests=[], code_executed="")
    assert not drop_formula_correlations(g, own) and g.correlations
