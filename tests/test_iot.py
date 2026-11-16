"""Domain 7 (IoT / manufacturing): the pack's own rules, on its datasets and small frames.
Definitions: docs/research/2026-11-16-iot.md; the plan's traps T1–T21."""
import pandas as pd
import pytest

import domain_pack as dp

IOT = dp.pack_for("iot")
WO_ROLES = {"work_order": "wonum", "work_type": "worktype", "status": "status",
            "actual_start": "actstart", "actual_finish": "actfinish", "asset": "assetnum"}


def _wos() -> pd.DataFrame:
    return pd.DataFrame({
        "wonum": ["W1", "W2", "W3", "W4", "W5"], "assetnum": ["P1", "P1", "P2", "P2", "P2"],
        "worktype": ["CM", "EM", "PM", "CM", "CM"], "status": ["COMP", "CLOSE", "COMP", "INPRG", "CAN"],
        "actstart": ["2026-10-24T23:30:00Z", "2026-10-01T08:00:00Z", "2026-10-02T08:00:00Z",
                     "2026-10-03T08:00:00Z", None],
        "actfinish": ["2026-10-25T02:15:00Z", "2026-10-01T09:00:00Z", "2026-10-02T12:00:00Z",
                      None, None]})


def test_a_derived_role_counts_as_present_wherever_a_kpi_is_judged_computable():
    """MTTR reads repair hours, derived from the actual start and finish. `measure` derived it,
    `computable` didn't: MTTR was offered nowhere (no tile choice, no intake match)."""
    assert dp.computable(IOT, "mttr", WO_ROLES)
    assert not dp.computable(IOT, "mttr", {k: v for k, v in WO_ROLES.items() if k != "actual_finish"})


def test_readings_through_the_production_path_are_the_truth():
    """T5–T10, T17 on machine_readings (CSV, the production reader and cleaning): °F converted,
    Bad and coded values blanked, double writes gone, the 6 clashing times kept and flagged, the
    fold of 25 Oct 2026 named as a clock change, S03 stuck, S05 silent. The line means = truth."""
    from tests.test_oracle import _built
    _, clean, contract, truth = _built("machine_readings")
    by_line = clean.groupby("Line")["Temperature"].mean()
    assert by_line.to_dict() == pytest.approx(truth["mean_temp_c_by_line"], rel=1e-9)
    assert len(clean) == truth["row_count"] - truth["exact_duplicates"]
    flags = {d.strategy: d.reason for d in _built("machine_readings")[0].cleaning_audit.decisions
             if d.operation == "flag"}
    assert flags["series_clash"].startswith(f"{truth['clashing_timestamps']} times")
    assert "2026-10-25 02:00" in flags["series_clock_change"]
    assert "S03 temperature at 68.4" in flags["series_stuck"] and "S05" in flags["series_gap"]
    assert "S01" not in flags["series_gap"] and "S03" not in flags["series_gap"]


def test_a_shift_report_and_a_work_order_log_are_no_series():
    """Many rows per machine and day by design: no clash, gap or stuck flag there."""
    from tests.test_oracle import _built
    for name in ("shift_production", "work_orders"):
        decisions = _built(name)[0].cleaning_audit.decisions
        assert not [d for d in decisions if d.strategy.startswith("series_")], name


def test_one_sensors_reading_over_time_is_a_control_chart_with_its_limits_said():
    """F8 (U2): S07 drifts +0.05 °C a day (T21). One series' continuous measure over time is an
    individuals chart of daily means: CL and limits x̄ ± 3·MR̄/1.128 from its first 30 days,
    drawn as reference lines, the days outside counted, and said to be control (not spec)
    limits on autocorrelated readings."""
    from tests.test_oracle import _built
    from agents.targeted_analysis_agent import handle_time_series
    from orchestrator.state import TargetedParams
    _, clean, contract, _ = _built("machine_readings")
    r = handle_time_series(clean, TargetedParams(metric_column="Temperature", segment_column="Sensor",
                                                 segment_value="S07"), contract)
    chart = r.chart_configs[0]
    lines = {x["label"]: x["value"] for x in chart.reference_lines}
    s07 = clean[clean["Sensor"] == "S07"].assign(day=lambda d: pd.to_datetime(d["Timestamp"]).dt.normalize())
    daily = s07.groupby("day")["Temperature"].mean().iloc[:30]
    mr = daily.diff().abs().mean()
    assert lines == pytest.approx({"CL": daily.mean(), "UCL": daily.mean() + 3 * mr / 1.128,
                                   "LCL": daily.mean() - 3 * mr / 1.128})
    assert len(chart.data) >= 90 and "control limits" in r.answer_text.lower()
    assert "%" not in r.answer_text   # °C has no true zero: changes in degrees, never percent
    assert "not specification limits" in r.answer_text and "days above the upper limit" in r.answer_text
    stuck = clean.assign(Temperature=68.4)   # S-d: no variation, no limits (a stuck instrument?)
    r = handle_time_series(stuck, TargetedParams(metric_column="Temperature", segment_column="Sensor",
                                                 segment_value="S07"), contract)
    assert not r.chart_configs[0].reference_lines and "no variation" in r.answer_text


def test_how_a_kpi_was_computed_names_derived_columns_and_unit_steps_in_words():
    """$0 answer rehearsal: MTTR's 'How' was ', divided by .' (the derived repair hours had no
    words) and MTBF's read '0.0166667 × run time' (minutes to hours)."""
    how = dp.describe_kpi(IOT, "mttr", WO_ROLES)
    assert "hours from actstart to actfinish" in how and "divided by distinct wonum" in how, how
    shifts = {"planned_minutes": "Planned", "unplanned_downtime": "Down", "failures": "Failures"}
    how = dp.describe_kpi(IOT, "mtbf", shifts)
    assert "run time ÷ 60" in how.lower() and "0.0166" not in how, how
    how = dp.describe_kpi(IOT, "oee", {**shifts, "ideal_cycle_s": "Cycle", "good_count": "Good"})
    assert "cycle × good ÷ 60" in how.lower(), how   # seconds × units → minutes, said


def test_mttr_reads_finished_corrective_orders_in_exact_hours():
    """T3/T4/T19, S-c: a PM is no repair, an open order has no finish yet, a cancelled one is no
    work; a repair across the 25 Oct 2026 clock change is 2.75 h in UTC. (2.75 + 1) / 2."""
    assert dp.compute_kpi(_wos(), WO_ROLES, IOT, "mttr") == 1.875
    assert dp.compute_kpi(_wos(), WO_ROLES, IOT, "corrective_wos") == 3   # W1, W2, W4; W5 cancelled
