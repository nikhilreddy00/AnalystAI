"""Phase 5.5 WP3 reviews (failure paths + parity, eval/correctness, frontend): one test per
finding, each failing on 1667fd6."""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd
import pytest

from agents.ingestion_agent import build_contract
from evals import datagen
from orchestrator.state import ConnectorType, TargetedParams


def _contract(df):
    return build_contract(df, ConnectorType.CSV, {})


def _balances(rows):
    return pd.DataFrame(rows, columns=["account_id", "snapshot_date", "segment", "branch",
                                       "balance", "deposits"])


def _findings(**kw):
    from orchestrator.state import AnalysisFindings
    base = dict(eda_summary={}, correlations={}, trends=[], segments=[], anomalies=[],
                statistical_tests=[], forecast_points=[], distributions={}, code_executed="")
    return AnalysisFindings(**{**base, **kw})


def _bank_contract():
    return _contract(datagen.make("bank_balances", 1)[0])


# ── a level's current total ─────────────────────────────────────────────────────────────────

def test_the_current_total_counts_each_account_once_where_it_is_now():
    from agents.utils import aggregate, latest_rows
    df = _balances([[1, "2026-01-31", "retail", "Downtown", 100.0, 0], [1, "2026-02-28", "retail", "Harbor", 110.0, 0],
                    [2, "2026-01-31", "retail", "Downtown", 500.0, 0],          # closed in February
                    [3, "2026-01-31", "retail", "Hillside", 1000.0, 0], [3, "2026-02-28", "retail", "Hillside", np.nan, 0],
                    [4, "2026-01-31", "retail", "Hillside", 50.0, 0], [4, "2026-02-28", "retail", "Hillside", 60.0, 0]])
    c = _bank_contract()
    got = aggregate(df, ["branch"], "balance", "last", c).to_dict()
    assert got == {"Harbor": 110.0, "Hillside": 60.0}
    bars = latest_rows(df, "balance", c).groupby("branch")["balance"].sum().to_dict()
    assert bars == got   # the answer and its chart agree


def test_an_undated_snapshot_is_never_the_latest():
    from agents.utils import aggregate
    df = _balances([[1, "2026-01-31", "retail", "A", 100.0, 0], [1, None, "retail", "A", 5.0, 0]])
    assert aggregate(df, ["branch"], "balance", "last", _bank_contract()).to_dict() == {"A": 100.0}


def test_a_level_whose_account_column_was_dropped_is_never_summed():
    from agents import targeted_analysis_agent as ta
    from agents.analysis_agent import _aggregation_line
    from agents.visualization_agent import build_context, group_bars
    df, _ = datagen.make("bank_balances", 1)
    c = _bank_contract()
    c.columns = [x for x in c.columns if x.name != "account_id"]   # as cleaning's drop_column
    df = df.drop(columns="account_id")
    r = ta.handle_top_n(df, TargetedParams(metric_column="balance", group_by="branch",
                                           aggregation="sum"), c)
    assert r.unanswerable_reason and "can't be totalled" in r.answer_text
    assert group_bars(df, "branch", "balance", "last", build_context(df, contract=c)) is None
    line = _aggregation_line(next(x for x in c.columns if x.name == "balance"), {x.name for x in c.columns})
    assert "was removed" in line


def test_the_built_in_mom_skips_a_level_without_its_account():

    from agents.analysis_agent import builtin_result
    from orchestrator.state import AnalysisTask
    df, _ = datagen.make("bank_balances", 1)
    c = _bank_contract()
    result = builtin_result(df.drop(columns="account_id"), c, AnalysisTask(raw_query="q", analysis_types=["trend"],
                                              target_metrics=["balance"]))
    assert result["trends"] == []   # no number beats the summed-snapshots trap


def test_the_mom_months_come_from_the_levels_own_time_column():

    from agents.analysis_agent import builtin_result
    from orchestrator.state import AnalysisTask
    df, truth = datagen.make("bank_balances", 1)
    df.insert(1, "open_date", "2020-01-01")   # an earlier date column: not the level's
    c = _contract(df)
    assert c.date_columns[0] == "open_date"
    result = builtin_result(df, c, AnalysisTask(raw_query="q", analysis_types=["trend"],
                                              target_metrics=["balance"]))
    got = [t["pct_change"] for t in result["trends"] if not t.get("segment")]
    assert got and abs(got[0] - truth["balance_mom_pct"]) < 0.01   # the level's own months


def test_an_explicit_sum_of_an_averaged_column_is_kept():
    from agents.utils import effective_agg
    c = _contract(datagen.make("employees", 1)[0])
    assert effective_agg(c, "salary", "sum") == "sum"      # "total payroll"
    assert effective_agg(c, "salary", None) == "mean"      # a default takes the layer's
    assert effective_agg(_bank_contract(), "balance", "sum") == "last"


def test_a_model_finding_that_sums_a_level_is_recomputed_not_failed():
    """5.5 live run: the banking overview failed when the model summed balances twice."""
    from agents.analysis_agent import repair_breakdowns
    from orchestrator.state import SegmentResult
    df, _ = datagen.make("bank_balances", 1)
    seg = SegmentResult(segment_column="segment", metric_column="balance", aggregation="sum",
                        results=[{"name": "business", "value": 1.0}])
    f = _findings(segments=[seg])
    notes = repair_breakdowns(f, df, _bank_contract())
    latest = df[df.snapshot_date == df.snapshot_date.max()].groupby("segment").balance.sum()
    assert notes and {r["name"]: r["value"] for r in f.segments[0].results} == pytest.approx(
        latest.to_dict()) and f.segments[0].aggregation == "last"
    gone = _findings(segments=[seg.model_copy(update={"aggregation": "sum"})])
    assert repair_breakdowns(gone, df.drop(columns="account_id"), _bank_contract()) and not gone.segments


def test_charts_of_a_summed_level_finding_show_the_current_total():
    from agents.visualization_agent import build_context, charts_for_findings
    from orchestrator.state import SegmentResult
    df, _ = datagen.make("bank_balances", 1)
    seg = SegmentResult(segment_column="segment", metric_column="balance", aggregation="sum",
                        results=[{"name": "business", "value": 1.0}])
    ctx = build_context(df, findings=_findings(segments=[seg]),
                        contract=_bank_contract())
    bars = [c for c in charts_for_findings(ctx) if c.x_key == "segment"]
    latest = df[df.snapshot_date == df.snapshot_date.max()].groupby("segment").balance.sum()
    assert bars and {r["segment"]: r["balance"] for r in bars[0].data} == pytest.approx(latest.to_dict())


def test_the_layer_rules_sit_outside_the_untrusted_block():
    from agents.analysis_agent import build_context
    from orchestrator.state import AnalysisSession, AnalysisTask
    s = AnalysisSession(session_id="x", data_contract=_bank_contract(), task_plan=AnalysisTask(
        raw_query="q", analysis_types=["trend"], target_metrics=["balance"]))
    text = build_context(s).render()
    before = text[:text.index("How columns aggregate")]
    assert before.count("<untrusted_data>") == before.count("</untrusted_data>") >= 1   # outside every block


# ── layers ──────────────────────────────────────────────────────────────────────────────────

def test_a_first_window_and_a_compound_entity_are_honoured(monkeypatch):
    import semantic_layer
    layer = semantic_layer.SemanticLayer.model_validate({"tables": [
        {"name": "t", "source": "f.t", "primary_key": ["acct", "ccy", "d"],
         "columns": [{"name": "acct", "type": "id"}, {"name": "ccy", "type": "dimension"},
                     {"name": "d", "type": "time"}, {"name": "bal", "type": "fact"}]}],
        "metrics": [{"name": "m", "table": "t", "expr": "SUM(t.bal)",
                     "non_additive_dimension": {"name": "d", "window": "first"}}]})
    monkeypatch.setattr(semantic_layer, "file_layers", lambda: (layer,))
    hints, _ = semantic_layer.column_hints(["acct", "ccy", "d", "bal"])
    assert hints["bal"]["aggregation"] == "first" and hints["bal"]["by"] == ["acct", "ccy"]


def test_a_configured_layer_that_is_a_shipped_sample_counts_once(monkeypatch):
    import semantic_layer
    from api.config import settings
    monkeypatch.setattr(settings, "semantic_layer_path", str(semantic_layer.SAMPLE_LAYERS / "banking.yaml"))
    semantic_layer.configured_layer.cache_clear()
    semantic_layer.file_layers.cache_clear()
    try:
        hints, note = semantic_layer.column_hints(list(datagen.make("bank_balances", 1)[0].columns))
        assert hints["balance"]["aggregation"] == "last", note
    finally:
        monkeypatch.undo()
        semantic_layer.configured_layer.cache_clear()
        semantic_layer.file_layers.cache_clear()


# ── answers, labels, logs ───────────────────────────────────────────────────────────────────

def test_an_empty_result_says_so_instead_of_crashing():
    from agents import targeted_analysis_agent as ta
    df = pd.DataFrame({"g": pd.Series([], dtype=str), "v": pd.Series([], dtype=float)})
    for h, p in (("handle_top_n", dict(metric_column="v", group_by="g")),
                 ("handle_segment_compare", dict(metric_column="v", segment_column="g"))):
        r = getattr(ta, h)(df, TargetedParams(**p), _contract(pd.DataFrame({"g": ["a"], "v": [1.0]})))
        assert r.unanswerable_reason and "no rows" in r.answer_text


@pytest.mark.parametrize("col", ["uuid", "guid", "bid", "paid"])
def test_a_short_or_unrelated_id_is_not_an_entity(col):
    from agents.visualization_agent import _detect_entity_singular
    df = pd.DataFrame({col: [f"x{i}" for i in range(5)], "amount": range(5)})
    assert _detect_entity_singular(df.columns, "", df) == "record"


def test_warnings_and_coloured_messages_are_scrubbed():
    import io
    import warnings

    from api.logging_scrub import ScrubFilter, install
    from llm.client import pii_values
    pii_values.set({"Ana Ito"})
    rec = logging.LogRecord("uvicorn.error", logging.INFO, "f", 1, "hi %s", ("Ana Ito",), None)
    rec.color_message = "hi %s"
    ScrubFilter().filter(rec)
    assert "color_message" not in rec.__dict__ and "Ana Ito" not in rec.getMessage()
    out = io.StringIO()
    h = logging.StreamHandler(out)
    logging.getLogger("py.warnings").addHandler(h)
    try:
        logging.captureWarnings(False)   # pytest swaps the warnings hook per test: start clean
        install()
        h.addFilter(ScrubFilter())
        warnings.warn("cannot read Ana Ito's row")
        assert "Ana Ito" not in out.getvalue() and "cannot read" in out.getvalue()
    finally:
        logging.getLogger("py.warnings").removeHandler(h)
        logging.captureWarnings(False)


@pytest.mark.parametrize("vals, unit, parsed", [
    (["10,50 €", "1.249,00 €", "3,00 €"], "EUR", [10.5, 1249.0, 3.0]),
    (["$1,249.00", "($3,000.00)", "$5"], "USD", [1249.0, -3000.0, 5.0]),
    (["$1", "€2", "$3"], None, None),          # two currencies: never added up
])
def test_written_currencies_parse_and_label(vals, unit, parsed):
    from agents.data_cleaning import try_numeric_conversion
    from agents.ingestion_agent import _written_currency
    s = pd.Series(vals * 4)
    assert _written_currency(s) == unit
    r = try_numeric_conversion(s)
    assert (r[0].tolist()[:3] if r else None) == parsed


def test_backend_money_matches_the_browsers_strings():
    """The same strings frontend/src/lib/formatUnit.test.ts expects from Intl en-US."""
    from agents.visualization_agent import _format_value
    assert _format_value(1234.5, "currency", "EUR") == "€1,234.50"
    assert _format_value(1234.5, "currency", "JPY") == "¥1,235"
    assert _format_value(1234.5, "currency", "CHF") == "CHF 1,234.50"
    assert _format_value(1234.5, "currency", "CAD") == "CA$1,234.50"
    assert _format_value(-3, "currency", "GBP") == "-£3.00"
    assert _format_value(70.617, "number", "°C") == "70.62 °C"
    assert _format_value(2.675, "number") == "2.68"
    assert _format_value(5, "number", "RPM") == "5.00 RPM"   # not a currency


def test_chart_axes_name_the_unit():
    from agents.visualization_agent import build_context, month_line
    df, _ = datagen.make("sensor_readings", 1)
    line = month_line(df, "reading_time", "temperature", build_context(df, contract=_contract(df)))
    assert "(°C)" in line.y_label


def test_staggered_weekly_snapshots_all_count_in_the_current_total():
    """Audit 1: 70 accounts, each snapshotted weekly on its own weekday, data ending early in a
    month: every account's latest balance counts (70,000, not the 30,000 of one month's slice)."""
    from agents.utils import aggregate
    rows = []
    for a in range(70):
        start = pd.Timestamp("2024-09-02") + pd.Timedelta(days=a % 7)
        for w in range(9):
            d = start + pd.Timedelta(weeks=w)
            if d <= pd.Timestamp("2024-11-03"):
                rows.append([a, d.strftime("%Y-%m-%d"), ["retail", "business"][a % 2], "B", 1000.0, 0])
    got = aggregate(_balances(rows), ["segment"], "balance", "last", _bank_contract())
    assert got.sum() == 70_000


def test_charts_answers_and_the_trend_share_the_levels_time_axis():
    from agents import targeted_analysis_agent as ta
    from agents.visualization_agent import build_context, month_line
    df, truth = datagen.make("bank_balances", 1)
    df.insert(1, "open_date", "2015-03-01")
    c = _contract(df)
    line = month_line(df, build_context(df, contract=c).date, "balance", build_context(df, contract=c))
    assert abs(line.data[-1]["balance"] - truth["month_end_total_last"]) < 0.01
    r = ta.handle_time_series(df, TargetedParams(metric_column="balance"), c)
    assert "2026-10-31" in r.answer_text and "2015" not in r.answer_text


def test_a_birth_date_never_becomes_the_charts_time_axis():
    from agents.visualization_agent import build_context
    df = pd.DataFrame({"date_of_birth": pd.date_range("1950-01-01", periods=10, freq="400D"),
                       "order_date": pd.date_range("2026-01-01", periods=10), "amount": range(10)})
    assert build_context(df, contract=_contract(df)).date == "order_date"


def test_a_breakdown_for_a_stated_period_is_dropped_not_replaced_by_today():
    from agents.analysis_agent import repair_breakdowns
    from orchestrator.state import SegmentResult
    df, _ = datagen.make("bank_balances", 1)
    f = _findings(segments=[SegmentResult(segment_column="segment", metric_column="balance",
                                          aggregation="sum", results=[{"name": "b", "value": 1.0}])])
    notes = repair_breakdowns(f, df, _bank_contract(), timeframe="2026-Q2")
    assert not f.segments and "2026-Q2" in notes[0]


def test_the_level_mom_drops_a_partial_last_month_like_its_chart():

    from agents.analysis_agent import builtin_result
    from orchestrator.state import AnalysisTask
    df, _ = datagen.make("bank_balances", 1)
    df = df[df.snapshot_date <= "2026-10-09"]   # one Friday of October
    c = _bank_contract()
    result = builtin_result(df, c, AnalysisTask(raw_query="q", analysis_types=["trend"],
                                              target_metrics=["balance"]))
    from agents.utils import aggregate
    m = pd.to_datetime(df.snapshot_date).dt.to_period("M")
    tot = aggregate(df.assign(_m=m), ["_m"], "balance", "last", c, over_time=True)
    want = round((tot.iloc[-2] / tot.iloc[-3] - 1) * 100, 2)   # Sep vs Aug: October is partial
    got = [t for t in result["trends"] if not t.get("segment")]
    assert got and got[0]["pct_change"] == want and got[0]["n"] > 0


# ── final audit 1: parsing ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("vals, want", [
    (["1.249,50", "980,00", "2.000", "15,25", "3,10"], [1249.5, 980.0, 2000.0, 15.25, 3.1]),
    (["1,5", "2,25", "10", "3"], [1.5, 2.25, 10.0, 3.0]),
    (["1,250", "2,500", "10"], [1250, 2500, 10]),          # US thousands stay thousands
])
def test_decimal_commas_are_read_as_decimals(vals, want):
    from agents.data_cleaning import try_numeric_conversion
    assert try_numeric_conversion(pd.Series(vals))[0].tolist() == want


@pytest.mark.parametrize("name, dob", [("births", False), ("birth_rate", False),
                                       ("birth_weight_g", False), ("birth_year", True),   # fail closed
                                       ("date_of_birth", True), ("birth_date", True), ("dob", True)])
def test_only_birth_dates_are_dates_of_birth(name, dob):
    from guardrails import pii_kind
    values = ["1950-01-02", "1961-03-04"] if dob else [120, 98]
    assert bool(pii_kind(name, values)) is dob


def test_json_keeps_codes_as_written():
    from connectors.csv_connector import _read_bytes
    df, _ = _read_bytes(b'[{"zip": "02134", "id": "00731", "amt": 1.5}]', ".json")
    assert df.loc[0, "zip"] == "02134" and df.loc[0, "id"] == "00731" and df.loc[0, "amt"] == 1.5


@pytest.mark.parametrize("csv", [b"month,x\n01,1\n12,2\n", b"hhmm,x\n0930,1\n1400,2\n"])
def test_padded_months_and_times_stay_numbers(csv):
    from connectors.csv_connector import _read_bytes
    assert pd.api.types.is_numeric_dtype(_read_bytes(csv, ".csv")[0].iloc[:, 0])


def test_no_kpi_tile_averages_a_level_over_its_snapshots():
    from agents.visualization_agent import classify_columns, generate_smart_kpis
    df, _ = datagen.make("bank_balances", 1)
    kpis = generate_smart_kpis(df, classify_columns(df), contract=_bank_contract())
    assert not any(k.recipe and k.recipe.column == "balance" for k in kpis)


def test_the_shown_code_for_a_level_takes_the_latest_snapshot_first():
    from agents import targeted_analysis_agent as ta
    df, _ = datagen.make("bank_balances", 1)
    r = ta.handle_top_n(df, TargetedParams(metric_column="balance", group_by="segment"), _bank_contract())
    code = r.chart_configs[0].rationale.code
    assert "latest_rows" in code and ".sum()" in code and "(USD)" in r.chart_configs[0].y_label


def test_user_errors_name_a_step_not_an_exception():
    from orchestrator.graph import _fail
    from orchestrator.state import AnalysisSession
    s = AnalysisSession(session_id="f")
    _fail(s, "Intake agent", KeyError("x"))
    assert "KeyError" not in s.error_message and "question-reading" in s.error_message
