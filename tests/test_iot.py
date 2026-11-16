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


def test_mttr_reads_finished_corrective_orders_in_exact_hours():
    """T3/T4/T19, S-c: a PM is no repair, an open order has no finish yet, a cancelled one is no
    work; a repair across the 25 Oct 2026 clock change is 2.75 h in UTC. (2.75 + 1) / 2."""
    assert dp.compute_kpi(_wos(), WO_ROLES, IOT, "mttr") == 1.875
    assert dp.compute_kpi(_wos(), WO_ROLES, IOT, "corrective_wos") == 3   # W1, W2, W4; W5 cancelled
