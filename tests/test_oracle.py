"""The oracle (plan v2 R0, docs/research/2026-11-02-recurring-bug-classes.md): every number a user
reads is checked against the generator's truth — a banker's rule computed with plain pandas — and
not only against the other paths (all consumers can be wrong the same way). Then the paths must
agree, and invariances must hold (row order, header spellings, a closed account, an export overlap).

Runs at $0 through the production path: the CSV reader, the contract, cleaning, the row-kind
column. A known live bug is a strict xfail naming the plan step that fixes it: CI stays green, and
the day it is fixed the xfail fails, so the marker must go. An audit finding closes only with a new
relation here or a guard (bug → invariant)."""
import asyncio
import io
import json
import re
from functools import cache

import pandas as pd
import pytest

import domain_pack
from evals import datagen

LIVE = {"strict": True}


# ── Adapters: one per consumer, each the production entry point ──────────────────────────────

@cache
def _built(name: str, seed: int = 1, transform: str = ""):
    """(session, cleaned frame, contract, truth) through the production reader and cleaning."""
    from agents.cleaning_agent import run_cleaning_agent
    from connectors.csv_connector import CSVConnector, _read_bytes
    from orchestrator.state import AnalysisSession, AnalysisTask, ConnectorConfig, ConnectorType
    raw, truth = datagen.make(name, seed)
    raw = TRANSFORMS[transform](raw) if transform else raw
    df = _read_bytes(raw.to_csv(index=False).encode(), ".csv")[0]
    contract = CSVConnector(ConnectorConfig(type=ConnectorType.CSV)).build_data_contract(df)
    s = AnalysisSession(session_id="oracle", data_contract=contract, task_plan=AnalysisTask(
        raw_query="o", analysis_types=["eda"], target_metrics=[]))
    s.__dict__["_df_parquet"] = _parquet(df)
    clean, audit = asyncio.run(run_cleaning_agent(s))
    s.cleaning_audit = audit
    s.__dict__["_df_parquet"] = _parquet(clean)
    return s, clean, contract, truth


def _parquet(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    df.to_parquet(buf, index=False)
    return buf.getvalue()


def _num(text: str) -> float:
    """'$47,521,711.85' → 47521711.85; '0.2%' → 0.2."""
    return float(re.sub(r"[^0-9.\-]", "", str(text)))


def tiles(name: str, **kw) -> dict[str, float]:
    from agents.visualization_agent import pack_kpis
    _, clean, contract, _ = _built(name, **kw)
    return {k.recipe.kpi: _num(k.value) for k in pack_kpis(clean, contract)}


def answer(name: str, intent: str, question: str = "", **params):
    """The targeted answer to `question` (its words matter: 'average' is a mean, 'by' a total):
    (text, {x: y} of its first chart)."""
    from agents.targeted_analysis_agent import run_targeted_analysis
    from orchestrator.state import AnalysisTask, QuestionIntent, TargetedParams
    s, _, _, _ = _built(name)
    s.task_plan = AnalysisTask(raw_query=question or "q", analysis_types=["eda"], target_metrics=[],
                               question_intent=QuestionIntent(intent),
                               targeted_params=TargetedParams(**params))
    r = run_targeted_analysis(s)
    chart = r.chart_configs[0] if r.chart_configs else None
    points = {str(row[chart.x_key]): float(row[chart.y_keys[0]]) for row in chart.data} if chart else {}
    if chart is not None and chart.value_format == "percent":
        points = {k: v / 100 for k, v in points.items()}
    return r.answer_text, points


def drill_chart(name: str, x: str, y: str) -> dict[str, float]:
    from agents.drill_down_agent import _chart_data
    s, _, contract, _ = _built(name)
    return {row[x]: row[y] for row in _chart_data(s.__dict__["_df_parquet"], "BarChart", x, y, contract)}


def builtin(name: str, targets: list[str], segments: list[str], types: list[str]) -> dict:
    """The analysis agent's built-in statistics, on the frame the sandbox gets (with row kinds)."""
    from agents import analysis_agent
    from orchestrator.state import AnalysisTask
    _, clean, contract, _ = _built(name)
    frame, _ = domain_pack.with_row_kind(clean, contract)
    return json.loads(json.dumps(analysis_agent.builtin_result(frame, contract, AnalysisTask(
        raw_query="q", target_metrics=targets, segments=segments, analysis_types=types)), default=str))


def segment(res: dict, by: str, metric: str, agg: str) -> dict[str, float]:
    seg = next(x for x in res["segments"] if x["segment_column"] == by
               and x["metric_column"] == metric and x["aggregation"] == agg)
    return {r["name"]: r["value"] for r in seg["results"]}


def close(got: dict | float, want: dict | float, tol: float = 0.01) -> bool:
    if isinstance(want, dict):
        return set(map(str, want)) <= set(map(str, got)) and all(
            abs(got[str(k)] - v) <= tol * max(1, abs(v)) / 100 for k, v in want.items())
    return abs(got - want) <= tol * max(1, abs(want)) / 100


# ── Truth: the loan tape (the clean export) ───────────────────────────────────────────────────

def test_loan_tape_tiles_are_the_truth():
    t, truth = tiles("loan_tape"), _built("loan_tape")[3]
    assert close(t["total_balance"], truth["outstanding_last"])
    assert t["open_accounts"] == truth["accounts_last"]
    assert close(t["avg_balance"], truth["avg_balance_last"])
    assert close(t["npl_ratio"], truth["npl_ratio_pct"], tol=1)


def test_loan_tape_balance_by_product_is_the_truth_on_every_path():
    truth = _built("loan_tape")[3]["balance_by_product_last"]
    _, kpi = answer("loan_tape", "segment_compare", kpi="total_balance", segment_column="Product")
    _, col = answer("loan_tape", "segment_compare", metric_column="Outstanding Principal",
                    segment_column="Product", aggregation="sum")
    built = segment(builtin("loan_tape", ["Outstanding Principal"], ["Product"], ["segmentation"]),
                    "Product", "Outstanding Principal", "last")
    assert close(kpi, truth) and close(col, truth) and close(built, truth)


def test_loan_tape_average_balance_by_branch_on_answers():
    truth = _built("loan_tape")[3]["avg_balance_by_branch"]
    _, kpi = answer("loan_tape", "segment_compare", kpi="avg_balance", segment_column="Branch")
    _, col = answer("loan_tape", "segment_compare", "What's the average balance per loan by branch?",
                    metric_column="Outstanding Principal", segment_column="Branch", aggregation="mean")
    built = segment(builtin("loan_tape", ["Outstanding Principal"], ["Branch"], ["segmentation"]),
                    "Branch", "Outstanding Principal", "mean")
    assert close(kpi, truth) and close(col, truth) and close(built, truth)



def test_loan_tape_average_balance_by_branch_on_the_drill_down_chart():
    truth = _built("loan_tape")[3]["avg_balance_by_branch"]
    assert close(drill_chart("loan_tape", "Branch", "Outstanding Principal"), truth)


# ── Truth: the loan tape as core systems export it ───────────────────────────────────────────

def test_a_messy_loan_tape_is_read_as_banking():
    c = _built("loan_tape_messy")[2]
    assert (c.domain, c.domain_confidence) == ("banking", "strong")


def test_a_messy_loan_tape_tiles_are_the_truth():
    t, truth = tiles("loan_tape_messy"), _built("loan_tape_messy")[3]
    assert close(t["total_balance"], truth["outstanding_last"])
    assert t["open_accounts"] == truth["accounts_last"]


def test_loans_that_stop_reporting_are_out_of_the_built_in_breakdown():
    # through the clean tape's headers (detection aside): sold mortgages must not count
    raw, truth = datagen.make("loan_tape_messy", 1)
    assert truth["sold_accounts"] == 25
    t = _built("loan_tape_messy", transform="canonical")
    built = builtin_on(t, ["Outstanding Principal"], ["Product"])
    assert close(segment(built, "Product", "Outstanding Principal", "last"), truth["balance_by_product_last"])


# ── Truth: a bank statement (newest first, Debit / Credit columns) ───────────────────────────

def test_a_bank_statement_is_read_as_banking():
    c = _built("bank_statement")[2]
    assert c.domain == "banking"


def test_a_bank_statement_raises_no_high_null_gate():
    assert _built("bank_statement")[1] is not None and not _built("bank_statement")[0].cleaning_audit.high_null_columns


def test_a_bank_statement_closing_balance_is_the_truth():
    truth = _built("bank_statement")[3]
    _, pts = answer("bank_statement", "time_series", metric_column="Balance", frequency="M")
    assert close(pts, {k: v for k, v in _month_keys(truth["month_end_balance"], pts).items()})


def test_a_bank_statement_flows_are_the_truth():
    t, truth = tiles("bank_statement"), _built("bank_statement")[3]
    assert close(t["inflows"], truth["inflows"]) and close(t["outflows"], truth["outflows"])
    assert close(t["net_flow"], truth["net_flow"])


# ── Truth: a ledger with a Dr/Cr indicator ────────────────────────────────────────────────────


@pytest.mark.parametrize("transform", ["", "presigned"])   # signed once, never twice
def test_a_dr_cr_ledger_nets_its_flows(transform):
    t, truth = tiles("ledger_drcr", transform=transform), _built("ledger_drcr")[3]
    assert close(t["net_flow"], truth["net_flow"])
    assert close(t["outflows"], truth["outflows"])


# ── Truth: a daily deposit book in three currencies ──────────────────────────────────────────

def test_a_split_by_currency_is_each_currency():
    truth = _built("deposits_daily")[3]["total_by_currency_last"]
    _, pts = answer("deposits_daily", "segment_compare", "Total balance by currency",
                    kpi="total_balance", segment_column="Currency")
    assert close(pts, truth)


def test_the_main_currency_balance_is_the_truth():
    assert close(tiles("deposits_daily")["total_balance"], _built("deposits_daily")[3]["total_usd_last"])


def test_month_end_balances_leave_closed_accounts_out():
    truth = _built("deposits_daily")[3]["month_end_total_usd"]
    _, pts = answer("deposits_daily", "time_series", kpi="total_balance", frequency="M")
    assert close(pts, {m: v for m, v in truth.items() if m in pts})


# ── Truth: the card ledger and deposit snapshots of Domain 2 ─────────────────────────────────

def test_card_ledger_fees_and_spend_are_the_truth():
    truth = _built("card_ledger")[3]
    assert close(tiles("card_ledger")["fee_income"], truth["fee_income_total"])
    _, spend = answer("card_ledger", "segment_compare", kpi="spend", segment_column="Merchant Category",
                      period_start="2026-08-01", period_end="2026-10-31")
    assert close(spend, truth["spend_by_category_q"])


def test_bank_balances_month_end_and_segment_averages_are_the_truth():
    truth = _built("bank_balances")[3]
    assert close(tiles("bank_balances")["total_balance"], truth["month_end_total_last"])
    _, avg = answer("bank_balances", "segment_compare", kpi="avg_balance", segment_column="segment")
    assert close(avg, truth["avg_balance_by_segment"])


# ── Invariances ───────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", ["loan_tape", "card_ledger", "bank_balances", "retail_lines"])
def test_row_order_changes_no_number(name):
    assert tiles(name) == tiles(name, transform="shuffle")


@pytest.mark.parametrize("name", ["loan_tape", "card_ledger"])
def test_an_exact_export_overlap_changes_no_number(name):
    assert tiles(name) == tiles(name, transform="overlap")


def test_header_spellings_change_no_number():
    assert tiles("loan_tape") == tiles("loan_tape", transform="synonyms")


# ── Transforms (metamorphic relations) ────────────────────────────────────────────────────────

def _shuffle(df: pd.DataFrame) -> pd.DataFrame:
    """Rows in another order. An export whose lines carry a layout (Shopify: an order's fields on its
    first line) keeps each order's lines together and in order; the orders move."""
    if "Name" in df.columns and df["Name"].astype(str).str.startswith("#").all():
        order = {k: i for i, k in enumerate(pd.Series(df["Name"].unique()).sample(frac=1, random_state=7))}
        return df.assign(_o=df["Name"].map(order)).sort_values("_o", kind="stable").drop(columns="_o").reset_index(drop=True)
    return df.sample(frac=1, random_state=7).reset_index(drop=True)


def _overlap(df: pd.DataFrame) -> pd.DataFrame:   # the last 200 rows exported twice
    return pd.concat([df, df.tail(200)], ignore_index=True)


def _synonyms(df: pd.DataFrame) -> pd.DataFrame:
    return df.rename(columns={"Outstanding Principal": "Principal Balance", "As Of Date": "Snapshot Date",
                              "Loan ID": "Account Number", "Days Past Due": "DPD"})


def _canonical(df: pd.DataFrame) -> pd.DataFrame:   # the messy tape under the clean tape's headers
    return df.rename(columns={"Account": "Loan ID", "As Of": "As Of Date", "Cur Bal": "Outstanding Principal",
                              "Days Late": "Days Past Due", "Status": "Loan Status"}).replace(
        {"Loan Status": {"Paid in Full": "Paid Off", "Charged-off": "Charged Off", "Non Accrual": "Non-Accrual"}})


def _presigned(raw: pd.DataFrame) -> pd.DataFrame:
    """A ledger that already signs its amounts and keeps the Dr/Cr column beside them."""
    return raw.assign(Amount=raw["Amount"].where(raw["Dr/Cr"] != "D", -raw["Amount"]))


TRANSFORMS = {"shuffle": _shuffle, "overlap": _overlap, "synonyms": _synonyms, "canonical": _canonical,
              "presigned": _presigned}


def builtin_on(built, targets: list[str], segments: list[str]) -> dict:
    from agents import analysis_agent
    from orchestrator.state import AnalysisTask
    _, clean, contract, _ = built
    frame, _ = domain_pack.with_row_kind(clean, contract)
    return json.loads(json.dumps(analysis_agent.builtin_result(frame, contract, AnalysisTask(
        raw_query="q", target_metrics=targets, segments=segments, analysis_types=["segmentation"])),
        default=str))


def _month_keys(truth: dict, pts: dict) -> dict:
    """Truth keyed 'YYYY-MM' → the chart's period labels ('YYYY-MM-DD' month ends)."""
    out = {}
    for label in pts:
        if label[:7] in truth:
            out[label] = truth[label[:7]]
    return out


# ── R2: the model's own breakdowns read the rows the tiles count ─────────────────────────────

def test_a_model_breakdown_of_a_money_column_is_recomputed_from_its_rows():
    """Plan v2 B2: the sandbox runs the model's code on every row; a breakdown of a column whose
    tiles count only some rows (pending, reversed, other currency out) is recomputed."""
    from agents.analysis_agent import repair_breakdowns
    from orchestrator.state import AnalysisFindings, SegmentResult
    _, clean, contract, _ = _built("card_ledger")
    by = contract.roles["merchant_category"]
    naive = clean.groupby(by)["Amount"].sum()
    f = AnalysisFindings(eda_summary={}, correlations={}, trends=[], anomalies=[], statistical_tests=[],
                         forecast_points=[], distributions={}, code_executed="", segments=[SegmentResult(
        segment_column=by, metric_column="Amount", aggregation="sum",
        results=[{"name": str(k), "value": float(v)} for k, v in naive.items()])])
    rows, _ = domain_pack.rows_for(clean, contract, "Amount")
    assert len(rows) < len(clean)   # the trap is there
    assert repair_breakdowns(f, clean, contract)
    assert close(segment({"segments": [s.model_dump() for s in f.segments]}, by, "Amount", "sum"),
                 rows.groupby(by)["Amount"].sum().round(4).to_dict())
    # the model's own filter (a quarter, spend only) answers the question: left as it is
    spend = clean[pd.to_numeric(clean["Amount"]) < 0].groupby(by)["Amount"].sum()
    g = AnalysisFindings(eda_summary={}, correlations={}, trends=[], anomalies=[], statistical_tests=[],
                         forecast_points=[], distributions={}, code_executed="", segments=[SegmentResult(
                             segment_column=by, metric_column="Amount", aggregation="sum",
                             results=[{"name": str(k), "value": float(v)} for k, v in spend.items()])])
    assert repair_breakdowns(g, clean, contract) == []
    assert g.segments[0].results[0]["value"] == float(spend.iloc[0])


# ── R4: machine-independent data ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", sorted(datagen.DATASETS))
def test_every_generated_float_has_at_most_4_decimals(name):
    """A float from exp()/log() differs in its last digits between macOS and Linux (the card
    ledger's salaries broke CI); a generator rounds what it emits, so pins are the same everywhere."""
    import numpy as np
    df, _ = datagen.make(name, 1)
    for c in df.columns:
        v = pd.to_numeric(df[c], errors="coerce") if df[c].dtype == object else df[c]
        if pd.api.types.is_float_dtype(v) and len(x := v.dropna()):
            assert np.allclose(x, x.round(4), rtol=0, atol=1e-9), c


def test_a_model_trend_the_built_ins_dont_make_says_who_computed_it():
    """Plan v2 B2: a QoQ of a money column whose figures leave rows out: no shared rule
    recomputes it, so its evidence says the model's code made it; a built-in MoM says nothing."""
    from agents.analysis_agent import repair_breakdowns
    from orchestrator.state import AnalysisFindings, TrendResult
    _, clean, contract, _ = _built("card_ledger")
    f = AnalysisFindings(eda_summary={}, correlations={}, anomalies=[], statistical_tests=[], segments=[],
                         forecast_points=[], distributions={}, code_executed="", trends=[
        TrendResult(column="Amount", period="QoQ", pct_change=3.0, direction="up"),
        TrendResult(column="Amount", period="MoM", pct_change=1.0, direction="up")])
    notes = repair_breakdowns(f, clean, contract, builtin={"trend:Amount:MoM"})
    assert len(notes) == 1 and notes[0].startswith("Amount QoQ: computed by the model's own code")


def test_each_repair_has_its_own_trace_label():
    from agents.analysis_agent import _repair_label
    assert _repair_label("Amount QoQ: computed by the model's own code, not …") == "model_trend_labelled"
    assert _repair_label("Amount by Merchant Category recomputed: Posted rows …") == "breakdown_recomputed"
    assert _repair_label("balance by segment recomputed per snapshot") == "level_recomputed"
    assert _repair_label("balance by segment recomputed as the latest total (…)") == "level_recomputed"
