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
    assert text.endswith(ENDING) and "%" not in text
    assert r.chart_configs[0].reference_lines == [
        {"value": 16.3191, "label": "UCL", "axis": "y"},
        {"value": 11.0, "label": "CL", "axis": "y"},
        {"value": 5.6809, "label": "LCL", "axis": "y"}]


def test_a_day_on_a_limit_is_not_beyond_it():
    """A point exactly on UCL or LCL is inside: only strictly beyond counts."""
    base = [10.0, 12.0] * 15
    _, ucl, lcl = control_limits(pd.Series(base))
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
    assert r.answer_text.endswith(" No control limits: no variation from day to day in the first "
                                  "days (a stuck instrument?).")


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
    assert f"M08: no failure in {run:,.2f} hours of run time." in text, text
    text, _ = answer("hr_roster", "segment_compare", "average salary by department", kpi="avg_salary",
                     group_by="Department")
    assert "Not shown: Legal, Finance (a group of fewer than 11 people" in text, text
    text, _ = answer("hr_roster", "segment_compare", "headcount by gender in sales", kpi="headcount",
                     group_by="Gender", segment_column="Department", segment_value="Sales")
    assert "1 group not shown (fewer than 11 people)." in text and "Non-binary" not in text, text
