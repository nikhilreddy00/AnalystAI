"""Domain 7 answers through their public outputs (answer text, chart data, reference lines): the
control chart of one series' reading, a trend's change in its units or in percent, a KPI asked
with no grain led by its overall value, the notes for hidden and zero-base groups. Expected
values are worked by hand on small frames (or come from the generator's data), never from the
code's own formula."""
import pandas as pd
import pytest

from agents.targeted_analysis_agent import control_limits, handle_kpi, handle_time_series
from orchestrator.state import ConnectorConfig, ConnectorType, QuestionIntent, TargetedParams
from tests.test_oracle import _built, answer

S07 = dict(metric_column="Temperature", segment_column="Sensor", segment_value="S07")
# 30 baseline days alternating 10/12: x̄ = 11, MR̄ = 2, 3·MR̄/1.128 = 5.319149 → UCL 16.319149,
# LCL 5.680851. Then 20 (above, 31 Aug), 3 (below), 17 (above, 7 Sep); the last step 11 → 13.
WORKED = [10, 12] * 15 + [20, 11, 11, 3, 11, 11, 11, 17, 11, 13]
ENDING = ("These are control limits from the process itself, not specification limits; readings "
          "close in time are alike, so points outside come more often than 3 sigma suggests.")


def _readings(values, sensor: str = "S07") -> pd.DataFrame:
    """One reading a day at noon from 1 Aug 2026, in machine_readings' cleaned shape."""
    return pd.DataFrame({
        "Timestamp": pd.date_range("2026-08-01 12:00", periods=len(values), freq="D"),
        "Sensor": sensor, "Line": "Line C", "Temperature": [float(v) for v in values],
        "Temp Unit": "°C", "Vibration (mm/s)": 1.0, "Pressure (bar)": 6.0, "Alarm": 0,
        "Quality": "Good"})


def _series(values, **params):
    contract = _built("machine_readings")[2]
    return handle_time_series(_readings(values), TargetedParams(**{**S07, **params}), contract)


def _generic(month_totals):
    """No domain: each month's total spread evenly over its days (full months, Jan 2026 on)."""
    from connectors.csv_connector import CSVConnector
    rows = []
    for i, total in enumerate(month_totals):
        days = pd.date_range(f"2026-{i + 1:02d}-01", periods=pd.Period(f"2026-{i + 1:02d}").days_in_month)
        rows += [{"day": d.strftime("%Y-%m-%d"), "amount": total / len(days)} for d in days]
    df = pd.DataFrame(rows)
    return df, CSVConnector(ConnectorConfig(type=ConnectorType.CSV)).build_data_contract(df)


def test_a_worked_control_chart_states_its_limits_and_the_days_outside():
    r = _series(WORKED)
    text = r.answer_text
    assert text.startswith("Temperature for Sensor S07 trended up by 3.00 (10.00 → 13.00) from "
                           "2026-08-01 to 2026-09-09 (freq=D, agg=mean)."), text
    # the latest step in degrees (13 − 11), never a percent of a reading with no true zero
    assert "Latest period (2026-09-09) vs the one before: +2.00 (11.00 → 13.00)." in text
    assert ("CL 11.00, UCL 16.32, LCL 5.68; after them 2 days above the upper limit (first "
            "2026-08-31) and 1 below the lower one. " + ENDING) in text
    # its 2 runs of 11 (1–2 and 4–6 Sep) follow the limits (audit pass 1, minor 9; review 2026-11-17)
    assert text.endswith("5 days are part of a run of one identical daily mean (first 2026-09-01): "
                         "a stuck instrument? Never counted beyond the limits.") and "%" not in text
    assert r.chart_configs[0].reference_lines == [
        {"value": 16.3191, "label": "UCL", "axis": "y"},
        {"value": 11.0, "label": "CL", "axis": "y"},
        {"value": 5.6809, "label": "LCL", "axis": "y"}]


def test_a_stuck_runs_days_are_never_points_beyond_the_limits():
    """Review 2026-11-17 (D7-11 partly back): S03 stuck at 68.40 from 20 Sep (cleaning flags it)
    gave '4 below the lower one'; 3 of them were the stuck run, its first day unnamed."""
    text, _ = answer("machine_readings", "time_series", "Is S03's temperature in control?",
                     metric_column="Temperature", segment_column="Sensor", segment_value="S03", frequency="D")
    assert "and 1 below the lower one" in text, text
    assert "3 days are part of a run of one identical daily mean (first 2026-09-20)" in text, text


def test_one_repeated_day_and_the_days_without_readings_are_counted():
    v = [10, 12] * 15 + [12, 14, 10, 13, 11]   # 31 Aug repeats 30 Aug's 12
    stuck = (" 2 days are part of a run of one identical daily mean (first 2026-08-30): a stuck "
             "instrument? Never counted beyond the limits.")
    assert _series(v).answer_text.endswith(stuck)
    contract = _built("machine_readings")[2]
    for drop, said in (([32], " 1 day without readings."), ([32, 33], " 2 days without readings.")):
        text = handle_time_series(_readings(v).drop(index=drop), TargetedParams(**S07), contract).answer_text
        assert text.endswith(stuck + said), text


def test_a_day_on_a_limit_is_not_beyond_it():
    """A point exactly on UCL or LCL is inside: only strictly beyond counts."""
    base = [10.0, 12.0] * 15
    _, ucl, lcl = control_limits(pd.Series(base, index=pd.date_range("2026-08-01", periods=30)))
    text = _series(base + [ucl, lcl]).answer_text
    assert "after them 0 days above the upper limit and 0 below the lower one." in text, text


def test_twenty_days_are_enough_for_limits_and_nineteen_are_not():
    r = _series([10, 12] * 10)
    assert "CL 11.00, UCL 16.32, LCL 5.68" in r.answer_text and r.chart_configs[0].reference_lines
    r = _series([10, 12] * 9 + [10])
    assert r.answer_text.startswith("Temperature for Sensor S07 trended flat"), r.answer_text
    assert r.answer_text.endswith(" No control limits: only 19 days to estimate them from (20 at least).")
    assert not r.chart_configs[0].reference_lines


def test_a_flat_baseline_says_why_there_are_no_limits():
    r = _series([11.0] * 35)
    assert r.answer_text.startswith("Temperature for Sensor S07 trended flat"), r.answer_text
    assert (" No control limits: no variation from day to day in the first days (a stuck "
            "instrument?). 35 days are part of a run of one identical daily mean") in r.answer_text
    # 30 days 10, 12, 12, 12, …: 14 repeat the day before, 16 left
    days = pd.date_range("2026-08-01", periods=30)
    assert control_limits(pd.Series(([10.0, 12.0, 12.0, 12.0] * 8)[:30], index=days)) == (
        "only 16 of the first days differ from the day before (20 at least; a stuck instrument?)")


def test_a_control_chart_reads_daily_means_whatever_aggregation_was_asked():
    """Two readings a day (x and x + 2): the day's mean is x + 1, never their sum 2x + 2."""
    contract = _built("machine_readings")[2]
    df = pd.concat([_readings(WORKED), _readings([v + 2 for v in WORKED])], ignore_index=True)
    r = handle_time_series(df, TargetedParams(**S07, aggregation="sum"), contract)
    assert "trended up by 3.00 (11.00 → 14.00)" in r.answer_text and "agg=mean" in r.answer_text
    assert r.chart_configs[0].reference_lines[1] == {"value": 12.0, "label": "CL", "axis": "y"}


@pytest.mark.parametrize("params", [
    dict(metric_column="Temperature", segment_column="Sensor"),   # no sensor named
    dict(metric_column="Temperature", segment_column="Line", segment_value="Line C"),   # a line
])
def test_a_reading_of_no_one_named_series_is_no_control_chart(params):
    contract = _built("machine_readings")[2]
    r = handle_time_series(_readings(WORKED), TargetedParams(**params), contract)
    assert not r.chart_configs[0].reference_lines and "Control limits" not in r.answer_text
    assert "(freq=M, agg=mean)" in r.answer_text, r.answer_text


def test_a_flow_trend_is_in_percent_and_a_daily_one_has_no_control_limits():
    """Months 100, 110, 120, 132: up 32.0% overall, the last step +10.0%."""
    df, contract = _generic([100, 110, 120, 132])
    text = handle_time_series(df, TargetedParams(metric_column="amount", aggregation="sum"),
                              contract).answer_text
    assert text == ("amount trended up 32.0% from 2026-01-31 to 2026-04-30 (freq=M, agg=sum). Latest "
                    "period (2026-04-30) vs the one before: +10.0% (120.00 → 132.00)."), text
    r = handle_time_series(df, TargetedParams(metric_column="amount", aggregation="sum", frequency="D"),
                           contract)
    assert "control limits" not in r.answer_text.lower() and not r.chart_configs[0].reference_lines


def test_no_percent_change_from_a_zero_or_negative_base():
    """Months 0, 50, −10, 20: from 0 there is no percent, nor from −10 to 20."""
    df, contract = _generic([0, 50, -10, 20])
    text = handle_time_series(df, TargetedParams(metric_column="amount", aggregation="sum"),
                              contract).answer_text
    assert text == ("amount trended up (0.0000 → 20.00) from 2026-01-31 to 2026-04-30 (freq=M, "
                    "agg=sum). Latest period (2026-04-30) vs the one before: (-10.00 → 20.00)."), text


def test_weekly_data_with_no_domain_is_shown_by_month():
    """No pack: no weekly-cadence convention, months as ever."""
    from connectors.csv_connector import CSVConnector
    df = pd.DataFrame({"wk": pd.date_range("2026-01-04", periods=12, freq="W-SUN").strftime("%Y-%m-%d"),
                       "amount": range(12)})
    contract = CSVConnector(ConnectorConfig(type=ConnectorType.CSV)).build_data_contract(df)
    text = handle_time_series(df, TargetedParams(metric_column="amount", aggregation="sum"),
                              contract).answer_text
    assert "(freq=M, agg=sum)" in text, text


def test_a_mean_of_weekly_sales_is_monthly():
    """A mean has no five-week sawtooth: weekly store sales averaged are shown by month."""
    text, points = answer("store_weeks", "time_series", "average weekly net sales over time",
                          metric_column="Net Sales", aggregation="mean")
    assert "(freq=M, agg=mean)" in text and all(k.endswith(("-30", "-31", "-28", "-29")) for k in points)


def test_an_outlier_scan_naming_the_reading_only_in_target_columns_is_its_control_chart():
    text, _ = answer("machine_readings", "outlier_scan", "Is S07 in control?",
                     target_columns=["Temperature"], segment_column="Sensor", segment_value="S07")
    assert text.startswith("Temperature for Sensor S07 trended") and "UCL" in text, text


def test_another_intent_on_one_sensors_reading_is_not_rerouted():
    text, _ = answer("machine_readings", "distribution", "S07 temperature distribution", **S07)
    assert text.startswith("Temperature: mean=") and "Control limits" not in text, text


def test_what_mtbf_lacks_names_the_kpi_it_builds_on_in_words():
    """MTBF is run time ÷ failures: with neither, it needs 'run time' (the KPI's words), never
    'run minutes' (its name)."""
    import domain_pack as dp
    from agents.targeted_analysis_agent import _missing
    assert _missing(dp.pack_for("iot"), "mtbf", {}) == "run time and failures"


def test_mttr_asked_with_no_grain_leads_with_its_value_over_the_reported_dates():
    from evals import datagen
    raw, truth = datagen.make("work_orders", 1)
    t = pd.to_datetime(raw["REPORTDATE"])
    text, _ = answer("work_orders", "time_series", "What is our MTTR?", kpi="mttr")
    assert text.startswith(f"MTTR ({t.min():%d %b %Y} to {t.max():%d %b %Y}): "
                           f"{truth['mttr_hours']:.2f} hours. MTTR by period"), text


def test_an_overall_value_reads_dates_as_text_and_skips_an_unreadable_one():
    """The KPI path on a frame whose report dates are still text, one of them unreadable: the
    overall value's span reads the readable ones."""
    _, clean, contract, truth = _built("work_orders")
    df = clean.assign(REPORTDATE=clean["REPORTDATE"].dt.strftime("%Y-%m-%dT%H:%M:%SZ"))
    df.loc[df.index[-1], "REPORTDATE"] = "not recorded"
    r = handle_kpi(df, TargetedParams(kpi="mttr"), contract, intent=QuestionIntent.TIME_SERIES)
    assert r.answer_text.startswith("MTTR ("), r.answer_text or r.unanswerable_reason
    assert f"{truth['mttr_hours']:.2f} hours." in r.answer_text.split("MTTR by period")[0]


def test_a_state_asked_with_no_grain_keeps_its_latest_value_first():
    text, _ = answer("loan_tape", "time_series", "What is our total balance?", kpi="total_balance")
    assert text.startswith("Total balance by period (as of "), text


def test_a_rate_of_a_part_with_none_of_its_rows_is_zero_when_the_data_has_them():
    """Stock code 34241 was never returned while the file has returns: its return rate is 0%,
    not unknown, and leads the answer over the dates it sold."""
    _, clean, _, _ = _built("b2b_invoices")
    sold = pd.to_datetime(clean.loc[clean["StockCode"].astype(str) == "34241", "InvoiceDate"])
    text, _ = answer("b2b_invoices", "time_series", "What is the return rate?", kpi="return_rate",
                     segment_column="StockCode", segment_value="34241")
    assert text.startswith(f"Return rate, 34241 ({sold.min():%d %b %Y} to {sold.max():%d %b %Y}): 0.0%."), text


def test_the_baseline_is_thirty_calendar_days_without_stuck_days():
    """Spec review (F8): the baseline was the first 30 points with data, so a 10-day silence
    pulled later days in; a stuck day (the day before's mean again) shrank MR̄. 20 days
    alternating 10/12 (x̄ 11, MR̄ 2), days 1–10 silent, two stuck 12s, then 100s from day 30."""
    days = [d for d in range(40) if not 1 <= d <= 10]
    values = [100.0 if d >= 30 else (10.0 if i % 2 == 0 else 12.0) for i, d in enumerate(days)]
    s = pd.Series(values, index=pd.Timestamp("2026-08-01") + pd.to_timedelta(days, unit="D"))
    s = pd.concat([s, pd.Series([12.0, 12.0], index=[pd.Timestamp("2026-08-30 06:00"),
                                                     pd.Timestamp("2026-08-30 12:00")])]).sort_index()
    cl, ucl, lcl = control_limits(s)
    assert (round(cl, 6), round(ucl, 6), round(lcl, 6)) == (11.0, 16.319149, 5.680851)


def test_the_overview_chart_says_a_line_it_cannot_draw():
    """Live check (S-a): Line D (0 planned minutes) vanished from the overview's OEE by Line
    bars, unsaid; the answer said it. The chart's why names it too."""
    from tests.test_dashboard_filters import _dashboard
    charts = _dashboard("shift_production")[2]
    oee = next(c for c in charts if c.title.startswith("OEE by Line"))
    assert "Line D" not in {d["Line"] for d in oee.data}
    assert "Not measurable, its base is 0: Line D." in oee.rationale.reason, oee.rationale.reason


def test_one_machine_with_no_failures_says_its_run_time_never_a_zero_base():
    """S-b (spec review): a filter to M08 (never failed) said 'its base is 0'; MTBF there is a
    lower bound: no failure in its run hours. Asked alone and as a tile on the filtered part."""
    from domain_pack import pack_for, unavailable_words
    from domain_pack.kpi import measure
    _, clean, contract, _ = _built("shift_production")
    m08 = clean.query("Machine == 'M08'")
    run = (m08["Planned Minutes"] - m08["Unplanned Downtime (min)"] - m08["Changeover (min)"]).sum() / 60
    text, _ = answer("shift_production", "segment_compare", "MTBF for M08", kpi="mtbf",
                     segment_column="Machine", segment_value="M08")
    assert f"no failure in {run:,.0f} hours of run time" in text and "base is 0" not in text, text
    iot = pack_for("iot")
    got = measure(m08, contract.roles, iot, "mtbf", by="Machine")
    assert unavailable_words(got, iot, iot.kpi("mtbf")) == \
        f"no failure in {run:,.0f} hours of run time (a lower bound)"


def test_each_answer_path_says_a_zero_base_in_the_kpis_words():
    """M08 never failed: MTBF on its rows is no failure in its 1,546 run hours, whichever path
    answers (a tile, a group, a series, a year-earlier comparison, a second KPI asked).
    Mutation gate: "OEE and MTBF for M08" gave the plant's MTBF (42.29 h) as M08's."""
    from agents.visualization_agent import pack_kpis
    _, clean, contract, _ = _built("shift_production")
    m08 = clean.query("Machine == 'M08'")
    none = "no failure in 1,546 hours of run time (a lower bound)"
    assert [(k.value, k.context) for k in pack_kpis(m08, contract, full=clean) if k.label == "MTBF"] == [
        ("—", none)]
    text, _ = answer("shift_production", "segment_compare", "OEE and MTBF for M08", kpi="oee",
                     segment_column="Machine", segment_value="M08")
    assert text.endswith(f" Also asked: MTBF: unavailable, {none}."), text
    for params, intent, said in (
            (dict(group_by="Machine"), QuestionIntent.SEGMENT_COMPARE, "MTBF can't be shown by Machine"),
            (dict(frequency="M"), QuestionIntent.TIME_SERIES, "MTBF can't be computed over time here")):
        r = handle_kpi(m08, TargetedParams(kpi="mtbf", **params), contract, intent=intent)
        assert r.answer_text == f"I couldn't answer this — {said}: {none}", r.answer_text
    date = contract.roles["date"]
    two = pd.concat([m08.assign(**{date: m08[date] - pd.DateOffset(years=1)}), m08], ignore_index=True)
    for params, said in (({}, "MTBF: no failure in 3,091"),   # answered: a lower bound (review)
                         (dict(group_by="Machine"), "can't be shown by Machine: no failure in 3,091")):
        r = handle_kpi(two, TargetedParams(kpi="mtbf", compare="yoy", **params), contract,
                       intent=QuestionIntent.SEGMENT_COMPARE)
        assert said in r.answer_text, r.answer_text


def test_a_protected_group_of_five_gets_no_overall_value():
    """Five policies of insured gender X: a protected group under 11 people has no figure."""
    _, clean, contract, _ = _built("policies")
    df = clean.copy()
    df.loc[df.index[:5], "Insured Gender"] = "X"
    r = handle_kpi(df, TargetedParams(kpi="written_premium", segment_column="Insured Gender",
                                      segment_value="X"), contract, intent=QuestionIntent.TIME_SERIES)
    assert r.answer_text.startswith("Written premium by period, X: latest"), r.answer_text


def test_groups_with_no_failures_and_groups_hidden_are_said():
    """M08 never failed: its run time (planned − downtime − changeover) is said, in hours."""
    m08 = _built("shift_production")[1].query("Machine == 'M08'")
    run = (m08["Planned Minutes"] - m08["Unplanned Downtime (min)"] - m08["Changeover (min)"]).sum() / 60
    assert m08["Failures"].sum() == 0
    text, _ = answer("shift_production", "segment_compare", "MTBF by machine", kpi="mtbf",
                     segment_column="Machine")
    assert f"M08: no failure in {run:,.2f} hours of run time (a lower bound)." in text, text
    text, _ = answer("hr_roster", "segment_compare", "average salary by department", kpi="avg_salary",
                     group_by="Department")
    assert "Not shown: Legal, Finance (a group of fewer than 11 people" in text, text
    text, _ = answer("hr_roster", "segment_compare", "headcount by gender in sales", kpi="headcount",
                     group_by="Gender", segment_column="Department", segment_value="Sales")
    assert "1 group not shown (fewer than 11 people)." in text and "Non-binary" not in text, text


def test_each_of_a_few_groups_is_named_with_its_value():
    """E2E gate e2e-86 ('average temperature on each line?'): the answer named the leader and the
    lowest of 3 lines; line C, the one whose °F readings were converted, was never said."""
    text, _ = answer("machine_readings", "segment_compare", "What is the average temperature on each line?",
                     metric_column="Temperature", segment_column="Line", aggregation="mean")
    assert "'Line B' leads at 71.99; 'Line C' at 71.14; 'Line A' is lowest at 69.96." in text, text


def test_the_work_order_tiles_carry_the_preventive_share_by_count():
    """Review 2026-11-17: the plan's work-order tiles are corrective WOs, MTTR, PM share (by
    count) and open WOs; PM share had no tile."""
    from agents.visualization_agent import pack_kpis
    _, clean, contract, truth = _built("work_orders")
    tiles = {k.label: k.value for k in pack_kpis(clean, contract)}
    assert tiles["Preventive share of work orders"] == f"{truth['pm_share']:.1%}", tiles


def test_mtbf_and_mttr_say_their_basis_and_a_machine_that_never_failed_is_answered():
    """Review 2026-11-17 (spec must-say; C5 near D7-14): 'MTBF: 42.29 hours' gave no failure
    count and no assumption, MTTR never said its 40 open orders were left out, M08's MTBF was
    'I couldn't answer this', and MTTR's How listed the 18 corrective spellings twice."""
    text, _ = answer("shift_production", "segment_compare", "what is MTBF", kpi="mtbf")
    assert text.startswith("MTBF: 42.29 hours. From 325 failures; it assumes a constant failure rate"), text
    text, _ = answer("shift_production", "segment_compare", "MTBF for M08", kpi="mtbf",
                     segment_column="Machine", segment_value="M08")
    assert "couldn't answer" not in text and "(a lower bound)" in text, text
    text, _ = answer("work_orders", "segment_compare", "what is our MTTR", kpi="mttr")
    assert "3.97 hours" in text and "40 open work orders are left out, so it can read low" in text, text
    assert text.count("emergency maintenance") == 1, text


@pytest.mark.parametrize("intent, question, params", [
    ("time_series", "monthly sum of temperature", dict(aggregation="sum", frequency="M")),
    ("segment_compare", "total temperature by line", dict(segment_column="Line", aggregation="sum")),
    ("top_n", "top sensors by total temperature", dict(group_by="Sensor", aggregation="sum", n=3))])
def test_a_reading_is_averaged_even_when_a_total_is_asked(intent, question, params):
    """Review 2026-11-17 (T18; C6, D1-16): "total temperature by line" said 'Line B leads at
    468,696.77'. A pack's number role is never summed (Role.kind 'number'); a 0/1 flag still
    counts (HR's `left`). The answer says why."""
    text, _ = answer("machine_readings", intent, question, metric_column="Temperature", **params)
    assert "sum" not in text.lower().replace("summed", "") and "468,696" not in text, text
    assert "averaged, never summed" in text, text
    truth = _built("machine_readings")[3]
    assert not any(f"{v:,.0f}"[:5] in text for v in truth["naive_temp_sum_by_line"].values()), text
    if intent == "segment_compare":
        assert f"{truth['mean_temp_c_by_line']['Line B']:.2f}" in text, text


@pytest.mark.parametrize("n, said", [
    (5, "'a' leads at 5.00; 'b' at 4.00; 'c' at 3.00; 'd' at 2.00; 'e' is lowest at 1.00"),
    (6, "'a' leads at 6.00; 'f' is lowest at 1.00")])
def test_five_groups_are_each_named_and_six_only_their_ends(n, said):
    from agents.targeted_analysis_agent import _lead_and_last
    order = pd.Series(range(n, 0, -1), index=list("abcdef"[:n]), dtype=float)
    assert _lead_and_last(order) == said
