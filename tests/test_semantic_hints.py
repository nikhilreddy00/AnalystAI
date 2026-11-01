"""Phase 5.5 WP3: domain meaning in YAML for flat files (semantic_layers/), and units. The plan's
F4 (layer hints) and F5 (units) cells."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

import semantic_layer
from agents.ingestion_agent import build_contract, prepare_frame
from evals import datagen
from orchestrator.state import ConnectorType, TargetedParams


def _contract(df):
    return build_contract(df, ConnectorType.CSV, {})


def _hint(c, col):
    return next(x for x in c.columns if x.name == col)


# ── F4: matching ─────────────────────────────────────────────────────────────────────────────

def test_one_matching_table_gives_its_hints():
    c = _contract(datagen.make("bank_balances", 1)[0])
    b = _hint(c, "balance")
    assert (b.aggregation, b.aggregation_by, b.aggregation_time, b.unit) == (
        "last", ["account_id"], "snapshot_date", "USD")
    assert _hint(c, "deposits").aggregation == "sum"
    assert any("semantic layer table 'balances'" in a for a in c.source_evidence.assumptions)


def test_no_match_leaves_the_old_datasets_alone():
    for name in ("sales", "subscriptions", "simpson", "mixed_dates"):
        c = _contract(datagen.make(name, 1)[0])
        assert not any(x.aggregation or x.unit for x in c.columns if x.name != "revenue"), name


def test_two_matching_tables_give_none_and_say_so(monkeypatch):
    layer = semantic_layer.SemanticLayer.model_validate({"tables": [
        {"name": n, "source": f"f.{n}", "primary_key": ["a"],
         "columns": [{"name": "a", "type": "id"}, {"name": "x", "type": "fact"}]} for n in ("t1", "t2")],
        "metrics": [{"name": "m", "table": "t1", "expr": "SUM(t1.x)"}]})
    monkeypatch.setattr(semantic_layer, "file_layers", lambda: (layer,))
    hints, note = semantic_layer.column_hints(["a", "x"])
    assert hints == {} and "t1" in note and "t2" in note


def test_the_healthcare_layer_names_the_deidentified_columns():
    df, _ = datagen.make("encounters", 1)
    _, c = prepare_frame(df, _contract(df), ConnectorType.CSV)
    assert _hint(c, "length_of_stay").aggregation == "mean"
    assert _hint(c, "length_of_stay").unit == "days"


def test_a_level_without_an_entity_is_rejected():
    with pytest.raises(ValueError, match="needs an entity"):
        semantic_layer.SemanticLayer.model_validate({"tables": [
            {"name": "t", "source": "f.t", "primary_key": ["d"],
             "columns": [{"name": "d", "type": "time"}, {"name": "b", "type": "fact"}]}],
            "metrics": [{"name": "m", "table": "t", "expr": "SUM(t.b)",
                         "non_additive_dimension": {"name": "d", "window": "last"}}]})


async def test_a_resume_keeps_the_hints_its_run_started_with(monkeypatch, run_env, tmp_path):
    """The contract is checkpointed; a crash resume reloads the frame, never the contract."""
    import connectors.csv_connector as csvc
    from orchestrator.runner import _restore_frame
    from orchestrator.state import AnalysisSession, ConnectorConfig
    monkeypatch.setattr(csvc, "_UPLOADS_DIR", tmp_path.resolve())
    df, _ = datagen.make("bank_balances", 1)
    path = tmp_path / "b.csv"
    path.write_text(df.to_csv(index=False))
    s = AnalysisSession(session_id="s", connector_config=ConnectorConfig(
        type=ConnectorType.CSV, file_path=str(path)))
    from agents.ingestion_agent import run_ingestion_agent
    s.data_contract = await run_ingestion_agent(s, None)
    before = s.data_contract.model_dump_json()
    monkeypatch.setattr(semantic_layer, "file_layers", lambda: ())   # the layer was edited
    await _restore_frame(run_env, s, f"raw:{s.data_contract.fingerprint}")
    assert s.data_contract.model_dump_json() == before and run_env.frame


# ── F4: every consumer ──────────────────────────────────────────────────────────────────────

def test_analysis_context_explains_levels_only_where_a_layer_matched():
    from agents.analysis_agent import build_context
    from orchestrator.state import AnalysisSession, AnalysisTask
    task = AnalysisTask(raw_query="q", analysis_types=["trend"], target_metrics=["balance"])
    s = AnalysisSession(session_id="x", data_contract=_contract(datagen.make("bank_balances", 1)[0]),
                        task_plan=task)
    assert "balance (USD) is a level" in build_context(s).render()
    s.data_contract = _contract(datagen.make("sales", 1)[0])
    assert "aggregate (semantic layer)" not in build_context(s).render()


@pytest.mark.parametrize("name, col, key", [("bank_balances", "balance", "balance_mom_pct"),
                                            ("sensor_readings", "temperature", "temp_mom_pct"),
                                            ("saas_accounts", "mrr", "mrr_mom_pct")])
def test_the_built_in_mom_uses_the_layers_aggregation(name, col, key):
    from agents.analysis_agent import _build_standard_code
    from orchestrator.state import AnalysisTask
    df, truth = datagen.make(name, 1)
    c = _contract(df)
    result = {"trends": [], "correlations": {}, "segments": []}
    exec(_build_standard_code(c, AnalysisTask(raw_query="q", analysis_types=["trend"],
                                              target_metrics=[col])),
         {"df": df, "pd": pd, "np": np, "result": result, "json": json})
    got = [t["pct_change"] for t in result["trends"] if not t.get("segment")]
    assert got and abs(got[0] - truth[key]) < 0.01


def test_monthly_line_and_bars_add_up_a_level_by_its_latest_snapshot():
    from agents.visualization_agent import build_context, group_bars, month_line
    df, truth = datagen.make("bank_balances", 1)
    ctx = build_context(df, contract=_contract(df))
    line = month_line(df, "snapshot_date", "balance", ctx)
    assert abs(line.data[-1]["balance"] - truth["month_end_total_last"]) < 0.01
    bars = group_bars(df, "segment", "balance", "last", ctx)
    latest = df[df.snapshot_date == df.snapshot_date.max()].groupby("segment").balance.sum()
    assert {r["segment"]: r["balance"] for r in bars.data} == pytest.approx(latest.to_dict())


@pytest.mark.parametrize("handler, params", [
    ("handle_time_series", dict(metric_column="balance", aggregation="sum", frequency="M")),
    ("handle_top_n", dict(metric_column="balance", group_by="segment", aggregation="sum")),
    ("handle_segment_compare", dict(metric_column="balance", segment_column="segment",
                                    aggregation="sum")),
])
def test_an_explicit_sum_of_a_level_becomes_its_period_end_total(handler, params):
    from agents import targeted_analysis_agent as ta
    df, truth = datagen.make("bank_balances", 1)
    r = getattr(ta, handler)(df, TargetedParams(**params), _contract(df))
    assert ("period-end total" if handler == "handle_time_series" else "latest total") in r.answer_text
    naive = f"{truth['naive_last_month_sum']:,.2f}"
    assert naive not in r.answer_text


def test_a_reading_is_averaged_when_no_aggregation_is_asked():
    from agents import targeted_analysis_agent as ta
    df, truth = datagen.make("sensor_readings", 1)
    r = ta.handle_top_n(df, TargetedParams(metric_column="temperature", group_by="line"),
                        _contract(df))   # top-N defaults to a sum; the layer says it averages
    assert f"{truth['mean_temp_by_line']['B']:,.2f}" in r.answer_text


def test_an_explicit_mean_or_count_is_kept():
    from agents.utils import effective_agg
    c = _contract(datagen.make("bank_balances", 1)[0])
    assert effective_agg(c, "balance", "mean") == "mean" and effective_agg(c, "balance", "count") == "count"
    assert effective_agg(c, "deposits", None) == "sum" and effective_agg(None, "x", None) is None


# ── F5: units ───────────────────────────────────────────────────────────────────────────────

def _kpi(df, contract):
    from agents.visualization_agent import classify_columns, generate_smart_kpis, kpi_mismatches
    kpis = generate_smart_kpis(df, classify_columns(df), contract=contract)
    assert not kpi_mismatches(kpis, df)   # the recheck formats the same way as the tile
    return next((k for k in kpis if k.recipe and k.recipe.kind == "metric_avg"), None)


def test_a_layer_unit_formats_the_tile():
    df, _ = datagen.make("sensor_readings", 1)
    k = _kpi(df, _contract(df))
    assert k.value.endswith("\u00a0°C") and k.recipe.unit == "°C" and "$" not in k.value


def test_a_currency_written_in_the_file_is_used():
    df = pd.DataFrame({"charge": ["€1,249.00", "€12.50", "€3.00"] * 10, "x": range(30)})
    c = _contract(df)
    assert _hint(c, "charge").unit == "EUR"
    from agents.visualization_agent import _format_value
    assert _format_value(1249.0, "currency", "EUR") == "€1,249.00"


def test_no_unit_keeps_the_name_rule_so_sales_still_shows_dollars():
    df, _ = datagen.make("sales", 1)
    k = _kpi(df, _contract(df))
    assert k.value.startswith("$") and k.recipe.currency is None


def test_an_unknown_code_is_a_suffix_not_a_crash():
    from agents.visualization_agent import _format_value, _kpi_format_kind
    assert _kpi_format_kind("x", pd.Series([1.0]), set(), "EURO") == "number"
    assert _format_value(2.5, "number", "EURO") == "2.50\u00a0EURO"
    assert _format_value(-3.0, "currency", "GBP") == "-£3.00"


def test_a_known_currency_makes_money_of_a_column_the_name_rule_misses():
    df = pd.DataFrame({"charge": [f"€{v:,.2f}" for v in np.linspace(10, 900, 40)],
                       "visit": [f"V{i}" for i in range(40)]})
    c = _contract(df)
    df["charge"] = df["charge"].str.replace("€", "").str.replace(",", "").astype(float)   # cleaned
    k = _kpi(df, c)
    assert k.value.startswith("€") and k.recipe.format_kind == "currency" and k.recipe.currency == "EUR"
