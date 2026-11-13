"""The ledger (Domain 6 S2): an entity × month-end value matrix behind MRR movements and cohort
retention. A toy MRR book (snapshots by month) with every move planted; the truth is written by
hand per customer. The bridge identity (open + new + expansion + reactivation − contraction −
churn = close) holds by construction and is checked here and per segment."""
import pandas as pd
import pytest
import yaml

from domain_pack import Pack, compute_kpi, computable, describe_kpi, spell_facts

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
# a ledger's sentences are its pack's words (C9: a pack with moves must have them): the SaaS
# pack's, so each sentence below is the one users read
BOOK = BOOK.replace("month_labels: true}", "month_labels: true, words: " + __import__("json").dumps(
    __import__("domain_pack").pack_for("saas").spell.words.model_dump()) + "}")

MONTHS =[f"2025-{m:02d}-01" for m in range(10, 13)] + [f"2026-{m:02d}-01" for m in range(1, 11)]
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
    # from Jan 2026 on: the first month has no month before it in the snapshots, so its moves
    # are unknown (left out), never $0; Feb's are known (none)
    jan = df[df["month"] >= "2026-01-01"]
    by = compute_kpi(jan.assign(_p=pd.to_datetime(jan["month"]).dt.to_period("M")), roles, pack,
                     "churned_mrr", by="_p", full=jan)
    assert by.index[0] == pd.Period("2026-02", "M") and by.iloc[0] == 0


def test_the_story_says_a_money_stocks_fall_from_its_peak_and_each_months_moves(book):
    """By hand from PLAN: MRR is 940 at Nov 2025 (its peak in the window) and 770 at the as-of
    (−18.1%); a customer churns in Dec (F) and one in Mar (D)."""
    _, df, roles = book
    raw = yaml.safe_load(BOOK)
    raw["kpis"][0]["tile"] = True   # MRR as the stock the story reads
    facts = spell_facts(Pack.model_validate(raw), df, roles)
    assert "MRR peaked at $940.00 (Nov 2025) and is $770.00 at 31 Oct 2026 (-18.1%)." in facts
    assert "Churned customers by month (12 months to 31 Oct 2026): " + ", ".join(
        f"{p} {1 if p in ('2025-12', '2026-03') else 0}"
        for p in pd.period_range("2025-11", "2026-10", freq="M").astype(str)) + "." in facts


def test_by_month_a_cohort_rate_needs_its_whole_year(book):
    """Each month's NRR follows its own cohort, 12 months back: only Oct 2026's lies wholly in
    the data (Oct 2025 on); every earlier month's is unknown, never a shorter cohort's rate."""
    pack, df, roles = book
    by = compute_kpi(df.assign(_p=pd.to_datetime(df["month"]).dt.to_period("M")), roles, pack,
                     "nrr", by="_p", full=df)
    assert list(by.index) == [pd.Period("2026-10", "M")]
    assert by.iloc[0] == pytest.approx((100 + 150 + 300 + 0 + 80 + 90) / 940)


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


def test_a_part_whose_row_labels_are_not_the_wholes_counts_by_label(book):
    """SpellView (5.7b-1) precomputes the KPI's kinds over the whole data; a part relabelled
    away from it (labels outside, or landing on other rows) reads what it read before: values
    pinned from the code before the refactor."""
    pack, df, roles = book
    smb = df[df["segment"] == "SMB"]
    outside, overlap = smb.set_axis(range(1000, 1000 + len(smb))), smb.set_axis(range(5, 5 + len(smb)))
    assert compute_kpi(outside, roles, pack, "mrr", full=df) == 0   # nobody in force: $0 (C5)
    assert compute_kpi(outside, roles, pack, "new_mrr", by="segment", full=df) is None
    assert compute_kpi(overlap, roles, pack, "mrr", full=df) == 600
    assert compute_kpi(overlap, roles, pack, "opening_mrr", by="segment", full=df).to_dict() == {"SMB": 180}
    assert compute_kpi(overlap, roles, pack, "nrr", full=df) == pytest.approx(0.6794871794871795)


MOVED = """
- {name: transfer_in_mrr, label: MRR moved in, unit: money, num: [[{spell: moves, move: transfer_in, sum: mrr}]]}
- {name: transfer_out_mrr, label: MRR moved out, unit: money, num: [[{spell: moves, move: transfer_out, sum: mrr}]]}
- {name: customers_in, label: Customers moved in, unit: count, num: [[{spell: moves, move: transfer_in}]]}
- {name: customers_out, label: Customers moved out, unit: count, num: [[{spell: moves, move: transfer_out}]]}
"""


def _moved_pack() -> Pack:
    """The book with transfer KPIs, money and count, and concurrent rows (a customer in two
    segments at once)."""
    raw = yaml.safe_load(BOOK)
    raw["kpis"] += yaml.safe_load(MOVED)
    raw["spell"]["concurrent"] = True
    return Pack.model_validate(raw)


def _rows(c, seg, mrr, months) -> pd.DataFrame:
    return pd.DataFrame([(m, c, mrr, seg) for m in months], columns=["month", "customer_id", "mrr", "segment"])


def test_a_mover_across_parts_is_a_transfer_by_hand(book):
    """GLOSSARY Move (transfer), by hand on the book (window: Nov 2025 to Oct 2026).
    - G: SMB 60 → Ent 90 in May: 60 moved, +30 expansion in Ent.
    - X: SMB 40, adds Ent 40 in Jan (both), drops SMB in Apr. In money, Jan is Ent expansion and
      Apr SMB contraction (no matched fall / rise). As a count, a part gained (Jan) and lost (Apr)
      while X stays is a transfer each way.
    - Y: $0.50 SMB → Ent in Jun, a transfer under $1.
    - Z: SMB 70 until May, its segment blank from Jun. A blank group is a part too, so Z moved
      out of SMB (into no named group), never churned.
    - W: a −$30 credit at the opening. A line is never below 0."""
    _, df, roles = book
    pack, m = _moved_pack(), MONTHS
    df = pd.concat([df, _rows("X", "SMB", 40, m[:6]), _rows("X", "Ent", 40, m[3:]),
                    _rows("Y", "SMB", 0.5, m[:8]), _rows("Y", "Ent", 0.5, m[8:]),
                    _rows("Z", "SMB", 70, m[:8]), _rows("Z", None, 70, m[8:]),
                    _rows("W", "SMB", -30, m[:1])], ignore_index=True)

    def by(k):
        return {s: v for s, v in compute_kpi(df, roles, pack, k, by="segment").items() if v}
    assert by("customers_in") == {"Ent": 3}             # X Jan, G May, Y Jun
    assert by("customers_out") == {"SMB": 4}            # X Apr, G May, Y Jun, Z Jun
    assert by("transfer_in_mrr") == {"Ent": 60 + 0.5}   # G, Y (Z's 70 went to no named group)
    assert by("transfer_out_mrr") == {"SMB": 60 + 0.5 + 70}
    assert by("churned_mrr") == {"SMB": 80, "Ent": 200}   # F and D only
    assert compute_kpi(df, roles, pack, "opening_mrr") == 940 + 40 + 0.5 + 70   # W: 0, not −30


def test_how_each_move_is_counted_in_words(book):
    """An answer's How for each move: the words written here, by hand."""
    roles, pack = book[2], _moved_pack()
    went = " moves: each customer_id whose value went "
    rule = {"new_mrr": "new" + went + "from 0 (never in force before) to above 0",
            "reactivation_mrr": "reactivation" + went + "from 0 back above 0 (in force before)",
            "expansion_mrr": "expansion" + went + "up",
            "contraction_mrr": "contraction" + went + "down (not to 0)",
            "churned_mrr": "churn" + went + "to 0",
            "transfer_in_mrr": "value moved in from another group: each customer_id's rise in a "
                               "group matched by its fall in another",
            "transfer_out_mrr": "value moved out to another group: each customer_id's fall in a "
                                "group matched by its rise in another"}
    for k, how in rule.items():
        assert describe_kpi(pack, k, roles).split(";")[0] == (
            f"Sum of mrr over the {how} between consecutive month-ends in the period"), k
