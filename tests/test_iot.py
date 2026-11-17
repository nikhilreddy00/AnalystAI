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


def test_a_flow_kpi_asked_without_a_period_leads_with_its_overall_value():
    """Smoke run, e2e-85: 'What is our MTTR?' came back as intent time_series with no frequency,
    and the answer gave only the latest month (2.99 h), never the MTTR (3.97 h). A ratio or flow
    asked with no grain and no period leads with its value over the data; the latest period
    follows. A state (a balance, MRR at the as-of) keeps its latest value as the answer."""
    from tests.test_oracle import answer
    from evals import datagen
    truth = datagen.make("work_orders", 1)[1]
    text, _ = answer("work_orders", "time_series", "What is our MTTR?", kpi="mttr")
    assert f"{truth['mttr_hours']:.2f} hours" in text.split("latest")[0], text
    text, _ = answer("work_orders", "time_series", "MTTR by month", kpi="mttr", frequency="M")
    assert text.startswith("MTTR by period"), text   # a grain asked: the series, as before


def test_an_outlier_scan_of_one_sensors_reading_is_its_control_chart():
    """Intake run, intake-iot-05: 'Is sensor S07's temperature in control?' came as outlier_scan
    with the right params (Temperature, Sensor S07, daily). One series' continuous measure: its
    outliers are the points beyond its control limits, so it is answered by the control chart."""
    from tests.test_oracle import answer
    text, _ = answer("machine_readings", "outlier_scan", "Is sensor S07's temperature in control?",
                     target_columns=["Temperature"], metric_column="Temperature",
                     segment_column="Sensor", segment_value="S07", frequency="D")
    assert "Control limits" in text and "UCL" in text, text
    text, _ = answer("machine_readings", "outlier_scan", "Any odd vibration values?",
                     target_columns=["Vibration (mm/s)"])
    assert "Control limits" not in text   # no named series: the scan, as before


def test_what_a_kpi_lacks_is_said_with_each_column_once():
    """domain_diff: 'MTTR needs repair hours and work type and repair hours and work order and
    work type' (its two sides repeated their columns)."""
    from agents.targeted_analysis_agent import _missing
    assert _missing(IOT, "mttr", {"asset": "a"}) == "repair hours and work type and work order"


def test_what_a_kpi_lacks_names_only_the_sides_that_lack_and_each_way_apart():
    """Mutation gate on _missing: a side the data supports is never named; a side with two ways
    says each ('x, or y and z'); a word shared with another side is merged only when every side
    has one way; a computable KPI lacks 'other columns'."""
    import yaml

    from agents.targeted_analysis_agent import _missing
    from domain_pack import Pack
    from tests.test_second_pack import BANK
    # availability: run time lacking, planned minutes there: only run time is named
    assert _missing(IOT, "availability", {"planned_minutes": "p"}) == "run time"
    # scrap rate: two ways for its numerator, one for its denominator
    assert _missing(IOT, "scrap_rate", {"asset": "a"}) == \
        "scrap count, or good count and total count and total count"
    two_ways = Pack.model_validate(yaml.safe_load(BANK.replace("kpis:\n", """kpis:
  - {name: odd, label: Odd, unit: number, num: [[{sum: amount}], [{sum: quantity}]], den: [[{sum: amount}]]}
""")))
    assert _missing(two_ways, "odd", {"account_id": "a"}) == "amount, or quantity and amount"
    assert _missing(IOT, "good_count", {"good_count": "g"}) == "other columns"


def test_a_value_no_pack_word_list_knows_is_said_never_silently_dropped():
    """Audit pass 1 (C6, D2-16): 'Corrective Maintenance' or 'Cancelled - duplicate' matched no
    exact pack word and fell out of MTTR and every count, said nowhere. Every pack: a value of a
    role its word lists read (a status, a KPI condition's list) that no list knows is counted
    and said in the cleaning audit, with what it is left out of."""
    from agents.cleaning_agent import CleanContext, clean
    df = pd.DataFrame({
        "wonum": [f"W{i}" for i in range(6)], "assetnum": ["P1"] * 6,
        "worktype": ["CM", "Modification", "Modification", "PM", "Project", "Corrective Maintenance"],
        "status": ["COMP", "COMP", "Cancel requested", "COMP", "COMP", "COMP"]})
    roles = {"work_order": "wonum", "asset": "assetnum", "work_type": "worktype", "status": "status"}
    _, audit = clean(CleanContext(df=df, ids={"wonum"}, dates=set(), targets=set(),
                                  drop_high_null=False, pack=IOT, roles=roles))
    kpis = "MTTR, corrective work orders, preventive share of work orders"
    assert [(d.column, d.operation, d.reason, d.rows_affected) for d in audit.decisions
            if d.strategy == "unlisted_values"] == [
        ("status", "flag", "1 rows have a status no word list of the pack names: Cancel requested (1): "
         "read as counted (no status list holds them).", 1),
        ("worktype", "flag", "3 rows have a work type no word list of the pack names: Modification (2), "
         f"Project (1): left out of {kpis}.", 3)]
    # every status known, then four unknown work types: the three commonest named
    four = df.assign(worktype=["Mod", "Mod", "Proj", "Calib", "Audit", "CM"], status="COMP")
    _, audit = clean(CleanContext(df=four, ids={"wonum"}, dates=set(), targets=set(),
                                  drop_high_null=False, pack=IOT, roles=roles))
    assert [d.reason for d in audit.decisions if d.strategy == "unlisted_values"] == [
        "5 rows have a work type no word list of the pack names: Mod (2), Proj (1), Calib (1): "
        f"left out of {kpis}."]
    known = df.assign(worktype=["CM", "Emergency Repair", "PM", "cm", "Inspection", "Reactive"],
                      status="COMP")
    _, audit = clean(CleanContext(df=known, ids={"wonum"}, dates=set(), targets=set(),
                                  drop_high_null=False, pack=IOT, roles=roles))
    assert not [d for d in audit.decisions if d.strategy == "unlisted_values"]


def test_a_quality_flag_matches_by_its_first_word_and_an_unknown_unit_is_never_averaged():
    """Audit pass 1 (C6, C5): 'Bad - Sensor Failure' / 'Bad_NoComm' kept their readings (exact
    match on 'bad'); 'Uncertain - Last Usable' was not counted; ℉ / 'degrees F' / 'F.' stayed
    unconverted and °R was averaged with °C. Quality flags match by their first word; unit codes
    by their letters (NFKC: ℉ is °F); a unit the pack can't convert is left out and said."""
    from agents.cleaning_agent import CleanContext, clean
    q = ["Good", "Bad - Sensor Failure", "Bad_NoComm", "bad input", "Uncertain - Last Usable",
         "Good", "Good", "Good", "Good"]
    u = ["°C", "°C", "°C", "°C", "°C", "℉", "degrees F", "F.", "°R"]
    df = pd.DataFrame({"sensor": ["S1"] * 9, "temperature": [70.0, 60, 61, 62, 63, 212, 212, 212, 500],
                       "quality": q, "unit": u})
    roles = {"asset": "sensor", "temperature": "temperature", "reading_quality": "quality", "temp_unit": "unit"}
    out, audit = clean(CleanContext(df=df, ids={"sensor"}, dates=set(), targets=set(),
                                    drop_high_null=False, pack=IOT, roles=roles))
    assert out["temperature"].iloc[1:4].isna().all() and out["temperature"].iloc[4] == 63.0
    assert out["temperature"].iloc[5:8].round(6).tolist() == [100.0, 100.0, 100.0]
    assert pd.isna(out["temperature"].iloc[8])   # °R: no conversion known, never averaged
    reasons = " ".join(d.reason for d in audit.decisions)
    assert "1 rows marked" in reasons and "°R (1)" in reasons
    assert out["temperature"].mean() == pytest.approx((70 + 63 + 300) / 5)


def test_epoch_seconds_are_their_dates_never_1970():
    """Audit pass 1 (C2): a historian's Unix-epoch timestamps (SiteWise timeInSeconds) were read
    by the answers as nanoseconds: 'Temperature in 1970-01'. The one time rule reads epoch
    seconds as dates; the answers read times through it."""
    from connectors.base import as_times
    from tests.test_oracle import answer
    secs = pd.Series([1772312400, 1772316000, None, 1774990800])
    assert [str(t)[:13] for t in as_times(secs)] == ["2026-02-28 21", "2026-02-28 22", "NaT", "2026-03-31 21"]
    assert as_times(secs.astype("Int64").astype(str).replace("<NA>", None)).iloc[0] == as_times(secs).iloc[0]
    assert as_times(pd.Series([3, 5, 7])).isna().all()   # small counts are no epoch
    # epoch seconds are [9e8, 2.2e9) on 80% of the numbers at least (ingestion's typing range,
    # review 2026-11-17); the rest are NaT
    assert [str(t) for t in as_times(pd.Series([9e8] * 4 + [5.0]))] == ["1998-07-09 16:00:00"] * 4 + ["NaT"]
    assert [str(t) for t in as_times(pd.Series([2.2e9 - 1] * 4 + [2.2e9]))] == \
        ["2039-09-18 23:06:39"] * 4 + ["NaT"]
    assert as_times(pd.Series([2.2e9] + [2.2e9 - 1] * 3 + [5.0])).isna().all()   # 3 of 5
    from agents.targeted_analysis_agent import handle_time_series
    from connectors.csv_connector import CSVConnector
    from orchestrator.state import ConnectorConfig, ConnectorType, TargetedParams
    df = pd.DataFrame({"Timestamp": [1772312400 + 86400 * i for i in range(60)],
                       "Temperature": [70.0 + i % 3 for i in range(60)]})
    contract = CSVConnector(ConnectorConfig(type=ConnectorType.CSV)).build_data_contract(df)
    text = handle_time_series(df, TargetedParams(metric_column="Temperature", date_column="Timestamp",
                                                 frequency="M"), contract).answer_text
    assert "1970" not in text and "2026" in text, text
    assert answer   # imported for the dataset path above


def test_a_derived_roles_range_is_checked_zero_and_negative_repairs_flagged():
    """Audit pass 1 (C5, the planted trap 6): ranges: {repair_hours: {gt: 0}} never ran (repair
    hours are derived after cleaning), so 0 h and finish-before-start repairs went unsaid."""
    from tests.test_oracle import _built
    flags = [d for d in _built("work_orders")[0].cleaning_audit.decisions if d.strategy == "out_of_range"]
    assert len(flags) == 1 and flags[0].rows_affected == 2 and "kept" in flags[0].reason


def test_a_blanked_reading_is_no_clash_and_the_control_chart_names_stuck_and_silent_days():
    """Audit pass 1 (minor): a Bad row (its readings blanked) beside a Good one at the same time
    was called a clash; S03's stuck days and S05's silent day went unsaid on their charts."""
    from agents.cleaning_agent import CleanContext, clean
    from agents.targeted_analysis_agent import handle_time_series
    from orchestrator.state import TargetedParams
    from tests.test_oracle import _built
    t = pd.date_range("2026-10-01", periods=30, freq="h")
    df = pd.DataFrame({"sensor": "S1", "ts": list(t) + [t[3]], "temperature": [70.0 + i % 4 for i in range(30)] + [5.0],
                       "quality": ["Good"] * 30 + ["Bad"]})
    roles = {"asset": "sensor", "date": "ts", "temperature": "temperature", "reading_quality": "quality"}
    _, audit = clean(CleanContext(df=df, ids={"sensor"}, dates={"ts"}, targets=set(),
                                  drop_high_null=False, pack=IOT, roles=roles))
    assert not [d for d in audit.decisions if d.strategy == "series_clash"]
    _, clean_df, contract, _ = _built("machine_readings")
    for sensor, said in (("S03", "identical daily mean"), ("S05", "without readings")):
        r = handle_time_series(clean_df, TargetedParams(metric_column="Temperature", segment_column="Sensor",
                                                        segment_value=sensor), contract)
        assert said in r.answer_text, (sensor, r.answer_text)


@pytest.mark.xfail(strict=True, reason="D7-13 (Open): the load reads any two offsets an hour apart "
                   "as one place across a clock change, so two sites on +01:00 and +02:00 become "
                   "one wall-clock time (the Shopify -0500/-0400 rule, every domain since D1)")
def test_two_fixed_offsets_an_hour_apart_in_june_are_two_instants():
    from connectors.base import parse_text_dates
    t = parse_text_dates(pd.Series(["2026-06-01 10:00:00+01:00", "2026-06-01 10:00:00+02:00"]))
    assert t.iloc[0] != t.iloc[1]


def test_audit_pass_2_negative_spans_blank_units_and_vibration_only_series():
    """Audit pass 2: (1) a finish before its start was blanked before the range check, so never
    flagged (repairs; healthcare's LOS too); (2) a blank unit beside °F rows was averaged as °C;
    (3) a sensor with only vibration left had its clashes unread, and the clock-change fold
    counted series that had no reading."""
    from agents.cleaning_agent import CleanContext, clean
    from tests.test_oracle import _built
    wo, _ = __import__("evals.datagen", fromlist=["make"]).make("work_orders", 1)
    done = wo.index[wo["ACTSTART"].notna() & wo["ACTFINISH"].notna()][:4]
    wo.loc[done, "ACTFINISH"] = (pd.to_datetime(wo.loc[done, "ACTSTART"]) - pd.Timedelta(hours=3)
                                 ).dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    roles = {"work_order": "WONUM", "asset": "ASSETNUM", "work_type": "WORKTYPE", "status": "STATUS",
             "actual_start": "ACTSTART", "actual_finish": "ACTFINISH", "date": "REPORTDATE"}
    for c in ("ACTSTART", "ACTFINISH", "REPORTDATE"):
        wo[c] = pd.to_datetime(wo[c])
    _, audit = clean(CleanContext(df=wo, ids={"WONUM"}, dates=set(), targets=set(),
                                  drop_high_null=False, pack=IOT, roles=roles))
    assert [d.rows_affected for d in audit.decisions if d.strategy == "out_of_range"] == [6]
    df = pd.DataFrame({"sensor": "S1", "temperature": [20.0, 68.0, 68.0] * 3,
                       "unit": ["°C", "°F", ""] * 3})
    out, audit = clean(CleanContext(df=df, ids={"sensor"}, dates=set(), targets=set(), drop_high_null=False,
                                    pack=IOT, roles={"asset": "sensor", "temperature": "temperature",
                                                     "temp_unit": "unit"}))
    assert out["temperature"].mean() == pytest.approx(20.0)
    t = list(pd.date_range("2026-10-01", periods=30, freq="h"))
    v = pd.DataFrame({"sensor": ["S1"] * 30 + ["S2"] * 30 + ["S2"], "ts": t + t + [t[3]],
                      "vibration": [1.0 + i % 3 for i in range(60)] + [9.0]})
    _, audit = clean(CleanContext(df=v, ids={"sensor"}, dates={"ts"}, targets=set(), drop_high_null=False,
                                  pack=IOT, roles={"asset": "sensor", "date": "ts", "vibration": "vibration"}))
    assert [d.rows_affected for d in audit.decisions if d.strategy == "series_clash"] == [1]
    # two live sensors repeat one hour; three with every reading blanked are no series to share it
    live = pd.DataFrame({"sensor": ["S1"] * 31 + ["S2"] * 31, "ts": (t + [t[5]]) * 2,
                         "vibration": [1.0 + i % 3 for i in range(62)]})
    dead = pd.DataFrame({"sensor": [f"D{i}" for i in range(3) for _ in t], "ts": t * 3, "vibration": None})
    _, audit = clean(CleanContext(df=pd.concat([live, dead], ignore_index=True), ids={"sensor"},
                                  dates={"ts"}, targets=set(), drop_high_null=False, pack=IOT,
                                  roles={"asset": "sensor", "date": "ts", "vibration": "vibration"}))
    assert [d.strategy for d in audit.decisions if d.strategy.startswith("series_c")] == ["series_clock_change"]
    assert _built("machine_readings")   # the stock readings still read as before (other tests)


def test_a_shift_over_100_percent_performance_is_flagged_never_capped():
    """T12 (spec review): performance over 100% means a wrong ideal cycle. The planted shift is
    said in cleaning and kept: the pooled figure still reads its real numbers."""
    from agents.cleaning_agent import CleanContext, clean as run
    from tests.test_oracle import _built
    _, clean, contract, truth = _built("shift_production")
    _, audit = run(CleanContext(df=clean, ids=set(), dates=set(), targets=set(),
                                drop_high_null=False, pack=IOT, roles=contract.roles))
    flags = [d for d in audit.decisions if d.strategy == "kpi_over_max"]
    assert [d.rows_affected for d in flags] == [truth["over_100_performance_rows"]] == [1]
    assert "Performance rate over 100%" in flags[0].reason and "ideal cycle" in flags[0].reason


def test_each_rows_ratio_over_its_max_is_flagged_and_a_ratio_at_the_max_is_not():
    """Worked by hand: 100 run minutes each; performance = ideal cycle s × total ÷ 60 ÷ 100."""
    from agents.cleaning_agent import CleanContext, clean
    cols = ["planned_minutes", "unplanned_downtime", "total_count", "ideal_cycle_s", "good_count"]

    def flags(rows, pack=IOT):
        _, audit = clean(CleanContext(df=pd.DataFrame(rows, columns=cols), ids=set(), dates=set(),
                                      targets=set(), drop_high_null=False, pack=pack,
                                      roles={c: c for c in cols}))
        return [(d.column, d.operation, d.reason, d.rows_affected) for d in audit.decisions
                if d.strategy == "kpi_over_max"]
    words = "the ideal cycle time is likely wrong (set too long)"
    assert flags([[100, 0, 60, 120, 60], [100, 0, 50, 120, 50], [100, 0, 30, 60, 30]]) == [   # 120%, 100%, 30%
        ("ideal_cycle_s", "flag", f"1 row with Performance rate over 100%: {words}; kept, never capped.", 1)]
    assert flags([[100, 0, 60, 120, 60], [100, 0, 70, 120, 70], [100, 0, 30, 60, 30]]) == [   # 120%, 140%
        ("ideal_cycle_s", "flag", f"2 rows with Performance rate over 100%: {words}; kept, never capped.", 2)]
    plain = IOT.model_copy(update={"kpis": [k.model_copy(update={"max_words": None}) for k in IOT.kpis]})
    assert flags([[100, 0, 60, 120, 60]], plain) == [
        ("ideal_cycle_s", "flag", "1 row with Performance rate over 100%; kept, never capped.", 1)]
    assert flags([[100, 0, 60, None, 60]]) == []   # no ideal cycle: performance not computable


def test_a_blank_failure_code_is_no_failure_never_missing():
    """Live check (D1-04): a preventive order has no failure code by design; the high-null gate
    asked about it, an alert called it 48% missing and an action said to back-fill it."""
    from domain_pack.columns import layout_blanks
    from tests.test_oracle import _built
    _, clean, contract, _ = _built("work_orders")
    assert clean["FAILURECODE"].isna().mean() > 0.4 and "FAILURECODE" in layout_blanks(clean, contract)


def test_a_machine_id_is_no_date_filter():
    """Browser read (mock stack): the dashboard offered 'Machine' as a date range; pandas reads
    'M01' as 00:00:01 on 1 Jan of year 1. Columns are dated by the load's one rule (F2)."""
    from agents.visualization_agent import classify_columns
    df = pd.DataFrame({"Machine": [f"M{i:02d}" for i in range(1, 11)] * 3,
                       "Day": pd.date_range("2026-08-01", periods=30).strftime("%Y-%m-%d"),
                       "x": range(30)})
    roles = classify_columns(df)
    assert "Machine" not in roles["datetime_cols"] and "Day" in roles["datetime_cols"]


def test_a_span_ending_before_its_start_is_blank_unless_signed_for_the_range_check():
    from domain_pack.columns import derived_roles
    wo = pd.DataFrame({"s": ["2026-01-01T10:00Z"] * 2, "f": ["2026-01-01T12:30Z", "2026-01-01T08:00Z"]})
    roles = {"actual_start": "s", "actual_finish": "f"}
    hours = [derived_roles(wo, roles, IOT, **kw)[0].iloc[:, -1].tolist() for kw in ({}, {"signed": True})]
    assert str(hours) == "[[2.5, nan], [2.5, -2.0]]"
    stay = pd.DataFrame({"a": ["2026-01-03"] * 2, "d": ["2026-01-05", "2026-01-01"]})
    roles = {"admit_date": "a", "date": "d"}
    days = [derived_roles(stay, roles, dp.pack_for("healthcare"), **kw)[0].iloc[:, -1].tolist()
            for kw in ({}, {"signed": True})]
    assert str(days) == "[[2.0, nan], [2.0, -2.0]]"


def test_a_kpi_reads_the_status_that_kinds_its_rows_and_its_derived_roles_sources():
    """D7-04 (P4 on the work-order book): blanking STATUS changed corrective counts (a cancelled
    order counted) and blanking ACTFINISH changed MTTR, though kpi_roles said neither is read."""
    assert "status" in dp.kpi_roles(IOT, "corrective_wos")
    assert {"actual_start", "actual_finish", "repair_hours"} <= dp.kpi_roles(IOT, "mttr")


def test_mttr_reads_finished_corrective_orders_in_exact_hours():
    """T3/T4/T19, S-c: a PM is no repair, an open order has no finish yet, a cancelled one is no
    work; a repair across the 25 Oct 2026 clock change is 2.75 h in UTC. (2.75 + 1) / 2."""
    assert dp.compute_kpi(_wos(), WO_ROLES, IOT, "mttr") == 1.875
    assert dp.compute_kpi(_wos(), WO_ROLES, IOT, "corrective_wos") == 3   # W1, W2, W4; W5 cancelled


@pytest.mark.xfail(strict=True, reason="D7-19 (Open): a count by group the model's code writes reads "
                   "every row; standard_stats replaces a level's totals and means, never a count, so "
                   "e2e-89's failure codes counted 22 cancelled orders (333, truth 311)")
def test_a_models_count_by_group_reads_the_counted_rows():
    from agents.analysis_agent import model_frame, standard_stats
    from orchestrator.state import AnalysisTask
    from tests.test_oracle import _built
    _, df, contract, _ = _built("work_orders")
    framed, _ = model_frame(df, contract)
    every = framed.groupby("FAILURECODE")["WONUM"].count()
    raw = {"segments": [{"segment_column": "FAILURECODE", "metric_column": "WONUM", "aggregation": "count",
                         "results": [{"name": k, "value": float(v)} for k, v in every.items()]}]}
    standard_stats(raw, df, contract, AnalysisTask(raw_query="overview", target_metrics=[],
                                                   segments=["FAILURECODE"], analysis_types=["segmentation"]))
    seg = next(s for s in raw["segments"] if s["segment_column"] == "FAILURECODE")
    assert sum(r["value"] for r in seg["results"]) == 311
