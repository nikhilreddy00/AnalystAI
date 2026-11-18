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


THERMO = BANK.replace("  quantity:", """  temp: {kind: number, additive: false, headers: [temp]}
  temp_unit: {kind: category, headers: [temp_unit]}
  quantity:""") + """units:
  temp: {unit_role: temp_unit, to: "°C", convert: {"°F": [0.5555555555555556, -17.77777777777778],
                                                   degf: [0.5555555555555556, -17.77777777777778],
                                                   "deg F": [0.5555555555555556, -17.77777777777778]}}
"""


def test_a_measure_in_another_unit_is_converted_by_its_unit_column_and_said():
    """F7 (Domain 7, the user's call U1): a site's sensors report °F beside °C ones; averaged
    together the mean was ~20 degrees off. Cleaning converts by each row's unit column (exact,
    logged), never by guessing from the values (C7): without a unit column nothing changes."""
    from agents.cleaning_agent import CleanContext, clean
    pack = Pack.model_validate(__import__("yaml").safe_load(THERMO))
    df = pd.DataFrame({"account_id": list("ABCD"), "amount": [1.0] * 4,
                       "temp": [70.0, 158.0, 212.0, 71.0], "temp_unit": ["°C", "°F", "DegF ", "°C"]})
    roles = {c: c for c in df.columns}
    out, audit = clean(CleanContext(df=df, ids={"account_id"}, dates=set(), targets=set(),
                                    drop_high_null=False, pack=pack, roles=roles))
    assert out["temp"].round(6).tolist() == [70.0, 70.0, 100.0, 71.0]
    assert out["temp_unit"].tolist() == ["°C"] * 4
    said = [d.reason for d in audit.decisions if d.column == "temp" and "converted to °C" in d.reason]
    assert len(said) == 2 and "in °F" in said[0] and "in degf" in said[1]   # one per unit code
    out, _ = clean(CleanContext(df=df.drop(columns="temp_unit"), ids={"account_id"}, dates=set(),
                                targets=set(), drop_high_null=False, pack=pack,
                                roles={c: c for c in df.columns if c != "temp_unit"}))
    assert out["temp"].tolist() == [70.0, 158.0, 212.0, 71.0]
    with pytest.raises(ValueError, match="units"):
        Pack.model_validate(__import__("yaml").safe_load(THERMO.replace("unit_role: temp_unit", "unit_role: nope")))


SERIES = THERMO + "series_key: account_id\nstuck_hours: {temp: 24}\n"


def _series_frame() -> pd.DataFrame:
    t = list(pd.date_range("2026-10-20", periods=96, freq="h"))
    rows = [{"account_id": "S1", "txn_date": x, "amount": 1.0, "temp": 70.0 + (i % 5) * 0.1,
             "temp_unit": "°C"} for i, x in enumerate(t)]
    rows += [{"account_id": "S2", "txn_date": x, "amount": 1.0, "temp": 68.4 if 10 <= i < 40 else 70.0 + (i % 3) * 0.2,
              "temp_unit": "°C"} for i, x in enumerate(t) if not 60 <= i < 80]   # stuck 30 h, a 20 h gap
    df = pd.DataFrame(rows)
    copy, clash = df.iloc[[5, 6]], df.iloc[[7]].assign(temp=99.0)
    return pd.concat([df, copy, clash], ignore_index=True)


def test_a_series_double_write_goes_and_its_gaps_clashes_and_stuck_runs_are_said():
    """F4 (Domain 7): a historian writes a row twice (an exact copy: dropped and said); a time
    with two different values, a 20 h silence and a value stuck for 30 h are flagged, kept, and
    reach the report's alerts. Read per series, never across them."""
    from agents.cleaning_agent import CleanContext, clean
    from agents.insight_agent import _alerts
    from orchestrator.state import AnalysisSession
    pack = Pack.model_validate(__import__("yaml").safe_load(SERIES))
    df = _series_frame()
    roles = {c: c for c in df.columns}
    out, audit = clean(CleanContext(df=df, ids={"account_id"}, dates={"txn_date"}, targets=set(),
                                    drop_high_null=False, pack=pack, roles=roles))
    assert len(out) == len(df) - 2
    flags = {d.strategy: d.reason for d in audit.decisions if d.operation == "flag"}
    assert "1 time" in flags["series_clash"] and "S1" in flags["series_clash"]
    assert "S2" in flags["series_gap"] and "(21 h)" in flags["series_gap"]   # 20 hourly rows missing
    assert "S2" in flags["series_stuck"] and "68.4" in flags["series_stuck"] and "for 29 h" in flags["series_stuck"]
    assert "S1" not in flags["series_stuck"] + flags["series_gap"]
    alerts = " ".join(a.description for a in _alerts(AnalysisSession(session_id="t", cleaning_audit=audit))[0])
    assert all(flags[k] in alerts for k in ("series_clash", "series_gap", "series_stuck"))
    out, audit = clean(CleanContext(df=df, ids={"account_id"}, dates={"txn_date"}, targets=set(),
                                    drop_high_null=False, pack=Pack.model_validate(
                                        __import__("yaml").safe_load(THERMO)), roles=roles))
    assert len(out) == len(df) and not any(d.strategy.startswith("series_") for d in audit.decisions)


def test_a_closed_vocabulary_says_its_unknown_values_and_a_selection_does_not():
    """closed_words' second reader: a toy pack whose 'channel' every value should be named
    (branch / online) says an unknown one; without the declaration the same list is a selection
    (its other values by design) and says nothing."""
    from agents.cleaning_agent import CleanContext, clean
    toy = BANK.replace("  quantity:", "  channel: {kind: category, headers: [channel]}\n  quantity:").replace(
        "kpis:\n", "kpis:\n  - {name: online, label: Online, num: [[{sum: amount, where: "
        "[[{role: channel, in: [online, web]}]]}]]}\n  - {name: branch, label: Branch, num: "
        "[[{sum: amount, where: [[{role: channel, in: [branch]}]]}]]}\n")
    df = pd.DataFrame({"account_id": list("ABC"), "amount": [1.0, 2.0, 3.0],
                       "channel": ["online", "branch", "phone"]})
    roles = {c: c for c in df.columns}

    def said(yaml_text):
        _, audit = clean(CleanContext(df=df, ids={"account_id"}, dates=set(), targets=set(),
                                      drop_high_null=False, roles=roles,
                                      pack=Pack.model_validate(__import__("yaml").safe_load(yaml_text))))
        return [d.reason for d in audit.decisions if d.strategy == "unlisted_values"]
    assert said(toy) == []
    assert said(toy + "closed_words: [channel]\n") == [
        "1 rows have a channel no word list of the pack names: phone (1): left out of online, branch."]


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
    from domain_pack.kpi import Unavailable, measure, unavailable_words, zero_note
    pack = Pack.model_validate(__import__("yaml").safe_load(PER_UNIT))
    df = pd.DataFrame({"account_id": ["A", "A", "B", "C"], "amount": [10.0, 20.0, 50.0, 0.0],
                       "quantity": [1, 2, 0, 0]})
    roles = {c: c for c in df.columns}
    got = measure(df, roles, pack, "per_unit", by="account_id")
    assert got.values.to_dict() == {"A": 10.0} and dict(got.zero) == {"B": 50.0, "C": 0.0}
    # C has nothing on either side (a machine never run): not measurable, never "none in 0"
    assert zero_note(pack.kpi("per_unit"), got.zero, str) == \
        " B: none counted against 50.0. Not measurable, its base is 0: C."
    plain = measure(df, roles, pack, "per_unit_plain", by="account_id")
    assert zero_note(pack.kpi("per_unit_plain"), plain.zero, str) == \
        " Not measurable, its base is 0: B, C."
    only_zero = measure(df[df.account_id != "A"], roles, pack, "per_unit", by="account_id")
    assert only_zero == Unavailable("zero_denominator", zero=50.0)
    # said in the KPI's words when there is a numerator and the KPI has them, else generic
    k = pack.kpi("per_unit")
    assert [unavailable_words(u, pack, kpi) for u, kpi in (
        (only_zero, k), (only_zero, None), (Unavailable("zero_denominator"), k),
        (Unavailable("role_missing", roles=("quantity",)), k))] == [
        "none counted against 50", "its base is 0", "its base is 0", "the data has no quantity"]
    # one figure: B's 50 over 0 keeps its numerator; C's 0 over 0 has none
    assert [measure(df[df.account_id == a], roles, pack, "per_unit") for a in "BC"] == [
        Unavailable("zero_denominator", zero=50.0), Unavailable("zero_denominator")]


def test_a_small_protected_group_with_a_zero_base_is_hidden_not_said():
    """F9 review: the zero-base groups skipped the small-cell rule, so a protected group of 2
    people with no base had its numerator said. Every group is checked first."""
    from domain_pack.kpi import Unavailable, measure
    pack = Pack.model_validate(__import__("yaml").safe_load(
        PER_UNIT + "protected_roles: [status]\nperson_key: account_id\n"))
    people = [f"P{i}" for i in range(14)]
    df = pd.DataFrame({"account_id": people, "amount": [10.0] * 14,
                       "quantity": [1] * 12 + [0, 0], "status": ["posted"] * 12 + ["held"] * 2})
    got = measure(df, {c: c for c in df.columns}, pack, "per_unit", by="status")
    assert "held" in got.hidden and got.zero == ()
    # every group small (one valued, one at a 0 base): hidden, never its 0 base said
    assert measure(df[df.status == "held"].assign(quantity=[1, 0]), {c: c for c in df.columns}, pack,
                   "per_unit", by="status") == Unavailable("small_cell")
    # the complement of a hidden value is another value, never a zero base: with one small
    # valued group (V, 3 people) beside 2 large ones and a small zero-base group, the next
    # smallest VALUED group is hidden with it
    pay = Pack.model_validate(__import__("yaml").safe_load(   # amount as a protected measure (pay)
        PER_UNIT + "protected_roles: [amount]\nperson_key: account_id\n"))
    groups = ["V"] * 3 + ["L"] * 12 + ["M"] * 15 + ["Z"] * 2
    df = pd.DataFrame({"account_id": [f"P{i}" for i in range(32)], "amount": [10.0] * 32,
                       "quantity": [1] * 30 + [0, 0], "status": groups})
    got = measure(df, {c: c for c in df.columns}, pay, "per_unit", by="status")
    assert set(got.hidden) == {"V", "L", "Z"} and list(got.values.index) == ["M"]


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


OVERDUE = BANK.replace("  quantity:", """  due_date: {kind: date, headers: [due_date]}
  days_overdue: {kind: number, additive: false, headers: [days_overdue]}
  quantity:""").replace("kpis:\n", """derived: {days_overdue: [as_of, days, due_date]}
kpis:
  - {name: overdue, label: Overdue, unit: count, num: [[{nunique: account_id, where: [[{role: days_overdue, gt: 0}]]}]]}
""")


def test_an_open_item_is_overdue_as_of_the_whole_datas_last_date():
    """Domain 8 (L1), a second reader of the as-of operand: a bill due 10 Jan is overdue as of
    the data's last posting (20 Jan), whichever part is asked; the as-of is no role of the data
    and only a day span may start from it."""
    pack = Pack.model_validate(__import__("yaml").safe_load(OVERDUE))
    df = pd.DataFrame({"account_id": ["A", "B", "C"], "amount": [1.0, 2.0, 3.0],
                       "txn_date": ["2026-01-05", "2026-01-20", "2026-01-06"],
                       "due_date": ["2026-01-10", "2026-01-25", "2026-01-30"]})
    roles = {c: c for c in ("account_id", "amount", "txn_date", "due_date")}
    assert domain_pack.compute_kpi(df, roles, pack, "overdue") == 1.0           # A only
    part = df[df["account_id"] == "A"]   # alone, A's last posting (5 Jan) is before its due date
    assert domain_pack.compute_kpi(part, roles, pack, "overdue", full=df) == 1.0
    assert domain_pack.computable(pack, "overdue", roles) and not domain_pack.computable(
        pack, "overdue", {k: v for k, v in roles.items() if k != "txn_date"})
    with pytest.raises(ValueError, match="as_of"):
        Pack.model_validate(__import__("yaml").safe_load(
            OVERDUE.replace("[as_of, days, due_date]", "[as_of, hours, due_date]")))
