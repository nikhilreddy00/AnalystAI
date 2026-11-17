"""Domain 7 parsing edges: clock times and day dates stripped from claims, a column's time rule,
the chart roles of text dates, a role column's names at exactly half, and the overview's groups."""
import pandas as pd

import domain_pack
from connectors.base import as_times
from guardrails import pii_kind, without_dates
from orchestrator.state import (AnalysisSession, ChartConfig, ChartType, QuestionIntent,
                                TargetedResponse)


def _answer(text: str) -> AnalysisSession:
    s = AnalysisSession(session_id="t")
    s.targeted_response = TargetedResponse(intent=QuestionIntent.SEGMENT_COMPARE, answer_text=text)
    return s


# ── guardrails: clock times and day dates ─────────────────────────────────────

def test_clock_times_go_by_hour_seconds_and_am_pm():
    assert without_dates("ran 13:30 x", False) == "ran   x"      # 13 is an hour, not a ratio
    assert without_dates("ran 12:30 x", False) == "ran 12:30 x"  # could be a duration: kept
    assert without_dates("ran 2:30pm x", False) == "ran   x"
    assert without_dates("ran 2:30:15 x", False) == "ran   x"


def test_slash_and_month_day_dates_leave_one_space():
    assert without_dates("shipped 3/14/2025 ok", False) == "shipped   ok"
    assert without_dates("on March 5 we", False) == "on   we"


def test_a_role_column_half_of_whose_values_are_logins_is_a_name():
    assert pii_kind("technician", ["JSMITH", "pump 3"])[0] == "name"


# ── evals: answer_numbers ─────────────────────────────────────────────────────

def test_a_year_after_a_cue_is_no_number():
    from evals.scoring import answer_numbers
    assert answer_numbers(_answer("In 2025 sales were 40")) == [40.0]


def test_report_numbers_read_the_charts_and_answer_numbers_do_not():
    from evals.scoring import answer_numbers
    s = _answer("sales were 40")
    s.chart_configs = [ChartConfig(id="c", chart_type=ChartType.BAR, title="t", x_key="x",
                                   y_keys=["y"], data=[{"x": "a", "y": 777}], source_finding="f")]
    assert answer_numbers(s) == [40.0]
    assert 777.0 in answer_numbers(s, report=True)


# ── connectors: as_times ──────────────────────────────────────────────────────

def test_as_times_keeps_the_dates_of_a_column_with_junk():
    out = as_times(pd.Series(["2024-01-05", "pump"]))
    assert out.iloc[0] == pd.Timestamp("2024-01-05") and pd.isna(out.iloc[1])
    assert as_times(pd.Series([None, None])).isna().all()


# ── visualization: classify_columns ───────────────────────────────────────────

def _roles(df):
    from agents.visualization_agent import classify_columns
    return classify_columns(df)


def test_text_dates_judged_on_the_first_100_rows_at_80_percent():
    dates = pd.date_range("2024-01-01", periods=80).strftime("%Y-%m-%d").tolist()
    when = dates + [f"junk{i}" for i in range(120)]        # rows 0-99: 80 dates, 20 junk
    df = pd.DataFrame({"when": when, "line": ["a", "b", "c", "d"] * 50})
    roles = _roles(df)
    assert "when" in roles["datetime_cols"]
    assert "line" in roles["low_card_cat"]                 # the next column is still read


def test_three_distinct_text_dates_are_categories_four_are_dates():
    three = pd.DataFrame({"when": ["2024-01-01", "2024-01-02", "2024-01-03"] * 4})
    four = pd.DataFrame({"when": ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04"] * 3})
    assert "when" not in _roles(three)["datetime_cols"]
    assert "when" in _roles(four)["datetime_cols"]


# ── visualization: _overview_asks ─────────────────────────────────────────────

PACK = {
    "domain": "toyplant", "signals": [{"a": ["unit"], "b": ["site"]}],
    "roles": {"unit": {"kind": "id", "headers": ["unit"]},
              "site": {"kind": "category", "headers": ["site"]},
              "day": {"kind": "date", "headers": ["day"]}},
    "kpis": [{"name": "units", "label": "Units", "num": [[{"nunique": "unit"}]], "tile": True},
             {"name": "sites", "label": "Sites", "num": [[{"nunique": "site"}]], "tile": True},
             {"name": "site_share", "label": "Sites per unit", "num": [[{"nunique": "site"}]],
              "den": [[{"nunique": "unit"}]], "tile": True}],
    "overview_groups": ["site"], "overview_kpis": ["units", "sites", "site_share"],
    "caveats": {"read_as_domain_weak": "x"}}


def _asks(roles):
    from agents.visualization_agent import _overview_asks
    pack = domain_pack.Pack.model_validate(PACK)
    return [(i.value, p.kpi, p.group_by) for i, p in _overview_asks(pack, roles, list(roles.values()))]


def test_a_count_of_the_group_by_itself_is_skipped_but_a_ratio_of_it_is_kept():
    roles = {r: r for r in ("unit", "site", "day")}
    assert _asks(roles) == [("time_series", "units", None), ("top_n", "units", "site"),
                            ("top_n", "site_share", "site")]


def test_no_group_column_gives_the_time_series_only():
    roles = {r: r for r in ("unit", "day")}
    assert _asks(roles) == [("time_series", "units", None)]
