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
    per value (domain_pack.count_rows), never every row. Fails for any builder that counts the
    frame it was handed."""
    from domain_pack import count_rows
    from tests.test_dashboard_filters import _dashboard
    clean, contract, charts, *_ = _dashboard(name)
    for c in (c for c in charts if c.source_finding in COUNT_CHARTS and c.rationale):
        col = c.rationale.x
        key, val = COUNT_CHARTS[c.source_finding]
        want = count_rows(clean, contract, col, None)[0][col].dropna().astype(str).value_counts()
        got = {str(r[key or col]): r[val] for r in c.data}
        assert got == {k: int(want[k]) for k in got}, (name, c.title)


def _stats(raw: dict, name: str, segments: list[str], query: str = "overview") -> dict:
    """standard_stats as a run calls it: on the model's frame (row kind and derived columns
    added; run_analysis_agent swaps df_parquet for it). logi-4's paid re-measure: on that frame
    measure() found no derived roles, so the KPI segment was emptied and the run had no findings."""
    from agents.analysis_agent import model_frame, standard_stats
    from orchestrator.state import AnalysisTask
    _, df, contract, _ = _built(name)
    standard_stats(raw, model_frame(df, contract)[0], contract, AnalysisTask(
        raw_query=query, target_metrics=[], segments=segments, analysis_types=["eda"]))
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


def test_a_kpi_built_on_derived_roles_is_the_products_on_the_models_frame():
    """logi-4 paid re-measure (analysis-20261119T181740Z, 0/3): on-time reads derived roles (days
    late, days past due); on the model's frame, which already holds those columns, measure() said
    role_missing, the segment was emptied, and the run failed 'every finding section is empty'."""
    truth = _built("tms_shipments")[3]["on_time_by_carrier"]
    raw = _stats({"segments": [{"segment_column": "Carrier", "metric_column": "Delivered At", "aggregation": "ratio",
                                "label": "on_time_delivery", "results": [{"name": k, "value": 0.5} for k in truth]}]},
                 "tms_shipments", ["Carrier"])
    assert {r["name"]: r["value"] for r in raw["segments"][0]["results"]} == pytest.approx(truth, abs=1e-6)


def test_a_segments_rows_behind_it_are_the_counted_records():
    """e2e-89 paid re-measure (e2e-20261119T184927Z, 0/3): the counts were recounted to 311, but
    each segment's evidence n was the sandbox's every-row count (333 with a failure code), and the
    report said 'among the 333 work orders with a Failure Code'. n is the counted records with a
    value in the segment's columns, for the model's own segments too."""
    from agents.analysis_agent import _parse_findings, build_evidence
    _, df, _, truth = _built("work_orders")
    every = df.groupby("FAILURECODE")["WONUM"].count()
    raw = _stats({"segments": [{"segment_column": "FAILURECODE", "metric_column": "WONUM", "aggregation": "count",
                                "results": [{"name": k, "value": float(v)} for k, v in every.items()]},
                               {"segment_column": "FAILURECODE", "metric_column": "WONUM", "aggregation": "mean",
                                "results": [{"name": k, "value": 1.0} for k in every.index]}],
                  "non_null": {"FAILURECODE": 333, "WONUM": 640}, "n_rows": 640}, "work_orders", ["FAILURECODE"])
    ev = build_evidence(_parse_findings(raw, "", []), raw, "", "", None)
    assert [e.row_count for e in ev if e.kind == "segment"] == [truth["corrective_wos"]] * 2


def test_a_categorys_builtin_distribution_counts_the_counted_records():
    """e2e-89 re-measure: the sandbox's built-in value counts (every row) gave the report its
    failure codes (SEAL 82 … = 333) beside a 311 tile. standard_stats replaces them, as it replaces
    a column's summary (counted_summary)."""
    _, df, _, truth = _built("work_orders")
    every = [{"label": k, "count": int(v)} for k, v in df["FAILURECODE"].value_counts().items()]
    raw = _stats({"distributions": {"FAILURECODE": every, "WORKTYPE": [{"label": "PM", "count": 1}]}},
                 "work_orders", [])
    assert sum(d["count"] for d in raw["distributions"]["FAILURECODE"]) == truth["corrective_wos"]
    assert {d["label"] for d in raw["distributions"]["WORKTYPE"]} == {"PM", "CM", "EM"}
    assert "FAILURECODE value counts: Left out, as the tiles count: 39 cancelled work orders." in raw["assumptions"]


def test_every_count_a_user_reads_says_what_it_left_out():
    """Q2 (the user's call, from the Spec review): a chart the user asks for counts the counted
    records and says so; a KPI group the product can't give is named, never dropped silently."""
    from agents.visualization_agent import build_single_chart
    from orchestrator.state import TargetedParams
    _, df, contract, _ = _built("work_orders")
    ch = build_single_chart(df, TargetedParams(group_by="WORKTYPE", metric_column="STATUS"), contract)
    assert sum(r["count"] for r in ch.data) == 640 - 39
    assert "39 cancelled work orders" in ch.rationale.reason
    raw = _stats({"segments": [{"segment_column": "Carrier", "metric_column": "Delivered At", "aggregation": "ratio",
                                "label": "on_time_delivery", "results": [{"name": "UPS", "value": 0.9},
                                                                         {"name": "Nowhere", "value": 0.5}]}]},
                 "tms_shipments", ["Carrier"])
    assert any("Nowhere" in a for a in raw["assumptions"])


def test_a_label_with_its_own_window_or_qualifier_stays_the_models():
    """$0 replay (now tests/test_replay.py's analysis replay): a glossary match turned 'on-time for LTL' and
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
    from domain_pack import count_rows
    _, df, contract, truth = _built("tms_shipments")
    rows, how = count_rows(df, contract, "Carrier", None)
    live = df[(df["Status"].str.lower() != "cancelled") & (df["Currency"] == "USD")]
    assert rows.groupby("Carrier").size().to_dict() == live.groupby("Carrier").size().to_dict()
    assert len(rows) == truth["shipments"] and "cancelled" in how


def test_a_count_by_the_column_that_decides_a_kind_keeps_that_kind():
    """The ruled-out one-filter fix: a Status breakdown exists to show the cancelled rows. Put
    back only the kind Status decides; other currencies stay out (as the tiles): 62 cancelled,
    4 of them in another currency."""
    from domain_pack import count_rows
    _, df, contract, truth = _built("tms_shipments")
    rows, _ = count_rows(df, contract, "Status", None)
    assert (rows["Status"].str.lower() == "cancelled").sum() == truth["cancelled"] - 4 == 58
    assert (rows["Currency"] == "USD").all()


def test_a_count_by_an_id_keeps_every_other_rule():
    """Doubt review of the first draft: judging kinds without the group column switched off its
    other rules: by Claim ID, health_claims counted 663 earlier versions (5,999 for 5,336); by
    Claim Number, loss_run counted every valuation (38,255 for 3,975). Only a column whose values
    name a kind (status, currency, a void or non-product code) puts that kind back."""
    from domain_pack import count_rows
    for name, col, counted in (("health_claims", "Claim ID", 5336), ("loss_run", "Claim Number", 3975)):
        _, df, contract, _ = _built(name)
        assert len(count_rows(df, contract, col, None)[0]) == counted, name


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


def test_a_distribution_by_a_kind_column_a_number_or_a_clean_file_is_as_before():
    """Mutation gate: a Status distribution keeps its cancelled orders (the kind it names); a
    number's distribution reads every row; a file with nothing left out says nothing more."""
    from agents.targeted_analysis_agent import handle_distribution
    from orchestrator.state import TargetedParams
    _, df, contract, truth = _built("work_orders")
    r = handle_distribution(df, TargetedParams(target_columns=["STATUS"]), contract)
    assert {d["value"]: d["count"] for d in r.chart_configs[0].data}["CAN"] == truth["cancelled_wos"]
    r = handle_distribution(df.assign(n=range(len(df))), TargetedParams(target_columns=["n"]), contract)
    assert "mean=319.50" in r.answer_text
    _, df, contract, _ = _built("shipments")
    r = handle_distribution(df, TargetedParams(target_columns=["carrier"]), contract)
    assert r.answer_text.endswith("%).")


def test_a_kpi_the_data_cannot_give_by_group_drops_the_segment_and_says_why():
    """No order lines in a shipment file: OTIF by order is unavailable, so the model's figure is
    dropped (never shown beside tiles that can't make it) and the reason is said."""
    raw = _stats({"segments": [{"segment_column": "Carrier", "metric_column": "Delivered At", "aggregation": "ratio",
                                "label": "otif_order", "results": [{"name": "UPS", "value": 0.9}]}]},
                 "tms_shipments", ["Carrier"])
    assert raw["segments"] == [] and any("the data has no" in a for a in raw["assumptions"])


def test_each_segment_is_judged_on_its_own():
    """A filtered count is kept, a plain count after it is still recounted, a count one off the
    plain one (the model's own filter) is kept; the recount keeps the model's group names."""
    _, df, _, truth = _built("work_orders")
    plain = df.groupby("FAILURECODE")["WONUM"].count()
    near = [{"name": k, "value": float(v) - (k == plain.index[0])} for k, v in plain.items()]
    own = [{"name": "M01", "value": 3.0}]
    raw = _stats({"segments": [
        {"segment_column": "ASSETNUM", "metric_column": "WONUM", "aggregation": "count", "results": [dict(r) for r in own]},
        {"segment_column": "FAILURECODE", "metric_column": "WONUM", "aggregation": "count",
         "results": [{"name": k, "value": float(v)} for k, v in plain.items()]},
        {"segment_column": "FAILURECODE", "metric_column": "WONUM", "aggregation": "Count",
         "results": [dict(r) for r in near]}]}, "work_orders", ["FAILURECODE"])
    first, second, third = raw["segments"]
    assert first["results"] == own and third["results"] == near
    assert sum(r["value"] for r in second["results"]) == truth["corrective_wos"]
    assert {r["name"] for r in second["results"]} <= set(plain.index.astype(str))
    assert raw["assumptions"] == ["WONUM count by FAILURECODE: Left out, as the tiles count: 39 cancelled work orders."]


def test_a_number_a_kpi_sums_by_absolute_value_reads_the_kpis_rows_too():
    from domain_pack import pack_for
    from domain_pack.kpi import counted
    pack = pack_for("logistics").model_copy(deep=True)
    term = pack.kpi("cost_per_lb").den[0][0]
    term.sum_abs, term.sum = term.sum, None
    assert counted(pack, "weight")


@pytest.mark.xfail(strict=True, reason="PR24-03 (Open): a count of rows by group counts rows, the count "
                   "tile distinct records or entities in force (loss_run: 1,098 claimant rows of Collision "
                   "for 940 claims); counting by record key needs a partition rule (a line-level group "
                   "counts one claim in several groups)")
def test_a_count_by_group_counts_the_records_the_count_tile_counts():
    """D7-19 audit pass (Sonnet): the kind rule is the tiles', the unit is not."""
    import domain_pack as dp
    _, df, contract, _ = _built("loss_run")
    roles = {r: c for r, c in contract.roles.items() if c in df.columns}
    tile = dp.compute_kpi(df, roles, dp.pack_for(contract.domain), "claims", by="Coverage")
    rows = dp.count_rows(df, contract, "Coverage", None)[0]["Coverage"].value_counts()
    assert rows.to_dict() == tile.astype(int).to_dict()


def test_a_sum_of_a_measure_a_kpi_sums_reads_the_kpis_rows():
    """D8 audit pass 1: 'sum of Weight by Mode' read cancelled rows (a weight at $0); cost per lb
    sums Weight over counted rows, so every total of Weight does."""
    from domain_pack import rows_for
    _, df, contract, truth = _built("tms_shipments")
    rows, how = rows_for(df, contract, "Weight")
    assert len(rows) == truth["shipments"] and how
