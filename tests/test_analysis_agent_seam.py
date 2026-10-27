"""The analysis agent at its public seam, with a fake LLM and a fake sandbox (no network)."""
from __future__ import annotations

import io
import json

import pandas as pd
import pytest

import agents.analysis_agent as analysis_agent
from connectors.csv_connector import CSVConnector
from evals import datagen
from llm.client import LLMError, LLMResult
from orchestrator.state import AnalysisSession, AnalysisTask, ConnectorConfig, ConnectorType
from sandbox.executor import ExecutionResult


def _session() -> AnalysisSession:
    df, _ = datagen.make_sales(1)
    s = AnalysisSession(session_id="seam")
    s.data_contract = CSVConnector(ConnectorConfig(type=ConnectorType.CSV)).build_data_contract(df)
    s.task_plan = AnalysisTask(raw_query="q", target_metrics=["revenue"], analysis_types=["correlation"])
    buf = io.BytesIO()
    df.to_parquet(buf, index=False)
    s.__dict__["_df_parquet"] = buf.getvalue()
    return s


async def test_analysis_agent_runs_recorded_code_and_parses_findings(recorded_llm, monkeypatch):
    recorded_llm.responses.append("```python\nresult['correlations']['revenue__units'] = 0.5\n```")
    seen = {}

    async def fake_run_code(code, df_parquet):
        seen["code"] = code
        return ExecutionResult(stdout="", stderr="", error=None,
                               output_json=json.dumps({"correlations": {"revenue__units": 0.5}}))

    monkeypatch.setattr(analysis_agent, "run_code", fake_run_code)
    findings = await analysis_agent.run_analysis_agent(_session(), recorded_llm)

    assert findings.correlations == {"revenue__units": 0.5}
    assert "result['correlations']['revenue__units'] = 0.5" in seen["code"]   # fences stripped
    assert recorded_llm.calls[0]["agent"] == "analysis"


async def test_analysis_agent_refusal_is_loud(recorded_llm):
    recorded_llm.responses.append(LLMResult(text="", stop_reason="refusal"))
    with pytest.raises(LLMError):
        await analysis_agent.run_analysis_agent(_session(), recorded_llm)


def _tool_call(name: str, args: dict, id_: str = "t1") -> LLMResult:
    return LLMResult(text="", stop_reason="tool_use",
                     tool_calls=[{"id": id_, "name": name, "input": args}],
                     content=[{"type": "tool_use", "id": id_, "name": name, "input": args}])


def _fake_sandbox(monkeypatch, output: dict):
    async def fake_run_code(code, df_parquet):
        return ExecutionResult(stdout="", stderr="", error=None, output_json=json.dumps(output))
    monkeypatch.setattr(analysis_agent, "run_code", fake_run_code)


async def test_tool_results_are_masked_and_delimited(recorded_llm, monkeypatch):
    _fake_sandbox(monkeypatch, {"correlations": {"revenue__units": 0.5}})
    recorded_llm.responses += [
        "import os",                                   # rejected → the retry may use tools
        _tool_call("sample_rows", {"n": 3, "columns": ["customer_email", "customer_name", "revenue"]}),
        "result['correlations']['revenue__units'] = 0.5",
    ]
    await analysis_agent.run_analysis_agent(_session(), recorded_llm)

    assert recorded_llm.calls[0]["tools"] is None      # first attempt: schema + samples suffice
    assert recorded_llm.calls[1]["tools"] == analysis_agent.TOOLS
    second = recorded_llm.calls[2]["messages"]
    tool_result = second[-1]["content"][0]
    assert second[-2]["role"] == "assistant" and second[-2]["content"][0]["type"] == "tool_use"
    assert tool_result["tool_use_id"] == "t1" and not tool_result["is_error"]
    assert tool_result["content"].startswith("<untrusted_data>")
    assert "@example.com" not in tool_result["content"]
    assert tool_result["content"].count("[REDACTED]") == 6     # 3 rows × 2 PII columns


async def test_tool_loop_is_bounded(recorded_llm, monkeypatch):
    _fake_sandbox(monkeypatch, {"correlations": {"revenue__units": 0.5}})
    rounds = analysis_agent._MAX_TOOL_ROUNDS
    recorded_llm.responses.append("import os")
    recorded_llm.responses += [_tool_call("get_schema", {"column": "revenue"}, f"t{i}")
                               for i in range(rounds)]
    recorded_llm.responses.append("result['correlations']['revenue__units'] = 0.5")
    await analysis_agent.run_analysis_agent(_session(), recorded_llm)
    assert [c["tool_choice"] for c in recorded_llm.calls[1:]] == [None] * rounds + [{"type": "none"}]


def test_unknown_column_tool_call_is_an_error_not_a_crash():
    df, _ = datagen.make_sales(1)
    text, is_error = analysis_agent.run_tool("get_schema", {"column": "ghost"}, df)
    assert is_error and "ghost" in text
    text, is_error = analysis_agent.run_tool("get_schema", {"column": "customer_name"}, df)
    assert not is_error and "[REDACTED]" in text and "@" not in text


async def test_every_finding_carries_evidence(recorded_llm, monkeypatch):
    _fake_sandbox(monkeypatch, {
        "correlations": {"revenue__discount": 0.01},
        "statistical_tests": [{"test_name": "pearson revenue~discount", "statistic": 0.01,
                               "p_value": 0.62, "interpretation": "r=0.01, p=0.62: no link"}],
        "non_null": {"revenue": 3025, "discount": 3025}, "n_rows": 3025,
        "assumptions": ["Pearson r between revenue and discount assumes a linear relationship",
                        "units is capped at 8 per order"],
    })
    recorded_llm.responses.append("result['correlations']['revenue__discount'] = 0.01")
    findings = await analysis_agent.run_analysis_agent(_session(), recorded_llm)

    ev = {e.id: e for e in findings.evidence}
    assert set(ev) == {"corr:revenue__discount", "test:0"}
    corr = ev["corr:revenue__discount"]
    assert corr.row_count == 3025 and corr.confidence == "medium"          # weak |r|
    assert "revenue__discount" in corr.result_excerpt and "revenue__discount" in corr.code
    assert ev["test:0"].confidence == "low" and "not significant" in ev["test:0"].confidence_reason
    assert corr.assumptions == ["Pearson r between revenue and discount assumes a linear relationship"]


@pytest.mark.parametrize("n, p, r, imputed, level", [
    (20, None, 0.9, 0, "low"), (500, 0.2, None, 0, "low"), (500, None, 0.9, 30, "low"),
    (500, None, 0.1, 0, "medium"), (500, 0.001, None, 10, "medium"), (500, 0.001, 0.8, 0, "high"),
])
def test_confidence_rules(n, p, r, imputed, level):
    assert analysis_agent.confidence(n, p, r, imputed)[0] == level


def test_fences_are_found_inside_prose():
    from agents.utils import strip_markdown_fences
    text = "Now I have what I need — here it is:\n```python\nx = 1\n```\nDone."
    assert strip_markdown_fences(text) == "x = 1"
    assert strip_markdown_fences("```json\n{\"a\": 1}\n```") == '{"a": 1}'
    assert strip_markdown_fences("plain") == "plain"


def _run_builtin(df, task: AnalysisTask) -> dict:
    """Execute header + standard + guaranteed code locally (as E2B would) on `df`."""
    import contextlib
    contract = CSVConnector(ConnectorConfig(type=ConnectorType.CSV)).build_data_contract(df)
    code = (analysis_agent._CODE_HEADER.replace("df = pd.read_csv('/data/df.csv')", "")
            + analysis_agent._build_standard_code(contract, task)
            + analysis_agent._build_guaranteed_code(contract) + analysis_agent._CODE_FOOTER)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        exec(code, {"df": df})
    return json.loads(out.getvalue())


def test_standard_block_computes_planted_truths():
    df, truth = datagen.make_sales(2)
    raw = _run_builtin(df, AnalysisTask(raw_query="q", target_metrics=["revenue", "discount"],
                                        segments=["segment"],
                                        analysis_types=["trend", "correlation"]))
    mom = {t["segment"]: t["pct_change"] for t in raw["trends"] if t["column"] == "revenue"}
    assert mom[None] == pytest.approx(truth["total_mom_pct"], abs=0.01)
    assert mom["B"] == pytest.approx(truth["segB_mom_pct"], abs=0.01)
    assert raw["correlations"]["revenue__discount"] == pytest.approx(truth["corr_revenue_discount"], abs=1e-3)
    assert "trend:revenue (B):MoM" in raw["builtin"] and "corr:revenue__discount" in raw["builtin"]
    seg_b = next(t for t in raw["trends"] if t["column"] == "revenue" and t["segment"] == "B")
    month = pd.to_datetime(df["order_date"]).dt.to_period("M")
    assert seg_b["n"] == int(((df["segment"] == "B") & month.isin(month.unique()[-2:])).sum())
    disc = next(t for t in raw["trends"] if t["column"] == "discount" and t["segment"] is None)
    by_month = df.groupby(month)["discount"].mean().sort_index()   # a rate: mean, not sum
    assert disc["pct_change"] == pytest.approx((by_month.iloc[-1] / by_month.iloc[-2] - 1) * 100, abs=0.01)

    simpson, t = datagen.make_simpson(1)
    raw = _run_builtin(simpson, AnalysisTask(raw_query="q", target_metrics=["converted"],
                                             segments=["group", "department"],
                                             analysis_types=["segmentation"]))
    joint = next(s for s in raw["segments"] if s["segment_column"] == "group|department")
    got = {r["name"]: r["value"] for r in joint["results"]}
    assert got["treatment|Hard"] == pytest.approx(t["dept_conversion"]["Hard:treatment"])
    assert not raw["trends"]                                   # trend wasn't requested


def test_standard_block_never_overrides_the_model():
    df, _ = datagen.make_sales(1)
    contract = CSVConnector(ConnectorConfig(type=ConnectorType.CSV)).build_data_contract(df)
    code = analysis_agent._build_standard_code(contract, AnalysisTask(
        raw_query="q", target_metrics=["revenue", "units"], analysis_types=["correlation"]))
    g = {"df": df, "pd": __import__("pandas"),
         "result": {"correlations": {"units__revenue": 0.5}, "trends": [], "segments": []}}
    exec(code, g)
    assert g["result"]["correlations"] == {"units__revenue": 0.5} and not g["result"]["builtin"]


async def test_analysis_node_streams_evidence(recorded_llm, monkeypatch):
    import asyncio
    import orchestrator.graph as graph
    _fake_sandbox(monkeypatch, {"correlations": {"revenue__units": 0.5},
                                "non_null": {"revenue": 3025, "units": 3025}, "n_rows": 3025})
    recorded_llm.responses.append("result['correlations']['revenue__units'] = 0.5")
    monkeypatch.setattr(graph, "get_client", lambda: recorded_llm)
    queue: asyncio.Queue = asyncio.Queue()
    session = _session()
    await graph.analysis_node({"session": session, "df_parquet": session.__dict__["_df_parquet"],
                               "event_queue": queue})
    events = [queue.get_nowait() for _ in range(queue.qsize())]
    ev = next(e for e in events if e["type"] == "evidence")
    assert ev["data"][0]["id"] == "corr:revenue__units" and ev["data"][0]["row_count"] == 3025


def test_builtin_mom_replaces_the_models():
    df, truth = datagen.make_sales(2)
    contract = CSVConnector(ConnectorConfig(type=ConnectorType.CSV)).build_data_contract(df)
    code = analysis_agent._build_standard_code(contract, AnalysisTask(
        raw_query="q", target_metrics=["revenue"], analysis_types=["trend"]))
    wrong = {"column": "revenue", "period": "MoM", "direction": "up", "pct_change": 2.49}
    g = {"df": df, "pd": __import__("pandas"),
         "result": {"correlations": {}, "trends": [wrong], "segments": []}}
    exec(code, g)
    (mom,) = [t for t in g["result"]["trends"] if t["segment"] is None]
    assert mom["pct_change"] == pytest.approx(truth["total_mom_pct"], abs=0.01)


def test_eda_only_findings_are_not_empty_when_eda_was_asked():
    f = analysis_agent._parse_findings({"eda_summary": {"revenue": {"mean": 1.0}}}, "", [])
    assert analysis_agent.review_findings(f, {}, ["revenue"], {"eda"}) is None
    assert "empty" in analysis_agent.review_findings(f, {}, ["revenue"], {"correlation"})
