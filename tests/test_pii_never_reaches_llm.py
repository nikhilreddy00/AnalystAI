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


def test_guard_is_case_insensitive_and_scales_with_the_text_not_the_values():
    """Review: model code can upper-case a name; 200k values must not slow every request."""
    import time
    from llm.client import pseudonymize
    vals = {f"user{i}@example.com" for i in range(100_000)} | {f"First{i} Last{i}" for i in range(100_000)}
    vals.add("Jane Doe")
    pseudonymize("warm", vals)
    start = time.perf_counter()
    out, mapping = pseudonymize("JANE DOE, x.Jane Doe, Jane Doering " + "word " * 6000, vals)
    assert time.perf_counter() - start < 0.5
    assert "JANE DOE" not in out and "x.Jane Doe" not in out and "Jane Doering" in out


def test_restored_values_cannot_break_json_and_overflow_fails_closed():
    from llm.client import OVERFLOW, pii_token, pseudonymize, restore
    tok = pii_token('Liam "Lee" O\'Brien')
    assert json.loads(restore('{"name": "%s"}' % tok, {tok: 'Liam "Lee" O\'Brien'}))["name"] \
        == 'Liam "Lee" O\'Brien'
    out, _ = pseudonymize("mail a.b@example.org", {OVERFLOW, "Somebody Else"})
    assert "a.b@example.org" not in out


def test_unicode_case_folding_cannot_shift_the_guard_offsets():
    """Security review: 'İ'.lower() is 2 characters; slicing a lowercased copy misaligned the
    match and let the value through."""
    from llm.client import pseudonymize
    out, _ = pseudonymize("İİİ İstanbul: Jane Doe called", {"Jane Doe"})
    assert "Jane Doe" not in out


@pytest.mark.parametrize("text,masked", [("+14155552671", True), ("call +44 20 7946 0958.", True),
                                         ("+12345678.9", False), ("delta +1234567.89", False)])
def test_bare_e164_is_a_phone_and_a_decimal_tail_is_not(text, masked):
    from guardrails import MASK, mask_pii
    assert (MASK in mask_pii(text)) is masked


def test_switching_datasets_keeps_guarding_the_old_answers(monkeypatch):
    """Security review: the conversation still quotes the old data after a connector change."""
    from api.routes import chat
    from memory import session_data
    df = _table()
    buf = io.BytesIO()
    df.to_parquet(buf, index=False)
    monkeypatch.setattr(session_data, "read_cleaned_parquet", lambda sid: buf.getvalue())
    s = _session()
    s.cleaned_data_path = "x"
    assert set(REPS) <= chat._last_turn_pii(s)
