"""Protected roles (Domain 5, S2): a group by a protected attribute, or a protected measure
(pay) by any group, of fewer than 11 distinct people is never shown, on any data (not only
patient data), and is counted in people, never rows."""
import pandas as pd
import pytest
import yaml

from agents.visualization_agent import filtered_dashboard
from domain_pack import Pack, compute_kpi
from guardrails import claim_small_cell

TOY = """
domain: toystaff
signals:
  - {who: [person_id], start: [start_date]}
roles:
  snapshot_date: {kind: date, headers: [snapshot_date]}
  start_date:    {kind: date, headers: [start_date]}
  end_date:      {kind: date, headers: [end_date]}
  person_id:     {kind: id, headers: [person_id]}
  team:          {kind: category, headers: [team]}
  gender:        {kind: category, headers: [gender]}
  pay:           {kind: money, headers: [pay]}
spell: {entity: person_id, start: start_date, end: end_date}
protected_roles: [gender, pay]
kpis:
  - {name: people, label: People, unit: count, num: [[{spell: active}]]}
  - {name: avg_pay, label: Average pay, unit: money, num: [[{spell: active, sum: pay}]],
     den: [[{kpi: people}]]}
caveats:
  read_as_domain_weak: "Read as staff data: {roles}."
"""
ROLES = {r: r for r in ("start_date", "end_date", "person_id", "team", "gender", "pay")}


@pytest.fixture
def pack(monkeypatch):
    monkeypatch.setenv("ANALYSTAI_TODAY", "2026-11-01")
    return Pack.model_validate(yaml.safe_load(TOY))


def _staff():
    """Big: 30 people (15 women); Tiny: 4 in force now + 12 who left long ago (16 rows)."""
    rows = [(f"B{i}", "2020-01-01", "", "Big", "Female" if i % 2 else "Male", 100.0 + i)
            for i in range(30)]
    rows += [(f"T{i}", "2021-01-01", "", "Tiny", "Female", 90.0) for i in range(4)]
    rows += [(f"X{i}", "2019-01-01", "2020-06-30", "Tiny", "Male", 80.0) for i in range(12)]
    rows += [("Z", "2026-10-31", "", "Big", "Male", 1.0)]   # the as-of
    return pd.DataFrame(rows, columns=["person_id", "start_date", "end_date", "team", "gender", "pay"])


def test_pay_of_a_group_under_11_people_in_force_is_hidden(pack):
    by = compute_kpi(_staff(), ROLES, pack, "avg_pay", by="team")
    assert "Big" in by.index and "Tiny" not in by.index   # 16 rows, 4 people in force


def test_a_headcount_by_an_unprotected_group_is_shown(pack):
    by = compute_kpi(_staff(), ROLES, pack, "people", by="team")
    assert by.to_dict() == {"Big": 31, "Tiny": 4}


def test_a_headcount_by_a_protected_group_under_11_is_hidden(pack):
    df = _staff()
    tiny = df[df["team"] == "Tiny"]
    by = compute_kpi(tiny, ROLES, pack, "people", by="gender", full=df)
    assert by is None or "Female" not in by.index   # 4 women in force


def test_the_dashboard_shows_nothing_for_a_protected_part_under_11_people(pack, monkeypatch):
    import agents.visualization_agent as viz
    from orchestrator.state import DataContract
    monkeypatch.setattr(viz, "pack_for", lambda d: pack if d == "toystaff" else None)
    contract = DataContract.model_construct(domain="toystaff", domain_confidence="weak",
                                            roles=ROLES, phi=False, columns=[])
    df = _staff()
    out = filtered_dashboard(df, contract, [], {"team": {"kind": "equality", "value": "Tiny"},
                                                "gender": {"kind": "equality", "value": "Female"}})
    assert out["suppressed"] and out["rows"] is None and not out["tiles"]
    women = {"gender": {"kind": "equality", "value": "Female"}}
    assert not viz._protected_part(df, viz.apply_filters(df, women), contract, women)   # 15 + 4


def test_a_sentence_naming_a_small_protected_group_is_flagged_and_a_plain_count_is_not():
    nouns = ["women", "men", "female employees", "asian employees"]
    assert claim_small_cell("Only 2 women in Legal left this year.", nouns)
    assert claim_small_cell("3 senior Asian employees were promoted.", nouns)
    assert claim_small_cell("Finance lost 3 employees in March.", nouns) is None
