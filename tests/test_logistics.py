"""Domain 8 (logistics): the pack's own rules, on its datasets and small frames.
Definitions: docs/research/2026-11-18-logistics.md; the plan's traps T1–T20."""
import pandas as pd
import pytest

import domain_pack as dp

LOGI = dp.pack_for("logistics")
SHIP = {"date": "ship_date", "promised_date": "promised", "delivered": "delivered",
        "shipment": "id", "status": "status"}
DOCK = {"date": "appt", "arrived": "arr", "departed": "dep", "appointment": "id", "status": "status"}


def _ships(*rows) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["id", "ship_date", "promised", "delivered", "status"])


def test_none_late_is_a_measured_rate_not_an_unknown_one():
    """D1-40 (C5): a span blank on every row because no row qualifies (no shipment late: days
    late is blank on each) is no missing column. On-time was read off every shipment instead
    (the denominator fell to its last alternative): 0.5 for 1 of 1."""
    df = _ships(("S0", "2026-01-01", None, "2026-01-01", "Delivered"),
                ("S1", "2026-01-01", "2026-01-02", "2026-01-01", "Delivered"))
    assert dp.compute_kpi(df, SHIP, LOGI, "on_time_delivery") == 1.0


def test_every_delivery_late_is_zero_percent_on_time():
    """D1-40 (C5): no row meets a numeric condition anywhere (not one shipment on time) is a
    measured 0%, never "unknown" — that answer is for a kind of row an export may omit."""
    df = _ships(("S0", "2026-01-01", "2026-01-02", "2026-01-05", "Delivered"),
                ("S1", "2026-01-01", "2026-01-02", "2026-01-04", "Delivered"))
    assert dp.compute_kpi(df, SHIP, LOGI, "on_time_delivery") == 0.0
    assert dp.compute_kpi(df, SHIP, LOGI, "avg_days_late") == 2.5


def test_no_long_dwell_is_zero_percent_over_two_hours():
    df = pd.DataFrame({"id": ["A1", "A2"], "status": ["Completed"] * 2,
                       "appt": ["2026-06-01 08:00"] * 2, "arr": ["2026-06-01 07:50"] * 2,
                       "dep": ["2026-06-01 09:00", "2026-06-01 08:30"]})
    assert dp.compute_kpi(df, DOCK, LOGI, "visits_over_2h") == 0.0


TRUTH = {   # dataset → {kpi: truth key}
    "tms_shipments": {"on_time_delivery": "on_time_rate", "avg_transit_days": "avg_transit_days",
                      "cost_per_shipment": "cost_per_shipment", "cost_per_lb": "cost_per_lb",
                      "shipments": "shipments", "open_shipments": "open_shipments",
                      "returned_shipments": "returned", "avg_days_late": "avg_days_late",
                      "fuel_share": "fuel_share"},
    "order_lines": {"otif_line": "otif_line", "otif_order": "otif_order", "unit_fill": "unit_fill",
                    "line_fill": "line_fill", "orders": "orders"},
    "dock_appointments": {"avg_dwell": "avg_dwell_hours", "on_time_arrival": "on_time_arrival",
                          "visits_over_2h": "visits_over_2h", "no_shows": "no_shows",
                          "visits": "visits"},
}


@pytest.mark.parametrize("name", list(TRUTH))
def test_each_dataset_through_the_production_path_is_the_truth(name):
    """T1–T20 on the three datasets (CSV, the production reader and cleaning): LB/KG converted,
    a blank unit left out of cost per lb, CAD rows out (one population), cancelled out, early
    deliveries on time, a delivery before its ship date no span, returned shipments not on time,
    no-shows apart, an overnight dwell exact."""
    from tests.test_oracle import _built
    _, clean, contract, truth = _built(name)
    assert contract.domain == "logistics"
    roles = {r: c for r, c in contract.roles.items() if c in clean.columns}
    got = {k: dp.compute_kpi(clean, roles, LOGI, k) for k in TRUTH[name]}
    assert got == pytest.approx({k: truth[t] for k, t in TRUTH[name].items()}, rel=1e-9)


@pytest.mark.parametrize("by,truth_key,kpi", [("Carrier", "on_time_by_carrier", "on_time_delivery"),
                                               ("Mode", "transit_by_mode", "avg_transit_days"),
                                               ("Mode", "cost_per_lb_by_mode", "cost_per_lb")])
def test_shipments_by_group_are_pooled_per_group(by, truth_key, kpi):
    """T1: each carrier's own on-time (its delivered on time ÷ its own base), never a mean."""
    from tests.test_oracle import _built
    _, clean, contract, truth = _built("tms_shipments")
    roles = {r: c for r, c in contract.roles.items() if c in clean.columns}
    got = dp.compute_kpi(clean, roles, LOGI, kpi, by=by)
    assert got.to_dict() == pytest.approx(truth[truth_key], rel=1e-9)
    if kpi == "on_time_delivery":
        assert dp.compute_kpi(clean, roles, LOGI, kpi) != pytest.approx(truth["naive_mean_of_carrier_rates"])


def test_the_old_shipments_file_pairs_its_flag_with_its_own_base():
    """RT-11: alternatives are picked per side; a flag numerator (Σ on_time) must divide by all
    shipments (e2e-34's pooled carrier X), never by a delivered base it doesn't have."""
    from evals import datagen
    from tests.test_oracle import _built
    _, clean, contract, truth = _built("shipments")
    roles = {r: c for r, c in contract.roles.items() if c in clean.columns}
    got = dp.compute_kpi(clean, roles, LOGI, "on_time_delivery", by="carrier")
    assert got.to_dict() == pytest.approx(truth["pooled_ontime"], rel=1e-9)
    df, _ = datagen.make("shipments", 1)
    assert dp.compute_kpi(clean, roles, LOGI, "cost_per_lb") == pytest.approx(
        df["cost"].sum() / (df["weight_kg"] * 2.20462).sum(), rel=1e-9)


@pytest.mark.parametrize("roles,want", [(DOCK, "on_time_arrival"), (SHIP, "on_time_delivery")])
def test_the_same_words_name_the_kpi_this_data_can_compute(roles, want):
    """RT-7 (D1-16, C6): "on-time rate" is on-time delivery on a shipment file and on-time
    arrival on a dock log; one word routed to one KPI asked a dock log for a promised date."""
    hits = dp.glossary_hits(LOGI, "What is our on-time rate by carrier?", [], roles=roles)
    assert [c for c, _ in hits] == [want]
    assert [c for c, _ in dp.glossary_hits(LOGI, "on-time rate by carrier", [])] == ["on_time_delivery"]


def test_an_answer_names_a_signed_difference_it_was_counted_on():
    """$0 rehearsal: 'OTIF (lines): 85.4%. How: , divided by …' — a derived difference (shipped −
    ordered) had no name, so the numerator's words were empty."""
    roles = {"date": "Order Date", "promised_date": "Requested Date", "delivered": "Delivered Date",
             "order_line_id": "Order Line ID", "qty_ordered": "Qty Ordered", "qty_shipped": "Qty Shipped",
             "status": "Status"}
    how = dp.describe_kpi(LOGI, "otif_line", roles)
    assert how.startswith("Distinct Order Line ID where") and "Qty Shipped − Qty Ordered ≥ 0" in how


def test_a_line_number_is_a_label_never_a_measure():
    """Golden, D4-21 (C7): 'Line No trended up 11.4% (agg=sum)' — a line number restarting in
    each order (10, 20, 30) was summed by month. The pack names it a category."""
    from tests.test_oracle import _built
    _, _, contract, _ = _built("order_lines")
    col = next(c for c in contract.columns if c.name == "Line No")
    assert contract.roles.get("line_number") == "Line No" and not col.is_metric


def test_an_id_is_never_a_chart_breakdown():
    """$0 browser read, D4-21 (C7): the overview drew 'Line No Breakdown — 10 Leads at 33%': a
    line number the contract calls an id was still a few-valued category to the charts."""
    from agents.visualization_agent import build_context
    from tests.test_oracle import _built
    _, clean, contract, _ = _built("order_lines")
    roles = build_context(clean, contract=contract).roles
    assert "Line No" in contract.id_columns
    assert not any("Line No" in v for k, v in roles.items() if isinstance(v, list) and k != "id_cols")


def test_the_brief_names_every_population_the_kpis_read():
    """D1-20 (gate, logi-5 1/3): L1 counted lines open past their request as failed, but the
    brief still said 'delivered lines only' and the model followed it. Each population a KPI's
    where reads (past due, returned, release, the cancelled kind) is in the brief."""
    brief = LOGI.say("rows_brief")
    for said in ("still open whose promised date is before", "still open whose requested date is before",
                 "returned to sender", "left the warehouse or were backordered",
                 f"row_kind '{LOGI.code('cancel')}'"):
        assert said in brief, said


SHIP_DUE = {**SHIP, "date": "ship_date"}


def test_past_due_is_signed_for_the_range_check_and_never_replaces_a_users_column():
    """Mutation gate (derived_roles): cleaning reads spans signed, so a promise after the as-of
    is a negative number there (blank for a KPI); a column of the user's named like a derived
    role is never overwritten."""
    from domain_pack.columns import derived_roles
    df = _ships(("S0", "2026-01-01", "2026-01-03", None, "In Transit"),
                ("S1", "2026-01-10", "2026-01-20", None, "In Transit"))
    signed, roles = derived_roles(df, SHIP_DUE, LOGI, signed=True)
    assert signed[roles["past_due_days"]].tolist() == [7.0, -10.0]
    unsigned, _ = derived_roles(df, SHIP_DUE, LOGI)
    assert unsigned["days_past_due"].iloc[0] == 7.0 and pd.isna(unsigned["days_past_due"].iloc[1])
    mine = df.assign(days_past_due=["x", "y"])
    kept, roles = derived_roles(mine, SHIP_DUE, LOGI)
    assert kept["days_past_due"].tolist() == ["x", "y"] and "past_due_days" not in roles


def test_a_promise_column_blank_on_every_row_is_unknown_never_a_rate():
    """Mutation gate (_blank): a span's input column empty everywhere (an export without
    promise dates) can't be measured: on-time is unknown, never a number from another base."""
    df = _ships(("S0", "2026-01-01", None, "2026-01-02", "Delivered"),
                ("S1", "2026-01-01", None, "2026-01-03", "Delivered"))
    v = dp.measure(df, SHIP, LOGI, "on_time_delivery")
    assert isinstance(v, dp.Unavailable) and v.reason == "blank_column"


def test_the_as_of_is_named_in_words_in_answers_and_the_brief():
    roles = {**SHIP_DUE, "carrier": "carrier"}
    assert "days from promised to the data's last date > 0" in dp.describe_kpi(LOGI, "on_time_delivery", roles)
    brief = "\n".join(dp.pack_brief(LOGI, roles, []))
    assert "Column days_past_due is in the data for you: days from promised_date to the data's last date" in brief
    no_promise = {k: v for k, v in roles.items() if k != "promised_date"}
    assert "days_past_due" not in "\n".join(dp.pack_brief(LOGI, no_promise, []))
    own = "\n".join(dp.pack_brief(LOGI, {**roles, "past_due_days": "overdue"}, []))
    assert "Column days_past_due is in the data" not in own


def test_the_longest_phrase_names_the_concept():
    """Mutation gate (_glossary_matches): 'shipments in transit' is open shipments, never the
    shipment count; 'line fill rate' is line fill, never unit fill."""
    assert [c for c, _ in dp.glossary_hits(LOGI, "How many shipments in transit by carrier?", [])] == \
        ["open_shipments"]
    assert [c for c, _ in dp.glossary_hits(LOGI, "line fill rate by warehouse", [])] == ["line_fill"]


def test_a_tracking_number_and_a_plate_are_masked_by_their_names_alone():
    from guardrails import is_pii_column
    assert is_pii_column("tracking_number") and is_pii_column("license_plate")
    assert not is_pii_column("tracking_events")


def test_without_a_status_column_on_time_is_never_a_delivered_only_rate():
    """Spec review: with no status, open shipments can't be told apart, and on-time fell to a
    delivered-only rate while its note said past-due ones count late. Unavailable instead."""
    df = _ships(("S0", "2026-01-01", "2026-01-03", "2026-01-02", "Delivered"),
                ("S1", "2026-01-01", "2026-01-03", None, "In Transit")).drop(columns="status")
    roles = {k: v for k, v in SHIP.items() if k != "status"}
    assert dp.compute_kpi(df, roles, LOGI, "on_time_delivery") is None


def _domain(csv: str):
    from tests.test_retail_pack import _contract, _load
    return _contract(_load(csv)).domain


@pytest.mark.parametrize("csv", [
    # a fulfilment export with prices (RT-12: retail's SKU + quantity + price + order shape)
    "Order ID,SKU,Quantity Ordered,Qty Shipped,Unit Price,Revenue,Order Date\n"
    "O1,A-1,4,4,20,80,2026-01-02\nO2,A-2,2,1,15,15,2026-01-03\n",
    # research §7 header variants: a PRO number, a SCAC, a committed and an actual delivery date
    "PRO Number,SCAC,Ship Date,Committed Date,Actual Delivery Date,Total Charges\n"
    "P1,ODFL,2026-01-02,2026-01-05,2026-01-05,410.5\nP2,SAIA,2026-01-03,2026-01-06,2026-01-08,388\n",
    "Load ID,Carrier Name,Pickup Date,Delivery Date,Weight (lbs)\n"
    "L1,J.B. Hunt,2026-02-01,2026-02-03,41000\nL2,XPO,2026-02-02,2026-02-05,38000\n",
    "Appt ID,Facility,Appointment Time,Check In Time,Check Out Time,Driver\n"
    "A1,Reno DC,2026-06-01 08:00,2026-06-01 07:52,2026-06-01 09:40,Ann Lee\n"
    "A2,Reno DC,2026-06-01 09:00,2026-06-01 09:20,2026-06-01 12:05,Raj Patel\n",
])
def test_logistics_exports_read_as_logistics(csv):
    assert _domain(csv) == "logistics"


@pytest.mark.parametrize("csv", [
    # an insurer's carrier is no freight carrier: a policy file stays insurance or nothing
    "Policy ID,Carrier,Effective Date,Written Premium\nP1,Chubb,2026-01-01,1200\n"
    "P2,Travelers,2026-02-01,900\n",
    # a store's sales with a ship date are retail
    "Order ID,SKU,Quantity,Unit Price,Revenue,Ship Date\nO1,A-1,1,20,20,2026-01-02\n"
    "O2,A-2,2,15,30,2026-01-03\n",
])
def test_other_domains_with_a_logistics_word_are_not_logistics(csv):
    assert _domain(csv) != "logistics"


def test_a_shipment_open_past_its_promise_is_late():
    """U1 (research §2.1, trap 7): a delivered-only on-time rate overstates; a shipment still open
    after its promised date, as of the data's last ship date, is late. Not yet due: out."""
    from tests.test_oracle import _built
    _, clean, contract, truth = _built("tms_shipments")
    roles = {r: c for r, c in contract.roles.items() if c in clean.columns}
    assert dp.compute_kpi(clean, roles, LOGI, "on_time_delivery") == pytest.approx(
        truth["on_time_rate"], rel=1e-9)
    assert truth["on_time_rate"] < truth["naive_open_left_out"]


def test_an_order_line_open_past_its_request_fails_otif():
    from tests.test_oracle import _built
    _, clean, contract, truth = _built("order_lines")
    roles = {r: c for r, c in contract.roles.items() if c in clean.columns}
    got = {k: dp.compute_kpi(clean, roles, LOGI, k) for k in ("otif_line", "otif_order")}
    assert got == pytest.approx({"otif_line": truth["otif_line"], "otif_order": truth["otif_order"]})
    assert truth["otif_order"] < truth["naive_otif_order_delivered_only"]


def test_a_filter_never_moves_the_as_of_date():
    """RT-3: carrier A's last shipment left on 5 Jan, the data runs to 20 Jan: A's shipment due
    10 Jan and still open is late as of the data, not 'not yet due' as of A's own rows."""
    df = _ships(("A1", "2026-01-02", "2026-01-04", "2026-01-03", "Delivered"),
                ("A2", "2026-01-05", "2026-01-10", None, "In Transit"),
                ("B1", "2026-01-20", "2026-01-23", None, "In Transit"))
    df["carrier"] = ["A", "A", "B"]
    roles = {**SHIP, "carrier": "carrier"}
    part = df[df["carrier"] == "A"]
    assert dp.compute_kpi(part, roles, LOGI, "on_time_delivery", full=df) == 0.5
    assert dp.compute_kpi(df, roles, LOGI, "on_time_delivery", by="carrier").to_dict() == {"A": 0.5}


def test_a_kind_of_row_the_export_may_omit_is_still_unknown():
    """The other side of the rule: no open shipment anywhere is unknown (an export of delivered
    shipments only), never "0 open"."""
    df = _ships(("S0", "2026-01-01", "2026-01-02", "2026-01-02", "Delivered"))
    assert dp.compute_kpi(df, SHIP, LOGI, "open_shipments") is None
