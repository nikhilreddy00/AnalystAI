"""Domain 7 (IoT / manufacturing): the pack's own rules, on its datasets and small frames.
Definitions: docs/research/2026-11-16-iot.md; the plan's traps T1–T21."""
import pandas as pd

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


def test_mttr_reads_finished_corrective_orders_in_exact_hours():
    """T3/T4/T19, S-c: a PM is no repair, an open order has no finish yet, a cancelled one is no
    work; a repair across the 25 Oct 2026 clock change is 2.75 h in UTC. (2.75 + 1) / 2."""
    assert dp.compute_kpi(_wos(), WO_ROLES, IOT, "mttr") == 1.875
    assert dp.compute_kpi(_wos(), WO_ROLES, IOT, "corrective_wos") == 3   # W1, W2, W4; W5 cancelled
