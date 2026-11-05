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


def test_a_year_over_year_answer_on_a_protected_pack_never_crashes():
    """Audit pass 3: a reused name made compute_kpi(cut=True) raise on insurance YoY answers."""
    from tests.test_oracle import answer
    for params in (dict(group_by="State"), dict(segment_column="Insured Gender", segment_value="F")):
        text, _ = answer("policies", "top_n", "q", kpi="written_premium", compare="yoy",
                         period_start="2025-01-01", period_end="2025-06-30", **params)
        assert "A year earlier" in text, text   # compared, not only 'no comparison'


def test_a_crafted_filter_on_any_domains_id_or_personal_column_shows_nothing():
    """Audit pass 4: relaxing the refused filters to 'the person key' let one patient's claims
    through on healthcare (no person key there). Every id and personal column is refused, but a
    pack's declared grouping dimension (a store number) stays a cross-filter."""
    from agents.visualization_agent import filtered_dashboard
    from domain_pack import pack_for
    from tests.test_dashboard_filters import PACK_DATASETS, _dashboard
    for name in PACK_DATASETS:
        clean, contract, charts, _, _ = _dashboard(name)
        pack = pack_for(contract.domain)
        dims = {contract.roles.get(r) for r in pack.overview_groups}
        for col in (set(contract.id_columns) | set(contract.pii_columns)) - dims:
            if col not in clean.columns or clean[col].dropna().empty:
                continue
            value = str(clean[col].dropna().iloc[0])
            out = filtered_dashboard(clean, contract, charts, {col: {"kind": "equality", "value": value}})
            assert out["suppressed"] and not out["tiles"], (name, col)


def test_a_split_chart_loses_a_small_cells_counts_and_spread_too():
    """Audit pass 3: a hidden series kept its __n and __ci beside it (gender counts under 11)."""
    from agents.visualization_agent import _without_small_cells, build_context, group_bars
    _, clean, contract, _ = _built("hr_roster")
    ctx = build_context(clean, contract=contract)
    chart = group_bars(clean, "Department", "Annual_Salary", "mean", ctx, split="Gender")
    out = _without_small_cells(chart, ctx)
    for row in (out.data if out else []):
        assert "Non-binary__n" not in row or row["Non-binary__n"] >= 11, row
        assert set(row) - {"Department"}, row   # no empty bar


def test_two_date_ranges_that_dont_overlap_show_nothing():
    from agents.visualization_agent import filtered_dashboard
    from tests.test_dashboard_filters import _dashboard
    clean, contract, charts, _, _ = _dashboard("hr_roster")
    out = filtered_dashboard(clean, contract, charts, {
        "Hire_Date": {"kind": "date_range", "from": "2024-01-01", "to": "2024-12-31"},
        "Termination_Date": {"kind": "date_range", "from": "2026-01-01", "to": "2026-03-31"}})
    assert out["rows"] == 0 and not out["tiles"]


def test_the_partial_month_is_decided_on_the_packs_own_dates_never_a_birth_date():
    """Audit pass 4: Date_of_Birth (the column with the most dates) decided the partial month."""
    import asyncio

    from agents.cleaning_agent import run_cleaning_agent
    from tests.test_oracle import _parquet
    s, clean, contract, _ = _built("employees")
    df = clean.assign(date_of_birth=pd.to_datetime("1980-01-01"))
    df.loc[df.index[0], "hire_date"] = pd.Timestamp("2026-12-15")   # one pre-boarded hire
    s.__dict__["_df_parquet"] = _parquet(df)
    _, audit = asyncio.run(run_cleaning_agent(s))
    assert audit.partial_months.get("hire_date") == ["2026-10", 24], audit.partial_months
    assert "date_of_birth" not in audit.partial_months


def test_the_fallback_chart_never_calls_a_layout_blank_missing():
    """Audit pass 5: the fallback had its own partial copy of the layout rule (marker fields only):
    Charge Off Date, Debit/Credit, Case Reserve were 'missing'. It reads domain_pack.layout_blanks."""
    from agents.visualization_agent import _contract_fallback_charts
    from domain_pack import layout_blanks
    from tests.test_dashboard_filters import PACK_DATASETS
    for name in PACK_DATASETS:
        _, clean, contract, _ = _built(name)
        charts = _contract_fallback_charts(contract, True, clean)
        shown = {r["label"] for c in charts for r in c.data}
        assert not shown & set(layout_blanks(clean, contract)), (name, shown & set(layout_blanks(clean, contract)))


def test_the_fallback_reads_the_frame_it_describes_and_keeps_markers_without_one():
    """Audit pass 6: with a frame the bars were the contract's pre-cleaning shares; without one
    the marker fields (an end date of someone still employed) were 'missing' again."""
    from agents.visualization_agent import _contract_fallback_charts
    from domain_pack import marker_columns, pack_for
    _, clean, contract, _ = _built("hr_roster")
    for row in _contract_fallback_charts(contract, True, clean)[0].data:
        assert row["count"] == round(float(clean[row["label"]].isna().mean() * 100), 1), row
    bare = _contract_fallback_charts(contract)
    shown = {r["label"] for c in bare for r in c.data}
    assert not shown & set(marker_columns(pack_for("hr"), contract.roles))


# ── the $0 senior audit over every domain's answer paths (before 'done') ───────────────────────

def test_a_groups_size_counts_the_people_the_kpi_counts_never_its_contractors():
    """Non-binary attrition in 2023 was shown: 11 in force, one a contractor the KPI leaves out."""
    from tests.test_oracle import answer
    text, _ = answer("hr_roster", "segment_compare", "q", kpi="attrition_rate", segment_column="Gender",
                     segment_value="Non-binary", period_start="2023-01-01", period_end="2023-12-31")
    assert "isn't shown" in text and "%" not in text.split("isn't shown")[0][-30:], text


def test_a_filter_or_a_group_on_a_pay_value_is_one_persons_pay():
    from tests.test_oracle import answer
    text, _ = answer("hr_snapshots", "segment_compare", "q", kpi="headcount",
                     segment_column="Base_Salary", segment_value="84600.0")
    assert "isn't shown" in text or "can't" in text, text
    text, _ = answer("employees", "segment_compare", "q", kpi="avg_salary", segment_column="salary")
    assert "84300" not in text and "$84,300" not in text, text   # never a pay value as a label


def test_a_rosters_pay_total_by_department_is_current_staff():
    """'Total salary by department' summed leavers' pay on a roster (37.2M vs 22.0M payroll)."""
    from tests.test_oracle import answer
    _, _, _, truth = _built("hr_roster")
    _, by = answer("hr_roster", "segment_compare", "Average salary by department",
                   metric_column="Annual_Salary", segment_column="Department", aggregation="mean")
    assert abs(by["Engineering"] - truth["avg_salary_by_dept_asof"]["Engineering"]) < 1, by


def test_a_kpi_never_reports_a_group_it_leaves_out_by_definition():
    from tests.test_oracle import answer
    text, by = answer("hr_roster", "top_n", "headcount by worker type", kpi="headcount",
                      group_by="Worker_Type")
    assert "Contractor" not in by and "1 employees" not in text, (by, text)
