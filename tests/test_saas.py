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
    assert {"start_event_date", "end_event_date", "in_force_now", "end_date_unknown"} <= set(extra)
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


def _task(question: str):
    from orchestrator.state import AnalysisTask
    return AnalysisTask(raw_query=question, target_metrics=["mrr"], analysis_types=["segmentation"])


def _findings(*segments):
    from orchestrator.state import AnalysisFindings
    return AnalysisFindings(eda_summary={}, correlations={}, trends=[], segments=list(segments),
                            anomalies=[], statistical_tests=[], code_executed="")


def test_a_breakdown_of_part_of_a_level_is_never_replaced_by_the_level():
    """Smoke run saas-8 (lesson 13): the model's churned MRR by month (a part of MRR, filed under
    the mrr column) was replaced four times by MRR by month, a level_recomputed repair meant for a
    level summed over every snapshot. The question names a move (churned MRR): the breakdown is
    that move, the model's own meaning (GLOSSARY Move breakdown)."""
    from agents.analysis_agent import repair_breakdowns
    from orchestrator.state import SegmentResult
    df, contract, pack, roles, t = _setup("saas_mrr_snapshots")
    churn = SegmentResult(segment_column="month", metric_column="mrr", aggregation="sum", results=[
        {"name": f"{k}-01", "value": v} for k, v in t["churned_mrr_by_month_window"].items()])
    f = _findings(churn)
    repair_breakdowns(f, df, contract, _task("How much MRR did we lose to churned customers each "
                                             "month over the last 12 months?"))
    assert {r["name"]: r["value"] for r in f.segments[0].results} == {
        f"{k}-01": v for k, v in t["churned_mrr_by_month_window"].items()}
    naive = df.groupby("segment")["mrr"].sum()   # the bug it is for: MRR summed over 24 months
    summed = SegmentResult(segment_column="segment", metric_column="mrr", aggregation="sum",
                           results=[{"name": k, "value": float(v)} for k, v in naive.items()])
    f = _findings(summed)
    assert repair_breakdowns(f, df, contract, _task("What is MRR by segment?"))
    assert {r["name"]: r["value"] for r in f.segments[0].results} == pytest.approx(t["mrr_by_segment_now"])


def test_a_low_level_by_month_with_no_move_asked_is_the_level():
    """5.7b-2b (C4, PR1-04): the repair kept any breakdown below the level as "a part of it", so a
    model's MRR by month that summed half the customers was kept. The question names no move: it
    is the level by month, whatever the values (decided by the question, never by the value)."""
    from agents.analysis_agent import repair_breakdowns
    from agents.utils import aggregate
    from orchestrator.state import SegmentResult
    df, contract, pack, roles, t = _setup("saas_mrr_snapshots")
    level = aggregate(df, ["month"], "mrr", "last", contract)
    low = SegmentResult(segment_column="month", metric_column="mrr", aggregation="sum",
                        results=[{"name": str(k), "value": float(v) / 2} for k, v in level.items()])
    f = _findings(low)
    assert repair_breakdowns(f, df, contract, _task("What was MRR each month?"))
    assert [r["value"] for r in f.segments[0].results] == pytest.approx([float(v) for v in level])


def _summed(df, by: str, metric: str = "mrr"):
    """The bug the repair is for: a level summed over every snapshot, by `by`."""
    from orchestrator.state import SegmentResult
    naive = df.groupby(by)[metric].sum()
    return SegmentResult(segment_column=by, metric_column=metric, aggregation="sum",
                         results=[{"name": str(k), "value": float(v)} for k, v in naive.items()])


def test_the_level_repair_reads_the_named_kpi_and_runs_with_no_task():
    """5.7b-2b gate (C4): a targeted question's KPI is the named one, whatever the words (a
    churned-MRR card asked "MRR each month?" keeps the model's sum). With no task plan nothing
    is named: the summed level is the level (the generator's MRR by segment now)."""
    from agents.analysis_agent import repair_breakdowns
    from orchestrator.state import TargetedParams
    df, contract, pack, roles, t = _setup("saas_mrr_snapshots")
    task = _task("What was MRR each month?").model_copy(
        update={"targeted_params": TargetedParams(kpi="churned_mrr")})
    f = _findings(_summed(df, "segment"))
    before = [dict(r) for r in f.segments[0].results]
    assert repair_breakdowns(f, df, contract, task) == [] and f.segments[0].results == before
    f = _findings(_summed(df, "segment"))
    assert repair_breakdowns(f, df, contract, None)
    assert {r["name"]: r["value"] for r in f.segments[0].results} == pytest.approx(t["mrr_by_segment_now"])


def test_the_level_repair_goes_on_past_a_breakdown_it_leaves_alone():
    """5.7b-2b gate: a breakdown the repair leaves as it is (a measure the model derived, with no
    column of its own) never stops it: the summed MRR after it is still the level."""
    from agents.analysis_agent import repair_breakdowns
    from orchestrator.state import SegmentResult
    df, contract, pack, roles, t = _setup("saas_mrr_snapshots")
    derived = SegmentResult(segment_column="segment", metric_column="net_new_mrr", aggregation="sum",
                            results=[{"name": "SMB", "value": 1.0}])
    f = _findings(derived, _summed(df, "segment"))
    assert repair_breakdowns(f, df, contract, _task("What is MRR by segment?"))
    assert f.segments[0].results == [{"name": "SMB", "value": 1.0}]
    assert {r["name"]: r["value"] for r in f.segments[1].results} == pytest.approx(t["mrr_by_segment_now"])


def test_a_level_read_at_its_first_value_is_repaired_too():
    """5.7b-2b gate: a level the contract reads at each account's first value (a 'first'
    window) is a level: a sum of it over every snapshot is repaired, never kept."""
    from agents.analysis_agent import repair_breakdowns
    df, contract, pack, roles, t = _setup("saas_mrr_snapshots")
    contract = contract.model_copy(update={"columns": [
        c.model_copy(update={"aggregation": "first"}) if c.name == "mrr" else c for c in contract.columns]})
    f = _findings(_summed(df, "segment"))
    assert repair_breakdowns(f, df, contract, _task("What is MRR by segment?"))
    assert f.segments[0].aggregation == "first"


def test_every_summed_level_with_no_account_column_is_removed():
    """5.7b-2b gate: with its account column gone no level can be read: each summed level is
    removed, by its snapshot time and by any group alike, one after the other."""
    from agents.analysis_agent import repair_breakdowns
    df, contract, pack, roles, t = _setup("saas_mrr_snapshots")
    df = df.drop(columns=["customer_id"])
    f = _findings(_summed(df, "month"), _summed(df, "segment"), _summed(df, "plan"))
    notes = repair_breakdowns(f, df, contract, _task("What is MRR by segment?"))
    assert f.segments == [] and len(notes) == 3


def test_a_level_summed_for_a_stated_period_is_removed():
    """5.7b-2b gate: the question's period is the task's: a level summed over it is removed
    (its current total would answer another question)."""
    from agents.analysis_agent import repair_breakdowns
    df, contract, pack, roles, t = _setup("saas_mrr_snapshots")
    task = _task("What was MRR by segment in Q1 2026?").model_copy(update={"timeframe": "Q1 2026"})
    f = _findings(_summed(df, "segment"))
    assert repair_breakdowns(f, df, contract, task) == [
        "mrr by segment for Q1 2026 removed: it summed a balance-like value over every snapshot"]
    assert f.segments == []


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


def test_the_ledger_facts_give_the_model_its_numbers_by_month_and_by_group():
    """Analysis gate (lesson 12): saas-10 / 11 / 8 re-derived a cohort by segment or a month's
    churn and went wrong; the facts now carry them, equal to truth."""
    df, _, pack, roles, t = _setup("saas_mrr_snapshots")
    facts = " ".join(dp.spell_facts(pack, df, roles))
    for k, v in t["monthly_logo_churn_by_month"].items():
        assert f"{k} {v:.2%}" in facts, k
    for k, v in t["churned_mrr_by_month_window"].items():
        assert f"{k} ${v:,.2f}" in facts, k
    df, _, pack, roles, t = _setup("saas_subscriptions")
    facts = " ".join(dp.spell_facts(pack, df, roles))
    for k, v in t["logo_churn_by_segment"].items():
        assert f"{k} {v:.1%}" in facts, k


def test_a_level_by_group_reads_the_stock_tiles_rows():
    """Insight gate (ins-49): MRR by segment from the column path added trials, paused, test
    accounts and EUR subscriptions (Mid $121k, truth $113k). One rule: in_force_counted."""
    from agents.utils import aggregate
    df, contract, pack, roles, t = _setup("saas_subscriptions")
    assert aggregate(df, "segment", "mrr", "sum", contract).to_dict() == pytest.approx(t["mrr_by_segment"])


def test_a_question_naming_two_kpis_gets_both():
    """E2E gate (e2e-79/81): 'MRR and how many paying customers' / 'NRR and GRR' answered one.
    domain_pack.named_kpis lists both; the answer gives the others too."""
    from tests.test_oracle import answer
    _, _, _, _, t = _setup("saas_mrr_snapshots")
    text, _ = answer("saas_mrr_snapshots", "segment_compare",
                     "What is our MRR and how many paying customers do we have?", kpi="paying_customers")
    assert f"${t['mrr']:,.2f}" in text and "539" in text
    text, _ = answer("saas_invoices", "segment_compare", "What were our total billings, by line type?",
                     kpi="billings", group_by="line_type")
    assert "$5,931,219.00" in text


def test_a_rates_top_and_bottom_are_the_same_customers():
    """Audit pass 1 (Standards M1): GRR's top kept every customer with MRR, its bottom only paying
    statuses: a customer paused at the opening and active now made GRR 600%. One paying
    population (saas.yaml's anchor); GRR never passes 100%."""
    df, _, pack, roles, _ = _setup("saas_mrr_snapshots")
    d = df.assign(status="active")
    lo = d["month"] == d["month"].max() - pd.DateOffset(months=12)
    d.loc[lo & (d["customer_id"] == d.loc[lo, "customer_id"].iloc[0]), "status"] = "paused"
    r = {**roles, "status": "status"}
    assert dp.compute_kpi(d, r, pack, "grr") <= 1.0
    for k in ("nrr", "grr", "logo_churn_rate"):   # the first alternative, top and bottom
        kpi = pack.kpi(k)
        terms = [*kpi.num[0], *(pack.kpi(t.kpi).num[0][0] for t in kpi.den[0] if t.kpi)]
        assert len({str(t.where) for t in terms}) == 1, k


def test_a_group_with_only_other_currency_rows_is_no_zero_group():
    """Audit pass 1 (expert M1): 'MRR by country: FR at $0.00' (FR is billed in euros, left out
    as another currency, never \$0)."""
    df, _, pack, roles, _ = _setup("saas_subscriptions")
    by = dp.compute_kpi(df, roles, pack, "mrr", by=roles["country"])
    assert "FR" not in by.index and "DE" not in by.index and (by > 0).all()


def test_a_cohort_rate_over_an_asked_period_is_the_12_month_cohort_ending_there():
    """Audit pass 1 (expert M3): NRR for Jan-Jun 2026 was the 6-month cohort (90.8%) labelled
    '12 months'; truth for the 12-month cohort ending 30 Jun 2026: 89.27%."""
    df, _, pack, roles, _ = _setup("saas_mrr_snapshots")
    w = (pd.Timestamp("2026-01-01"), pd.Timestamp("2026-06-30"))
    v = dp.compute_kpi(df, roles, pack, "nrr", window=w)
    snaps = datagen.make("saas_mrr_snapshots", 1)[0]
    lo, hi = snaps[snaps["month"] == "2025-06-01"], snaps[snaps["month"] == "2026-06-01"]
    truth = hi[hi["customer_id"].isin(lo["customer_id"])]["mrr"].sum() / lo["mrr"].sum()
    assert v == pytest.approx(truth)
    assert dp.window_words(pack, df, roles, "nrr", w) == "12-month cohort to 30 Jun 2026"


def test_the_mrr_bridge_is_answered_where_the_data_holds_it():
    """Audit pass 1 (expert M4): 'MRR bridge' was refused on the MRR export, whose facts hold it;
    the partial month of a spell flow is never the headline (expert M2)."""
    from tests.test_oracle import answer
    _, _, _, _, t = _setup("saas_mrr_snapshots")
    text, _ = answer("saas_mrr_snapshots", "top_n", "MRR bridge", kpi="mrr_bridge")
    assert text.startswith("MRR bridge") and f"${t['mrr']:,.2f}" in text and f"${t['expansion_mrr']:,.2f}" in text
    text, _ = answer("saas_subscriptions", "time_series", "Churned MRR by month", kpi="churned_mrr",
                     frequency="M")
    assert text.split(";")[0].find("latest 2026-09") > 0 and "2026-10 so far" in text


def test_stripes_percent_off_reaches_mrr():
    """Spec M2: a percent_off column (0-100) got no role (a rate kind wants 0-1): MRR ignored the
    discount with no note."""
    raw = datagen.make("saas_subscriptions", 1)[0]
    d = raw.rename(columns={"discount_pct": "percent_off"}).assign(
        percent_off=lambda x: x["percent_off"] * 100)
    contract = __import__("connectors.csv_connector", fromlist=["x"]).CSVConnector(
        __import__("orchestrator.state", fromlist=["x"]).ConnectorConfig(type="csv")).build_data_contract(d)
    from agents.ingestion_agent import add_pack_columns
    out, c, _ = add_pack_columns(d, contract, "csv")
    assert c.roles.get("discount_pct") == "percent_off"
    row = out[out["percent_off"] > 0].iloc[0]
    months = {"month": 1, "year": 12}[row["interval"]] * row["interval_count"]
    assert row["mrr"] == pytest.approx(row["unit_amount"] * row["quantity"] * (1 - row["percent_off"] / 100) / months)


def test_a_finished_datasets_pct_header_keeps_its_scale():
    """5.7b-2c (C7 red-team 6): `pct` expands to `percent` (saas.yaml abbreviations), so a
    declared `discount_percent` would read the finished dataset's 0.1 as 0.1%. Only Stripe's own
    headers declare percent; discount_pct stays a guessed fraction."""
    raw = datagen.make("saas_subscriptions", 1)[0]
    contract = __import__("connectors.csv_connector", fromlist=["x"]).CSVConnector(
        __import__("orchestrator.state", fromlist=["x"]).ConnectorConfig(type="csv")).build_data_contract(raw)
    from agents.ingestion_agent import add_pack_columns
    from domain_pack.columns import scale_of
    out, c, _ = add_pack_columns(raw, contract, "csv")
    assert c.roles["discount_pct"] == "discount_pct"
    assert scale_of(dp.pack_for("saas"), "discount_pct", c.roles) is None
    row = out[out["discount_pct"] > 0].iloc[0]
    months = {"month": 1, "year": 12}[row["interval"]] * row["interval_count"]
    assert row["mrr"] == pytest.approx(row["unit_amount"] * row["quantity"] * (1 - row["discount_pct"]) / months)


def test_a_benchmark_verdict_after_the_metric_is_never_said():
    """Audit pass 2 (a stored ins-04 report): 'net revenue retention of 106.5% — healthy' passed
    the never_say pattern, which wanted 'is/looks' before the verdict."""
    import re
    pack = dp.pack_for("saas")
    bench = next(n for n in pack.never_say if "benchmark" in n.reason)
    for t in ("with net revenue retention of 106.5% — healthy, but below last year",
              "Logo churn of 18.8% is healthy for a B2B SaaS business", "NRR is best-in-class"):
        assert re.search(bench.pattern, t, re.I), t
    for t in ("Net revenue retention was 91.1% over the 12 months to 31 Oct 2026.",
              "Logo churn rose to 18.8% from 13.7%."):
        assert not re.search(bench.pattern, t, re.I), t


def test_on_short_data_the_bridge_still_adds_up_and_says_its_span():
    """Fix-diff review of audit 1: on 7 months of data the cohort opened before the data (\$0) and
    the bridge was off by the opening MRR."""
    df, _, pack, roles, _ = _setup("saas_mrr_snapshots")
    short = df[pd.to_datetime(df["month"]) >= "2026-04-01"]
    k = {n: dp.compute_kpi(short, roles, pack, n) for n in
         ("mrr", "opening_mrr", "new_mrr", "expansion_mrr", "reactivation_mrr", "contraction_mrr", "churned_mrr")}
    assert k["opening_mrr"] > 0
    assert k["mrr"] == pytest.approx(k["opening_mrr"] + k["new_mrr"] + k["expansion_mrr"]
                                     + k["reactivation_mrr"] - k["contraction_mrr"] - k["churned_mrr"])
    assert not dp.window_words(pack, short, roles, "nrr").startswith("12-month")


def test_the_packs_prose_names_the_window_the_numbers_read():
    """PR7-03 (C2): read_as_domain and the alternatives said 'over the 12 months to that date' on
    7 months of data, beside a bridge of 6. The prose's span is the Window's: the moves' and the
    cohort's months as window_words says them, 12 on a full year, and no number when no window is
    known (a first question, before cleaning)."""
    import re
    df, _, pack, roles, _ = _setup("saas_mrr_snapshots")
    assert dp.window_span(pack, df, roles) == {"window": "12 months", "moves": "12 months",
                                              "cohort": "12 months", "window_adj": " 12-month"}
    short = df[pd.to_datetime(df["month"]) >= "2026-04-01"]
    spans = dp.window_span(pack, short, roles)
    moves = int(dp.window_words(pack, short, roles, "new_mrr").split()[0])
    cohort = int(dp.window_words(pack, short, roles, "nrr").split("-")[0])
    assert spans["moves"] == f"{moves} months" and spans["cohort"] == f"{cohort} months"
    # one {moves} names the movements and the retention in one sentence: on the default window
    # both start at the same date (Window.moves_from = cohort_start)
    assert moves == cohort
    said = pack.say("unsupported_alternatives", spans)
    assert f"retention over the {moves} months to the latest date" in said
    assert "over the months to the latest date" in pack.say("unsupported_alternatives")
    audit = __import__("orchestrator.state", fromlist=["x"]).CleaningAudit.model_construct(
        decisions=[], date_ranges={}, partial_months={}, main_currency=None, window_span=spans)
    text = " ".join(dp.pack_caveats(pack, audit, roles, "strong"))
    assert re.search(rf"over the {moves} months to that date, .* paying {cohort} months earlier", text)
    assert not re.search(r"\b12[- ]months?\b", text)
    assert f"MRR over the {moves} months to the latest date" in pack.define("mrr_bridge", spans)
    brief = " ".join(dp.pack_brief(pack, roles, ["mrr"], spans=spans))
    assert f"customers paying {cohort} months earlier" in brief and "12 months earlier" not in brief
    hr = dp.pack_for("hr")   # the adjective: 'the 7-month window the facts name'
    assert "each exit in the 7-month window" in hr.say("rows_brief", {"window_adj": " 7-month"})
    assert "each exit in the window the facts name" in hr.say("rows_brief")


def test_a_scoped_bridge_question_is_never_answered_with_the_whole_file():
    from tests.test_oracle import answer
    text, _ = answer("saas_mrr_snapshots", "top_n", "MRR bridge by plan", kpi="mrr_bridge", group_by="plan")
    assert not text.startswith("MRR bridge, 12 months")


def test_one_odd_discount_never_rescales_the_column(monkeypatch):
    """Fix-diff review: one 1.5 among 0.2 rates divided every rate by 100."""
    from tests.test_pack_columns import TOY
    import yaml
    pack = dp.Pack.model_validate(yaml.safe_load(TOY))
    df = pd.DataFrame({"unit_id": list("abc"), "rent": [100.0] * 3, "rebate": [0.2, 0.2, 1.5],
                       "term": ["monthly"] * 3})
    out, notes = dp.pack_columns(df, pack, {r: r for r in df.columns})
    assert out["monthly_rent"].tolist()[:2] == [80.0, 80.0] and pd.isna(out["monthly_rent"].iloc[2])
    assert any("disagree" in n for n in notes)


def test_pass_3_windows_spans_and_the_list_bridge():
    """Audit pass 3: a 20-month window's moves were cut to 12 (new MRR \$176k, truth \$263,762);
    a month-labelled cohort's span counted from the label (1 Apr), not its month-end; the list's
    bridge was \$198 off (add-ons changing a continuing customer's MRR had no line)."""
    df, _, pack, roles, t = _setup("saas_mrr_snapshots")
    v = dp.compute_kpi(df, roles, pack, "new_mrr", window=(pd.Timestamp("2025-03-01"), None))
    assert v == pytest.approx(263762.11, abs=0.01)
    short = df[pd.to_datetime(df["month"]) >= "2026-04-01"]
    assert dp.window_words(pack, short, roles, "nrr") == "6-month cohort to 31 Oct 2026"
    assert dp.window_words(pack, short, roles, "opening_mrr") == "on 30 Apr 2026"
    assert not any("a year earlier (12-month cohort" in ln for ln in dp.spell_story(pack, df, roles))
    df, _, pack, roles, t = _setup("saas_subscriptions")
    line = next(ln for ln in dp.spell_story(pack, df, roles) if ln.startswith("MRR bridge"))
    nums = [float(x.replace(",", "")) for x in __import__("re").findall(r"\$([\d,]+\.\d\d)", line)]
    tail = nums[-1]
    assert "net expansion less contraction (not split in this file) $198.00" in line
    assert tail == pytest.approx(t["mrr"])


def test_a_neutral_strong_or_weak_is_no_verdict():
    import re
    bench = next(n for n in dp.pack_for("saas").never_say if "benchmark" in n.reason)
    for t in ("Logo churn rose to 20.1% while new MRR showed strong growth.",
              "Net revenue retention fell to 91.1%, driven by weak expansion in SMB.",
              "Logo churn of 18.8% is strong evidence of a problem."):
        assert not re.search(bench.pattern, t, re.I), t
    for t in ("gross revenue retention remains strong at 99.3%.",
              "with net revenue retention of 106.5% — healthy, but below last year"):
        assert re.search(bench.pattern, t, re.I), t


# audit pass 5's own sentences (a reviewer wrote them; "NRR had a strong quarter" is ambiguous
# and left out) plus the real report sentences of passes 2-4
_V = ["Strong NRR of 106.5% shows customers expand.", "Weak retention in SMB drags MRR.",
 "Poor retention among monthly plans.", "Excellent net revenue retention of 112%.",
 "NRR remained strong year over year.", "Retention was strong month over month.",
 "Gross revenue retention is above the benchmark.", "NRR of 106.5% is well above the SMB median.",

 "Retention is solid at 95%.", "Churn is low for a SaaS business.", "NRR is robust.",
 "Retention is a strong point for the business.", "Churn is a weak spot.",
 "Retention is strong, at 99.3%.", "GRR is weak; expansion is fine.",
 "Churn of 3% is poor by SaaS standards.", "NRR above 100% signals a healthy business.",
 "A best-in-class 120% NRR.", "Logo churn is excellent at 2%.", "Net revenue retention is world-class.",
 "NRR is top-quartile.", "Retention compares well with SaaS peers."]
_N = ["Churn was concentrated in poor-fit accounts.", "Retention data is poor for 2024 (few rows).",
 "Churn rose despite strong performance in Enterprise.", "NRR fell to 91.1% after a strong start to 2025.",
 "Churn among healthy accounts was 2%.", "Logo churn rose amid strong headwinds in SMB.",
 "Retention shows a strong seasonal pattern.", "Churn shows a strong upward trend.",
 "NRR saw a strong rebound in Q3.", "Churn hit weak cohorts hardest.", "Churn was 4% in weak markets.",
 "Retention fell; strong customers stayed.", "NRR reached 106.5%, driven by strong upsell.",
 "Logo churn of 18.8% coincided with strong new MRR.", "Churn spiked after a poor product release.",
 "GRR was 99.3% with strong expansion.", "Churn rose 3 points; a strong correlation with price.",
 "Retention by plan: Pro 95%, Basic 80%.", "Churn was highest among poor payers.",
 "NRR was 106.5%; healthy is not a grade this report gives.", "Churn fell after strong onboarding.",
 "Retention improved with strong adoption of the new tier."]
VERDICTS = [*_V, "Logo churn is below the industry benchmark at 1.8% a month.",
            "GRR sits below median for mid-market SaaS.", "Retention is weaker than the SaaS median.", "NRR is healthy.", "Churn is weak at 4%.", "Retention, at 95.0% GRR, is solid.",
            "Overall, churn was poor.", "Retention looks robust, at 112%.", "NRR (as of October 2026) is strong.", "NRR, as of October, at 106.5%, is strong.",
            "Retention, hence, is robust.", "Net dollar retention, so far, seems strong.", "NRR, as of October 2026, is strong.", "Retention, as expected, remains healthy.",
            "Logo churn, yet again, was poor.", "NRR, so far this year, is healthy.",
            "Churn, because of annual contracts, is weak.", "NRR, thus far, remains robust.", "Net revenue retention, at 106.5% over the 12 months to 31 Oct 2026, is strong.",
            "NRR, which reached 104%, remains healthy.", "GRR of 91.2%, up from 89%, is solid.",
            "Retention in Enterprise, SMB and Mid-Market is strong.", "Churn, at 2.1% a month, is relatively strong.", "with net revenue retention of 106.5% — healthy, but below", "GRR remains strong at 99.3%.",
            "Logo churn of 18.8% is healthy for a B2B SaaS business", "NRR is healthy and improving."]
NEUTRAL = [*_N, "Retention in EMEA (where demand was solid) reached 96.4%.",
           "Churn rose in March after an outage, when support response times were weak.",
           "Churn rose when support was weak.",
           "Churn was 1.9%, while expansion from top-quartile accounts drove NRR to 107%.", "NRR rose 3.5 points as upsell activity was strong in Q3.",
           "Churn fell because onboarding was strong.", "Retention improved since adoption was healthy.",
           "Churn rose in SMB so the pipeline is weak.", "NRR fell with higher churn, so the team is strong.",
           "Churn rose with lower retention, as onboarding was weak.",
           "Retention fell with more churn, since adoption was poor.",
           "MRR rose despite churn, as expansion was strong.", "Churn dropped in Q3, so the team is strong.",
           "Churn was flat, so the pipeline remains healthy.", "Retention held, as adoption was strong.", "Logo churn rose to 20.1% while new MRR showed strong growth.",
           "Net revenue retention fell to 91.1%, driven by weak expansion in SMB.",
           "Logo churn of 18.8% is strong evidence of a problem.",
           "Net revenue retention was 91.1% over the 12 months to 31 Oct 2026."]


@pytest.mark.parametrize("t", VERDICTS)
def test_every_benchmark_verdict_is_never_said(t):
    """Audit pass 4: the pass-3 narrowing let 11 of 21 verdicts through."""
    import re
    bench = next(n for n in dp.pack_for("saas").never_say if "benchmark" in n.reason)
    assert re.search(bench.pattern, t, re.I)


@pytest.mark.parametrize("t", NEUTRAL)
def test_a_neutral_sentence_is_no_verdict(t):
    import re
    bench = next(n for n in dp.pack_for("saas").never_say if "benchmark" in n.reason)
    assert not re.search(bench.pattern, t, re.I)


@pytest.mark.parametrize("header, vals, want", [("rebate_pct", [10, 12.5, 15, 7.5], [90, 87.5, 85, 92.5]),
                                                ("rebate", [1, 1, 1, 20], [99, 99, 99, 80]),
                                                ("rebate", [0.2, 0.2, 0.1], [80, 80, 90])])
def test_discount_scales(header, vals, want):
    """Audit pass 4: fractional Stripe percents (12.5) and 1% read right; never rescaled by one.
    5.7b-2c (PR1-07): 12.5 is read as a percent under a header that declares it (Stripe's
    percent_off); under an undeclared one it could be an amount, so it is said unreadable."""
    from tests.test_pack_columns import TOY
    import yaml
    pack = dp.Pack.model_validate(yaml.safe_load(TOY))
    df = pd.DataFrame({"unit_id": [str(i) for i in range(len(vals))], "rent": [100.0] * len(vals),
                       header: vals, "term": ["monthly"] * len(vals)})
    out, _ = dp.pack_columns(df, pack, {"unit_id": "unit_id", "rent": "rent", "rebate": header,
                                        "term": "term"})
    assert out["monthly_rent"].tolist() == pytest.approx(want)


def test_a_leap_february_cohort_point_is_kept():
    """Audit pass 4: with data from Feb 2024, Feb 2025's 12-month cohort starts 28 Feb 2024,
    'before' the first known state (29 Feb 2024) by a day: dropped as NaN. Months are compared."""
    df, _, pack, roles, _ = _setup("saas_mrr_snapshots")
    shifted = df.assign(month=pd.to_datetime(df["month"]) - pd.DateOffset(months=9))
    frame = shifted.assign(_p=pd.Series(pd.NaT, index=shifted.index).astype("period[M]"))
    by = dp.compute_kpi(frame, roles, pack, "nrr", by="_p", full=shifted)
    assert pd.notna(by.get(pd.Period("2025-02", "M")))


def test_pass_5_minors():
    """All-1 discounts are no 100% off; no bridge or churn-count line without its span or rate;
    the bridge's span is the moves' own words."""
    from tests.test_pack_columns import TOY
    import yaml
    pack = dp.Pack.model_validate(yaml.safe_load(TOY))
    df = pd.DataFrame({"unit_id": ["a", "b"], "rent": [100.0, 100.0], "rebate": [1, 1], "term": ["monthly"] * 2})
    out, notes = dp.pack_columns(df, pack, {r: r for r in df.columns})
    assert out["monthly_rent"].isna().all() and any("unclear" in n for n in notes)
    df, _, pack, roles, t = _setup("saas_mrr_snapshots")
    one = df[pd.to_datetime(df["month"]) == pd.to_datetime(df["month"]).max()]
    story = dp.spell_story(pack, one, roles)
    assert not any(ln.startswith("MRR bridge") or "left at a month-end" in ln for ln in story)
    line = next(ln for ln in dp.spell_story(pack, df, roles) if ln.startswith("MRR bridge"))
    assert line.startswith(f"MRR bridge, {dp.window_words(pack, df, roles, 'new_mrr')}:")


@pytest.mark.parametrize("t, verdict", [
    ("Churn was poor-fit driven.", False), ("Retention figures are poor for 2024 (few rows).", False),
    ("Churn coverage is weak before 2025.", False), ("The churn analysis is solid.", False),
    ("Retention is still quite solid.", True), ("Churn is low compared with industry benchmarks.", True),
    ("NRR is impressive at 115%.", True)])
def test_pass_6_sentences(t, verdict):
    import re
    bench = next(n for n in dp.pack_for("saas").never_say if "benchmark" in n.reason)
    assert bool(re.search(bench.pattern, t, re.I)) == verdict


def test_short_snapshots_bridge_months_are_its_moves():
    """Pass 6: on Aug-Oct snapshots the bridge said '3 months' (2 months of moves)."""
    df, _, pack, roles, _ = _setup("saas_mrr_snapshots")
    short = df[pd.to_datetime(df["month"]) >= "2026-08-01"]
    assert dp.window_words(pack, short, roles, "new_mrr") == "2 months to 31 Oct 2026"
    assert next(ln for ln in dp.spell_story(pack, short, roles) if ln.startswith("MRR bridge")).startswith(
        "MRR bridge, 2 months to 31 Oct 2026: $538,652.55 on 31 Aug 2026")


@pytest.mark.parametrize("t", ["NRR was 104%; expansion was strong in Enterprise.",
                               "Retention fell to 88% while pipeline is healthy.",
                               "Churn in SMB, where new bookings are strong, rose to 4%."])
def test_a_verdict_in_another_clause_is_not_the_metrics(t):
    import re
    bench = next(n for n in dp.pack_for("saas").never_say if "benchmark" in n.reason)
    assert not re.search(bench.pattern, t, re.I)


def test_one_month_of_moves_is_unknown_never_zero():
    df, _, pack, roles, _ = _setup("saas_mrr_snapshots")
    one = df[pd.to_datetime(df["month"]) == pd.to_datetime(df["month"]).max()]
    assert dp.compute_kpi(one, roles, pack, "new_mrr") is None


def test_a_partial_first_month_is_no_year_over_year_base():
    """Live run (JSON invoices): '+109% year over year' compared Sep 2026 with the 5 days of Sep
    2025 the data covers. The built-in leaves a partial first month out of every comparison."""
    from tests.test_oracle import builtin
    r = builtin("saas_invoices", ["amount"], [], ["trend"])
    assert {t["period"] for t in r["trends"] if not t.get("segment")} == {"MoM"}


def test_a_group_of_former_rows_scores_its_own_reading():
    """Audit pass 8: mean MRR by status (canceled = only former rows) was false at x1.0."""
    from evals.scoring import false_findings
    from orchestrator.state import AnalysisFindings, SegmentResult
    df, *_ = _setup("saas_subscriptions")
    m = df.groupby("status")["mrr"].mean().dropna()
    seg = SegmentResult(segment_column="status", metric_column="mrr", aggregation="mean",
                        results=[{"name": k, "value": float(v)} for k, v in m.items() if k == "canceled"])
    out = false_findings(AnalysisFindings(eda_summary={}, correlations={}, trends=[], segments=[seg],
                                          anomalies=[], statistical_tests=[], code_executed=""), df, {},
                         ("saas_subscriptions", datagen.make("saas_subscriptions", 1)[0]))
    assert out["false"] == 0


def test_a_mixed_groups_former_rows_alone_are_false():
    """Audit pass 9: mean MRR by plan over only the former rows of mixed groups passed."""
    from evals.scoring import false_findings
    from orchestrator.state import AnalysisFindings, SegmentResult
    from agents.analysis_agent import model_frame
    df, contract, *_ = _setup("saas_subscriptions")
    f, _ = model_frame(df, contract)
    gone = df[f["in_force_now"] != "yes"].groupby("plan")["mrr"].mean()
    seg = SegmentResult(segment_column="plan", metric_column="mrr", aggregation="mean",
                        results=[{"name": k, "value": float(v)} for k, v in gone.items()])
    out = false_findings(AnalysisFindings(eda_summary={}, correlations={}, trends=[], segments=[seg],
                                          anomalies=[], statistical_tests=[], code_executed=""), df, {},
                         ("saas_subscriptions", datagen.make("saas_subscriptions", 1)[0]))
    now = df[f["in_force_now"] == "yes"].groupby("plan")["mrr"].mean()
    mixed = [k for k in gone.index if k in now.index and abs(gone[k] - now[k]) > 0.01 * abs(now[k])]
    assert mixed and out["false"] == len(mixed)


def test_month_labels_are_judged_once_for_the_as_of_and_the_first_known_month():
    """Design review: spell_as_of read labels only when every snapshot is on the 1st, first_known
    whenever the first one is. One mid-month snapshot: neither reads the 1st as a month's end."""
    df, _, pack, roles, _ = _setup("saas_mrr_snapshots")
    mixed = df.copy()
    last = pd.to_datetime(mixed["month"]) == pd.to_datetime(mixed["month"]).max()
    mixed.loc[last, "month"] = "2026-10-15"
    first = dp.spell_from(pack, mixed, roles)
    assert dp.spell_as_of(pack, mixed, roles) == pd.Timestamp("2026-10-15")
    assert dp.first_known(pack, mixed, roles) == first
    assert dp.first_known(pack, df, roles) == first + pd.offsets.MonthEnd(0)


def test_a_next_billing_date_never_makes_a_subscription_list_snapshots(monkeypatch):
    """Review: a list with a `billing_date` column (each subscription's next bill) was read as
    monthly snapshots: MRR $476k for $869k, logo churn 100%. The snapshot role needs the entity
    to repeat over its dates when the file has the spell's start."""
    import numpy as np

    from tests import test_oracle

    def next_bill(raw):
        days = pd.to_timedelta(np.arange(len(raw)) % 28, unit="D")
        return raw.assign(billing_date=(pd.Timestamp("2026-11-01") + days).strftime("%Y-%m-%d"))
    monkeypatch.setitem(test_oracle.TRANSFORMS, "next_bill", next_bill)
    _, df, contract, truth = test_oracle._built("saas_subscriptions", transform="next_bill")
    pack = dp.pack_for(contract.domain)
    roles = {r: c for r, c in contract.roles.items() if c in df.columns}
    assert "month" not in roles
    for k in ("mrr", "logo_churn_rate"):
        assert dp.compute_kpi(df, roles, pack, k) == pytest.approx(truth[k], rel=1e-9), k


def test_an_mrr_export_keeps_its_billing_date_as_the_snapshot_month():
    """reg-subscriptions: account × billing_date MRR rows are snapshots (the entity repeats)."""
    from evals import datagen
    raw, _ = datagen.make("subscriptions", 7)
    assert dp.detect(raw, {"billing_date"})[1].get("month") == "billing_date"


@pytest.mark.parametrize("name", ["saas_subscriptions", "saas_mrr_snapshots", "hr_roster", "hr_snapshots"])
def test_a_duplicate_row_index_gives_the_same_spell_kpis(name):
    """Review: _in_force memoised row labels, so a frame with a repeated index crashed
    ('cannot reindex on an axis with duplicate labels') or counted rows twice."""
    df, _, pack, roles, _ = _setup(name)
    dup = df.copy()
    dup.index = dup.index % (len(dup) // 3)
    for k in (k.name for k in pack.kpis if dp.spell_terms(pack, k.name) and dp.computable(pack, k.name, roles)):
        assert dp.compute_kpi(dup, roles, pack, k) == dp.compute_kpi(df, roles, pack, k), k
    with pytest.raises(ValueError, match="unique row labels"):
        dp.compute_kpi(dup.head(50), roles, pack, "mrr" if "saas" in name else pack.kpis[0].name, full=dup)


@pytest.mark.parametrize("name, cut", [("hr_snapshots", "Snapshot_Date"), ("saas_mrr_snapshots", "month")])
def test_one_snapshot_keeps_its_snapshot_role(name, cut):
    """Review of the first billing_date fix: dropping the role on row shape alone dropped a real
    one-snapshot extract (the as-of fell back to the latest hire date). A trusted header stays."""
    from evals import datagen
    raw, _ = datagen.make(name, 1)
    one = raw[raw[cut] == raw[cut].max()]
    if name == "saas_mrr_snapshots":   # with a start column, as a warehouse model has
        one = one.assign(created="2024-01-15")
    assert dp.detect(one, {cut, "created", "Hire_Date", "Termination_Date"} & set(one.columns))[1].get(snapshot_of(name)) == cut


def snapshot_of(name):
    return dp.snapshot_role(dp.pack_for("hr" if name.startswith("hr") else "saas"))


def test_a_next_billing_date_with_add_ons_is_still_no_snapshot(monkeypatch):
    """Spec review: most customers on a base plan + an add-on billed on different days put each
    customer on 2+ dates; each subscription (customer and start) is still on one."""
    import numpy as np

    from tests import test_oracle

    def add_ons(raw):
        twin = raw.assign(subscription_id=raw["subscription_id"] + "_addon",
                          created=(pd.to_datetime(raw["created"]) + pd.Timedelta(days=3)).dt.strftime("%Y-%m-%d"))
        both = pd.concat([raw, twin], ignore_index=True)
        days = pd.to_timedelta(np.arange(len(both)) % 28, unit="D")
        return both.assign(billing_date=(pd.Timestamp("2026-11-01") + days).strftime("%Y-%m-%d"))
    monkeypatch.setitem(test_oracle.TRANSFORMS, "add_ons", add_ons)
    _, df, contract, _ = test_oracle._built("saas_subscriptions", transform="add_ons")
    assert "month" not in contract.roles


def test_a_status_held_for_today_only_is_said_wherever_the_list_is_read():
    """5.7b-2 (C5, user decision 2026-11-10): a list's paused or unpaid status is today's, with
    no date it began, so those subscriptions are out of every month-end, never churned at a
    guessed date; the file's story and every spell KPI answer say so (counted). A list without
    one says nothing; snapshots carry each month's own status, so it never applies to them."""
    pack = dp.pack_for("saas")
    roles = {"customer": "customer_id", "created": "created", "ended_at": "ended_at",
             "status": "status", "mrr": "mrr"}
    df = pd.DataFrame({"customer_id": ["c0", "c1", "c2"],
                       "created": pd.to_datetime(["2025-01-01"] * 3),
                       "ended_at": pd.to_datetime([pd.NaT] * 3),
                       "status": ["active", "paused", "unpaid"], "mrr": [100.0, 50.0, 70.0]})
    note = dp.today_only(pack, df, roles)
    assert note.startswith("2 rows") and "today" in note
    assert note in dp.spell_story(pack, df, roles)
    assert dp.today_only(pack, df.assign(status="active"), roles) == ""
    snaps = df.assign(month=pd.Timestamp("2026-10-01"))
    assert dp.today_only(pack, snaps, {**roles, "month": "month"}) == ""
    from agents.targeted_analysis_agent import handle_kpi
    from orchestrator.state import TargetedParams
    _, clean, contract, _ = _built("saas_subscriptions")   # the generator pauses ~3%
    held = dp.today_only(pack, clean, {r: c for r, c in contract.roles.items() if c in clean.columns})
    assert held and held in handle_kpi(clean, TargetedParams(kpi="mrr"), contract).answer_text


def test_a_move_by_segment_says_what_moved_in_from_and_out_to_another_segment():
    """5.7b-2b (C3): churned MRR by segment no longer hides a customer who changed segment: the
    answer says the lead segment's MRR moved in from and out to another segment (never new or
    churned), the reference's numbers, in the pack's words ("transfer" is a bank transaction)."""
    from agents.targeted_analysis_agent import handle_kpi
    from evals.reference_kpis import reference
    from orchestrator.state import TargetedParams
    df, contract, pack, roles, _ = _setup("saas_mrr_snapshots")
    ref = reference("saas_mrr_snapshots", datagen.make("saas_mrr_snapshots", 1)[0])
    got = dp.compute_kpi(df, roles, pack, "churned_mrr", by="segment")
    lead = got.idxmax()
    text = handle_kpi(df, TargetedParams(kpi="churned_mrr", group_by="segment"), contract).answer_text
    t_in, t_out = (ref[f"transfer_{k}_mrr_by_segment"].get(lead, 0.0) for k in ("in", "out"))
    assert t_in or t_out, "the generator moves a customer between segments (PR1-03)"
    assert text.startswith("Churned MRR by segment")
    assert (f". {lead}: ${t_in:,.2f} moved in from another segment and ${t_out:,.2f} moved out to "
            "another segment") in text
    assert "transfer" not in text.lower()
    # a count says no money moved (its transfers are no KPI)
    text = handle_kpi(df, TargetedParams(kpi="churned_customers", group_by="segment"), contract).answer_text
    assert "moved in from" not in text


def test_what_moved_between_segments_is_said_for_the_period_and_part_asked():
    """5.7b-2b gate (C3): the moved-in/out sentence reads the asked window (to 30 Apr 2026 the
    reference on the snapshots to then has no Enterprise mover, so none is said) and, on a named
    plan, the whole book's lines: a Starter customer who left the plan moved out of it (the
    reference by plan and segment), never a churn."""
    from agents.targeted_analysis_agent import handle_kpi
    from evals.reference_kpis import MOVES, reference, reference_by
    from orchestrator.state import TargetedParams
    df, contract, pack, roles, _ = _setup("saas_mrr_snapshots")
    raw = datagen.make("saas_mrr_snapshots", 1)[0]
    ref = reference("saas_mrr_snapshots", raw[pd.to_datetime(raw["month"]) <= "2026-04-30"])
    text = handle_kpi(df, TargetedParams(kpi="churned_mrr", group_by="segment", period_end="2026-04-30"),
                      contract).answer_text
    assert text.startswith("Churned MRR by segment (12 months to 30 Apr 2026): Enterprise leads")
    assert not any(ref[f"transfer_{k}_mrr_by_segment"].get("Enterprise") for k in ("in", "out"))
    assert "moved in from" not in text
    _, mv = reference_by("saas_mrr_snapshots", raw, ["plan", "segment"], "mrr", "sum")
    t_in, t_out = (mv[len(MOVES) + i].get("Starter|SMB", 0.0) for i in (0, 1))
    text = handle_kpi(df, TargetedParams(kpi="churned_mrr", group_by="segment", segment_column="plan",
                                         segment_value="Starter"), contract).answer_text
    assert text.startswith("Churned MRR by segment, Starter") and t_in and t_out
    # a move within SMB from Starter to Pro is in it: the part's column is said beside the group's
    assert (f". SMB: ${t_in:,.2f} moved in from another plan or segment and ${t_out:,.2f} moved "
            "out to another plan or segment (customers who changed plan or segment") in text


def test_the_story_says_the_ledger_and_each_rate_against_a_year_earlier():
    """5.7b-2a gate: the story's rate lines against the reference (a year earlier: the reference
    a year back), a cohort without a full year-earlier cohort says so, the churned-customer
    count beside its rate, and the largest month of churned MRR with its one big customer
    (generator truth)."""
    from evals.reference_kpis import reference
    df, _, pack, roles, t = _setup("saas_mrr_snapshots")
    story = dp.spell_story(pack, df, roles)
    assert (f"Net revenue retention (12-month cohort to 31 Oct 2026): {t['nrr']:.1%}; "
            "no full year-earlier window in the data.") in story   # the data opens Nov 2024
    assert any(ln.startswith(f"{t['churn_n']:,.0f} customers left at a month-end in the window "
                             "(churned customers); logo churn counts only the customers in force on "
                             "31 Oct 2025 who are not in force now") for ln in story), story
    big = next(ln for ln in story if ln.startswith("The largest month of churned MRR"))
    top = pd.Period(t["biggest_churn_month"], "M")
    assert big.startswith(f"The largest month of churned MRR: {top.strftime('%b %Y')} with "
                          f"${t['churned_mrr_by_month_window'][str(top)]:,.2f}"), big
    assert f", {t['northwind_share_of_big_month']:.0%} of it one customer_id" in big, big
    df, _, pack, roles, t = _setup("saas_subscriptions")
    now, back = reference("saas_subscriptions", df), reference("saas_subscriptions", df, pd.Timestamp("2025-10-29"))
    assert (f"Logo churn (12-month cohort to 29 Oct 2026): {now['logo_churn_rate']:.1%}; a year earlier "
            f"(12-month cohort to 29 Oct 2025): {back['logo_churn_rate']:.1%}, "
            f"{(now['logo_churn_rate'] - back['logo_churn_rate']) * 100:+.1f} points.") in dp.spell_story(pack, df, roles)


@pytest.mark.parametrize("n", [15, 16])
def test_a_cohort_rate_by_group_is_a_fact_up_to_15_groups(n):
    """The ledger facts give a cohort rate by group (equal to the reference) for at most 15
    groups; 16 is no fact line (too many to quote)."""
    from evals.reference_kpis import reference
    df, _, pack, roles, _ = _setup("saas_subscriptions")
    ids = {c: i for i, c in enumerate(df["customer_id"].unique())}
    df = df.assign(segment=[f"S{ids[c] % n:02d}" for c in df["customer_id"]])
    line = next((ln for ln in dp.spell_facts(pack, df, roles) if ln.startswith("Logo churn by segment")), None)
    if n == 16:
        assert line is None, line
        return
    want = reference("saas_subscriptions", df)["logo_churn_rate_by_segment"]
    assert len(want) == n and line is not None
    said = dict(x.rsplit(" ", 1) for x in line.split("): ", 1)[1].rstrip(".").split(", "))
    assert {k: float(v.rstrip("%")) / 100 for k, v in said.items()} == pytest.approx(want, abs=0.0005)


def test_a_total_and_an_unavailable_kpi_the_question_names_are_both_said():
    """5.7b-2a gate: 'total billings by type' gives the total in the data's own currency (the
    generator's billings, in euros); a second KPI the question names that the rows can't support
    is said with its reason (one month of snapshots: no month of moves), never left out."""
    from agents.targeted_analysis_agent import _also_named
    from orchestrator.state import TargetedParams
    _, df, contract, t = _built("saas_invoices")
    pack = dp.pack_for(contract.domain)
    text = _also_named(df.assign(currency="EUR"), TargetedParams(kpi="billings", group_by="line_type"),
                       contract, pack, "total billings by type")
    d = pd.to_datetime(df[contract.roles["invoice_date"]])   # PR1-02: billings names its period
    span = f"{d.min():%-d %b %Y} to {d.max():%-d %b %Y}"
    assert text == f" Also asked: Billings in total ({span}): €{t['billings']:,.2f}.", text
    df, contract, pack, _, _ = _setup("saas_mrr_snapshots")
    one = df[pd.to_datetime(df["month"]) == pd.to_datetime(df["month"]).max()]
    text = _also_named(one, TargetedParams(kpi="mrr"), contract, pack, "What is our MRR and new MRR?")
    assert text == " Also asked: New MRR: unavailable, the data covers too short a span.", text


def test_billings_names_the_invoice_dates_it_sums():
    """PR1-02 (C2): billings summed every invoice line with no period beside it (the file's time
    axis is the MRR month, not the invoice date), so a tile read as a month's or a year's. It
    names its invoice dates, and an asked period's."""
    _, df, contract, _ = _built("saas_invoices")
    pack, roles = dp.pack_for(contract.domain), {r: c for r, c in contract.roles.items() if c in df.columns}
    d = pd.to_datetime(df[roles["invoice_date"]])
    assert dp.window_words(pack, df, roles, "billings") == f"{d.min():%-d %b %Y} to {d.max():%-d %b %Y}"
    asked = d[d >= "2026-07-01"]
    assert dp.window_words(pack, df, roles, "billings", ("2026-07-01", None)) == \
        f"{asked.min():%-d %b %Y} to {asked.max():%-d %b %Y}"
    void = df.iloc[[0]].assign(**{roles["status"]: "void", roles["invoice_date"]: "2024-01-15"})
    assert dp.window_words(pack, pd.concat([df, void], ignore_index=True), roles, "billings") == \
        f"{d.min():%-d %b %Y} to {d.max():%-d %b %Y}"   # a void bills nothing: not its date either
    raw = pack.model_dump(by_alias=True)
    raw["kpis"] = [{**k, "dated_by": "plan"} if k["name"] == "billings" else k for k in raw["kpis"]]
    with pytest.raises(ValueError, match="dated_by plan is not a date role"):
        dp.Pack.model_validate(raw)
