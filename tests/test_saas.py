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
