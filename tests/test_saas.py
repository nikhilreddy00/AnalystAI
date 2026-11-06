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
VERDICTS = [*_V, "NRR, as of October 2026, is strong.", "Retention, as expected, remains healthy.",
            "Logo churn, yet again, was poor.", "NRR, so far this year, is healthy.",
            "Churn, because of annual contracts, is weak.", "NRR, thus far, remains robust.", "Net revenue retention, at 106.5% over the 12 months to 31 Oct 2026, is strong.",
            "NRR, which reached 104%, remains healthy.", "GRR of 91.2%, up from 89%, is solid.",
            "Retention in Enterprise, SMB and Mid-Market is strong.", "Churn, at 2.1% a month, is relatively strong.", "with net revenue retention of 106.5% — healthy, but below", "GRR remains strong at 99.3%.",
            "Logo churn of 18.8% is healthy for a B2B SaaS business", "NRR is healthy and improving."]
NEUTRAL = [*_N, "Churn dropped in Q3, so the team is strong.",
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


@pytest.mark.parametrize("vals, want", [([10, 12.5, 15, 7.5], [90, 87.5, 85, 92.5]),
                                        ([1, 1, 1, 20], [99, 99, 99, 80]),
                                        ([0.2, 0.2, 0.1], [80, 80, 90])])
def test_discount_scales(vals, want):
    """Audit pass 4: fractional Stripe percents (12.5) and 1% read right; never rescaled by one."""
    from tests.test_pack_columns import TOY
    import yaml
    pack = dp.Pack.model_validate(yaml.safe_load(TOY))
    df = pd.DataFrame({"unit_id": [str(i) for i in range(len(vals))], "rent": [100.0] * len(vals),
                       "rebate": vals, "term": ["monthly"] * len(vals)})
    out, _ = dp.pack_columns(df, pack, {r: r for r in df.columns})
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
                                          anomalies=[], statistical_tests=[], code_executed=""), df, {})
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
                                          anomalies=[], statistical_tests=[], code_executed=""), df, {})
    now = df[f["in_force_now"] == "yes"].groupby("plan")["mrr"].mean()
    mixed = [k for k in gone.index if k in now.index and abs(gone[k] - now[k]) > 0.01 * abs(now[k])]
    assert mixed and out["false"] == len(mixed)
