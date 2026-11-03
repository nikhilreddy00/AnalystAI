"""Banking (Phase 5.7, domain 2): a balance is a level — it adds across accounts at one date,
never across dates. The account-state and row-state tables of the plan, one test per cell
group; each fails without its rule."""
import pandas as pd
import pytest

import domain_pack
from connectors.csv_connector import CSVConnector
from domain_pack import compute_kpi, pack_for
from orchestrator.state import ConnectorConfig, ConnectorType


def _contract(df):
    return CSVConnector(ConnectorConfig(type=ConnectorType.CSV)).build_data_contract(df)


def _tape(rows):
    """Loan-tape rows: (loan, as_of, balance, dpd, status, product)."""
    return pd.DataFrame(rows, columns=["Loan ID", "As Of Date", "Outstanding Principal",
                                       "Days Past Due", "Loan Status", "Product"])


BANK = pack_for("banking")
# Two month-ends. L1 current; L2 90+ DPD; L3 charged off in October (its September balance must
# not stand in for October); L4 paid off in October (a zero row, then gone); L5 non-accrual at
# 45 DPD (NPL by status, not by days); L6 opens in October.
TAPE = _tape([
    ("L1", "2026-09-30", 1000.0, 0, "Current", "auto"),
    ("L2", "2026-09-30", 500.0, 60, "Delinquent", "card"),
    ("L3", "2026-09-30", 300.0, 120, "Delinquent", "card"),
    ("L4", "2026-09-30", 200.0, 0, "Current", "personal"),
    ("L5", "2026-09-30", 400.0, 30, "Non-Accrual", "auto"),
    ("L1", "2026-10-31", 950.0, 0, "Current", "auto"),
    ("L2", "2026-10-31", 480.0, 95, "Delinquent", "card"),
    ("L3", "2026-10-31", 290.0, 150, "Charged Off", "card"),
    ("L4", "2026-10-31", 0.0, 0, "Paid Off", "personal"),
    ("L5", "2026-10-31", 390.0, 45, "Non-Accrual", "auto"),
    ("L6", "2026-10-31", 2000.0, 0, "Current", "mortgage"),
])
OCT = 950 + 480 + 390 + 2000   # L3 charged off, L4 paid off: out


@pytest.fixture(scope="module")
def tape():
    c = _contract(TAPE)
    assert c.domain == "banking" and c.domain_confidence == "strong"
    return TAPE, c


def test_the_outstanding_balance_is_october_never_the_two_months_added(tape):
    df, c = tape
    assert compute_kpi(df, c.roles, BANK, "total_balance") == pytest.approx(OCT)
    assert compute_kpi(df, c.roles, BANK, "open_accounts") == 4


def test_a_loan_charged_off_this_month_is_out_not_its_last_months_balance(tape):
    """Red-team B1: kinds filtered before the latest row put L3's September 300 in October."""
    df, c = tape
    sep = df[df["As Of Date"] == "2026-09-30"]
    assert compute_kpi(sep, c.roles, BANK, "total_balance", full=df) == pytest.approx(2400.0)
    assert compute_kpi(df, c.roles, BANK, "total_balance") != pytest.approx(OCT + 300)


def test_npl_is_90_plus_or_non_accrual_by_balance_never_counted_twice(tape):
    """Red-team B2: L2 is 95 DPD; L5 is non-accrual at 45 DPD; each is counted once."""
    df, c = tape
    assert compute_kpi(df, c.roles, BANK, "npl_ratio") == pytest.approx((480 + 390) / OCT)
    assert compute_kpi(df, c.roles, BANK, "delinquency_30") == pytest.approx((480 + 390) / OCT)


def test_an_unknown_dpd_is_in_the_denominator_and_not_the_numerator():
    df = _tape([("A", "2026-10-31", 100.0, None, "Current", "auto"),
                ("B", "2026-10-31", 300.0, 120, "Delinquent", "auto"),
                ("A", "2026-09-30", 100.0, 0, "Current", "auto"),
                ("B", "2026-09-30", 300.0, 90, "Delinquent", "auto")])
    c = _contract(df)
    assert compute_kpi(df, c.roles, BANK, "npl_ratio") == pytest.approx(300 / 400)


def test_a_balance_by_product_is_each_accounts_latest_balance(tape):
    df, c = tape
    by = compute_kpi(df, c.roles, BANK, "total_balance", by=c.roles["product"])
    assert by.to_dict() == {"auto": 1340.0, "card": 480.0, "mortgage": 2000.0}
    avg = compute_kpi(df, c.roles, BANK, "avg_balance")
    assert avg == pytest.approx(OCT / 4)   # a paid-off zero never drags the average


def test_the_ingestion_level_hint_comes_from_the_pack_and_equals_the_layers():
    """A pack balance role sets the semantic layer's level hint (red-team m4: one rule)."""
    df, c = _tape_contract()
    bal = next(x for x in c.columns if x.name == "Outstanding Principal")
    assert (bal.aggregation, bal.aggregation_by, bal.aggregation_time) == (
        "last", ["Loan ID"], "As Of Date")
    from evals import datagen
    bb = _contract(datagen.make_bank_balances(1)[0])   # the layer matches these columns too
    hint = next(x for x in bb.columns if x.name == "balance")
    assert (hint.aggregation, hint.aggregation_by, hint.aggregation_time) == (
        "last", [bb.roles["account"]], bb.roles["date"])


def _tape_contract():
    return TAPE, _contract(TAPE)


def test_an_average_balance_by_group_is_per_account_at_its_latest_snapshot(tape):
    from agents.utils import aggregate
    df, c = tape
    on_books = df[~df["Loan Status"].isin(["Charged Off", "Paid Off"])]
    got = aggregate(on_books, ["Product"], "Outstanding Principal", "mean", c)
    assert got["auto"] == pytest.approx((950 + 390) / 2)   # never the 4 snapshots' mean


def test_a_balance_on_a_comparison_question_is_never_summed_across_dates(tape):
    """`_asked_agg` turns a comparison's mean into a sum for money roles; for a level the sum is
    each account's latest balance (effective_agg → last), never the snapshots added."""
    from agents.targeted_analysis_agent import _asked_agg
    from agents.utils import effective_agg
    from orchestrator.state import TargetedParams
    df, c = tape
    p = _asked_agg(TargetedParams(metric_column="Outstanding Principal", segment_column="Product",
                                  aggregation="mean"), c, "Balance by product?")
    assert effective_agg(c, "Outstanding Principal", p.aggregation) == "last"
    p = _asked_agg(TargetedParams(metric_column="Outstanding Principal", segment_column="Product",
                                  aggregation="mean"), c, "Average balance by product?")
    assert p.aggregation == "mean"


def test_debit_and_credit_columns_net_with_blanks_on_the_other_side():
    """Red-team m2: [credit, '-', debit] would be NaN on every row."""
    df = pd.DataFrame({"Account ID": ["A1", "A1", "A2", "A2"],
                       "Posting Date": ["2026-10-01", "2026-10-02", "2026-10-01", "2026-10-03"],
                       "Debit": [50.0, None, 20.0, None], "Credit": [None, 200.0, None, 30.0],
                       "Running Balance": [950.0, 1150.0, 80.0, 110.0]})
    c = _contract(df)
    assert c.domain == "banking"
    assert compute_kpi(df, c.roles, BANK, "net_flow") == pytest.approx(160.0)
    assert compute_kpi(df, c.roles, BANK, "inflows") == pytest.approx(230.0)
    assert compute_kpi(df, c.roles, BANK, "outflows") == pytest.approx(70.0)
    level = next(x for x in c.columns if x.name == "Running Balance")   # a level, no KPI
    assert (level.aggregation, level.aggregation_by) == ("last", ["Account ID"])
    assert compute_kpi(df, c.roles, BANK, "total_balance") is None


LEDGER = pd.DataFrame({
    "Transaction ID": [f"T{i}" for i in range(1, 9)],
    "Account ID": ["A1"] * 4 + ["A2"] * 4,
    "Posting Date": ["2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04"] * 2,
    "Transaction Type": ["PURCHASE", "FEE", "REVERSAL", "TRANSFER",
                         "PURCHASE", "FEE", "DEPOSIT", "PURCHASE"],
    "Amount": [-40.0, -25.0, 25.0, -100.0, -10.0, -5.0, 300.0, -60.0],
    "Status": ["posted", "reversed", "posted", "posted", "posted", "posted", "posted", "pending"],
})


def test_ledger_rows_reversed_reversal_and_pending_are_out_of_money_figures():
    """Row state × consumer: the reversed fee and its reversal entry, and a pending purchase,
    are in no money KPI; spend leaves out transfers and fees."""
    c = _contract(LEDGER)
    assert (c.domain, c.domain_confidence) == ("banking", "strong")
    kinds = domain_pack.row_kinds(LEDGER, c.roles, BANK)
    assert list(kinds) == ["sale", "refunded", "non_product", "sale",
                           "sale", "sale", "sale", "cancel"]
    assert compute_kpi(LEDGER, c.roles, BANK, "fee_income") == pytest.approx(5.0)
    assert compute_kpi(LEDGER, c.roles, BANK, "spend") == pytest.approx(50.0)
    assert compute_kpi(LEDGER, c.roles, BANK, "net_flow") == pytest.approx(-40 - 100 - 10 - 5 + 300)
    assert compute_kpi(LEDGER, c.roles, BANK, "transactions") == 5
    assert compute_kpi(LEDGER, c.roles, BANK, "open_accounts") is None   # a ledger has none


def test_where_words_ignore_case_spaces_dashes_and_underscores():
    s = pd.DataFrame({"r": ["Non-Accrual", "NON_ACCRUAL", "non accrual", "accrual", None]})
    cond = domain_pack.Cond.model_validate({"role": "x", "in": ["non accrual"]})
    assert list(domain_pack._meets(s, {"x": "r"}, [[cond]])) == [True, True, True, False, False]


def test_the_schema_refuses_a_level_without_its_key_and_a_condition_without_one_operator():
    base = BANK.model_dump(by_alias=True, exclude_none=True)
    with pytest.raises(ValueError, match="over_time last needs snapshot_key"):
        domain_pack.Pack.model_validate({**base, "snapshot_key": None,
                                         "kpis": [k for k in base["kpis"] if k["at"] == "all"
                                                  and not any(t.get("kpi") for a in k["num"]
                                                              for t in a)]})
    with pytest.raises(ValueError, match="exactly one of"):
        domain_pack.Cond.model_validate({"role": "dpd", "ge": 90, "lt": 120})


def test_a_balance_report_says_its_as_of_date(tape):
    from orchestrator.state import CleaningAudit
    df, c = tape
    audit = CleaningAudit(decisions=[], rows_before=11, rows_after=11, columns_coerced=[],
                          duplicates_removed=0, outliers_flagged=[], high_null_columns=[],
                          summary="", date_ranges={"As Of Date": ["2026-09-30", "2026-10-31", 2]})
    said = domain_pack.pack_caveats(BANK, audit, c.roles, "strong")
    assert "Balances are as of 31 Oct 2026: each account's latest snapshot, added across " \
           "accounts, never across dates." in said
    retail = pack_for("retail")
    assert not any("as of" in x for x in domain_pack.pack_caveats(retail, audit, {}, "strong"))


def test_a_drill_down_on_a_weak_match_is_checked_and_one_with_no_pack_is_not():
    """Domain 1 left drill-down checks gated to strong matches (R5): an account + balance frame
    is a weak banking match, and an invented number or a rate cut credited with a change goes."""
    from agents.drill_down_agent import checked_answer
    from orchestrator.state import AnalysisSession
    weak = pd.DataFrame({"account_id": [1001, 1002, 1003], "account_type": ["a", "b", "a"],
                         "balance": [2500.0, 10400.5, 800.2]})
    c = _contract(weak)
    assert (c.domain, c.domain_confidence) == ("banking", "weak")
    s = AnalysisSession(session_id="t", data_contract=c)
    shown = ["Balances total 13,700.70 across 3 accounts."]
    out = checked_answer("Balances total 13,700.70. The rate cut drove that. It was 99,999.",
                         s, shown)
    assert out.startswith("Balances total 13,700.70.") and "2 sentence(s)" in out
    plain = AnalysisSession(session_id="t", data_contract=_contract(
        pd.DataFrame({"x": ["a", "b"], "y": [1.0, 2.0]})))
    assert checked_answer("It was 99,999.", plain, shown) == "It was 99,999."


@pytest.mark.parametrize("text, masked", [
    ("GB82WEST12345698765432", True),
    ("pay GB82 WEST 1234 5698 7654 32 now", True),
    ("GB82 WEST 1234 5698 7654 32 1234", True),       # a trailing number never hides it
    ("gb82west12345698765432", True),                 # lower case
    ("GB82WEST12345698765433", False),                # bad checksum: a code, not an IBAN
    ("SKU AB12CDEFGHIJKLMN", False),
    ("RU0204452560040702810412345678901", True),     # a registry country beyond Europe
    ("REFAB12GB82WEST12345698765432", True),          # inside a failed candidate
    ("id dc296822a02cb0f de76627c71637faa", False),   # hex pseudonyms
])
def test_an_iban_is_masked_wherever_it_sits(text, masked):
    from guardrails import MASK, mask_pii, pii_strings
    assert (MASK in mask_pii(text)) is masked
    assert bool(pii_strings(text)) is masked
    if masked:
        assert "5698" not in mask_pii(text)


def test_a_balance_chart_and_answer_count_the_rows_the_tile_counts(tape):
    """The chart path took each loan's latest row without the row filter: L3, charged off in
    October, kept its September balance in 'card' (the tile left it out)."""
    from agents.targeted_analysis_agent import handle_segment_compare
    from agents.visualization_agent import build_context, group_bars
    from orchestrator.state import TargetedParams
    df, c = tape
    r = handle_segment_compare(df, TargetedParams(metric_column="Outstanding Principal",
                                                  segment_column="Product", aggregation="sum"), c)
    assert "'mortgage' leads at 2,000.00; 'card' is lowest at 480.00" in r.answer_text
    bars = group_bars(df, "Product", "Outstanding Principal", "last", build_context(df, contract=c))
    got = {row["Product"]: row["Outstanding Principal"] for row in bars.data}
    assert got == {"auto": 1340.0, "card": 480.0, "mortgage": 2000.0}   # the tile's numbers


def test_the_built_in_trend_of_a_balance_counts_the_tiles_rows(tape):
    """Review: the sandbox's level branch skipped the row kinds, so its MoM counted L3's
    charge-off-month balance that the tile leaves out."""
    import io
    import json

    from agents import analysis_agent
    from orchestrator.state import AnalysisTask
    df, c = tape
    frame, _ = domain_pack.with_row_kind(df, c)
    out = io.StringIO(json.dumps(analysis_agent.builtin_result(
       frame, c, AnalysisTask(
                raw_query="q", target_metrics=["Outstanding Principal"], analysis_types=["trend"])), default=str))
    mom = next(t["pct_change"] for t in json.loads(out.getvalue())["trends"]
               if t["column"] == "Outstanding Principal" and t["period"] == "MoM")
    assert mom == pytest.approx((OCT / 2400 - 1) * 100, abs=0.01)


def test_a_ledgers_running_balance_keeps_its_quiet_accounts():
    """Review: a running balance is no snapshot; an account that last moved weeks ago is not
    closed (the stopped-account cut is for snapshots at a cadence)."""
    from agents.utils import latest_rows
    busy = [("A1", f"2026-10-{d:02d}", 100.0 + d) for d in (1, 2, 6, 7, 15, 16, 24, 31)]
    quiet = [("A2", "2026-09-03", 500.0), ("A2", "2026-09-21", 450.0), ("A2", "2026-10-02", 400.0)]
    df = pd.DataFrame(busy + quiet, columns=["Account ID", "Posting Date", "Balance"])
    df["Amount"] = 1.0
    c = _contract(df)
    assert c.domain == "banking"
    rows = latest_rows(df, "Balance", c)
    assert sorted(rows["Balance"]) == [131.0, 400.0]   # A2 kept


def test_a_newest_first_statements_last_row_of_a_day_is_its_last_transaction():
    from agents.utils import latest_rows
    df = pd.DataFrame({"Account ID": ["A1"] * 4, "Posting Date": ["2026-10-02", "2026-10-02",
                                                                  "2026-10-01", "2026-10-01"],
                       "Amount": [-5.0, -10.0, -20.0, 100.0], "Balance": [65.0, 70.0, 80.0, 100.0]})
    c = _contract(df)
    assert list(latest_rows(df, "Balance", c)["Balance"]) == [65.0]


def test_a_weak_banking_match_still_never_sums_a_balance():
    weak = pd.DataFrame({"account_id": [1, 2, 1, 2], "month": ["2026-09-30"] * 2 + ["2026-10-31"] * 2,
                         "balance": [10.0, 20.0, 11.0, 21.0]})
    c = _contract(weak)
    assert c.domain_confidence == "weak"
    bal = next(x for x in c.columns if x.name == "balance")
    assert (bal.aggregation, bal.aggregation_by) == ("last", ["account_id"])


def test_the_iban_scan_ignores_non_ascii_lookalikes():
    from guardrails import mask_pii
    assert mask_pii("GB82WESTK2345698765432") == "GB82WESTK2345698765432"   # no crash


def test_a_correlation_on_snapshot_data_counts_each_account_once(tape):
    """Audit: correlations over all 14 snapshots counted each loan 14 times."""
    import io
    import json

    from agents import analysis_agent
    from orchestrator.state import AnalysisTask
    df, c = tape
    out = io.StringIO(json.dumps(analysis_agent.builtin_result(
       df, c, AnalysisTask(
                raw_query="q", target_metrics=["Outstanding Principal", "Days Past Due"],
                analysis_types=["correlation"])), default=str))
    got = json.loads(out.getvalue())["correlations"]["Outstanding Principal__Days Past Due"]
    latest = df[(df["As Of Date"] == "2026-10-31")   # on the books only, as the tiles (entity_rows)
                & ~df["Loan Status"].isin(["Charged Off", "Paid Off"])]
    assert got == pytest.approx(latest["Outstanding Principal"].corr(latest["Days Past Due"]), abs=1e-4)
    task = AnalysisTask(raw_query="q", target_metrics=["Outstanding Principal"],
                        analysis_types=["correlation"])
    s = __import__("orchestrator.state", fromlist=["AnalysisSession"]).AnalysisSession(
        session_id="t", data_contract=c, task_plan=task)
    assert "one row per Loan ID (its latest snapshot)" in analysis_agent.build_context(s).render()


def test_a_state_kpi_by_month_is_each_month_end(tape):
    """Audit B2: 'NPL ratio by month' collapsed to one point (latest rows over the whole frame)."""
    df, c = tape
    frame = df.assign(_period=pd.to_datetime(df["As Of Date"]).dt.to_period("M"))
    got = compute_kpi(frame, c.roles, BANK, "total_balance", by="_period", full=df)
    assert {str(k): v for k, v in got.items()} == {"2026-09": 2400.0, "2026-10": OCT}
    npl = compute_kpi(frame, c.roles, BANK, "npl_ratio", by="_period", full=df)
    assert npl[pd.Period("2026-09")] == pytest.approx((300 + 400) / 2400)   # L3 120 DPD, L5 non-accrual


def test_audit_pass_1_kpis_refusals_and_unknowns(tape):
    """M2 charge-offs are a KPI; M3 a concept with no KPI is refused in its own words; M6 a
    one-date extract totals; M7 no matching rows anywhere is unknown, never $0.00."""
    from orchestrator.state import QuestionIntent as Q
    df, c = tape
    assert compute_kpi(df, c.roles, BANK, "charge_offs") == pytest.approx(290.0)   # L3, once
    one = pd.DataFrame({"account_id": [1, 2, 3], "account_type": ["a", "b", "a"],
                        "balance": [2500.0, 10400.5, 800.2]})
    oc = _contract(one)
    assert compute_kpi(one, oc.roles, BANK, "total_balance") == pytest.approx(13700.7)
    assert compute_kpi(LEDGER, _contract(LEDGER).roles, BANK, "interest_charged") is None
    import io

    from agents.targeted_analysis_agent import run_targeted_analysis
    from orchestrator.state import AnalysisSession, AnalysisTask, TargetedParams
    buf = io.BytesIO()
    df.to_parquet(buf, index=False)
    s = AnalysisSession(session_id="t", data_contract=c, task_plan=AnalysisTask(
        raw_query="What is our NIM by product?", analysis_types=["eda"], target_metrics=[],
        question_intent=Q.SEGMENT_COMPARE,
        targeted_params=TargetedParams(kpi="nim", segment_column="Product")))
    s.__dict__["_df_parquet"] = buf.getvalue()
    r = run_targeted_analysis(s)
    assert "interest income and interest expense" in (r.unanswerable_reason or r.answer_text)


def test_a_balance_by_segment_is_built_in_and_matches_the_tile(tape):
    """Audit M5: the model's own 'last' kept closed loans' rows (Card $859,390 vs $856,530)."""
    import io
    import json

    from agents import analysis_agent
    from orchestrator.state import AnalysisTask
    df, c = tape
    frame, _ = domain_pack.with_row_kind(df, c)
    model = {"segments": [   # the model's own: a wrong total, and its NPL ratio filed under the balance
        {"segment_column": "Product", "metric_column": "Outstanding Principal", "aggregation": "last",
         "results": [{"name": "card", "value": 780.0}]},
        {"segment_column": "Product", "metric_column": "Outstanding Principal", "aggregation": "ratio",
         "results": [{"name": "card", "value": 0.5}]}]}
    out = io.StringIO(json.dumps(analysis_agent.builtin_result(frame, c, AnalysisTask(
        raw_query="q", target_metrics=["Outstanding Principal"], segments=["Product"],
        analysis_types=["segmentation"]), model), default=str))
    every = [x for x in json.loads(out.getvalue())["segments"] if x["metric_column"] == "Outstanding Principal"]
    assert [x["aggregation"] for x in every if x["aggregation"] not in ("last", "mean")] == ["ratio"]
    segs = [x for x in every if x["aggregation"] == "last"]
    assert len(segs) == 1
    avg = next(x for x in every if x["aggregation"] == "mean")   # per loan on the books
    assert {r["name"]: r["value"] for r in avg["results"]} == {"auto": 670.0, "card": 480.0,
                                                                "mortgage": 2000.0}
    assert {r["name"]: r["value"] for r in segs[0]["results"]} == {"auto": 1340.0, "card": 480.0,
                                                                     "mortgage": 2000.0}


def test_a_state_by_month_on_weekly_snapshots_is_never_an_average_week():
    """Rehearsal: 'total balance by month' on weekly snapshots was divided by its weeks
    ($589,590.76 'average week'); a balance is as of the month's last snapshot."""
    import io

    from agents.targeted_analysis_agent import run_targeted_analysis
    from evals import datagen
    from orchestrator.state import AnalysisSession, AnalysisTask, QuestionIntent, TargetedParams
    df, truth = datagen.make("bank_balances", 1)
    c = _contract(df)
    buf = io.BytesIO()
    df.to_parquet(buf, index=False)
    s = AnalysisSession(session_id="t", data_contract=c, task_plan=AnalysisTask(
        raw_query="total balance by month", analysis_types=["eda"], target_metrics=[],
        question_intent=QuestionIntent.TIME_SERIES,
        targeted_params=TargetedParams(kpi="total_balance", frequency="M")))
    s.__dict__["_df_parquet"] = buf.getvalue()
    r = run_targeted_analysis(s)
    assert f"${truth['month_end_total_last']:,.2f}" in r.answer_text and "average week" not in r.answer_text
    assert f"{truth['balance_mom_pct']:+.1f}%" in r.answer_text


def test_a_ledger_says_no_balance_as_of_date():
    """Audit minor: 'Balances are as of 3 Nov 2026' on a ledger with no balance KPI."""
    from orchestrator.state import CleaningAudit
    c = _contract(LEDGER)
    audit = CleaningAudit(decisions=[], rows_before=8, rows_after=8, columns_coerced=[],
                          duplicates_removed=0, outliers_flagged=[], high_null_columns=[],
                          summary="", date_ranges={"Posting Date": ["2026-10-01", "2026-10-04", 1]})
    assert not any("as of" in x for x in domain_pack.pack_caveats(BANK, audit, c.roles, "strong"))


def test_a_levels_mean_by_group_is_one_number_in_the_answer_and_its_chart():
    """Builder audit M1: SaaS 'mean MRR by plan' answered 1,132.90 and charted 1,013.20."""
    from agents.targeted_analysis_agent import handle_segment_compare
    from evals import datagen
    from orchestrator.state import TargetedParams
    df = datagen.make("saas_accounts", 1)[0]
    c = _contract(df)
    r = handle_segment_compare(df, TargetedParams(metric_column="mrr", segment_column="plan",
                                                  aggregation="mean"), c)
    bars = {row["plan"]: row["mrr"] for row in r.chart_configs[0].data}
    lead = max(bars, key=bars.get)
    assert f"'{lead}' leads at {bars[lead]:,.2f}" in r.answer_text


def test_masking_fails_closed_and_column_detection_does_not_over_match():
    """Security review: an IBAN glued to more characters is still masked; only the column
    classifier needs a whole token (codes are not called IBAN columns)."""
    from guardrails import MASK, mask_pii, pii_kind
    assert MASK in mask_pii("GB82WEST12345698765432X9")
    assert pii_kind("ref", ["GB82WEST12345698765432X9"] * 3) is None
    assert pii_kind("ref", ["GB82WEST12345698765432"] * 3)[0] == "iban"


@pytest.mark.parametrize("days, label", [(7, "weekly"), (14, "fortnightly"), (30, "monthly"),
                                         (10, None), (1, "daily")])
def test_a_levels_cadence_is_a_clean_one_or_none(days, label):
    from agents.ingestion_agent import _cadence
    t = pd.date_range("2026-01-01", periods=6, freq=f"{days}D")
    df = pd.DataFrame({"a": ["x"] * 6 + ["y"] * 6, "t": list(t) * 2})
    assert _cadence(df, ["a"], "t") == label


def test_an_undated_row_is_never_an_accounts_latest_snapshot_in_a_correlation(tape):
    import io
    import json

    from agents import analysis_agent
    from orchestrator.state import AnalysisTask
    df, c = tape
    bad = pd.concat([df, pd.DataFrame([{"Loan ID": "L1", "As Of Date": None,
                                        "Outstanding Principal": 1e9, "Days Past Due": 999.0}])],
                    ignore_index=True)
    out = io.StringIO(json.dumps(analysis_agent.builtin_result(
       bad, c, AnalysisTask(
                raw_query="q", target_metrics=["Outstanding Principal", "Days Past Due"],
                analysis_types=["correlation"])), default=str))
    latest = df[(df["As Of Date"] == "2026-10-31")   # on the books only (entity_rows)
                & ~df["Loan Status"].isin(["Charged Off", "Paid Off"])]
    got = json.loads(out.getvalue())["correlations"]["Outstanding Principal__Days Past Due"]
    assert got == pytest.approx(latest["Outstanding Principal"].corr(latest["Days Past Due"]), abs=1e-4)


def test_audit_pass_2_periods_joint_levels_and_one_correlation(tape):
    """M1 a month with no charge-offs is $0, not skipped; M2 a level by two segments is built
    in from latest rows; M3 one correlation rule (entity_rows) for the built-in and the answer."""
    import io
    import json

    from agents import analysis_agent
    from agents.targeted_analysis_agent import handle_correlation
    from orchestrator.state import AnalysisTask, TargetedParams
    df, c = tape
    frame = df.assign(_period=pd.to_datetime(df["As Of Date"]).dt.to_period("M"))
    co = compute_kpi(frame, c.roles, BANK, "charge_offs", by="_period", full=df)
    assert {str(k): v for k, v in co.items()} == {"2026-09": 0.0, "2026-10": 290.0}
    two = df.assign(Branch=["A", "B"] * 5 + ["A"])
    c2 = _contract(two)
    kinds, _ = domain_pack.with_row_kind(two, c2)
    out = io.StringIO(json.dumps(analysis_agent.builtin_result(
       kinds, c2, AnalysisTask(
                raw_query="q", target_metrics=["Outstanding Principal", "Days Past Due"],
                segments=["Product", "Branch"], analysis_types=["segmentation", "correlation"])), default=str))
    res = json.loads(out.getvalue())
    joint = [x for x in res["segments"] if x["segment_column"] == "Product|Branch"
             and x["metric_column"] == "Outstanding Principal" and x["aggregation"] == "last"]
    assert len(joint) == 1 and joint[0]["aggregation"] == "last"
    book = domain_pack.entity_rows(kinds, c2)
    assert sorted(book["Loan ID"]) == ["L1", "L2", "L5", "L6"]
    want = book["Outstanding Principal"].corr(book["Days Past Due"])
    assert res["correlations"]["Outstanding Principal__Days Past Due"] == pytest.approx(want, abs=1e-4)
    r = handle_correlation(df, TargetedParams(target_columns=["Outstanding Principal", "Days Past Due"]), c)
    assert f"{want:.3f}".lstrip("0") in r.answer_text or f"{want:.2f}" in r.answer_text


def test_pass_3_stopped_accounts_count_once_and_a_zero_month_is_a_month(tape):
    """M1: a correlation keeps an account that stopped reporting (its last row), as the sandbox
    does; M2: 'vs $0.00 the period before', never 'no earlier period'."""
    import io

    from agents.targeted_analysis_agent import run_targeted_analysis
    from orchestrator.state import AnalysisSession, AnalysisTask, QuestionIntent, TargetedParams
    df, c = tape
    stopped = df[~((df["Loan ID"] == "L1") & (df["As Of Date"] == "2026-10-31"))]
    assert "L1" in set(domain_pack.entity_rows(stopped, c)["Loan ID"])
    buf = io.BytesIO()
    df.to_parquet(buf, index=False)
    s = AnalysisSession(session_id="t", data_contract=c, task_plan=AnalysisTask(
        raw_query="charge-offs by month", analysis_types=["eda"], target_metrics=[],
        question_intent=QuestionIntent.TIME_SERIES,
        targeted_params=TargetedParams(kpi="charge_offs", frequency="M")))
    s.__dict__["_df_parquet"] = buf.getvalue()
    r = run_targeted_analysis(s)
    assert "vs $0.00 the period before (no % change from a zero or negative base)" in r.answer_text
    assert "no earlier complete period" not in r.answer_text


def test_a_period_on_an_event_date_reads_the_datas_coverage_from_its_time_axis():
    """Live run: 'charged off this year' sliced by Charge Off Date said 'the data starts 28 Feb
    2026, so the period is incomplete' (the first charge-off); the tape starts Sep 2025."""
    import io

    from agents.targeted_analysis_agent import run_targeted_analysis
    from evals import datagen
    from orchestrator.state import AnalysisSession, AnalysisTask, QuestionIntent, TargetedParams
    df = datagen.make("loan_tape", 1)[0]
    c = _contract(df)
    buf = io.BytesIO()
    df.to_parquet(buf, index=False)
    s = AnalysisSession(session_id="t", data_contract=c, task_plan=AnalysisTask(
        raw_query="How much did we charge off this year, by product?", analysis_types=["eda"],
        target_metrics=[], question_intent=QuestionIntent.SEGMENT_COMPARE,
        targeted_params=TargetedParams(kpi="charge_offs", segment_column="Product",
                                       date_column="Charge Off Date", period_start="2026-01-01",
                                       period_end="2026-12-31")))
    s.__dict__["_df_parquet"] = buf.getvalue()
    r = run_targeted_analysis(s)
    assert "the data starts" not in r.answer_text and "$21,767.95" in r.answer_text


def test_the_ledger_generator_is_the_same_on_every_machine():
    """CI (Linux) generated another card_ledger than macOS: salaries were an unrounded exp(),
    whose last digits differ by platform. Every amount is in cents."""
    from evals import datagen
    for name, col in (("card_ledger", "Amount"), ("card_ledger", "Balance After"),
                      ("loan_tape", "Outstanding Principal")):
        s = datagen.make(name, 1)[0][col].dropna()
        assert (s.round(2) == s).all(), (name, col)


def test_a_percent_change_needs_a_positive_base():
    """Final audit: net flow −$4,464 → +$9,170 was called '−305.4%'."""
    from agents.utils import pct_change
    assert pct_change(110.0, 100.0) == pytest.approx(10.0)
    assert pct_change(9170.0, -4464.0) is None and pct_change(5.0, 0.0) is None


def test_a_split_by_currency_keeps_every_currency(tape):
    """Final audit M1: 'total balance by currency' returned USD only. Split by currency, each
    currency is its own group (never added to another); everything else stays main-currency, so
    tiles side by side read the same rows (balance / accounts = average)."""
    df, c = tape
    fx = df.assign(Currency=["USD"] * 10 + ["EUR"])   # L6 (opened in October) in euros
    cf = _contract(fx)
    by = compute_kpi(fx, cf.roles, BANK, "total_balance", by="Currency")
    assert by.to_dict() == {"EUR": 2000.0, "USD": OCT - 2000}
    assert compute_kpi(fx, cf.roles, BANK, "total_balance") == pytest.approx(OCT - 2000)
    assert compute_kpi(fx, cf.roles, BANK, "open_accounts") == 3   # USD, as the balance beside it


# ── Plan v2 R3: header variants (one vocabulary, abbreviations in the pack) ──────────────────

@pytest.mark.parametrize("headers, role", [
    (["Account", "As Of", "Cur Bal", "Days Late"], {"account": "Account", "date": "As Of",
                                                    "balance": "Cur Bal", "dpd": "Days Late"}),
    (["Acct No", "As Of Dt", "OS Prin", "DPD"], {"account": "Acct No", "date": "As Of Dt",
                                                 "balance": "OS Prin"}),
    (["ACCT_NBR", "SNAPSHOT_DATE", "CURR_BAL", "DAYS_PAST_DUE"], {"account": "ACCT_NBR",
                                                                 "balance": "CURR_BAL"}),
    (["Acct ID", "Txn Dt", "Txn Amt", "Txn Type"], {"account": "Acct ID", "amount": "Txn Amt",
                                                    "txn_type": "Txn Type", "date": "Txn Dt"}),
])
def test_abbreviated_headers_read_as_their_roles(headers, role):
    from domain_pack import detect
    n = 30
    cols = {}
    for h in headers:
        low = h.lower()
        cols[h] = (["2026-09-30"] * (n // 2) + ["2026-10-31"] * (n - n // 2) if any(w in low for w in ("as of", "date", "dt"))
                   else [f"A{i % 15}" for i in range(n)] if "acc" in low
                   else ["PURCHASE", "FEE", "PAYMENT"] * (n // 3) if "type" in low
                   else [i % 4 * 30 for i in range(n)] if "day" in low or low == "dpd"
                   else [100.5 + i for i in range(n)])
    df = pd.DataFrame(cols)
    dates = {h for h in headers if any(w in h.lower() for w in ("as of", "date", "dt"))}
    domain, roles, _ = detect(df, dates)
    assert domain == "banking"
    assert {k: roles.get(k) for k in role} == role


@pytest.mark.parametrize("headers", [
    ["Customer", "Invoice Date", "Cur Bal", "Days Late"],          # AR aging: an invoice word
    ["Employee ID", "Leave Bal", "As Of"],                         # leave balances
    ["Patient", "Bal", "Visit Date"],                              # a hospital's patient balance
])
def test_another_domains_balance_is_not_banking(headers):
    from domain_pack import detect
    df = pd.DataFrame({h: (["2026-10-31"] * 10 if "date" in h.lower() or "as of" in h.lower()
                           else [f"X{i}" for i in range(10)] if h in ("Customer", "Employee ID", "Patient")
                           else [float(i) for i in range(10)]) for h in headers})
    assert detect(df, {h for h in headers if "date" in h.lower() or "as of" in h.lower()})[0] != "banking"
