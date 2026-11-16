"""A second domain pack beside retail (Phase 5.7 reuse): detection picks the right pack, shared
code reads the pack's own fields (never retail role names), and retail is unchanged."""
import shutil
from pathlib import Path

import pandas as pd
import pytest

import domain_pack
from domain_pack import Pack, detect, pack_brief, pack_caveats, row_kinds

BANK = """
domain: toybank
strong:
  - {acct: [account_id], money: [amount], when: [txn_date]}
signals:
  - {acct: [account_id], money: [amount]}
roles:
  account_id: {kind: id, headers: [account_id, acct_no]}
  amount:     {kind: money, headers: [amount, txn_amount]}
  txn_date:   {kind: date, headers: [txn_date, posted_date]}
  quantity:   {kind: count, headers: [quantity]}
  status:     {kind: status, headers: [status]}
status_values: {ok: [posted], cancel: [reversed]}
kpis:
  - {name: deposits, label: Deposits, num: [[{sum: amount}]], tile: true}
caveats:
  read_as_domain_weak: "Read as banking data: {roles}."
  months_lt_13: "The data covers {months} months."
"""


@pytest.fixture
def two_packs(tmp_path, monkeypatch):
    for p in domain_pack.PACKS.glob("*.yaml"):
        shutil.copy(p, tmp_path / p.name)
    (tmp_path / "toybank.yaml").write_text(BANK)
    monkeypatch.setattr(domain_pack.pack, "PACKS", tmp_path)
    domain_pack.load_packs.cache_clear()
    yield domain_pack.pack_for("toybank")
    domain_pack.load_packs.cache_clear()


def _bank(n=60):
    return pd.DataFrame({"account_id": [f"A{i % 7}" for i in range(n)],
                         "amount": [10.0 + i for i in range(n)],
                         "txn_date": pd.date_range("2025-01-01", periods=n, freq="7D"),
                         "quantity": [-1] * n, "status": ["posted"] * n})


def test_each_pack_detects_its_own_data(two_packs):
    domain, roles, how = detect(_bank(), {"txn_date"})
    assert (domain, how) == ("toybank", "strong") and roles["txn_date"] == "txn_date"
    lines = pd.DataFrame({"order_id": ["1001", "1001", "1002"], "sku": ["A", "B", "A"],
                          "quantity": [1, 2, 1], "unit_price": [9.5, 3.0, 9.5]})
    assert detect(lines)[0] == "retail"


def test_a_strong_match_beats_another_packs_weak_one(two_packs):
    both = pd.DataFrame({"order_id": ["1001", "1001", "1002"], "sku": ["A", "B", "A"],
                         "quantity": [1, 2, 1], "unit_price": [9.5, 3.0, 9.5],
                         "account_id": ["X1", "X1", "X2"], "amount": [9.5, 6.0, 9.5]})
    assert detect(both)[:1] == ("retail",) and detect(both)[2] == "strong"


def test_shared_code_never_borrows_retail_role_names(two_packs):
    """The toy pack has `quantity` and `status` roles but no negative_is_return / order_key:
    a negative quantity is no 'return', and no retail words reach its brief or caveats."""
    pack, df = two_packs, _bank()
    roles = {"account_id": "account_id", "amount": "amount", "txn_date": "txn_date",
             "quantity": "quantity", "status": "status"}
    assert set(row_kinds(df, roles, pack)) == {"sale"}
    text = " ".join(pack_brief(pack, roles, ["deposits"], grain="line",
                               present={"sale", "test"}))
    for word in ("order", "sales", "net sales", "promotion"):
        assert word not in text.lower(), word
    assert pack.date_role == "txn_date"


def test_the_profit_rule_is_only_a_pack_that_names_cost_roles(two_packs):
    from agents.quality_agent import check_claims  # noqa: F401  (the rule's home)
    assert two_packs.cost_roles == [] and domain_pack.pack_for("retail").cost_roles


def test_months_caveat_reads_the_packs_own_date_role(two_packs):
    from types import SimpleNamespace
    audit = SimpleNamespace(decisions=[], partial_months={}, main_currency=None, window_span={},
                            date_ranges={"txn_date": ["2025-01-01", "2025-06-30", 6]})
    roles = {"account_id": "account_id", "amount": "amount", "txn_date": "txn_date"}
    assert pack_caveats(two_packs, audit, roles) == ["The data covers 6 months."]


def test_two_strong_matches_are_no_match_and_traced(two_packs, monkeypatch):
    import llm.client
    seen = []
    monkeypatch.setattr(llm.client, "trace_verdict", lambda *a: seen.append(a[-1]))
    both = pd.DataFrame({"order_id": ["1001", "1001", "1002"], "sku": ["A", "B", "A"],
                         "quantity": [1, 2, 1], "unit_price": [9.5, 3.0, 9.5],
                         "account_id": ["X1", "X1", "X2"], "amount": [9.5, 6.0, 9.5],
                         "txn_date": pd.to_datetime(["2025-01-01"] * 3)})
    assert detect(both, {"txn_date"}) == (None, {}, None) and seen == ["domain_ambiguous"]


def test_the_validator_needs_nothing_retail():
    Pack.model_validate(__import__("yaml").safe_load(BANK))
    assert Path(domain_pack.PACKS / "retail.yaml").exists()


GAUGE = BANK.replace("  quantity:", """  quality: {kind: category, headers: [quality]}
  quantity:""") + """invalid_when:
  quality: {values: [Bad, '0'], doubtful: [Uncertain], blanks: [amount]}
"""


def test_a_value_its_own_flag_marks_invalid_is_blanked_and_said_never_averaged():
    """F6 (Domain 7): a historian's Bad reading is no measurement (OPC UA: never used), yet a
    non-additive measure reads every row (rows_for), so it reached every mean and chart. The
    pack names the flag: those values are blanked like a sentinel, the row kept and counted;
    Uncertain is kept and counted. A pack without the field changes nothing."""
    from agents.cleaning_agent import CleanContext, clean
    pack = Pack.model_validate(__import__("yaml").safe_load(GAUGE))
    df = pd.DataFrame({"account_id": list("ABCDE"), "amount": [10.0, 900.0, 12.0, 11.0, 13.0],
                       "quality": ["Good", "Bad", "Uncertain", "0", "good"]})
    roles = {c: c for c in df.columns}
    out, audit = clean(CleanContext(df=df, ids={"account_id"}, dates=set(), targets=set(),
                                    drop_high_null=False, pack=pack, roles=roles))
    assert len(out) == 5 and out["amount"].isna().tolist() == [False, True, False, True, False]
    said = " ".join(d.reason for d in audit.decisions)
    assert "2 amount values" in said and "1 row" in said and "Uncertain" in said
    out, _ = clean(CleanContext(df=df, ids={"account_id"}, dates=set(), targets=set(),
                                drop_high_null=False, pack=Pack.model_validate(
                                    __import__("yaml").safe_load(BANK)),
                                roles={"account_id": "account_id", "amount": "amount"}))
    assert out["amount"].notna().all()
    with pytest.raises(ValueError, match="invalid_when"):
        Pack.model_validate(__import__("yaml").safe_load(GAUGE.replace("blanks: [amount]", "blanks: [nope]")))


PER_UNIT = BANK.replace("kpis:\n", """kpis:
  - {name: per_unit, label: Amount per unit, unit: number, num: [[{sum: amount}]],
     den: [[{sum: quantity}]], zero_words: "none counted against {num}"}
  - {name: per_unit_plain, label: Amount per unit, unit: number, num: [[{sum: amount}]],
     den: [[{sum: quantity}]]}
""")


def test_a_group_with_a_zero_base_is_said_never_dropped_or_ranked():
    """F9 (Domain 7, C5): a ratio by group dropped every group whose base is 0 without a word (a
    machine with run time and no failures vanished from 'MTBF by machine'). It is kept apart
    from the ranked values with its numerator and said in the KPI's words, else generic ones."""
    from domain_pack.kpi import measure, zero_note
    pack = Pack.model_validate(__import__("yaml").safe_load(PER_UNIT))
    df = pd.DataFrame({"account_id": ["A", "A", "B", "C"], "amount": [10.0, 20.0, 50.0, 0.0],
                       "quantity": [1, 2, 0, 0]})
    roles = {c: c for c in df.columns}
    got = measure(df, roles, pack, "per_unit", by="account_id")
    assert got.values.to_dict() == {"A": 10.0} and dict(got.zero) == {"B": 50.0, "C": 0.0}
    assert zero_note(pack.kpi("per_unit"), got.zero, str) == " B: none counted against 50.0; C: none counted against 0.0."
    plain = measure(df, roles, pack, "per_unit_plain", by="account_id")
    assert zero_note(pack.kpi("per_unit_plain"), plain.zero, str) == \
        " Not measurable, its base is 0: B, C."
    only_zero = measure(df[df.account_id != "A"], roles, pack, "per_unit", by="account_id")
    assert only_zero.reason == "zero_denominator"


SETTLE = BANK.replace("  quantity:", """  settled_at: {kind: date, headers: [settled_at]}
  quantity:""").replace("kpis:\n", """kpis:
  - {name: settle_hours, label: Hours to settle, unit: number, unit_word: hours,
     num: [[{sum: [settled_at, hours, txn_date]}]], den: [[{nunique: account_id}]]}
""")


def test_a_duration_in_hours_reads_the_load_times_and_never_a_negative():
    """F3 (Domain 7): a sub-day duration, `[end, hours, start]` beside `days` (an IoT repair's
    start to finish): exact hours through the load's time rule (UTC offsets included), a negative
    span (finish before start) is no duration."""
    assert Pack.model_validate(__import__("yaml").safe_load(SETTLE)).kpi("settle_hours")
    df = pd.DataFrame({"account_id": ["A", "B", "C"], "amount": [1.0, 2.0, 3.0],
                       "txn_date": ["2026-10-24T22:00:00Z", "2026-10-25T00:30:00Z", "2026-10-25T09:00:00Z"],
                       "settled_at": ["2026-10-25T01:30:00Z", "2026-10-25T02:00:00Z", "2026-10-25T08:00:00Z"]})
    roles = {c: c for c in df.columns}
    hours = domain_pack.pack._column(df, roles, ["settled_at", "hours", "txn_date"])
    assert hours.tolist()[:2] == [3.5, 1.5] and pd.isna(hours.iloc[2])
    with pytest.raises(ValueError, match="hours"):
        Pack.model_validate(__import__("yaml").safe_load(SETTLE.replace("hours, txn_date", "minutes, txn_date")))
