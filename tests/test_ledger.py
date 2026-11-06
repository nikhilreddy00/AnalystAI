"""The ledger (Domain 6 S2): an entity × month-end value matrix behind MRR movements and cohort
retention. A toy MRR book (snapshots by month) with every move planted; the truth is written by
hand per customer. The bridge identity (open + new + expansion + reactivation − contraction −
churn = close) holds by construction and is checked here and per segment."""
import pandas as pd
import pytest
import yaml

from domain_pack import Pack, compute_kpi, computable

BOOK = """
domain: toymrr
strong:
  - {cust: [customer_id], mrr: [mrr]}
signals:
  - {cust: [customer_id], mrr: [mrr]}
roles:
  month:       {kind: date, headers: [month]}
  start_date:  {kind: date, headers: [start_date]}
  end_date:    {kind: date, headers: [end_date]}
  customer_id: {kind: id, headers: [customer_id]}
  mrr:         {kind: money, headers: [mrr], over_time: last}
  segment:     {kind: category, headers: [segment]}
spell: {entity: customer_id, start: start_date, end: end_date, month_labels: true}
snapshot_key: customer_id
kpis:
  - {name: mrr, label: MRR, unit: money, num: [[{spell: active, sum: mrr}]]}
  - {name: opening_mrr, label: Opening MRR, unit: money, num: [[{spell: opening, sum: mrr}]]}
  - {name: new_mrr, label: New MRR, unit: money, num: [[{spell: moves, move: new, sum: mrr}]]}
  - {name: expansion_mrr, label: Expansion MRR, unit: money, num: [[{spell: moves, move: expansion, sum: mrr}]]}
  - {name: contraction_mrr, label: Contraction MRR, unit: money, num: [[{spell: moves, move: contraction, sum: mrr}]]}
  - {name: churned_mrr, label: Churned MRR, unit: money, num: [[{spell: moves, move: churn, sum: mrr}]]}
  - {name: reactivation_mrr, label: Reactivation MRR, unit: money, num: [[{spell: moves, move: reactivation, sum: mrr}]]}
  - {name: churned_customers, label: Churned customers, unit: count, num: [[{spell: moves, move: churn}]]}
  - {name: new_customers, label: New customers, unit: count, num: [[{spell: moves, move: new}]]}
  - {name: opening_customers, label: Opening customers, unit: count, num: [[{spell: opening}]]}
  - {name: logo_churn_rate, label: Logo churn rate, unit: percent,
     num: [[{spell: opening}, {spell: retained, sign: -1}]], den: [[{kpi: opening_customers}]]}
  - {name: nrr, label: NRR, unit: percent, num: [[{spell: retained, sum: mrr}]], den: [[{kpi: opening_mrr}]]}
  - {name: grr, label: GRR, unit: percent, num: [[{spell: retained, sum: mrr, capped: true}]],
     den: [[{kpi: opening_mrr}]]}
caveats:
  read_as_domain_weak: "Read as MRR data: {roles}."
"""

MONTHS = [f"2025-{m:02d}-01" for m in range(10, 13)] + [f"2026-{m:02d}-01" for m in range(1, 11)]
# MRR per month Oct 2025 .. Oct 2026 (13 labels); 0 = absent that month
PLAN = {   # customer: (segment per month or one, mrr per month)
    "A": ("SMB", [100] * 13),                                # steady
    "B": ("SMB", [100] * 6 + [150] * 7),                     # expands +50 in Apr 2026
    "C": ("Ent", [400] * 4 + [300] * 9),                     # contracts −100 in Feb 2026
    "D": ("Ent", [200] * 5 + [0] * 8),                       # churns −200 in Mar 2026
    "E": ("SMB", [0] * 3 + [50] * 10),                       # new +50 in Jan 2026
    "F": ("SMB", [80] * 2 + [0] * 4 + [80] * 7),             # churns Dec, reactivates Apr
    "G": (["SMB"] * 7 + ["Ent"] * 6, [60] * 7 + [90] * 6),   # moves SMB→Ent with +30 in May
}


@pytest.fixture
def book(monkeypatch):
    monkeypatch.setenv("ANALYSTAI_TODAY", "2026-11-05")
    rows = []
    for c, (seg, mrr) in PLAN.items():
        for i, m in enumerate(MONTHS):
            if mrr[i]:
                rows.append((m, c, mrr[i], seg[i] if isinstance(seg, list) else seg))
    df = pd.DataFrame(rows, columns=["month", "customer_id", "mrr", "segment"])
    return Pack.model_validate(yaml.safe_load(BOOK)), df, {
        "month": "month", "customer_id": "customer_id", "mrr": "mrr", "segment": "segment"}


def test_the_bridge_adds_up_and_each_move_is_its_customer(book):
    pack, df, roles = book
    k = {n: compute_kpi(df, roles, pack, n) for n in
         ("mrr", "opening_mrr", "new_mrr", "expansion_mrr", "contraction_mrr", "churned_mrr",
          "reactivation_mrr", "churned_customers", "new_customers")}
    # window: the 12 months to 31 Oct 2026, opening at 31 Oct 2025 (A B C D F G paying)
    assert k["opening_mrr"] == 100 + 100 + 400 + 200 + 80 + 60
    assert (k["new_mrr"], k["expansion_mrr"], k["contraction_mrr"]) == (50, 50 + 30, 100)
    assert (k["churned_mrr"], k["reactivation_mrr"]) == (200 + 80, 80)
    assert (k["churned_customers"], k["new_customers"]) == (2, 1)
    assert k["mrr"] == k["opening_mrr"] + k["new_mrr"] + k["expansion_mrr"] \
        + k["reactivation_mrr"] - k["contraction_mrr"] - k["churned_mrr"]


def test_cohort_retention_is_the_opening_customers_a_year_later(book):
    pack, df, roles = book
    opening = 940
    # F churned and came back: in the cohort, retained at 80; D gone; C contracted; B, G grew
    assert compute_kpi(df, roles, pack, "nrr") == pytest.approx((100 + 150 + 300 + 0 + 80 + 90) / opening)
    assert compute_kpi(df, roles, pack, "grr") == pytest.approx((100 + 100 + 300 + 0 + 80 + 60) / opening)
    assert compute_kpi(df, roles, pack, "logo_churn_rate") == pytest.approx(1 / 6)


def test_a_filter_keeps_each_move_where_it_happened(book):
    """A segment's bridge adds up over segments; G (SMB at the opening, Ent since May) stays in
    SMB's cohort with its Ent MRR, and its +30 counts where it happened (Ent)."""
    pack, df, roles = book
    for n in ("new_mrr", "expansion_mrr", "contraction_mrr", "churned_mrr", "reactivation_mrr"):
        by = compute_kpi(df, roles, pack, n, by="segment")
        assert by.sum() == compute_kpi(df, roles, pack, n), n
    smb = df[df["segment"] == "SMB"]
    assert compute_kpi(smb, roles, pack, "opening_mrr", full=df) == 100 + 100 + 80 + 60
    assert compute_kpi(smb, roles, pack, "nrr", full=df) == pytest.approx((100 + 150 + 80 + 90) / 340)
    assert compute_kpi(df, roles, pack, "expansion_mrr", by="segment")["Ent"] == 30


def test_by_month_the_moves_fall_in_their_month(book):
    pack, df, roles = book
    by = compute_kpi(df.assign(_p=pd.to_datetime(df["month"]).dt.to_period("M")), roles, pack,
                     "churned_mrr", by="_p", full=df)
    assert by[pd.Period("2025-12", "M")] == 80 and by[pd.Period("2026-03", "M")] == 200
    assert by.sum() >= 280


def test_on_a_subscription_list_history_terms_are_not_computable():
    """Red-team R4: a list holds today's price at both ends, so expansion would be a confident
    $0. Expansion, contraction and retention need the snapshot date role."""
    raw = yaml.safe_load(BOOK)
    pack = Pack.model_validate(raw)
    roles = {"customer_id": "customer_id", "mrr": "mrr", "start_date": "start_date",
             "end_date": "end_date"}
    assert not computable(pack, "expansion_mrr", roles) and not computable(pack, "nrr", roles)
    assert computable(pack, "new_mrr", roles) and computable(pack, "logo_churn_rate", roles)


def test_a_count_counts_customers_not_their_concurrent_subscriptions(book):
    """Found on saas_subscriptions: a customer with a base plan and an add-on counted twice in
    the opening cohort (229 vs 218). A count term's value is the entity's presence."""
    pack, df, roles = book
    two = pd.concat([df, df[df["customer_id"] == "A"].assign(mrr=5)], ignore_index=True)
    raw = yaml.safe_load(BOOK)
    raw["spell"]["concurrent"] = True
    both = Pack.model_validate(raw)
    assert compute_kpi(two, roles, both, "opening_customers") == 6
    assert compute_kpi(two, roles, both, "opening_mrr") == 940 + 5


def test_the_in_force_memo_returns_the_rule_and_dies_with_its_frame(book):
    import gc

    import domain_pack as dp
    pack, df, roles = book
    for t in pd.to_datetime(["2025-12-31", "2026-03-31", "2026-10-31"]):
        assert dp._in_force(df, roles, pack, t).equals(dp._in_force_at(df, roles, pack, t))
        assert dp._in_force(df, roles, pack, t).equals(dp._in_force_at(df, roles, pack, t))
    other = df.copy()
    other.loc[other["customer_id"] == "A", "mrr"] = 1   # another frame: never the first's memo
    assert dp._in_force(other, roles, pack, pd.Timestamp("2026-10-31"))["mrr"].min() == 1
    key = id(other)
    del other
    gc.collect()
    dp._in_force(df, roles, pack, pd.Timestamp("2026-10-31"))
    assert key not in dp._MEMO
