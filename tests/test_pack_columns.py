"""A column the product adds to the data at ingestion (Pack.columns, Domain 6 S1): a pack's
formula of several roles (a subscription's MRR from its price, seats, discount and billing
interval), so every reader (KPIs, charts, filters, the model's frame) reads one number. The toy
pack here is the second reader beside saas.yaml."""
import pandas as pd
import pytest
import yaml

from domain_pack import Pack, pack_columns

TOY = """
domain: toyrent
signals:
  - {unit: [unit_id], rent: [rent]}
roles:
  unit_id:  {kind: id, headers: [unit_id]}
  moved_in: {kind: date, headers: [moved_in]}
  rent:     {kind: money, headers: [rent]}
  units:    {kind: count, headers: [units]}
  rebate:   {kind: rate, headers: [rebate, rebate_pct, rebate_share],
             scales: {rebate_pct: percent, rebate_share: fraction}}
  term:     {kind: category, headers: [term], numbers: {monthly: 1, quarterly: 3, yearly: 12}}
  terms:    {kind: count, headers: [terms]}
  monthly_rent: {kind: money, headers: [monthly_rent]}
columns:
  monthly_rent: {times: [rent, units], net_of: rebate, over: [term, terms],
                 optional: [units, rebate, terms]}
caveats:
  read_as_domain_weak: "Read as rental data: {roles}."
"""


@pytest.fixture
def pack():
    return Pack.model_validate(yaml.safe_load(TOY))


def test_a_formula_of_several_roles_is_one_column(pack):
    df = pd.DataFrame({"unit_id": ["a", "b", "c", "d", "e"],
                       "rent": ["1,200", "300", "90", "50", "10"],
                       "units": [1, 2, None, 1, 1], "rebate": [0.25, None, 0, 0, 0],
                       "term": ["Yearly", "monthly", "quarterly", "weekly", "monthly"],
                       "terms": [1, 1, 1, 1, 2]})
    out, notes = pack_columns(df, pack, {r: r for r in df.columns})
    got = out["monthly_rent"].tolist()
    assert got[:3] == [75.0, 600.0, 30.0] and pd.isna(got[3]) and got[4] == 5.0
    assert notes == ["monthly_rent: 1 row has a value of term the formula doesn't know (weekly): left blank."]


def test_a_missing_required_role_or_an_own_column_adds_nothing(pack):
    df = pd.DataFrame({"unit_id": ["a"], "rent": [10.0]})
    assert pack_columns(df, pack, {"unit_id": "unit_id", "rent": "rent"})[0].columns.tolist() \
        == ["unit_id", "rent"]   # no term: no monthly rent, never a guess
    own = pd.DataFrame({"unit_id": ["a"], "rent": [10.0], "term": ["yearly"], "monthly_rent": [9.0]})
    out, _ = pack_columns(own, pack, {r: r for r in own.columns})
    assert out["monthly_rent"].tolist() == [9.0]   # the user's own column is never replaced


def test_every_finished_pack_adds_no_column():
    from domain_pack import load_packs
    assert all(not p.columns for p in load_packs() if p.domain != "saas")


def test_a_term_times_a_constant_is_a_run_rate_of_the_same_kind():
    """ARR = 12 × MRR (Domain 6 S1b): `times` scales a term; a KPI of a state KPI is a state
    ('as of 31 Oct', never '12 months to'), and a string where a number belongs is refused."""
    from tests.test_spell import ROLES, SUBS, _roster
    from domain_pack import compute_kpi, window_words
    raw = yaml.safe_load(SUBS)
    raw["kpis"] += [{"name": "seats_x12", "label": "Annual seats", "num": [[{"kpi": "seats", "times": 12}]]}]
    p = Pack.model_validate(raw)
    df = _roster()
    assert compute_kpi(df, ROLES, p, "seats_x12") == 12 * compute_kpi(df, ROLES, p, "seats")
    assert p.kpi("seats_x12").state and window_words(p, df, ROLES, "seats_x12").startswith("as of")
    raw["kpis"][-1]["num"][0][0]["times"] = "1/12"
    with pytest.raises(ValueError):
        Pack.model_validate(raw)


def test_a_percent_off_in_percent_is_read_as_a_rate(pack):
    """Audit pass 1 (Spec M2): Stripe's percent_off is 0-100; 20 means 20%, never 2,000%."""
    df = pd.DataFrame({"unit_id": ["a", "b"], "rent": [100.0, 100.0], "rebate": [20, None],
                       "term": ["monthly", "monthly"]})
    out, _ = pack_columns(df, pack, {r: r for r in df.columns})
    assert out["monthly_rent"].tolist() == [80.0, 100.0]


def _rents(pack, column, values):
    df = pd.DataFrame({"unit_id": [str(i) for i in range(len(values))], "rent": 100.0,
                       column: values, "term": "monthly"})
    out, notes = pack_columns(df, pack, {"unit_id": "unit_id", "rent": "rent", "rebate": column,
                                         "term": "term"})
    return [None if pd.isna(x) else round(x, 6) for x in out["monthly_rent"]], notes


def test_a_declared_percent_header_is_read_as_percent_whatever_its_values(pack):
    """C7 (ROADMAP 5.7b-2 step 6, PR1-07): Stripe's percent_off 0.5 is 0.5% off; the values'
    guess read it as 50%. A declared header is read at its scale, its bound said."""
    assert _rents(pack, "rebate_pct", [0.5, 20, 1, 0]) == ([99.5, 80.0, 99.0, 100.0], [])
    assert _rents(pack, "rebate_pct", [20, 150, -5]) == ([80.0, None, None], [
        "monthly_rent: 2 rows of rebate_pct disagree with the column's percent scale: left blank."])
    assert _rents(pack, "rebate_share", [0.2, 1, 5]) == ([80.0, 0.0, None], [
        "monthly_rent: 1 row of rebate_share disagree with the column's rate scale: left blank."])


def test_an_undeclared_discount_that_is_neither_whole_percents_nor_fractions_is_said_unreadable(pack):
    """PR1-07: a bare discount of 12.50 (an amount, or 12.5%?) was read as a percent; PR3-06: a
    column of only 1s (100% or 1%?) is unreadable too. Each is blank and said, never guessed."""
    assert _rents(pack, "rebate", [12.5, 10, 0]) == ([None, None, 100.0], [
        "monthly_rent: rebate has values over 1 with decimals: a percent can't be told from an "
        "amount, so those rows are left blank."])
    assert _rents(pack, "rebate", [1, 1, 0]) == ([None, None, 100.0], [
        "monthly_rent: rebate holds only 1s: its scale (100% or 1%) is unclear, so those rows "
        "are left blank."])
    assert _rents(pack, "rebate", [20, 1, 0.5, 200, 30]) == ([80.0, 99.0, None, None, 70.0], [
        "monthly_rent: 2 rows of rebate disagree with the column's percent scale: left blank."])
    assert _rents(pack, "rebate", [0.25, 0.5, 0]) == ([75.0, 50.0, 100.0], [])


def test_a_declared_percent_header_fits_its_rate_role_and_an_undeclared_one_must_read_0_to_1(pack):
    """Detection binds a rate role by its values (0–1), unless the header declares its scale: a
    percent_off of 20 still gets its role, and rate_values then says any value off its scale."""
    from domain_pack.detect import _fits
    r = pack.roles["rebate"]
    assert _fits(pd.Series([20.0, 10.0]), r, pack, False, "rebate_pct")
    assert not _fits(pd.Series([20.0, 10.0]), r, pack, False, "rebate")
    assert _fits(pd.Series([0.2, 0.1]), r, pack, False, "rebate")


def test_a_scale_names_a_header_of_its_role():
    raw = yaml.safe_load(TOY)
    raw["roles"]["rebate"]["scales"] = {"discount": "percent"}
    with pytest.raises(ValueError, match="scales"):
        Pack.model_validate(raw)


def test_every_blank_or_unreadable_input_is_said(pack):
    """Review: a blank interval (or price) left MRR blank with no note; only an unknown category
    was said. Every required input: its blank rows and its unreadable ones, counted."""
    df = pd.DataFrame({"unit_id": list("abcdef"), "rent": ["100", None, "n/a", "50", "50", "50"],
                       "term": ["monthly", "monthly", "monthly", None, None, "fortnightly"]})
    out, notes = pack_columns(df, pack, {r: r for r in df.columns})
    assert out["monthly_rent"].notna().tolist() == [True, False, False, False, False, False]
    assert notes == ["monthly_rent: 1 row has no rent: left blank.",
                     "monthly_rent: 1 row has a value of rent the formula doesn't know (n/a): left blank.",
                     "monthly_rent: 2 rows have no term: left blank.",
                     "monthly_rent: 1 row has a value of term the formula doesn't know (fortnightly): "
                     "left blank."]


def test_an_unreadable_optional_input_is_said_never_defaulted(pack):
    """Audit after PR #1: a blank optional input takes its default (no rebate = 0), but an
    unreadable one ('ten', 'n/a') became the default too, silently: a wrong rent with no note.
    Unreadable is never blank: the row is left blank and said, required or optional."""
    df = pd.DataFrame({"unit_id": list("abc"), "rent": ["100", "100", "100"],
                       "units": [1, "two", None], "term": ["monthly"] * 3})
    out, notes = pack_columns(df, pack, {r: r for r in df.columns})
    assert out["monthly_rent"].tolist()[0] == 100 and pd.isna(out["monthly_rent"].iloc[1])
    assert out["monthly_rent"].iloc[2] == 100   # blank optional: its default, 1 unit
    assert notes == ["monthly_rent: 1 row has a value of units the formula doesn't know (two): left blank."]
