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
