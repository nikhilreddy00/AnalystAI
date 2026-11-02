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
    monkeypatch.setattr(domain_pack, "PACKS", tmp_path)
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
    audit = SimpleNamespace(decisions=[], partial_months={}, main_currency=None,
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
