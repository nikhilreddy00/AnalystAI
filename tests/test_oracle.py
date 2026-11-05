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
import os
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
    from agents.ingestion_agent import prepare_frame
    from connectors.csv_connector import CSVConnector, _read_bytes
    from orchestrator.state import AnalysisSession, AnalysisTask, ConnectorConfig, ConnectorType
    raw, truth = datagen.make(name, seed)
    raw = TRANSFORMS[transform](raw) if transform else raw
    df = _read_bytes(raw.to_csv(index=False).encode(), ".csv")[0]
    contract = CSVConnector(ConnectorConfig(type=ConnectorType.CSV)).build_data_contract(df)
    # patient data: the PHI-protected copy, as production builds it (smoke run: pooling a
    # float code column broke parquet; this adapter skipped the step)
    df, contract = prepare_frame(df, contract, ConnectorType.CSV, scope="oracle")
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

@pytest.mark.parametrize("name", ["loan_tape", "card_ledger", "bank_balances", "retail_lines",
                                  "loss_run", "policies", "hr_roster", "hr_snapshots"])
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


# ── Source invariance (Domain 3, user request 2026-11-03): the same table through every
# connector reads as the same domain, roles and PHI mode, and every computable tile agrees.

def _mocked_source(source: str, df: pd.DataFrame, csv: str, monkeypatch):
    """A connector for the REST API or Google Sheets that serves `df` as those services do (JSON
    records; every cell a formatted string), through the production connector's load()."""
    import csv as csvmod
    import io
    import socket

    import httpx

    import connectors.api_connector as ac
    from connectors.base import get_connector
    from connectors.sheets_connector import SheetsConnector
    from orchestrator.state import ConnectorConfig, ConnectorType
    if source == "api":
        body = json.loads(df.to_json(orient="records", date_format="iso"))
        monkeypatch.setattr(socket, "getaddrinfo",
                            lambda host, *a, **k: [(0, 0, 0, "", ("93.184.216.34", 0))])
        real = httpx.AsyncClient
        monkeypatch.setattr(ac.httpx, "AsyncClient", lambda **kw: real(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body)), **kw))
        return get_connector(ConnectorConfig(type=ConnectorType.API, url="https://api.example.com/x"))
    cells = list(csvmod.reader(io.StringIO(csv)))

    class Sheet:
        sheet1 = property(lambda self: self)

        def get_all_values(self):
            return cells
    conn = SheetsConnector(ConnectorConfig(type=ConnectorType.SHEETS, spreadsheet_id="x"))
    monkeypatch.setattr(conn, "_client", lambda: type("C", (), {"open_by_key": lambda s, k: Sheet()})())
    return conn


@pytest.mark.parametrize("name", ["inpatient", "health_claims", "loan_tape", "retail_lines",
                                  "policies", "loss_run", "hr_roster", "hr_snapshots"])
@pytest.mark.parametrize("source", ["xlsx", "json", "parquet", "sqlite", "postgres", "api", "sheets"])
async def test_every_source_reads_the_same_as_the_csv(name, source, tmp_path, monkeypatch):
    import connectors.csv_connector as csvc
    import domain_pack
    from connectors.base import get_connector
    from evals import run
    if source == "postgres" and not os.environ.get("TEST_PG_URL"):
        pytest.skip("TEST_PG_URL not set")
    monkeypatch.setattr(run, "UPLOADS", tmp_path)
    monkeypatch.setattr(csvc, "_UPLOADS_DIR", tmp_path.resolve())
    case = {"dataset": {"name": name, "seed": 1}}
    df, _, csv = run.dataset(case)

    async def read(src):
        conn = (_mocked_source(src, df, csv, monkeypatch) if src in ("api", "sheets") else
                get_connector(run.source_config({**case, "source": src}, df, csv)))
        frame = await conn.load()
        return frame, conn.build_data_contract(frame)
    (want, wc), (got, gc) = await read("csv"), await read(source)
    assert (gc.domain, gc.domain_confidence, gc.phi) == (wc.domain, wc.domain_confidence, wc.phi)
    assert gc.roles == wc.roles and len(got) == len(want)
    pack = domain_pack.pack_for(wc.domain)
    for k in (k for k in pack.kpis if domain_pack.computable(pack, k.name, wc.roles)):
        a = domain_pack.compute_kpi(want, wc.roles, pack, k.name)
        b = domain_pack.compute_kpi(got, gc.roles, pack, k.name)
        assert (a is None and b is None) or b == pytest.approx(a, rel=1e-9), (source, k.name, a, b)


# ── Truth: healthcare (Domain 3) ──────────────────────────────────────────────────────────────

def test_inpatient_tiles_are_the_truth():
    """Discharge-based ALOS (days from admit, same day 0), crude 30-day readmission with the CMS
    index exclusions and censoring, crude mortality: never the naive all-rows numbers."""
    t, truth = tiles("inpatient"), _built("inpatient")[3]
    assert t["discharges"] == truth["inpatient_discharges"]
    assert close(t["alos"], truth["alos_inpatient"], tol=0.3)            # shown to 2 dp
    assert close(t["readmission_rate"], 100 * truth["readmit_rate_crude"], tol=1)
    assert close(t["mortality_rate"], 100 * truth["mortality_rate_crude"], tol=3)
    assert abs(t["readmission_rate"] - 100 * truth["naive_readmit_rate"]) > 2


def test_inpatient_readmission_and_alos_by_department_are_the_truth():
    _, rate = answer("inpatient", "top_n", "readmission rate by department", kpi="readmission_rate",
                     segment_column="Department", group_by="Department")
    assert close(rate, _built("inpatient")[3]["readmit_by_dept"], tol=0.5)
    _, alos = answer("inpatient", "top_n", "ALOS by department", kpi="alos",
                     segment_column="Department", group_by="Department")
    assert close(alos, _built("inpatient")[3]["alos_by_dept"], tol=0.3)


def test_claims_tiles_are_the_truth():
    """Last version of each claim line, voided chains out, paid not billed, the header total
    never summed, the denial rate on claims (not rows) excluding pending."""
    t, truth = tiles("health_claims"), _built("health_claims")[3]
    assert close(t["paid"], truth["paid_total_final"])
    assert close(t["allowed"], truth["allowed_total_final"])
    assert t["claims"] == truth["claims_final"] and t["members"] == truth["members"]
    assert close(t["denial_rate"], 100 * truth["denial_rate_claims"], tol=1)


def test_claims_denial_rate_by_payer_is_the_truth():
    _, rate = answer("health_claims", "top_n", "denial rate by payer", kpi="denial_rate",
                     segment_column="Payer", group_by="Payer")
    assert close(rate, _built("health_claims")[3]["denial_rate_by_payer"], tol=1)


def test_claims_paid_by_month_leaves_the_run_out_months_out():
    """Trap 4 (claims lag): Aug-Oct 2026 service months are still arriving; 'paid by month' must
    not report their drop as a change. Rehearsal found -64.8% (Sep vs Aug) reported as real."""
    text, points = answer("health_claims", "time_series", "How much did we pay each month?",
                          kpi="paid", frequency="M")
    truth = _built("health_claims")[3]
    assert not any(k.startswith(m) for k in points for m in truth["incomplete_months"]), points
    assert "2026-07" in " ".join(points) and "still arriving" in text


def test_claims_trend_chart_and_built_in_mom_leave_the_run_out_months_out():
    """One rule for the run-out on every month comparison: the overview's trend chart and the
    analysis built-in month over month (the sandbox's numbers), not only the answers."""
    from agents.visualization_agent import build_context, month_line
    _, clean, contract, truth = _built("health_claims")
    date = contract.roles["date"]
    line = month_line(clean, date, "Paid Amount", build_context(clean, contract=contract), monthly=True)
    months = [str(r[line.x_key])[:7] for r in line.data]
    assert months and not set(months) & set(truth["incomplete_months"]), months
    res = builtin("health_claims", ["Paid Amount"], [], ["trend"])
    paid = truth["paid_by_service_month"]
    want = 100 * (paid["2026-07"] / paid["2026-06"] - 1)   # the last month whose claims are in
    mom = next(t for t in res["trends"] if t["column"] == "Paid Amount" and t["period"] == "MoM")
    assert abs(mom["pct_change"] - want) < 0.1, (mom, want)


def test_alos_is_written_in_days():
    """'ALOS (days): 3.72' leaves the reader (and a scorer) guessing: the number carries its unit."""
    text, _ = answer("inpatient", "segment_compare", "What is our average length of stay?", kpi="alos")
    assert re.search(r"3\.72 days", text), text
    assert any(k.recipe.kpi == "alos" and k.value.endswith(" days")
               for k in __import__("agents.visualization_agent", fromlist=["pack_kpis"]).pack_kpis(
                   *_built("inpatient")[1:3]))


def test_procedure_codes_survive_cleaning_as_written():
    """Analysis gate (health-8): cleaning parsed CPT/HCPCS as numbers (95% parse), so 344 HCPCS
    codes (J1100) became missing and CPT codes read '99213.0'. A pack label role is never parsed."""
    _, clean, _, _ = _built("health_claims")
    codes = set(clean["CPT/HCPCS"].dropna().astype(str))
    assert "J1100" in codes and "99213" in codes and clean["CPT/HCPCS"].notna().all()


def test_stays_trend_over_the_discharge_date_and_claims_over_the_service_date():
    """Red-team M3: the time axis is the pack's date role (ALOS and readmission are
    discharge-based; claims are incurred by service date), never the file's first date column
    (Admit Date) or the first column named '…date' (Paid Date)."""
    from agents.utils import time_axis
    for name, want in (("inpatient", "Discharge Date"), ("health_claims", "Service From")):
        _, clean, contract, _ = _built(name)
        assert time_axis(contract, None, clean) == want, name


def test_a_trend_that_skips_incomplete_months_names_the_months_it_compares():
    """Insight gate: the evidence said 'Paid Amount MoM: +6.3%' and the report wrote 'from Aug to
    Sep 2026'; the built-in compared Jul with Jun (Aug-Oct still arriving). It names them."""
    from agents.analysis_agent import _parse_findings, build_evidence
    res = builtin("health_claims", ["Paid Amount"], [], ["trend"])
    mom = next(t for t in res["trends"] if t["period"] == "MoM" and not t["segment"])
    assert (mom["latest"], mom["previous"]) == ("2026-07", "2026-06")
    # the production path: the raw result parsed into findings (live run: the parser dropped them)
    f = _parse_findings(res, "", [])
    ev = build_evidence(f, res, "", "", None)
    assert any("(Jul 2026 vs Jun 2026)" in e.claim for e in ev), [e.claim for e in ev]
    # a stays trend over the data's own last two months names nothing (contexts stay pinned)
    res = builtin("inpatient", ["Total Charges"], [], ["trend"])
    assert all("latest" not in t for t in res["trends"])


def test_outlier_and_distribution_numbers_read_the_counted_rows():
    """Recurring class 1 (Domain 3 expert): '325 unusual billed amount values outside -441…'
    came from voided and replaced claim lines; the counted rows have no negative paid."""
    from agents.visualization_agent import build_context, fence_histogram
    _, clean, contract, _ = _built("health_claims")
    chart = fence_histogram(clean, "Paid Amount", build_context(clean, contract=contract))
    assert not str(chart.data[0]["bin"]).lstrip().startswith("-"), chart.data[:2]
    text, _ = answer("health_claims", "distribution", "distribution of paid", metric_column="Paid Amount")
    assert "-442" not in text and "−442" not in text, text


# ── Truth: insurance (Domain 4) ───────────────────────────────────────────────────────────────

def test_policy_table_tiles_are_the_truth():
    """Earned (not written) premium under the loss ratio; written net of cancellation rows."""
    import domain_pack as dp
    t, (_, clean, contract, truth) = tiles("policies"), _built("policies")
    written = dp.compute_kpi(clean, contract.roles, dp.pack_for("insurance"), "written_premium")
    assert close(t["earned_premium"], truth["earned_premium_total"])
    assert t["policies"] == truth["policies"]   # distinct policies, never rows or terms
    assert close(written, truth["written_premium_total"])
    assert abs(written - truth["written_premium_without_cancellations"]) > 1000


@pytest.mark.parametrize("kpi,key", [("loss_ratio", "loss_ratio_by_line"),
                                     ("frequency", "frequency_by_line"),
                                     ("severity", "severity_by_line"),
                                     ("pure_premium", "pure_premium_by_line"),
                                     ("avg_premium", "avg_premium_per_term_by_line"),
                                     ("retention", "retention_by_line")])
def test_policy_ratios_by_line_are_the_truth(kpi, key):
    """Per line (exposure units never add across lines), per term (not per policy), retention
    over the terms up for renewal: the traps' naive numbers differ (tests/test_domain_datasets)."""
    _, got = answer("policies", "top_n", f"{kpi} by line", kpi=kpi,
                    segment_column="Line of Business", group_by="Line of Business")
    assert close(got, _built("policies")[3][key], tol=0.5)


def test_loss_run_tiles_count_each_claimants_latest_valuation():
    """16 valuations of each claim: paid, reserves and incurred at the latest one only (summing
    valuations is ~9×); a reopened claim and a two-claimant claim each count once."""
    t, truth = tiles("loss_run"), _built("loss_run")[3]
    assert close(t["incurred"], truth["incurred_total"])
    assert close(t["paid"], truth["paid_total"])
    assert close(t["open_reserve"], truth["reserve_open_total"])
    assert t["claims"] == truth["claims"]
    assert close(t["severity"], truth["incurred_total"] / truth["claims"], tol=0.1)


def test_loss_run_by_line_is_the_truth():
    truth = _built("loss_run")[3]
    _, reserve = answer("loss_run", "top_n", "open reserves by line", kpi="open_reserve",
                        segment_column="Line of Business", group_by="Line of Business")
    _, sev = answer("loss_run", "top_n", "severity by line", kpi="severity",
                    segment_column="Line of Business", group_by="Line of Business")
    assert close(reserve, truth["open_reserve_by_line"]) and close(sev, truth["severity_by_line"], tol=0.1)


def test_loss_run_net_paid_takes_the_recoveries_off():
    import domain_pack as dp
    _, clean, contract, truth = _built("loss_run")
    pack = dp.pack_for("insurance")
    assert close(dp.compute_kpi(clean, contract.roles, pack, "paid_net"), truth["paid_net_total"])


def test_loss_run_names_its_developing_accident_years_and_raises_no_gate():
    import domain_pack as dp
    s, _, contract, _ = _built("loss_run")
    said = " ".join(dp.pack_caveats(dp.pack_for("insurance"), s.cleaning_audit, contract.roles,
                                    confidence="strong"))
    assert "30 Sep 2026 valuation, 2026 (9 months) and 2025 (21 months) are still developing" in said
    assert s.cleaning_audit.high_null_columns == []


def test_a_trend_over_developing_years_says_they_are_still_developing():
    """$0 rehearsal: 'losses by accident year' compared 2025 with 2024 (-20%) with no word that
    2025 is still developing; a drop there is development, not a trend. Both trend answers (a
    KPI and a column) say it; a pack without a maturity rule says nothing."""
    by_kpi, _ = answer("loss_run", "time_series", "losses by accident year", kpi="incurred", frequency="Y")
    by_col, _ = answer("loss_run", "time_series", "incurred by year", metric_column="Incurred",
                       frequency="Y", aggregation="sum")
    for text in (by_kpi, by_col):
        assert "2025 (21 months) are still developing" in text, text
    retail, _ = answer("retail_lines", "time_series", "sales by month", kpi="net_sales", frequency="M")
    premium, _ = answer("policies", "time_series", "written premium by month", kpi="written_premium")
    assert "developing" not in retail and "developing" not in premium   # premium doesn't develop


def test_a_built_in_trend_of_a_developing_measure_says_it_is_still_developing():
    """Live loss run: 'incurred fell sharply YoY (Aug 2026 vs Aug 2025)' was reported as a signal
    to investigate; both months are in still-developing accident years. The built-in trend's
    assumptions (they reach its evidence) say so; premium and other domains say nothing."""
    res = builtin("loss_run", ["Incurred"], [], ["trend"])
    loss = res["assumptions"]
    assert any(a.startswith("Incurred:") and "still developing" in a for a in loss), loss
    # audit pass 1 (expert): accident-month MoM/YoY of a developing measure is development, not a
    # trend: the built-in computes none (it reached headlines as 'reserves rose 5.8%')
    assert not [t for t in res["trends"] if t["column"] == "Incurred"], res["trends"]
    for name, col in (("policies", "Written Premium"), ("retail_lines", "Lineitem price")):
        res = builtin(name, [col], [], ["trend"])
        assert not any("developing" in a for a in res["assumptions"]), (name, res["assumptions"])


def test_a_top_n_naming_a_kpi_without_a_group_answers_the_kpi():
    """Domain 4 E2E (e2e-64/65): 'How much have we incurred in total?' / 'How many claims do we
    have?' came from intake as top_n + kpi with no group_by; the row handler said 'No grouping
    column specified'. A named KPI is answered by the KPI: overall, or by its segment column
    (the $0 rehearsal's 'ALOS by department' with segment_column only, healthcare)."""
    truth = _built("loss_run")[3]
    total, _ = answer("loss_run", "top_n", "How much have we incurred in total?", kpi="incurred",
                      metric_column="Incurred", aggregation="sum", order="desc")
    claims, _ = answer("loss_run", "top_n", "How many claims do we have?", kpi="claims")
    assert "33,674,954.88" in total and f"{truth['claims']:,}" in claims, (total, claims)
    _, alos = answer("inpatient", "top_n", "ALOS by department", kpi="alos", segment_column="Department")
    assert close(alos, _built("inpatient")[3]["alos_by_dept"], tol=0.3)


def test_a_kpi_for_one_named_status_judges_row_kinds_on_the_whole_data():
    """Pass 4 (major): filtering to 'Open' before row kinds were judged made an earlier Open
    valuation of a claim closed since count as its latest: $30.5M for $6.19M. A named value
    selects the rows counted; which version of a chain is the latest is judged on all rows."""
    _, clean, _, _ = _built("loss_run")
    lt = clean.sort_values("Valuation Date").groupby(["Claim Number", "Claimant Number"]).tail(1)
    opened = lt[lt["Claim Status"] == "Open"]
    text, _ = answer("loss_run", "segment_compare", "incurred for Open claims", kpi="incurred",
                     segment_column="Claim Status", segment_value="Open")
    assert f"{opened['Incurred'].sum():,.2f}" in text, text
    count, _ = answer("loss_run", "segment_compare", "How many claims are open?", kpi="claims",
                      segment_column="Claim Status", segment_value="Open")
    assert f"{opened['Claim Number'].nunique():,}" in count, count


def test_a_named_values_trend_judges_coverage_on_the_whole_file():
    """Pass 5 (minor, from the pass-3 filter): Orthopedics' first month was cut as 'covered only
    from 07 Nov' though the file starts 03 Nov; a value's own first row is not the data's edge."""
    text, _ = answer("inpatient", "time_series", "discharges by month for Orthopedics",
                     kpi="discharges", segment_column="Department", segment_value="Orthopedics",
                     frequency="M")
    assert "covered only from" not in text, text


# ── Post-Domain-4 audit (2026-11-04): what a period answer compares ──────────────────────────

def test_a_developing_measure_is_never_compared_across_periods_and_keeps_its_youngest_year():
    """Audit major 1+2: 'latest 2026-08 … (-57.3% year over year)', '2025 vs 2024 (-20.1%)' and
    'Jun–Aug 2026: -47.9% year over year' compared still-developing periods, and the by-year
    chart dropped 2026 (53% of the open reserves). One rule on every KPI path."""
    truth = _built("loss_run")[3]
    text, chart = answer("loss_run", "time_series", "incurred by accident year", kpi="incurred", frequency="Y")
    assert "2026" in chart and abs(chart["2026"] - truth["incurred_by_accident_year"]["2026"]) < 1
    assert "year over year" not in text and "the period before (" not in text, text
    assert "still developing" in text
    window, _ = answer("loss_run", "time_series", "incurred June to August 2026", kpi="incurred",
                       period_start="2026-06-01", period_end="2026-08-31", compare="yoy")
    assert "year over year" not in window and "still developing" in window, window
    by_line, _ = answer("policies", "top_n", "loss ratio by line 2025 vs 2024", kpi="loss_ratio",
                        group_by="Line of Business", period_start="2025-01-01",
                        period_end="2025-12-31", compare="yoy")
    assert " pts" not in by_line and "still developing" in by_line, by_line


def test_a_window_reaching_into_months_still_arriving_says_so():
    """Audit major 1: 'Paid Jul–Sep 2026: $38,896.51' included Aug–Sep, still arriving."""
    text, _ = answer("health_claims", "segment_compare", "paid July to September 2026", kpi="paid",
                     period_start="2026-07-01", period_end="2026-09-30")
    assert "still arriving" in text, text


def test_a_count_whose_filter_names_a_developing_role_is_not_developing():
    """Audit minor: 'Policies by month' got the losses-will-rise caveat (the Policies KPI filters
    on earned premium; it doesn't measure it)."""
    text, _ = answer("policies", "time_series", "policies by month", kpi="policies", frequency="M")
    assert "developing" not in text, text


def test_a_follow_up_rate_leaves_out_months_whose_window_runs_past_the_data():
    """Audit major 3: 'readmission latest 2026-10 0.0%, vs 10.1% (-10.1 pts)' on 8 index stays: a
    30-day follow-up after October's discharges runs past the data. Those months are left out of
    period answers, and the answer says so (domain_pack: the within term's window)."""
    text, chart = answer("inpatient", "time_series", "readmission rate by month", kpi="readmission_rate",
                         frequency="M")
    assert "2026-10" not in chart and "follow-up" in text, (text, list(chart)[-3:])


def test_an_id_column_is_never_a_measure_in_charts():
    """Audit major 4: 'top customers by revenue' drew a histogram of CustomerID values: the chart
    context read an integer id as a measure, a second copy of ingestion's id decision."""
    from agents.targeted_analysis_agent import run_targeted_analysis
    from agents.visualization_agent import build_context
    from orchestrator.state import AnalysisTask, ChartType, QuestionIntent, TargetedParams
    s, clean, contract, _ = _built("b2b_invoices")
    assert "CustomerID" in contract.id_columns
    assert "CustomerID" not in build_context(clean, contract=contract).measures
    s.task_plan = AnalysisTask(raw_query="top customers by revenue", analysis_types=["eda"], target_metrics=[],
                               question_intent=QuestionIntent.TOP_N,
                               targeted_params=TargetedParams(group_by="CustomerID", kpi="net_sales", n=10))
    chart = run_targeted_analysis(s).chart_configs[0]
    assert chart.chart_type in (ChartType.BAR, ChartType.HORIZONTAL_BAR), chart.chart_type


def test_an_average_on_claim_grain_data_is_per_claim():
    """Audit major 5: 'average incurred by coverage' gave Liability 19,102.77 per claimant row (per
    claim: 21,870.00); 'average paid by payer' gave Summit Mutual 60.83 per line ($101.71 per
    claim). A pack's average_per (YAML) makes an average of its money the total per distinct
    claim, in every answer and chart (agents.utils.aggregate); retail is unchanged."""
    _, by_cov = answer("loss_run", "segment_compare", "average incurred by coverage",
                       metric_column="Incurred", segment_column="Coverage", aggregation="mean")
    assert abs(by_cov["Liability"] - 21870.00) < 0.01, by_cov
    _, by_payer = answer("health_claims", "segment_compare", "average paid by payer",
                         metric_column="Paid Amount", segment_column="Payer", aggregation="mean")
    # over the claims with a paid amount: 412 of Summit's 482 (70 pending have none yet)
    assert abs(by_payer["Summit Mutual"] - 49026.14 / 412) < 0.01, by_payer
    _, retail = answer("retail_lines", "segment_compare", "average price by country",
                       metric_column="Lineitem price", segment_column="Shipping Country", aggregation="mean")
    clean = _built("retail_lines")[1]
    from domain_pack import rows_for
    counted = rows_for(clean, _built("retail_lines")[2], "Lineitem price")[0]
    want = counted.groupby("Shipping Country")["Lineitem price"].mean()
    assert all(abs(retail[k] - want[k]) < 0.01 for k in retail), (retail, want.to_dict())


def test_a_developing_measures_partial_latest_period_says_how_far_it_is_covered():
    """Fix-diff review (major): 'latest 2026Q4 $42,269.82' held October only; kept (it is the
    youngest accident period) but said: covered only to its last date."""
    text, chart = answer("claims", "time_series", "incurred by quarter", kpi="incurred", frequency="Q")
    assert "2026Q4" in chart and "2026Q4" in text and "covered only to" in text, text


# ── HR (Domain 5): every answer path = the generator's truth (computed without domain_pack) ──

def test_hr_attrition_by_department_divides_by_the_average_headcount():
    _, _, _, truth = _built("hr_roster")
    text, by = answer("hr_roster", "top_n", "attrition by department", kpi="attrition_rate",
                      group_by="Department")
    assert close(by, {k: v for k, v in truth["attrition_12m_by_dept"].items()}, tol=0.05), (by, text)
    assert "12 months to 31 Oct 2026" in text
    assert not close(by, {"Support": truth["attrition_12m"] * 2}, tol=0.05)   # sanity


def test_hr_overall_rates_and_counts():
    _, _, _, truth = _built("hr_roster")
    for kpi, key in [("attrition_rate", "attrition_12m"), ("voluntary_attrition", "voluntary_attrition_12m"),
                     ("involuntary_attrition", "involuntary_attrition_12m"), ("retirement_rate", "retirement_rate_12m")]:
        text, _ = answer("hr_roster", "segment_compare", f"what is our {kpi}?", kpi=kpi)
        assert f"{truth[key] * 100:.1f}%" in text, (kpi, text)
    text, _ = answer("hr_roster", "segment_compare", "how many employees do we have?", kpi="headcount")
    assert f"{truth['headcount_asof']:,}" in text and "as of 31 Oct 2026" in text, text
    assert truth["headcount_asof"] != truth["rows"]   # people, never rows


def test_hr_headcount_by_department_and_pay_of_a_small_department_is_hidden():
    _, _, _, truth = _built("hr_roster")
    _, by = answer("hr_roster", "top_n", "headcount by department", kpi="headcount", group_by="Department")
    assert by == {k: float(v) for k, v in truth["headcount_by_dept_asof"].items()}   # Legal (6) shown
    _, pay = answer("hr_roster", "top_n", "average salary by department", kpi="avg_salary",
                    group_by="Department")
    assert "Legal" not in pay and truth["legal_people_asof"] < 11   # pay of 6 people: never shown
    # complementary: one more held back, so Legal can't be worked back from the total
    assert len(pay) == len(truth["avg_salary_by_dept_asof"]) - 2
    want = {k: v for k, v in truth["avg_salary_by_dept_asof"].items() if k in pay}
    assert close(pay, want, tol=0.01), pay


def test_hr_a_partial_year_is_named_and_never_compared_as_a_year():
    _, _, _, truth = _built("hr_roster")
    text, series = answer("hr_roster", "time_series", "attrition by year", kpi="attrition_rate",
                          frequency="Y")
    full = {str(y): v for y, v in truth["attrition_by_year"].items()}
    assert close(series, full, tol=0.05), (series, text)   # 2026 shown, over its 10 months
    assert "2026 so far (10 months" in text and "not annualised" in text and "12 months to" not in text, text
    assert "latest 2025" in text   # compared: 2025 with 2024, never 2026 with a full year


def test_hr_an_asked_year_is_its_window_not_the_rows_dated_in_it():
    _, _, _, truth = _built("hr_roster")
    text, _ = answer("hr_roster", "segment_compare", "attrition in 2025", kpi="attrition_rate",
                     period_start="2025-01-01", period_end="2025-12-31")
    assert f"{truth['attrition_by_year'][2025 if 2025 in truth['attrition_by_year'] else '2025'] * 100:.1f}%" in text, text


def test_hr_one_row_rosters_lose_only_the_earlier_exits():
    _, _, _, truth = _built("hr_roster_onerow")
    text, _ = answer("hr_roster_onerow", "segment_compare", "headcount", kpi="headcount")
    assert f"{truth['headcount_asof']:,}" in text, text
    text, _ = answer("hr_roster_onerow", "segment_compare", "how many people left", kpi="separations")
    assert f"{truth['separations_12m']:,}" in text, text


def test_hr_snapshots_count_exits_where_people_left_and_never_sum_months():
    _, _, _, truth = _built("hr_snapshots")
    _, by = answer("hr_snapshots", "top_n", "attrition by department", kpi="attrition_rate",
                   group_by="Department")
    assert close(by, truth["attrition_12m_by_dept_at_exit"], tol=0.05), by
    text, _ = answer("hr_snapshots", "segment_compare", "headcount", kpi="headcount")
    assert f"{truth['headcount_asof']:,}" in text, text
    _, pay = answer("hr_snapshots", "top_n", "average salary by department", kpi="avg_salary",
                    group_by="Department")
    assert close(pay, {k: v for k, v in truth["avg_salary_by_dept_asof"].items() if k in pay}, tol=0.01)


def test_hr_employees_flag_file_keeps_sales_highest_on_the_12_month_rate():
    _, _, _, truth = _built("employees")
    _, by = answer("employees", "top_n", "attrition by department", kpi="attrition_rate",
                   group_by="department")
    assert close(by, truth["attrition_12m_by_dept"], tol=0.05) and max(by, key=by.get) == "Sales"


@pytest.mark.parametrize("name, key", [("hr_roster", "attrition_12m_by_dept"),
                                       ("hr_snapshots", "attrition_12m_by_dept_at_exit")])
def test_hr_a_model_following_the_brief_gets_the_truth(name, key):
    """Domain 5 red-team B6: the brief's recipe (count rows with an exit_event_date in the
    window, by their department, ÷ the facts' average headcount of that department), run as
    model code would, equals the generator's truth: the brief is enough, nothing re-derived."""
    from agents.analysis_agent import model_frame
    _, clean, contract, truth = _built(name)
    roles = {r: c for r, c in contract.roles.items() if c in clean.columns}
    frame, extra = model_frame(clean, contract)
    assert {"exit_event_date", "hire_event_date"} <= set(extra)
    e = pd.to_datetime(frame["exit_event_date"])
    exits = frame[(e > "2025-10-31") & (e <= "2026-10-31")].groupby("Department").size()
    line = next(x for x in domain_pack.spell_facts(domain_pack.pack_for("hr"), clean, roles)
                if " by Department " in x)
    avg = dict(re.findall(r"(\w+) ([\d,]+\.\d)", line.split("):", 1)[1]))
    got = {k: exits.get(k, 0) / float(v.replace(",", "")) for k, v in avg.items()}
    assert close(got, truth[key], tol=0.5), (got, truth[key])


def test_hr_the_frame_marks_who_is_in_force_and_which_exits_fall_in_the_window():
    """Domain 5 gate: models dropped the window (every exit ever) and kept people terminated
    without a date: the frame's own markers give the truth with a filter, nothing re-derived."""
    from agents.analysis_agent import model_frame
    for name in ("hr_roster", "hr_snapshots", "employees"):
        _, clean, contract, truth = _built(name)
        f, _ = model_frame(clean, contract)
        ent = contract.roles["employee"]
        if "headcount_asof" in truth:
            assert f.loc[f["in_force_now"] == "yes", ent].nunique() == truth["headcount_asof"]
        if "separations_12m" in truth:
            assert int((f["exit_in_window"] == "yes").sum()) == truth["separations_12m"]
    _, clean, contract, truth = _built("hr_roster")
    f, _ = model_frame(clean, contract)
    now = f[(f["in_force_now"] == "yes") & f["Gender"].isin(["Male", "Female"]) & f["Annual_Salary"].notna()]
    g = now.assign(p=now["Annual_Salary"] / now["FTE"]).groupby(["Job_Level", "Gender"])["p"].mean().unstack()
    assert close(((g["Male"] - g["Female"]) / g["Male"]).to_dict(), truth["pay_gap_mean_fte_by_level"], tol=0.01)


def test_hr_a_level_never_counts_an_exit_record_as_a_person():
    """Domain 5 gate (hr-8): the built-in 'mean Base_Salary by Department' at the latest
    snapshot read each person's latest row, exit records ('Terminated' final rows) included."""
    _, _, _, truth = _built("hr_snapshots")
    res = builtin("hr_snapshots", ["Base_Salary"], ["Department"], ["segmentation"])
    got = segment(res, "Department", "Base_Salary", "mean")
    assert close(got, truth["avg_salary_by_dept_asof"], tol=0.01), got


def test_hr_pay_of_a_department_under_11_people_never_leaves_the_builtins():
    """Domain 5 insight gate: 'average annual salary … $96,450 in Legal' (6 people) came from the
    built-in 'mean Annual_Salary by Department', a path that never read the protected rule."""
    from agents.analysis_agent import builtin_result, model_frame
    from orchestrator.state import AnalysisTask
    _, clean, contract, truth = _built("hr_roster")
    frame, _ = model_frame(clean, contract)
    pay = clean.groupby("Department")["Annual_Salary"].mean()   # what the sandbox's code reports
    raw = {"eda_summary": {}, "correlations": {}, "trends": [], "anomalies": [], "statistical_tests": [],
           "forecast_points": [], "distributions": {}, "head_preview": [], "dtypes_summary": {},
           "missing_pct": {}, "assumptions": [], "segments": [
               {"segment_column": "Department", "metric_column": "Annual_Salary", "aggregation": "mean",
                "results": [{"name": k, "value": float(v)} for k, v in pay.items()]}]}
    out = builtin_result(frame, contract, AnalysisTask(raw_query="q", analysis_types=["eda"],
                                                       target_metrics=[]), raw)
    got = segment(out, "Department", "Annual_Salary", "mean")
    assert "Legal" not in got and "Sales" in got and truth["legal_people_asof"] < 11, got
    assert any("fewer than 11 people" in a for a in out["assumptions"])
