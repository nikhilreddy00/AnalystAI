"""Domain 7 (IoT) pack rules the mutation gate found unpinned: how a KPI is said (a derived
role, a 1/N multiplier as '÷ N', |x| sums, filters), the derived-column brief, first questions
that need a year, the roles a KPI reads and measures, a denominator's structural reason, the
zero-base groups' small-cell check and their note. Expected values are worked by hand."""
import pandas as pd
import pytest
import yaml

import domain_pack
from domain_pack import Pack, first_questions, pack_brief
from domain_pack.kpi import kpi_measures, kpi_roles, measure, zero_note
from domain_pack.story import describe_kpi
from tests.test_protected_cells import TOY
from tests.test_second_pack import BANK, PER_UNIT

LEFT_OUT = "; over sale rows (return rows, refunded rows, cancel rows, test rows, non-product rows left out)."
SCALED = BANK.replace("kpis:\n", """kpis:
  - {name: tenth, label: T, num: [[{sum: amount, times: 0.1}]]}
  - {name: half, label: H, num: [[{sum: amount, times: 0.5}]]}
  - {name: double, label: D, num: [[{sum: amount, times: 2}]]}
  - {name: pt3, label: P, num: [[{sum: amount, times: 0.3}]]}
  - {name: none, label: N, num: [[{sum: amount, times: 0}]]}
  - {name: near_one, label: O, num: [[{sum: amount, times: 1.0000000001}]]}
  - {name: abs_posted, label: A, num: [[{sum_abs: amount, where: [[{role: status, in: [posted]}]]}]]}
""")
BANK_ROLES = {r: r for r in ("account_id", "amount", "txn_date", "quantity", "status")}


@pytest.mark.parametrize("name, extra", [
    ("retail_lines", {"UOM": ["each", "case"]}),        # a unit of measure is no temperature unit
    ("b2b_invoices", {"Unit": ["EA", "CS"]}),
    ("card_ledger", {"Currency": ["USD", "EUR"]}),      # a currency is never converted
    ("loan_tape", {"Units": ["USD", "USD"]}),
])
def test_another_domains_unit_column_is_never_read_as_a_reading_unit(name, extra):
    """Spec review (U1 negatives): iot's temp_unit headers (uom, unit, units) never pull a retail
    or banking file into the iot pack, and their values are never converted."""
    from domain_pack.detect import detect
    from evals.datagen import make
    from tests.test_dashboard_filters import DATASETS_BY_PACK
    df, _ = make(name, 1)
    df = df.assign(**{c: (v * len(df))[:len(df)] for c, v in extra.items()})
    domain, _, _ = detect(df, {c for c in df.columns if "date" in c.lower()})
    assert domain != "iot" and name in DATASETS_BY_PACK[domain]   # no iot pack: no units rule


def test_stops_are_never_failures():
    """Spec (step 4): stops, micro-stops and downtime events are no failures: MTBF over stops
    would read every short stop as a breakdown."""
    from domain_pack.detect import detect
    from evals.datagen import make
    df, _ = make("shift_production", 1)
    assert detect(df, {"Production Date"})[1].get("failures") == "Failures"   # the control
    for col in ("Stops", "Stop Count", "Downtime Events"):
        domain, roles, _ = detect(df.rename(columns={"Failures": col}), {"Production Date"})
        assert domain == "iot" and "failures" not in roles, (col, roles)


@pytest.mark.parametrize("name, said", [
    ("deposits", "Sum of amount"),               # times 1: no multiplier at all
    ("tenth", "Sum of amount ÷ 10"),            # 0.1 = 1/10
    ("half", "Sum of amount ÷ 2"),              # 0.5 = 1/2
    ("double", "2 × sum of amount"),            # 2 is no 1/N
    ("pt3", "0.3 × sum of amount"),             # 1/0.3 rounds to 3, but 3 × 0.3 ≠ 1
    ("none", "0 × sum of amount"),              # never a division by 0
    ("near_one", "1 × sum of amount"),          # not exactly 1, and never '÷ 1'
    ("abs_posted", "Sum of |amount| where status is posted"),
])
def test_a_multiplier_and_a_sum_are_said_as_written(name, said):
    pack = Pack.model_validate(yaml.safe_load(SCALED))
    assert describe_kpi(pack, name, BANK_ROLES) == said + LEFT_OUT


def test_iot_ratios_name_their_derived_hours_and_unit_scale():
    iot = domain_pack.pack_for("iot")
    assert describe_kpi(iot, "oee", {"good_count": "good", "ideal_cycle_s": "ict",
                                     "planned_minutes": "pm"}).startswith(
        "Sum of ict × good ÷ 60, divided by sum of pm;")
    cm = ("wt is " + " / ".join(domain_pack.pack_for("iot").kpi("mttr").num[0][0].where[0][0].in_)
          + " and hours from s to f ≥ 0")   # the pack's corrective spellings (audit pass 1)
    assert describe_kpi(iot, "mttr", {"actual_start": "s", "actual_finish": "f",
                                      "work_order": "wo", "work_type": "wt"}).startswith(
        f"Sum of hours from s to f where {cm}, divided by distinct wo where {cm};")


@pytest.mark.parametrize("n, den", [
    (96, "status is {a} or status is {b}"),        # 120 characters: short enough to say twice
    (97, "the same conditions hold"),                # 121: an answer's How says it once
])
def test_an_answers_how_says_a_long_repeated_condition_once(n, den):
    a, b = "a" * 40, "b" * (n - 40)
    w = f"[[{{role: status, in: [{a}]}}], [{{role: status, in: [{b}]}}]]"
    pack = Pack.model_validate(yaml.safe_load(BANK.replace("kpis:\n", (
        f"kpis:\n  - {{name: r, label: R, num: [[{{sum: amount, where: {w}}}]], "
        f"den: [[{{nunique: account_id, where: {w}}}]]}}\n"))))
    num = f"status is {a} or status is {b}"
    assert describe_kpi(pack, "r", BANK_ROLES, parts=True) == (
        f"Sum of amount where {num}, divided by distinct account_id where {den.format(a=a, b=b)};"
        + LEFT_OUT.replace("; ", " ", 1))
    # a brief or a tile keeps both
    assert describe_kpi(pack, "r", BANK_ROLES).count(num) == 2


def test_mttrs_how_names_its_corrective_spellings_once():
    iot = domain_pack.pack_for("iot")
    how = describe_kpi(iot, "mttr", {"actual_start": "s", "actual_finish": "f", "work_type": "wt",
                                     "work_order": "wo"}, parts=True)
    assert " / failure and hours from s to f ≥ 0, divided by distinct wo where the same " \
           "conditions hold;" in how


def test_the_brief_says_how_each_derived_column_is_made():
    iot = domain_pack.pack_for("iot")
    assert pack_brief(iot, {"actual_start": "s", "actual_finish": "f"}, []) == [
        "Column repair_hours is in the data for you: exact hours from actual_start to "
        "actual_finish. Use it; never compute your own, and never report a KPI as if it were a column."]
    gap = Pack.model_validate(yaml.safe_load(BANK.replace(
        "  status:     {kind: status", "  gap:        {kind: number, headers: [gap]}\n  status:     {kind: status")
        + "derived: {gap: [amount, '-', quantity]}\n"))
    assert pack_brief(gap, BANK_ROLES, []) == [
        "Column gap is in the data for you: amount − quantity. Use it; never compute your own, "
        "and never report a KPI as if it were a column."]


def test_a_year_question_needs_a_date_column():
    pack = Pack.model_validate(yaml.safe_load(
        BANK + "first_questions:\n  - {q: 'Same month last year?', needs: [year]}\n"))
    df = pd.DataFrame({"account_id": ["A", "B"], "amount": [1.0, 2.0]})
    assert first_questions(pack, {"account_id": "account_id", "amount": "amount"}, df) == []


def test_the_roles_a_kpi_reads_and_measures():
    iot = domain_pack.pack_for("iot")
    assert kpi_measures(iot, "good_count") == {"good_count"}
    assert kpi_measures(iot, "oee") == {"ideal_cycle_s", "good_count", "planned_minutes"}
    # the status (a cancelled work order) and the derived hours' own start and finish
    assert kpi_roles(iot, "mttr") == {"repair_hours", "actual_start", "actual_finish",
                                      "work_type", "work_order", "status"}
    no_status = Pack.model_validate(yaml.safe_load(BANK.replace(
        "status_values: {ok: [posted], cancel: [reversed]}\n", "")))
    assert kpi_roles(no_status, "deposits") == {"amount"}


def test_an_operators_mttr_on_a_filtered_part_is_a_small_cell():
    """The whole data gets the derived hours too: a part asked by a protected role (one
    operator, 3 work orders) is checked against it and hidden."""
    iot = domain_pack.pack_for("iot")
    df = pd.DataFrame({
        "wonum": [f"W{i}" for i in range(14)], "worktype": ["CM"] * 14,
        "operator": ["Ann"] * 3 + ["Bob"] * 11,
        "actstart": ["2026-03-01 08:00"] * 14, "actfinish": ["2026-03-01 11:00"] * 14})
    roles = {"work_order": "wonum", "work_type": "worktype", "operator": "operator",
             "actual_start": "actstart", "actual_finish": "actfinish"}
    ann = df[df.operator == "Ann"]
    assert measure(ann, roles, iot, "mttr", full=df, cut=("operator",)).reason == "small_cell"
    assert measure(df, roles, iot, "mttr").value == 3.0   # 08:00 to 11:00


SPELLS = TOY.replace("kpis:\n", """kpis:
  - {name: ends, label: Exits, unit: count, num: [[{spell: ends}]]}
  - {name: starts, label: Hires, unit: count, num: [[{spell: starts}]]}
  - {name: exit_per_hire, label: Exits per hire, unit: number, num: [[{kpi: ends}]],
     den: [[{kpi: starts}]]}
  - {name: pay_per_exit, label: Pay per exit, unit: number, num: [[{sum: pay}]],
     den: [[{spell: ends}]]}
""")
STAFF = {r: r for r in ("start_date", "end_date", "person_id", "team", "gender", "pay")}


def _staff(rows):
    return pd.DataFrame(rows, columns=["person_id", "start_date", "end_date", "team", "gender", "pay"])


@pytest.fixture
def spells(monkeypatch):
    monkeypatch.setenv("ANALYSTAI_TODAY", "2026-11-01")
    return Pack.model_validate(yaml.safe_load(SPELLS))


def test_a_missing_end_date_outranks_a_blank_numerator(spells):
    """pay is blank on every row (a data reason); the exits need an end date the data has no
    column for (structural): the structural reason is said."""
    df = _staff([(f"P{i}", "2020-01-01", "", "A", "F", None) for i in range(3)])
    roles = {k: v for k, v in STAFF.items() if k != "end_date"}
    assert measure(df, roles, spells, "pay_per_exit").reason == "end_date_missing"


def test_a_zero_base_groups_people_are_counted_in_the_asked_window(spells):
    """F: 12 women since 2015, 6 left on 15 Feb 2026, no hires. M: 12 men since 2015, 3 hired
    on 1 Jul 2026 (one left 31 Aug), Z hired 31 Oct 2026: 1 exit per 4 hires. In Jun–Oct, F
    has no exit and no hire (a zero base) and 6 women in force: hidden. Over the default year
    (to 31 Oct) F has 12 women and 6 exits: said, never hidden."""
    rows = [(f"F{i}", "2015-01-01", "2026-02-15" if i < 6 else "", "A", "F", 1.0) for i in range(12)]
    rows += [(f"M{i}", "2015-01-01", "", "A", "M", 1.0) for i in range(12)]
    rows += [(f"N{i}", "2026-07-01", "2026-08-31" if i == 0 else "", "A", "M", 1.0) for i in range(3)]
    df = _staff(rows + [("Z", "2026-10-31", "", "A", "M", 1.0)])
    got = measure(df, STAFF, spells, "exit_per_hire", by="gender",
                  window=(pd.Timestamp("2026-06-01"), pd.Timestamp("2026-10-31")))
    assert got.values.to_dict() == {"M": 0.25} and got.hidden == ("F",) and got.zero == ()
    year = measure(df, STAFF, spells, "exit_per_hire", by="gender")
    assert year.values.to_dict() == {"M": 0.25} and year.hidden == () and year.zero == (("F", 6.0),)


def test_a_zero_base_groups_people_are_counted_on_the_whole_data(spells):
    """Team A to 31 Dec 2025; team B's hire on 31 Oct 2026 ends the whole data's year
    (Nov 2025 – Oct 2026). In it F (team A) has no exit and no hire and 6 women in force (6 left
    in Jun 2025): hidden. Team A alone ends its year on 31 Dec 2025, when 12 women were in force."""
    rows = [(f"F{i}", "2015-01-01", "2025-06-30" if i < 6 else "", "A", "F", 1.0) for i in range(12)]
    rows += [(f"M{i}", "2015-01-01", "", "A", "M", 1.0) for i in range(12)]
    rows += [(f"N{i}", "2025-12-31", "", "A", "M", 1.0) for i in range(3)]
    df = _staff(rows + [("Z", "2026-10-31", "", "B", "M", 1.0)])
    a = df[df.team == "A"]
    got = measure(a, STAFF, spells, "exit_per_hire", by="gender", full=df)
    assert got.values.to_dict() == {"M": 0.0} and got.hidden == ("F",) and got.zero == ()
    alone = measure(a, STAFF, spells, "exit_per_hire", by="gender")
    assert alone.hidden == () and alone.zero == (("F", 6.0),)


GENDERED = PER_UNIT.replace("  status:     {kind: status",
                            "  gender:     {kind: category, headers: [gender]}\n  branch:     "
                            "{kind: category, headers: [branch]}\n  status:     {kind: status")


def test_a_zero_base_group_of_a_protected_part_is_hidden():
    """A part asked by gender (cut): its branches are about women. Branch Z (2 women, no units)
    is a small cell: hidden, never said as a zero base. Branch G (12 women) is shown."""
    pack = Pack.model_validate(yaml.safe_load(
        GENDERED + "protected_roles: [gender]\nperson_key: account_id\n"))
    df = pd.DataFrame({"account_id": [f"P{i}" for i in range(14)], "amount": [10.0] * 14,
                       "quantity": [2] * 12 + [0, 0], "branch": ["G"] * 12 + ["Z"] * 2,
                       "gender": ["F"] * 14})
    roles = {c: c for c in df.columns}
    got = measure(df, roles, pack, "per_unit", by="branch", cut=("gender",))
    assert got.values.to_dict() == {"G": 5.0} and got.hidden == ("Z",) and got.zero == ()


def test_a_zero_base_small_group_is_hidden_alone():
    """Amount is protected (pay) by branch: the complement rule adds the next smallest VALUED
    group only when one valued group is hidden. Here none is: Z (2 people, no units) is hidden
    alone, and L (12) and M (15) are shown."""
    pack = Pack.model_validate(yaml.safe_load(
        GENDERED + "protected_roles: [amount]\nperson_key: account_id\n"))
    df = pd.DataFrame({"account_id": [f"P{i}" for i in range(29)], "amount": [10.0] * 29,
                       "quantity": [1] * 27 + [0, 0], "branch": ["L"] * 12 + ["M"] * 15 + ["Z"] * 2})
    got = measure(df, {c: c for c in df.columns}, pack, "per_unit", by="branch")
    assert got.values.to_dict() == {"L": 10.0, "M": 10.0} and got.hidden == ("Z",)


def test_two_zero_base_groups_are_said_in_one_sentence():
    kpi = Pack.model_validate(yaml.safe_load(PER_UNIT)).kpi("per_unit")
    assert zero_note(kpi, (("B", 50.0), ("C", 20.0)), str) == \
        " B: none counted against 50.0; C: none counted against 20.0."
    assert zero_note(kpi, (), str) == ""
