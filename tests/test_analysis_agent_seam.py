"""The analysis agent at its public seam, with a fake LLM and a fake sandbox (no network)."""
from __future__ import annotations

import io
import json

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
    findings = await analysis_agent.run_analysis_agent(_session())

    assert findings.correlations == {"revenue__units": 0.5}
    assert "result['correlations']['revenue__units'] = 0.5" in seen["code"]   # fences stripped
    assert recorded_llm.calls[0]["agent"] == "analysis"


async def test_analysis_agent_refusal_is_loud(recorded_llm):
    recorded_llm.responses.append(LLMResult(text="", stop_reason="refusal"))
    with pytest.raises(LLMError):
        await analysis_agent.run_analysis_agent(_session())
