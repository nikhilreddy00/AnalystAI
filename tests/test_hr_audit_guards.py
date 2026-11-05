"""Domain 5 audit pass 1 (Spec review): the guards the plan promised, each truth-free or against
the generator's independent truth."""
import pandas as pd
import pytest

import domain_pack
from domain_pack import compute_kpi, detect, exit_records, pack_for
from tests.test_oracle import _built


def _roles(contract, df):
    return {r: c for r, c in contract.roles.items() if c in df.columns}


def _monthly(df):
    return df.assign(_period=pd.Series(pd.NaT, index=df.index).astype("period[M]"))


def test_a_roster_and_its_month_end_snapshots_give_the_same_headcount_every_month():
    """Guard (b): the same people as spells and as snapshots (built from the spells: a row per
    month-end each is in force, the department of that spell) give equal headcount per month and
    by department. Known difference: none for a stock (a same-month hire and exit is never at a
    month-end in either)."""
    _, roster, contract, _ = _built("hr_roster")
    pack, roles = pack_for("hr"), _roles(contract, roster)
    by_month = compute_kpi(_monthly(roster), roles, pack, "headcount", by="_period", full=roster)
    rows = []
    for p in by_month.index[-14:]:
        t = p.end_time.normalize()
        now = domain_pack._in_force(roster, roles, pack, t)
        rows.append(now.assign(Snapshot_Date=t.strftime("%Y-%m-%d")))
    snaps = pd.concat(rows, ignore_index=True)
    # a month-end listing of people in force, each with that month's status: leavers vanish
    snaps["Termination_Date"], snaps["Employment_Status"] = "", "Active"
    snap_roles = {**roles, "snapshot_date": "Snapshot_Date"}
    s_month = compute_kpi(_monthly(snaps), snap_roles, pack, "headcount", by="_period", full=snaps)
    for p in s_month.index:
        assert s_month[p] == by_month[p], (p, s_month[p], by_month[p])
    a = compute_kpi(roster, roles, pack, "headcount", by="Department", full=roster)
    b = compute_kpi(snaps, snap_roles, pack, "headcount", by="Department", full=snaps)
    assert a.to_dict() == b.to_dict()


def test_exit_records_and_in_force_are_one_rule():
    """Guard (d): on the newest snapshot, the people in force are exactly those listed there
    whose row is no exit record (one ended test, domain_pack._ended, read by both)."""
    _, snaps, contract, _ = _built("hr_snapshots")
    pack, roles = pack_for("hr"), _roles(contract, snaps)
    as_of = domain_pack.spell_as_of(pack, snaps, roles)
    newest = snaps[pd.to_datetime(snaps["Snapshot_Date"]) == as_of]
    listed = set(newest.loc[~exit_records(newest, contract), "Emp_ID"])
    in_force = set(domain_pack._in_force(snaps, roles, pack, as_of)["Emp_ID"])
    assert listed == in_force and len(listed) > 300


def test_every_department_reconciles_on_a_roster():
    """Guard (a), per department (a roster has no transfers): opening + starts − ends = closing,
    every month, the only gap people who left at an unknown date."""
    _, roster, contract, _ = _built("hr_roster")
    pack, roles = pack_for("hr"), _roles(contract, roster)
    undated = domain_pack._ended(roster, roles, pack, pd.Timestamp.max.normalize()) & \
        roster["Termination_Date"].isna()
    for dept in roster["Department"].dropna().unique():
        part = roster[roster["Department"] == dept]
        frame = _monthly(part)
        act, st, en = (compute_kpi(frame, roles, pack, k, by="_period", full=roster)
                       for k in ("headcount", "hires", "separations"))
        if act is None:
            continue
        st, en = st.reindex(act.index, fill_value=0), en.reindex(act.index, fill_value=0)
        gap = (act - act.shift(1) - st + en).iloc[1:]
        # exactly the undated leavers (employees) hired inside the axis: a start never in force
        hired = pd.to_datetime(part["Hire_Date"])
        inside = undated[part.index] & (hired > act.index[0].end_time) & (hired <= act.index[-1].end_time) \
            & ~part["Worker_Type"].isin(["Contractor"])
        assert (gap <= 0).all() and -gap.sum() == int(inside.sum()), (dept, gap[gap != 0], int(inside.sum()))


def test_a_balance_after_a_product_change_counts_in_its_new_product_only():
    """Spec item 1 (B3): `at: latest` under a named-value filter: an account that moved from
    Card to Mortgage in March is not in Card's balance (snapshot_rows' stopped cut: its last Card
    row is older than the newest month). Kept as a guard: the plan's root fix proved unneeded."""
    pack = pack_for("banking")
    rows = []
    for m in ("2026-01-31", "2026-02-28", "2026-03-31"):
        rows.append(("A", m, "Mortgage" if m == "2026-03-31" else "Card", 100.0))
        rows.append(("B", m, "Card", 50.0))
    df = pd.DataFrame(rows, columns=["account_id", "as_of_date", "product", "balance"])
    roles = {"account": "account_id", "date": "as_of_date", "product": "product", "balance": "balance"}
    card = df[df["product"] == "Card"]
    assert compute_kpi(card, roles, pack, "total_balance", full=df) == 50.0


@pytest.mark.parametrize("frame, domain, how", [
    (pd.DataFrame({"transaction_id": ["T1", "T2", "T3"], "cashier_id": ["E1", "E2", "E1"],
                   "sku": ["A", "B", "A"], "quantity": [1, 2, 1], "unit_price": [9.5, 3.0, 9.5],
                   "order_date": ["2026-01-02", "2026-01-02", "2026-01-03"]}), "retail", None),
    (pd.DataFrame({"employee_id": ["E1", "E2", "E3"], "department": ["Ops", "Ops", "IT"],
                   "work_date": ["2026-01-02"] * 3, "hours": [8, 7.5, 8]}), "hr", "weak"),
    (pd.DataFrame({"employee_id": ["E1", "E2", "E3"], "hire_date": ["2020-01-01"] * 3,
                   "termination_date": ["", "", "2025-05-01"], "pto_balance": [10, 4, 0],
                   "department": ["Ops", "Ops", "IT"]}), "hr", "strong"),
    (pd.DataFrame({"claim_id": ["C1", "C2", "C3"], "employee_id": ["E1", "E2", "E3"],
                   "hire_date": ["2020-01-01"] * 3, "loss_date": ["2026-01-02"] * 3,
                   "case_reserve": [100.0, 0.0, 50.0], "paid": [10.0, 5.0, 0.0]}), "insurance", "strong"),
])
def test_hr_detection_beside_other_domains(frame, domain, how):
    """Spec item 10: a POS file by cashier stays retail; a timesheet is weak HR; an HR file with a
    PTO balance is HR (balance is not a veto alone); a workers'-comp loss run with an employee and
    a hire date stays insurance."""
    dates = {c for c in frame.columns if "date" in c}
    got_domain, _, got_how = detect(frame, dates)
    assert got_domain == domain and (how is None or got_how == how), (got_domain, got_how)


def test_no_other_domains_value_reads_as_a_national_insurance_number():
    """Standards item 16: the NINO value rule (column level) never fires on another domain's
    codes (SKUs, order and account numbers, claim ids)."""
    from evals import datagen
    from guardrails import pii_kind
    for name in datagen.DATASETS:
        raw, _ = datagen.make(name, 1)
        for col in raw.columns:
            vals = raw[col].dropna().astype(str).head(200).tolist()
            kind = pii_kind("ref_code", vals)
            assert not kind or kind[0] != "national_id", (name, col)


def test_dataset_values_in_the_facts_never_reach_a_rule_line():
    """Standards item 10: the spell facts name departments and locations (the data's own text):
    they go inside the untrusted-data block with the roles, never as a rule line."""
    import io

    from agents import analysis_agent
    from orchestrator.state import AnalysisTask
    s, clean, contract, _ = _built("hr_roster")
    buf = io.BytesIO()
    clean.to_parquet(buf, index=False)
    out = analysis_agent._domain(contract, AnalysisTask(raw_query="attrition by department",
                                                       analysis_types=["eda"], target_metrics=[]),
                                 s.cleaning_audit, buf.getvalue())
    assert "Support" in out["domain_roles"] and not any("Support" in x for x in out["domain"])


def test_the_semantic_layer_never_calls_a_leaver_share_attrition():
    import yaml
    from pathlib import Path
    layer = yaml.safe_load(Path("semantic_layers/hr.yaml").read_text())
    names = {m["name"] for m in layer["metrics"]}
    assert "leaver_share" in names and "attrition_rate" not in names
