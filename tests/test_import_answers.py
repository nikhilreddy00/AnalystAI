"""Open (5.8c paid runs, 2026-11-23): two built-in answers wrong on a Stripe account, pinned at $0 on
Stripe's own export (the import reads the same: tests/test_imports.py's oracle), with the exact
plans the model gave in e2e-105 and e2e-107. Each is xfail-strict: the follow-up fix PR (user,
2026-11-23) turns it green, and an XPASS then fails the suite until its ledger row is closed."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

STRIPE = Path(__file__).parent / "fixtures" / "imports" / "stripe"


def _session(export: str, plan: dict):
    from agents.targeted_analysis_agent import run_targeted_analysis
    from connectors.csv_connector import CSVConnector, _read_bytes
    from orchestrator.state import AnalysisTask, ConnectorConfig, ConnectorType
    from tests.test_oracle import _prepared
    df, _ = _read_bytes((STRIPE / export).read_bytes(), ".csv")
    contract = CSVConnector(ConnectorConfig(type=ConnectorType.CSV)).build_data_contract(df)
    s, _, _, _ = _prepared(df, contract, ConnectorType.CSV, {})
    s.task_plan = AnalysisTask(**plan)
    return run_targeted_analysis(s).answer_text


@pytest.mark.xfail(strict=True, reason="P58-51 Open: what the import card says of a table (stale, "
                   "cut, empty) never reaches the dataset an analysis reads: its answers read the "
                   "last good copy as current and complete")
@pytest.mark.parametrize("case", ["stale", "cut", "empty"])
async def test_a_dataset_read_from_an_import_says_what_its_card_says(case, monkeypatch, tmp_path):
    """Audit 1 (senior audit M2, m3) and the plan's red-team line "a failed table's staleness
    said on every answer that reads it": the dataset carries its import's word for it."""
    from cryptography.fernet import Fernet

    from api.config import settings
    from api.rate_limit import limiter
    from connectors import csv_connector
    from connectors import imports as imp
    from orchestrator.runner import in_memory_deps
    from tests.fake_network import serve
    from tests.test_imports import Stripe, _connect, _inv, _settle, _sub
    from tests.test_workspace_api import _client
    import memory.session_store as ss
    monkeypatch.setattr(settings, "encryption_key", Fernet.generate_key().decode())
    monkeypatch.setattr(csv_connector, "_UPLOADS_DIR", tmp_path)
    limiter.reset()
    if case == "cut":
        monkeypatch.setattr(imp, "ROW_CAP", 1)
    async with _client(in_memory_deps()) as client:
        serve(monkeypatch, Stripe([_sub(1), _sub(2)], [_inv(1), _inv(2)]))
        body = (await _connect(client)).json()
        if case != "cut":
            monkeypatch.setattr(ss, "REFRESH_GAP_S", 0)
            serve(monkeypatch, Stripe([], [], key="other") if case == "stale" else Stripe([], []))
            await client.post(f"/api/imports/{body['id']}/refresh")
            await _settle(client)
        did = body["tables"][0]["dataset_id"]
        ds = (await client.get(f"/api/datasets/{did}")).json()
        assert (ds.get("import") or {}).get(case), ds


@pytest.mark.xfail(strict=True, reason="P58-46 Open: a subscription with two prices is two rows "
                   "of Stripe's export; the built-in count counts rows (8), not subscriptions (7)")
def test_active_subscriptions_are_counted_as_subscriptions_never_item_rows():
    answer = _session("export_subscriptions.csv", {
        "raw_query": "How many subscriptions have the status active?", "question_intent": "top_n",
        "analysis_types": ["eda", "segmentation"], "target_metrics": ["Status"],
        "segments": ["Status"],
        "targeted_params": {"target_columns": ["Status"], "metric_column": "Status",
                            "segment_column": "Status", "segment_value": "active",
                            "group_by": "Status", "order": "desc", "aggregation": "count"}})
    assert re.search(r"active\D{0,12}7(\.0+)?\b", answer), answer


@pytest.mark.xfail(strict=True, reason="P58-47 Open: the plan asks for paid invoices' Total by "
                   "Currency; the built-in groups by Paid and sums every currency (3,915.20)")
def test_money_is_never_summed_across_currencies():
    answer = _session("export_invoices.csv", {
        "raw_query": "What is the sum of the Total column over the paid invoices in USD?",
        "question_intent": "segment_compare", "analysis_types": ["eda", "segmentation"],
        "target_metrics": ["Total"], "segments": ["Paid", "Currency"],
        "targeted_params": {"target_columns": ["Total", "Paid", "Currency"],
                            "metric_column": "Total", "segment_column": "Paid",
                            "segment_value": "true", "group_by": "Currency",
                            "aggregation": "sum"}})
    assert "3,915" not in answer and re.search(r"415\.2", answer), answer
