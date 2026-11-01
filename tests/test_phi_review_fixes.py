"""Phase 5.5 WP2 reviews: one test per finding (security, failure paths, concurrency, prod
parity). Each fails on commit 0ed5060, the code the reviewers read."""
from __future__ import annotations

import io
import logging
import logging.config

import numpy as np
import pandas as pd
import pytest

from agents.errors import IngestionError
from agents.ingestion_agent import build_contract, prepare_frame
from evals import datagen
from llm.client import pii_values
from orchestrator.state import AnalysisSession, ConnectorConfig, ConnectorType


def _contract(df):
    return build_contract(df, ConnectorType.CSV, {})


def _parquet(df) -> bytes:
    buf = io.BytesIO()
    df.to_parquet(buf, index=False)
    return buf.getvalue()


# ── security (a) ────────────────────────────────────────────────────────────────────────────

def test_phi_mode_withholds_what_it_does_not_recognise():
    n = 60
    df = pd.DataFrame({"mrn": [f"MRN-{i}" for i in range(n)], "dx": ["E11.9", "I10"] * 30,
                       "next_of_kin": [f"Ana Garcia {i}" for i in range(n)],
                       "member_number": [f"HP9{i:06d}" for i in range(n)],
                       "zip5": [f"{10000 + i}" for i in range(n)],
                       "comment": ["Called John Smith", "", ""] * 20, "cost": np.linspace(1, 9, n)})
    out, c = prepare_frame(df, _contract(df), ConnectorType.CSV)
    text = out.to_csv(index=False) + c.model_dump_json()
    for raw in ("Ana Garcia", "HP9000001", "10005", "John Smith", "MRN-3"):
        assert raw not in text, raw
    assert "cost" in out.columns and "dx" in out.columns


def test_patient_records_without_an_mrn_still_trigger_phi_mode():
    df = pd.DataFrame({"member_id": [f"M{i}" for i in range(30)], "patient": ["Ana Ito", "Bo Li"] * 15,
                       "diagnosis": ["Type 2 diabetes", "Asthma"] * 15})
    assert _contract(df).phi
    df2 = pd.DataFrame({"member_id": [f"M{i}" for i in range(30)], "policyholder": ["Ana Ito"] * 30,
                        "premium": range(30)})
    assert not _contract(df2).phi   # an insurer's members are not patients


def test_pseudonyms_differ_between_users():
    from guardrails import pseudonym, pseudonym_key
    assert pseudonym("MRN-1", pseudonym_key("u1")) != pseudonym("MRN-1", pseudonym_key("u2"))


def test_a_typed_value_reaches_e2b_as_its_pseudonym_and_names_come_back():
    from agents.analysis_agent import e2b_frame, pseudonymize_literals, restore_pseudonyms
    df = pd.DataFrame({"customer_email": ["Jane.Doe@x.com", "bo@x.com"], "amount": [5.0, 7.0]})
    upload, back = e2b_frame(_parquet(df), ["customer_email"], "u1")
    code = pseudonymize_literals("total = df[df.customer_email == 'jane.doe@x.com'].amount.sum()", back)
    assert "jane.doe" not in code.lower()
    frame = pd.read_parquet(io.BytesIO(upload))
    assert frame[frame.customer_email == code.split("'")[1]].amount.sum() == 5.0   # filter matches
    pseudo = frame.customer_email.iloc[0]
    assert restore_pseudonyms({pseudo: {"name": pseudo}}, back) == {
        "Jane.Doe@x.com": {"name": "Jane.Doe@x.com"}}   # keys too


def test_e2b_masks_pii_inside_unflagged_text():
    from agents.analysis_agent import e2b_frame
    df = pd.DataFrame({"notes": ["refund to bob0@corp.com"] + ["ok"] * 9, "x": range(10)})
    upload, _ = e2b_frame(_parquet(df), [])
    assert "bob0@corp.com" not in pd.read_parquet(io.BytesIO(upload)).to_csv()


def test_sql_preview_of_patient_rows_hides_free_text_and_errors_are_masked():
    from agents.sql_agent import _db_error, _preview
    from connectors.sql_connector import QueryResult
    rows = [[f"MRN-{i}", f"Seen {i} with Jane Roe {i}", "Cardiology"] for i in range(30)]
    text = _preview(QueryResult(columns=["mrn", "clinical_note", "department"], rows=rows,
                                truncated=False))
    assert "Jane Roe" not in text and "MRN-1" not in text and "Cardiology" in text
    assert "a@b.io" not in _db_error(ValueError("bad value a@b.io"))


def test_nested_json_is_flattened_so_its_pii_is_found():
    from connectors.csv_connector import _read_bytes
    df, _ = _read_bytes(b'[{"holder": {"name": "Jane Doe"}, "amt": 1}, '
                        b'{"holder": {"name": "Bo Li"}, "amt": 2}]', ".json")
    assert "holder_name" in _contract(df).pii_columns


def _scrubbed(record_args, msg="%s: %s") -> str:
    from api.logging_scrub import ScrubFilter
    out = io.StringIO()
    h = logging.StreamHandler(out)
    h.addFilter(ScrubFilter())
    log = logging.getLogger("scrub-args")
    log.addHandler(h)
    log.propagate = False
    try:
        log.warning(msg, *record_args)
    finally:
        log.removeHandler(h)
    return out.getvalue()


def test_non_string_log_args_are_scrubbed():
    pii_values.set(None)
    assert "jane.doe@x.com" not in _scrubbed(("s1", ValueError("jane.doe@x.com 555-201-3344")))
    assert "3 rows" in _scrubbed((3,), "%d rows")   # numbers stay numbers


def test_targeted_errors_and_skipped_reviews_show_types_not_values(monkeypatch):
    from agents import targeted_analysis_agent as ta
    from orchestrator.state import TargetedParams

    def boom(*a, **k):   # pandas quotes the offending cell
        raise ValueError("could not convert string to float: 'Ana Ito'")
    monkeypatch.setattr(ta, "aggregate", boom)
    df = pd.DataFrame({"g": ["a", "b"], "v": [1.0, 2.0]})
    r = ta.handle_top_n(df, TargetedParams(metric_column="v", group_by="g"), _contract(df))
    assert "Ana Ito" not in r.answer_text and "ValueError" in r.answer_text


async def test_connection_test_never_probes_another_users_folder(tmp_path, monkeypatch):
    import httpx
    from fastapi import FastAPI

    import connectors.csv_connector as csvc
    from api.auth import UserProfile, get_current_user
    from api.routes import connectors
    monkeypatch.setattr(csvc, "_UPLOADS_DIR", tmp_path.resolve())
    other = tmp_path / "u2" / "secret.csv"
    other.parent.mkdir()
    other.write_text("a\n1\n")
    app = FastAPI()
    app.include_router(connectors.router, prefix="/connectors")
    app.dependency_overrides[get_current_user] = lambda: UserProfile(uid="u1")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/connectors/test", json={"type": "csv", "file_path": str(other)})
    assert r.json() == {"success": False, "message": "File not found."}


# ── failure paths (b) and concurrency (c) ──────────────────────────────────────────────────

async def test_a_missing_upload_is_a_typed_final_error(tmp_path, monkeypatch):
    import connectors.csv_connector as csvc
    monkeypatch.setattr(csvc, "_UPLOADS_DIR", tmp_path.resolve())
    conn = csvc.CSVConnector(ConnectorConfig(type=ConnectorType.CSV,
                                             file_path=str(tmp_path / "gone.csv")))
    with pytest.raises(IngestionError) as err:
        await conn.load()
    assert "Upload it again" in err.value.user_message and err.value.retryable is False


async def test_error_node_says_whether_a_retry_can_help(run_env):
    from orchestrator.graph import _fail, error_node
    s = AnalysisSession(session_id="e")
    _fail(s, "Data ingestion", IngestionError("x", "Upload it again."))
    await error_node({"session": s})
    events = [run_env.events.get_nowait() for _ in range(run_env.events.qsize())]
    assert {"type": "error", "message": "Upload it again.", "retryable": False} in events


async def test_sandbox_unavailable_and_timeout_read_as_such(monkeypatch, recorded_llm):
    import agents.analysis_agent as aa
    from agents.errors import SandboxError
    from orchestrator.state import AnalysisTask
    from sandbox.executor import ExecutionResult
    df, _ = datagen.make("claims", 1)
    for name, words in (("SandboxUnavailable", "unavailable"), ("SandboxTimeout", "too long")):
        async def boom(code, df_parquet=None, n=name):
            return ExecutionResult(stdout="", stderr="", error="x", error_name=n)
        monkeypatch.setattr(aa, "run_code", boom)
        recorded_llm.responses.append("result['x'] = 1")
        s = AnalysisSession(session_id="e", data_contract=_contract(df), task_plan=AnalysisTask(
            raw_query="q", analysis_types=["eda"], target_metrics=["claim_amount"]))
        s.__dict__["_df_parquet"] = _parquet(df)
        with pytest.raises(SandboxError) as err:
            await aa.run_analysis_agent(s, recorded_llm)
        assert words in err.value.user_message


def test_deidentified_frames_are_never_deduplicated_after_the_fact():
    from agents.cleaning_agent import clean, CleanContext
    df = pd.DataFrame({"mrn": [f"M{i}" for i in range(32)] + ["M0", "M1"],
                       "patient_name": [f"P{i}" for i in range(34)], "cost": [1.0] * 34})
    out, c = prepare_frame(df, _contract(df), ConnectorType.CSV)
    ctx = CleanContext(df=out, ids={"mrn"}, dates=set(), targets=set(), drop_high_null=False,
                       deidentified=bool(c.deidentified))
    cleaned, audit = clean(ctx)
    assert len(cleaned) == 34   # two visits with another name were not merged into one


async def test_a_phi_follow_up_seeds_the_guard_from_the_source_before_sql(monkeypatch):
    import agents.ingestion_agent as ia
    from memory import session_data
    calls = []
    monkeypatch.setattr(session_data, "read_cleaned_parquet", lambda sid: None)

    async def seed(session, llm):
        calls.append(session.session_id)
    monkeypatch.setattr(ia, "seed_phi_guard", seed)
    s = AnalysisSession(session_id="s", cleaned_data_path="x",
                        data_contract=_contract(datagen.make("encounters", 1)[0]))
    pii_values.set(set())
    await ia._seed_guard_from_last_turn(s, None)
    assert calls == ["s"]


def test_a_version_switch_keeps_guarding_the_old_patient_names(tmp_path, monkeypatch):
    from api.routes.chat import _last_turn_pii
    df, _ = datagen.make("encounters", 1)
    path = tmp_path / "enc.csv"
    path.write_text(df.to_csv(index=False))
    _, c = prepare_frame(df, _contract(df), ConnectorType.CSV)
    s = AnalysisSession(session_id="s", cleaned_data_path="x", data_contract=c,
                        connector_config=ConnectorConfig(type=ConnectorType.CSV, file_path=str(path)))
    assert df["patient_name"].iloc[0] in _last_turn_pii(s)


async def test_a_legacy_patient_thread_is_reingested_not_read_from_its_raw_cache(monkeypatch, run_env):
    import orchestrator.graph as g
    from memory import session_data
    df, _ = datagen.make("encounters", 1)
    legacy = _contract(df).model_copy(update={"phi": False})   # stored before PHI mode existed
    monkeypatch.setattr(session_data, "read_cleaned_parquet", lambda sid: _parquet(df))
    monkeypatch.setattr(g, "read_cleaned_parquet", lambda sid: _parquet(df))
    loaded = []

    async def ingest(session, llm, q=None, reuse=None, **kw):
        loaded.append(1)
        out, c = prepare_frame(df, _contract(df), ConnectorType.CSV)
        session.__dict__["_df_parquet"] = _parquet(out)
        return c
    monkeypatch.setattr(g, "run_ingestion_agent", ingest)
    from orchestrator.state import AnalysisTask
    s = AnalysisSession(session_id="s", cleaned_data_path="x", data_contract=legacy,
                        task_plan=AnalysisTask(raw_query="q", resume_from="analysis",
                                               analysis_types=["eda"], target_metrics=[]))
    out = await g.ingestion_node({"session": s, "user_message": "q"})
    assert loaded and out["session"].data_contract.phi
    assert "patient_name" not in pd.read_parquet(io.BytesIO(run_env.frame)).columns


async def test_a_run_whose_thread_was_deleted_fails_for_good():
    from memory.run_store import InMemoryRunStore
    from orchestrator.runner import execute, in_memory_deps
    from dataclasses import replace
    deps = replace(in_memory_deps(), run_store=InMemoryRunStore())
    run, _ = await deps.run_store.create(session_id="gone", user_id="u", input={"message": "q"})
    assert await execute(await deps.run_store.claim(), deps) == "failed"
    events = [e for _, e in await deps.run_store.events_after(run.id, 0)]
    assert any(e.get("type") == "error" and e.get("retryable") is False for e in events)


async def test_a_typed_failure_at_resume_keeps_its_message(monkeypatch):
    import orchestrator.runner as runner
    from memory.run_store import InMemoryRunStore
    from dataclasses import replace
    deps = replace(runner.in_memory_deps(), run_store=InMemoryRunStore())
    s = AnalysisSession(session_id="s")
    await deps.session_store.create(s)
    run, _ = await deps.run_store.create(session_id="s", user_id="default", input={"message": "q"})

    async def boom(*a, **k):
        raise IngestionError("missing", "The file behind this dataset is no longer available.")
    monkeypatch.setattr(deps.graph, "ainvoke", boom)
    assert await runner.execute(await deps.run_store.claim(), deps) == "failed"
    events = [e for _, e in await deps.run_store.events_after(run.id, 0)]
    assert {"type": "error", "message": "The file behind this dataset is no longer available.",
            "retryable": False} in events


# ── prod parity (d) ──────────────────────────────────────────────────────────────────────────

def test_uvicorn_access_lines_still_format_and_are_scrubbed():
    import uvicorn.config

    from api.logging_scrub import install
    logging.config.dictConfig(uvicorn.config.LOGGING_CONFIG)
    install()
    stream = io.StringIO()
    handler = logging.getLogger("uvicorn.access").handlers[0]
    handler.setStream(stream)
    logging.getLogger("uvicorn.access").info('%s - "%s %s HTTP/%s" %d', "10.1.2.3:5000", "GET",
                                             "/health?email=a@b.io", "1.1", 200)
    out = stream.getvalue()
    assert "GET" in out and "200" in out and "a@b.io" not in out


def test_a_scrubbed_traceback_is_not_reformatted_from_the_raw_exception():
    from api.logging_scrub import ScrubFilter
    pii_values.set({"Ana Ito"})
    rec = logging.LogRecord("x", logging.ERROR, "f", 1, "boom", None, None)
    try:
        raise KeyError("Ana Ito")
    except KeyError:
        import sys
        rec.exc_info = sys.exc_info()
    ScrubFilter().filter(rec)
    assert rec.exc_info is None and "Ana Ito" not in (rec.exc_text or "")


@pytest.mark.parametrize("csv, kind", [(b"qty,x\n05,1\n3,2\n12,3\n", "int64"),
                                       (b"y,x\n0123.5,1\n7.25,2\n", "float64"),
                                       (b"acct,x\n00731,1\n12345,2\n", "str")])
def test_only_fixed_width_codes_keep_their_zeros(csv, kind):
    from connectors.csv_connector import _read_bytes
    df, _ = _read_bytes(csv, ".csv")
    assert (str(df.dtypes.iloc[0]) == kind) or (kind == "str" and df.iloc[:, 0].map(type).eq(str).all())


def test_the_eval_loads_domain_data_with_the_products_reader():
    from evals.run import load_cases, working_frame
    case = next(c for c in load_cases("analysis", None) if c["domain"] == "banking")
    df, _ = working_frame(case)
    assert pd.api.types.is_datetime64_any_dtype(df["snapshot_date"])
