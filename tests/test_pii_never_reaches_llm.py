"""Hard rule (Phase 5 Ingestion): unmasked PII from the user's data never reaches an LLM.

Builds the real prompt inputs of every agent that reads data-derived text from a table with
PII the column names don't announce (`sales_rep` holds people) and scans them for the planted
values. The client guard (llm/client.py) is the backstop; these assert each builder is clean on
its own, so a guard hit in production means a new leak, not an expected one.
"""

import asyncio
import io
import json

import numpy as np
import pandas as pd
import pytest

from connectors.csv_connector import CSVConnector
from agents.ingestion_agent import remember_pii
from evals.scoring import leaked
from llm.client import RecordedClient, pii_values
from orchestrator.state import (
    AnalysisFindings,
    AnalysisSession,
    AnalysisTask,
    ConnectorConfig,
    ConnectorType,
    Evidence,
    Message,
    MessageRole,
)

REPS = ["Jane Doe", "Omar Haddad", "Priya Patel", "Liam O'Brien"]
EMAILS = [f"buyer{i}@example.com" for i in range(1, 6)]


def _table() -> pd.DataFrame:
    rng = np.random.default_rng(3)
    n = 120
    df = pd.DataFrame({
        "deal_id": range(1, n + 1),
        "close_date": pd.date_range("2025-01-01", periods=n, freq="D").strftime("%Y-%m-%d"),
        "region": rng.choice(["North", "South"], n),
        "sales_rep": rng.choice(REPS, n),
        "contact": rng.choice(EMAILS, n),
        "amount": rng.lognormal(5, 0.5, n).round(2),
    })
    return pd.read_csv(io.StringIO(df.to_csv(index=False)))


@pytest.fixture(autouse=True)
def guarded_run():
    """As api/routes/chat.py: each run has its PII guard set, which ingestion fills."""
    token = pii_values.set(set())
    yield
    pii_values.reset(token)


def _session() -> AnalysisSession:
    df = _table()
    s = AnalysisSession(session_id="pii",
                        data_contract=CSVConnector(ConnectorConfig(type=ConnectorType.CSV))
                        .build_data_contract(df),
                        task_plan=AnalysisTask(raw_query="Which rep closes the biggest deals?",
                                               target_metrics=["amount"], segments=["sales_rep"],
                                               analysis_types=["segmentation"]))
    buf = io.BytesIO()
    df.to_parquet(buf, index=False)
    s.__dict__["_df_parquet"] = buf.getvalue()
    remember_pii(df, s.data_contract)
    return s


PLANTED = REPS + EMAILS


def test_contract_samples_hold_no_pii():
    dump = json.dumps(_session().data_contract.model_dump(mode="json"), ensure_ascii=False)
    assert leaked(dump, PLANTED) == []


def test_intake_context_masks_earlier_answers():
    from agents.intake_agent import build_context
    s = _session()
    s.conversation_history = [
        Message(role=MessageRole.USER, content="Top reps by amount?"),
        Message(role=MessageRole.ASSISTANT,
                content="Jane Doe leads with $9,120, then Omar Haddad ($8,400)."),
    ]
    assert leaked(build_context(s, "and their contact emails?").render(), PLANTED) == []


def test_analysis_context_holds_no_pii():
    from agents.analysis_agent import build_context
    assert leaked(build_context(_session()).render(), PLANTED) == []


def test_insight_context_drops_breakdowns_by_person():
    from agents.insight_agent import build_context
    s = _session()
    s.analysis_findings = AnalysisFindings(
        eda_summary={}, correlations={}, trends=[], segments=[], anomalies=[],
        statistical_tests=[], code_executed="",
        evidence=[Evidence(id="seg:sales_rep/amount", kind="segment",
                           claim="Jane Doe has the highest mean amount (210.4).",
                           code="", result_excerpt=json.dumps({"Jane Doe": 210.4, "Omar Haddad": 150.0}),
                           row_count=120, confidence="high", confidence_reason="n=120")])
    assert leaked(build_context(s).render(), PLANTED) == []


@pytest.mark.parametrize("helper", ["generate_proactive_questions", "generate_data_narrative"])
def test_dataset_helpers_send_no_pii(helper):
    from agents import intake_agent
    llm = RecordedClient(['["q1"]', "{}"])
    try:
        asyncio.run(getattr(intake_agent, helper)(_session().data_contract, llm))
    except Exception:   # only the request matters here
        pass
    assert llm.calls and leaked(json.dumps(llm.calls, ensure_ascii=False, default=str), PLANTED) == []


def test_client_guard_tokenizes_and_restores():
    """The backstop: a question naming a customer still works; the model never sees the name."""
    _session()   # fills the guard
    llm = RecordedClient(['{"filter": "[PII_%s]"}' % __import__("hashlib").sha1(b"Jane Doe").hexdigest()[:8]])
    out = asyncio.run(llm.complete(messages=[{"role": "user", "content": "What did Jane Doe buy?"}],
                                   max_tokens=10, agent="t", prompt_version="v0"))
    assert "Jane Doe" not in json.dumps(llm.calls) and "[PII_" in json.dumps(llm.calls)
    assert out.text == '{"filter": "Jane Doe"}'   # restored for the code that reads it


def test_guard_leaves_calendar_words_and_short_values():
    from agents.ingestion_agent import known_pii_values
    df = pd.DataFrame({"first_name": ["May", "Al", "Grace"], "amount": [1, 2, 3]})
    contract = CSVConnector(ConnectorConfig(type=ConnectorType.CSV)).build_data_contract(df)
    assert known_pii_values(df, contract) == {"Grace"}


def test_guard_pattern_follows_the_values_not_the_set_object():
    """Security review: a pattern cached by id(set) could serve a dead run's pattern to a new
    run whose set reused the id, leaving the new values unmasked."""
    from llm.client import pseudonymize
    a = {"Jane Doe"}
    assert pseudonymize("Jane Doe", a)[0] != "Jane Doe"
    a.clear()
    a.add("Omar Haddad")             # same object, same size, new contents
    out, _ = pseudonymize("Omar Haddad and Jane Doe", a)
    assert "Omar Haddad" not in out and "Jane Doe" in out
