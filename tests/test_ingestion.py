"""Ingestion agent (Phase 5): roles from values, lossless loading, typed errors for files that
aren't tables, PII to the guard, determinism, and the targeted path through cleaning. Offline.
The eval suite (evals/cases/ingestion) covers the planted tables; these cover what it doesn't."""

import asyncio
import io
import time

import numpy as np
import pandas as pd
import pytest

from agents.errors import IngestionError
from agents.ingestion_agent import build_contract, infer_role, known_pii_values
from connectors.csv_connector import _read_bytes
from orchestrator.state import AnalysisSession, AnalysisTask, ConnectorType, QuestionIntent


def _role(values, name):
    return infer_role(pd.Series(values), name)[0]


@pytest.mark.parametrize("values,name,role", [
    ([1999, 2049, 1999, 2099] * 10, "price", "metric"),             # year-like values, no year name
    ([2021, 2022, 2023] * 10, "fiscal_year", "category"),
    (["SW1A 1AA", "EC1A 1BB", "W1A 0AX"] * 10, "postal_code", "category"),
    ([4155550142, 4155550143] * 10, "units_sold", "metric"),
    ([0.12, 0.5, 0.33] * 10, "lifetime_value", "metric"),             # 'time' in the name
    (["America/New_York", "Europe/Paris"] * 10, "timezone", "category"),
    ([1_714_000_000 + i * 86_400 for i in range(30)], "created_ts", "date"),
    ([5, 9, 2] * 10, "account_id", "id"),                             # repeated ids still ids
    (["#1001", "#1002", "#1003", "#1004", "#1005", "#1006"], "Order", "id"),
])
def test_roles_come_from_the_values(values, name, role):
    assert _role(values, name) == role


def test_a_phone_stored_as_a_number_is_personal_data_not_a_metric():
    df = pd.DataFrame({"phone": [4155550142, 4155550199] * 5, "amount": [1.5, 2.5] * 5})
    col = build_contract(df, ConnectorType.CSV).columns[0]
    assert col.pii == "phone" and not col.is_metric and col.sample_values[0] == "[REDACTED]"


def test_dates_with_junk_are_a_date_column_but_load_untouched():
    """Load is lossless: 'unknown' would vanish unlogged. Cleaning parses it with an audit entry."""
    text = "order_date,amount\n" + "".join(
        f"{'unknown' if i % 10 == 0 else f'2025-01-{i % 28 + 1:02d}'},{i}\n" for i in range(40))
    df, _ = _read_bytes(text.encode(), ".csv")
    assert df["order_date"].dtype == object or str(df["order_date"].dtype) in ("str", "string")
    assert build_contract(df, ConnectorType.CSV).columns[0].is_date


@pytest.mark.parametrize("content,suffix,says", [
    (b"", ".csv", "empty"),
    (b"   \n\n", ".csv", "empty"),
    (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + bytes(range(256)), ".csv", "can't be read"),
    (b"order_id,revenue\n", ".csv", "no data rows"),
    (b"anything", ".xls", ".xlsx"),
    (b"PK\x03\x04 not really a workbook", ".xlsx", "can't be read"),
])
def test_files_that_are_not_tables_get_a_plain_message(content, suffix, says):
    with pytest.raises(IngestionError) as err:
        _read_bytes(content, suffix)
    assert says in err.value.user_message


def test_a_report_export_loads_as_its_table():
    text = ("Acme Ltd\nSales by Customer\nJan - Mar 2025\n\n"
            "Date;Customer;Amount\n03.01.2025;Jane Doe;1.249,00\n04.01.2025;Omar Haddad;45,50\n"
            "TOTAL;;1.294,50\n")
    df, info = _read_bytes(text.encode("cp1252"), ".csv")
    assert list(df.columns) == ["Date", "Customer", "Amount"] and len(df) == 2
    assert df["Amount"].tolist() == [1249.0, 45.5]
    assert df["Date"].dt.month.tolist() == [1, 1] and df["Date"].dt.day.tolist() == [3, 4]
    assert info["delimiter"] == ";" and info["decimal"] == "," and len(info["notes"]) == 2


def test_a_bom_never_reaches_the_first_header():
    df, _ = _read_bytes("id,amount\n1,2\n".encode("utf-8-sig"), ".csv")
    assert list(df.columns) == ["id", "amount"]


def test_same_bytes_same_contract():
    text = "id,region,amount,email\n" + "".join(
        f"{i},{'NS'[i % 2]},{i * 1.5},u{i}@example.com\n" for i in range(50))
    a = build_contract(_read_bytes(text.encode(), ".csv")[0], ConnectorType.CSV)
    b = build_contract(_read_bytes(text.encode(), ".csv")[0], ConnectorType.CSV)
    assert a.model_dump() == b.model_dump() and len(a.fingerprint) == 64
    assert a.source_evidence.kind == "source" and "email" in a.pii_columns


def test_pii_headers_and_values_reach_the_guard_not_short_or_calendar_words():
    df = pd.DataFrame({"jane@example.com": [1, 2], "first_name": ["May", "Grace"],
                       "notes": ["call 555-201-3344", "ok"]})
    found = known_pii_values(df, build_contract(df, ConnectorType.CSV))
    assert {"jane@example.com", "Grace", "555-201-3344"} <= found and "May" not in found


def test_a_wide_table_profiles_quickly():
    rng = np.random.default_rng(0)
    df = pd.DataFrame(rng.random((200, 2000)), columns=[f"c{i}" for i in range(2000)])
    start = time.perf_counter()
    contract = build_contract(df, ConnectorType.CSV)
    assert contract.column_count == 2000 and time.perf_counter() - start < 30


def test_the_summary_states_computed_facts_only():
    df = pd.DataFrame({"order_date": pd.date_range("2025-01-01", periods=90, freq="D"),
                       "revenue": np.arange(90) * 1.0, "region": ["N", "S", "E"] * 30})
    s = build_contract(df, ConnectorType.CSV).schema_summary
    assert s.startswith("90 rows × 3 columns.") and "2025-01-01 to 2025-03-31 (daily)" in s


async def test_a_cached_resume_refills_the_guard(monkeypatch):
    """The cached-parquet path skips the loader; this run's guard still needs the PII values."""
    from llm.client import pii_values
    from orchestrator import graph
    df = pd.DataFrame({"customer_name": ["Jane Doe", "Omar Haddad"], "amount": [1.0, 2.0]})
    buf = io.BytesIO()
    df.to_parquet(buf, index=False)
    monkeypatch.setattr(graph, "read_cleaned_parquet", lambda sid: buf.getvalue())
    session = AnalysisSession(session_id="s", cleaned_data_path="x",
                              data_contract=build_contract(df, ConnectorType.CSV),
                              task_plan=AnalysisTask(raw_query="q", target_metrics=[],
                                                     analysis_types=["eda"], resume_from="analysis"))
    token = pii_values.set(set())
    try:
        await graph.ingestion_node({"session": session, "event_queue": asyncio.Queue()})
        assert {"Jane Doe", "Omar Haddad"} <= pii_values.get()
    finally:
        pii_values.reset(token)


# ── The targeted path reads cleaned data (Phase 5 Insight carry-over) ─────────────────────────

def _routed(intent, audit_high_null=(), answer=None, approval=False):
    from orchestrator.routing import route_after_cleaning, route_after_intake
    from orchestrator.state import CleaningAudit
    s = AnalysisSession(session_id="s", high_null_answer=answer,
                        task_plan=AnalysisTask(raw_query="q", target_metrics=[],
                                               analysis_types=["eda"], question_intent=intent))
    s.cleaning_audit = CleaningAudit(rows_before=1, rows_after=1, decisions=[], summary="",
                                     columns_coerced=[], duplicates_removed=0, outliers_flagged=[],
                                     high_null_columns=list(audit_high_null))
    return route_after_intake({"session": s}), route_after_cleaning({"session": s, "is_approval": approval})


def test_targeted_questions_go_through_cleaning():
    assert _routed(QuestionIntent.TOP_N) == ("cleaning", "targeted_analysis")
    assert _routed(QuestionIntent.OVERVIEW) == ("cleaning", "analysis")
    assert _routed(QuestionIntent.META_QUESTION)[0] == "targeted_analysis"   # reads no data


def test_the_high_null_gate_is_asked_once_per_session():
    assert _routed(QuestionIntent.TOP_N, ["notes"])[1] == "human_approval"
    assert _routed(QuestionIntent.TOP_N, ["notes"], answer="continue")[1] == "targeted_analysis"
    assert _routed(QuestionIntent.OVERVIEW, ["notes"], answer="drop")[1] == "analysis"


# ── Review findings (independent review of this PR) ────────────────────────────────────────────

@pytest.mark.parametrize("text,columns,first", [
    ("id,amount\n1,5,\n2,6,\n", ["id", "amount"], [1, 5]),                    # trailing delimiter
    ('id,note,amount\n1,"line one\nline two",5\n2,ok,6\n', ["id", "note", "amount"], None),
    ("id,amount,notes\n1,5\n2,6\n3,7\n4,8,late\n", ["id", "amount", "notes"], None),
    ("Acme, Inc.\nSales\n\nDate,Customer,Qty,Amount\n01/02/2025,Jane Doe,2,90.00\n"
     "01/03/2025,Omar,1,45.00\n", ["Date", "Customer", "Qty", "Amount"], None),
])
def test_the_header_is_the_first_row_unless_titles_sit_above_it(text, columns, first):
    df, _ = _read_bytes(text.encode(), ".csv")
    assert list(df.columns) == columns
    if first:
        assert df.iloc[0].tolist() == first


def test_a_us_thousand_in_a_semicolon_file_is_not_a_decimal_comma():
    df, info = _read_bytes(b"region;revenue\nNorth;1,234\nSouth;2,500\n", ".csv")
    assert info["decimal"] == "." and df["revenue"].tolist() == ["1,234", "2,500"]   # cleaning parses


def test_corrupt_parquet_and_json_get_the_typed_error():
    for content, suffix in ((b"PAR1 broken", ".parquet"), (b"{not json", ".json")):
        with pytest.raises(IngestionError):
            _read_bytes(content, suffix)


def test_nested_json_cells_are_profiled_not_crashed_on():
    df = pd.DataFrame({"tags": [["a", "b"], ["c"]] * 5, "email": ["a@x.com", "b@y.com"] * 5})
    contract = build_contract(df, ConnectorType.CSV)
    assert contract.pii_columns == ["email"] and known_pii_values(df, contract) >= {"a@x.com"}


def test_iso_timestamps_with_a_zone_are_dates():
    assert _role(["2025-01-31T10:00:00Z", "2025-02-01T11:00:00+00:00"] * 5, "created_at") == "date"


@pytest.mark.parametrize("text,masked", [
    ("delta: +1234567.89", False), ("rev +12.5%", False), ("up +12 345", False),
    ("+44 20 7946 0958", True), ("+49 30 5550123", True), ("+33 1 55 50 01 23", True),
])
def test_international_phones_without_eating_numbers(text, masked):
    from guardrails import MASK, mask_pii
    assert (MASK in mask_pii(text)) is masked


def test_sql_results_mask_people_under_role_names():
    """The SQL agent queries before ingestion registers the run's PII values with the guard."""
    from agents.sql_agent import _preview
    from connectors.sql_connector import QueryResult
    res = QueryResult(columns=["sales_rep", "total"],
                      rows=[["Jane Doe", 10], ["Omar Haddad", 9], ["Priya Patel", 8]],
                      truncated=False)
    assert "Jane Doe" not in _preview(res)


async def test_a_follow_up_registers_the_last_turns_pii_before_the_memory(monkeypatch):
    from agents import ingestion_agent
    from llm.client import pii_values
    from memory import session_data
    df = pd.DataFrame({"sales_rep": ["Jane Doe", "Omar Haddad"] * 3, "amount": [1.0] * 6})
    buf = io.BytesIO()
    df.to_parquet(buf, index=False)
    monkeypatch.setattr(session_data, "read_cleaned_parquet", lambda sid: buf.getvalue())
    session = AnalysisSession(session_id="s", cleaned_data_path="x",
                              data_contract=build_contract(df, ConnectorType.CSV))
    token = pii_values.set(set())
    try:
        ingestion_agent._seed_guard_from_last_turn(session)
        assert {"Jane Doe", "Omar Haddad"} <= pii_values.get()
    finally:
        pii_values.reset(token)
