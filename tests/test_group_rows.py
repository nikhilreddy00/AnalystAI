"""D7-19: a count or a sum by group reads the rows the tiles count (domain_pack.rows_for), from
the model's code and the generic charts alike. GLOSSARY: Counted record, Kind-deciding column."""
import json
from pathlib import Path

import pytest

from tests.test_oracle import _built

REPORTS = Path(__file__).parents[1] / "evals" / "reports"
from tests.test_dashboard_filters import PACK_DATASETS  # noqa: E402

# the generic count charts: (category key in a data row, its count key)
COUNT_CHARTS = {"composition_donut": ("name", "value"), "composition_horizontal_bar": (None, "count"),
                "value_counts": (None, "count")}


@pytest.mark.parametrize("name", PACK_DATASETS)
def test_every_generic_count_chart_counts_the_records_the_tiles_count(name):
    """D7-19, truth-free over every pack: a generic breakdown chart's bars are the counted records
    per value (rows_for count=True), never every row. Fails for any builder that counts the
    frame it was handed."""
    from domain_pack import rows_for
    from tests.test_dashboard_filters import _dashboard
    clean, contract, charts, *_ = _dashboard(name)
    for c in (c for c in charts if c.source_finding in COUNT_CHARTS and c.rationale):
        col = c.rationale.x
        key, val = COUNT_CHARTS[c.source_finding]
        want = rows_for(clean, contract, col, count=True)[0][col].dropna().astype(str).value_counts()
        got = {str(r[key or col]): r[val] for r in c.data}
        assert got == {k: int(want[k]) for k in got}, (name, c.title)


def _stats(raw: dict, name: str, segments: list[str], query: str = "overview") -> dict:
    from agents.analysis_agent import standard_stats
    from orchestrator.state import AnalysisTask
    _, df, contract, _ = _built(name)
    standard_stats(raw, df, contract, AnalysisTask(raw_query=query, target_metrics=[], segments=segments,
                                                   analysis_types=["eda"]))
    return raw


def test_a_models_kpi_by_group_is_the_products():
    """logi-7 gate (analysis-20261118T161409Z, $0): the model's cost per lb by mode kept
    cancelled shipments (Parcel 1.4027). A segment labelled with a pack KPI takes the product's
    per-group value (domain_pack.measure by=), as tiles and answers do. No 'segmentation' asked:
    an overview's labelled segment is replaced too."""
    run = json.loads((REPORTS / "analysis-20261118T161409Z.json").read_text())["runs"]["logi-7"][0]
    seg = next(s for s in run["output"]["segments"] if s.get("label") == "cost_per_lb")
    raw = _stats({"segments": [seg]}, "tms_shipments", ["Mode"])
    got = {r["name"]: r["value"] for r in raw["segments"][0]["results"]}
    assert got == pytest.approx(_built("tms_shipments")[3]["cost_per_lb_by_mode"], abs=0.0005)
    assert any("cost_per_lb" in a or "Cost per lb" in a for a in raw["assumptions"])


def test_a_label_with_its_own_window_or_qualifier_stays_the_models():
    """$0 replay (scripts/replay_breakdowns.py): a glossary match turned 'on-time for LTL' and
    'spend_2026_08' into the all-time KPI, 16 stored runs flipped to fail. Only the KPI's exact
    name is replaced; never a breakdown by a date (a trend)."""
    truth = _built("tms_shipments")[3]
    ltl =[{"name": k, "value": round(v, 6)} for k, v in truth["on_time_by_carrier_ltl"].items()]
    raw = _stats({"segments": [{"segment_column": "Carrier", "metric_column": "Delivered At", "aggregation": "ratio",
                                "label": "on_time_delivery_ltl", "results": [dict(r) for r in ltl]},
                               {"segment_column": "Ship Date", "metric_column": "Delivered At", "aggregation": "ratio",
                                "label": "on_time_delivery", "results": [{"name": "2026-08-03", "value": 0.5}]}]},
                 "tms_shipments", ["Carrier"])
    assert raw["segments"][0]["results"] == ltl and raw["segments"][1]["results"][0]["value"] == 0.5


def test_a_count_the_model_filtered_on_purpose_is_left_as_written():
    """Only a plain every-row count is the bug: 'cancelled work orders by machine' is the model's
    own filter and answers its question."""
    _, df, _, _ = _built("work_orders")
    own = df[df["STATUS"].str.upper().isin(["CAN", "CANCELLED"])].groupby("ASSETNUM")["WONUM"].count()
    assert own.sum() > 0
    results = [{"name": k, "value": float(v)} for k, v in own.items()]
    raw = _stats({"segments": [{"segment_column": "ASSETNUM", "metric_column": "WONUM", "aggregation": "count",
                                "results": [dict(r) for r in results]}]}, "work_orders", ["ASSETNUM"])
    assert raw["segments"][0]["results"] == results


def test_a_count_by_group_reads_the_counted_records():
    """The generic Carrier donut counted 3,000 rows beside the 2,840-shipment tile: cancelled
    and other-currency rows are no shipments."""
    from domain_pack import rows_for
    _, df, contract, truth = _built("tms_shipments")
    rows, how = rows_for(df, contract, "Carrier", count=True)
    live = df[(df["Status"].str.lower() != "cancelled") & (df["Currency"] == "USD")]
    assert rows.groupby("Carrier").size().to_dict() == live.groupby("Carrier").size().to_dict()
    assert len(rows) == truth["shipments"] and "cancelled" in how


def test_a_count_by_the_column_that_decides_a_kind_keeps_that_kind():
    """The ruled-out one-filter fix: a Status breakdown exists to show the cancelled rows. Put
    back only the kind Status decides; other currencies stay out (as the tiles): 62 cancelled,
    4 of them in another currency."""
    from domain_pack import rows_for
    _, df, contract, truth = _built("tms_shipments")
    rows, _ = rows_for(df, contract, "Status", count=True)
    assert (rows["Status"].str.lower() == "cancelled").sum() == truth["cancelled"] - 4 == 58
    assert (rows["Currency"] == "USD").all()


def test_a_count_by_an_id_keeps_every_other_rule():
    """Doubt review of the first draft: judging kinds without the group column switched off its
    other rules: by Claim ID, health_claims counted 663 earlier versions (5,999 for 5,336); by
    Claim Number, loss_run counted every valuation (38,255 for 3,975). Only a column whose values
    name a kind (status, currency, a void or non-product code) puts that kind back."""
    from domain_pack import rows_for
    for name, col, counted in (("health_claims", "Claim ID", 5336), ("loss_run", "Claim Number", 3975)):
        _, df, contract, _ = _built(name)
        assert len(rows_for(df, contract, col, count=True)[0]) == counted, name


def test_a_kpi_label_on_a_status_breakdown_keeps_the_cancelled_group():
    """Doubt review: 'Order count by status' matched the orders KPI and dropped Cancelled (110),
    while 'Orders by Status' kept it. A breakdown by a kind's own column is a count, whatever
    its label."""
    _, df, _, _ = _built("order_lines")
    every = df.groupby("Status")["Order ID"].count()
    seg = {"segment_column": "Status", "metric_column": "Order ID", "aggregation": "count",
           "label": "order count by status", "results": [{"name": k, "value": float(v)} for k, v in every.items()]}
    raw = _stats({"segments": [seg]}, "order_lines", ["Status"])
    got = {r["name"]: r["value"] for r in raw["segments"][0]["results"]}
    assert got[next(k for k in got if k.lower() == "cancelled")] == 110


def test_group_names_written_as_column_equals_value_are_matched():
    """Doubt review: names 'Mode=LTL' wiped the segment while the note claimed it was replaced."""
    run = json.loads((REPORTS / "analysis-20261118T161409Z.json").read_text())["runs"]["logi-7"][0]
    seg = json.loads(json.dumps(next(s for s in run["output"]["segments"] if s.get("label") == "cost_per_lb")))
    for r in seg["results"]:
        r["name"] = f"Mode={r['name']}"
    raw = _stats({"segments": [seg]}, "tms_shipments", ["Mode"])
    assert len(raw["segments"][0]["results"]) == 3


def test_a_malformed_model_segment_never_crashes_the_run():
    raw = _stats({"segments": [{"segment_column": ["Mode"], "metric_column": "Weight", "aggregation": "count",
                                "results": [{"name": "LTL", "value": 1}]},
                               {"segment_column": "Mode", "metric_column": "Weight", "aggregation": "count",
                                "results": [{"name": "LTL", "value": "n/a"}]}]}, "tms_shipments", ["Mode"])
    assert len(raw["segments"]) == 2


def test_a_mean_of_a_number_a_kpi_averages_reads_the_kpis_rows():
    """Doubt review: counted() still required `additive`, so a mapped Transit Days column (a KPI
    averages it) kept cancelled rows in its mean by mode."""
    from domain_pack import pack_for
    from domain_pack.kpi import counted
    pack = pack_for("logistics")
    assert [r for r in ("transit_days", "dwell_hours") if not counted(pack, r)] == []


def test_a_categorys_distribution_answer_counts_the_counted_records():
    """'How are work types distributed?' counted all 640 orders (CM + EM 333); the answer reads
    the counted records and says what it left out. A number's distribution is unchanged."""
    from agents.targeted_analysis_agent import handle_distribution
    from orchestrator.state import TargetedParams
    _, df, contract, truth = _built("work_orders")
    r = handle_distribution(df, TargetedParams(target_columns=["WORKTYPE"]), contract)
    counts = {d["value"]: d["count"] for d in r.chart_configs[0].data}
    assert counts["CM"] + counts["EM"] == truth["corrective_wos"]
    n = truth["row_count"] - truth["cancelled_wos"]
    assert r.answer_text == (f"WORKTYPE: 3 unique values; top = 'PM' ({counts['PM']} rows, "
                             f"{counts['PM'] / n * 100:.1f}%). Left out, as the tiles count: "
                             f"{truth['cancelled_wos']} cancelled work orders.")


def test_a_sum_of_a_measure_a_kpi_sums_reads_the_kpis_rows():
    """D8 audit pass 1: 'sum of Weight by Mode' read cancelled rows (a weight at $0); cost per lb
    sums Weight over counted rows, so every total of Weight does."""
    from domain_pack import rows_for
    _, df, contract, truth = _built("tms_shipments")
    rows, how = rows_for(df, contract, "Weight")
    assert len(rows) == truth["shipments"] and how
