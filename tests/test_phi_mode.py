"""Phase 5.5 WP2: E2B never sees personal values; patient data is analysed as a de-identified
working copy; logs and errors carry no values. Test names follow the plan's state × event tables
(F1 PHI run, F2 E2B upload, F3 boundaries)."""
from __future__ import annotations

import io
import json
import logging

import numpy as np
import pandas as pd
import pytest

from agents.errors import IngestionError
from agents.ingestion_agent import (
    build_contract,
    known_pii_values,
    prepare_frame,
    run_ingestion_agent,
    seed_phi_guard,
)
from api.config import settings
from evals import datagen
from guardrails import POOLED_LABEL, pseudonym, pseudonym_key, pseudonymize_columns, zip3
from llm.client import pii_values
from orchestrator.state import AnalysisSession, ConnectorConfig, ConnectorType


def _parquet(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    df.to_parquet(buf, index=False)
    return buf.getvalue()


def _contract(df: pd.DataFrame):
    return build_contract(df, ConnectorType.CSV, {})


def _patients(n: int = 40, **cols) -> pd.DataFrame:
    base = {"mrn": [f"MRN-{i:06d}" for i in range(n)], "cost": np.arange(n, dtype=float)}
    return pd.DataFrame({**base, **cols})


# ── F3: pseudonyms and Safe Harbor boundaries ────────────────────────────────────────────────

def test_pseudonym_is_canonical_keyed_and_one_way(monkeypatch):
    k = pseudonym_key()
    assert pseudonym(1001, k) == pseudonym(1001.0, k) == pseudonym(" 1001 ", k)
    assert pseudonym("MRN-1", k) != pseudonym("MRN-2", k)
    assert pseudonym(1001, k).startswith("ID-") and len(pseudonym(1001, k)) == 19
    monkeypatch.setattr(settings, "encryption_key", "another-key")
    assert pseudonym(1001, pseudonym_key()) != pseudonym(1001, k)   # nobody without the key


def test_empty_key_refuses_to_make_pseudonyms(monkeypatch):
    monkeypatch.setattr(settings, "encryption_key", "")
    with pytest.raises(ValueError, match="ENCRYPTION_KEY"):
        pseudonym_key()


def test_pseudonym_collision_fails_closed(monkeypatch):
    import guardrails
    monkeypatch.setattr(guardrails, "pseudonym", lambda v, k, c="": "ID-same")
    with pytest.raises(ValueError, match="collision"):
        pseudonymize_columns(pd.DataFrame({"a": ["x", "y"]}), ["a"], b"k")


@pytest.mark.parametrize("raw, want", [(2139, "021"), ("02139", "021"), ("02139-4307", "021"),
                                       ("03601", "000"), ("89301", "000"), ("94103", "941"),
                                       (None, None), ("n/a", "n/a")])
def test_zip3(raw, want):
    assert zip3(raw) == want


def test_age_cap_boundary():
    df = _patients(3, age=[89, 90, 104])
    out, c = prepare_frame(df, _contract(df), ConnectorType.CSV)
    assert out["age"].tolist() == [89, 90, 90]
    assert [d.rows_affected for d in c.deidentification if d.operation == "cap"] == [1]


def _pooled(values: list) -> list:
    df = _patients(len(values), department=values)
    out, _ = prepare_frame(df, _contract(df), ConnectorType.CSV)
    return out["department"].tolist()


def test_pool_boundary_10_pooled_11_kept():
    got = _pooled(["A"] * 11 + ["B"] * 10 + ["C"] * 20)
    assert got.count("A") == 11 and got.count("B") == 0 and got.count(POOLED_LABEL) == 10


def test_pool_counts_case_variants_together_and_missing_stays_missing():
    got = _pooled(["Cardio"] * 6 + ["cardio "] * 6 + [None] * 3 + ["unknown"] * 2 + ["X"] * 20)
    assert got.count(POOLED_LABEL) == 2            # the 2 "unknown" labels, not the 12 cardios
    assert got.count("Cardio") == 6 and got.count("cardio ") == 6
    assert sum(v is None or v != v for v in got) == 3   # missing is not a category


def test_a_single_small_group_stays_pooled_and_a_lone_group_is_untouched():
    assert _pooled(["Rad"] * 6 + ["X"] * 30 + ["Y"] * 30).count(POOLED_LABEL) == 6   # "Other" < 11
    assert set(_pooled(["X"] * 30)) == {"X"}
    assert _pooled(["Yes"] * 9 + ["No"] * 291).count("Yes") == 9   # a yes/no outcome: never pooled


def test_too_specific_text_is_withheld_dates_and_metrics_kept():
    n = 60
    df = _patients(n, pharmacy=[f"Pharmacy {i % 55}" for i in range(n)],   # > 50 distinct
                   visit_date=pd.date_range("2026-01-01", periods=n).strftime("%Y-%m-%d"),
                   amount=np.linspace(10.5, 99.5, n))
    out, _ = prepare_frame(df, _contract(df), ConnectorType.CSV)
    assert "pharmacy" not in out.columns            # fail closed: too specific to be a category
    assert POOLED_LABEL not in set(out["visit_date"]) and out["amount"].equals(df["amount"])


def test_non_patient_data_passes_unchanged():
    df, _ = datagen.make("employees", 1)
    out, c = prepare_frame(df, _contract(df), ConnectorType.CSV)
    assert out is df and not c.phi and not c.deidentification


def test_free_text_naming_a_patient_is_withheld_but_short_labels_stay():
    df = _patients(20, notes=["Patient Ana Ito seen today"] * 10 + [""] * 10,
                   ward=["North wing"] * 20)
    out, c = prepare_frame(df, _contract(df), ConnectorType.CSV)
    assert "notes" not in out.columns and "ward" in out.columns


# ── F1: a PHI run ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def encounters_file(tmp_path, monkeypatch):
    import connectors.csv_connector as csvc
    df, truth = datagen.make("encounters", 1)
    path = tmp_path / "uploads" / "enc.csv"
    path.parent.mkdir()
    path.write_text(df.to_csv(index=False))
    monkeypatch.setattr(csvc, "_UPLOADS_DIR", path.parent.resolve())
    return path, df, truth


def _session(path) -> AnalysisSession:
    return AnalysisSession(session_id="s1",
                           connector_config=ConnectorConfig(type=ConnectorType.CSV, file_path=str(path)))


async def test_phi_prepare_fresh(encounters_file):
    path, raw, truth = encounters_file
    guard: set = set()
    pii_values.set(guard)
    s = _session(path)
    c = await run_ingestion_agent(s, None)
    frame = pd.read_parquet(io.BytesIO(s.__dict__["_df_parquet"]))
    assert c.phi and not c.pii_columns
    assert not {"patient_name", "date_of_birth", "phone", "notes"} & set(frame.columns)
    assert frame["mrn"].str.match(r"^ID-[0-9a-f]{16}$").all()
    assert frame["zip_code"].str.len().eq(3).all() and frame["age"].max() <= 90
    assert POOLED_LABEL in set(frame["department"]) and "Radiology" not in set(frame["department"])
    assert {d.operation for d in c.deidentification} >= {"withhold", "pseudonymize", "generalize",
                                                        "cap", "pool"}
    assert "withheld" in c.schema_summary and any("withheld" in a for a in c.source_evidence.assumptions)
    assert raw["patient_name"].iloc[0] in guard and raw["mrn"].iloc[0] in guard   # raw values guarded
    text = c.model_dump_json()
    assert not any(v in text for v in (raw["patient_name"].iloc[0], raw["mrn"].iloc[0],
                                        raw["zip_code"].iloc[0]))


async def test_phi_resume_rebuilds_the_same_frame(encounters_file):
    path, _, _ = encounters_file
    a, b = _session(path), _session(path)
    ca, cb = await run_ingestion_agent(a, None), await run_ingestion_agent(b, None)
    assert ca.fingerprint == cb.fingerprint and a.__dict__["_df_parquet"] == b.__dict__["_df_parquet"]


async def test_phi_followup_guard_from_raw(encounters_file):
    path, raw, _ = encounters_file
    s = _session(path)
    s.data_contract = await run_ingestion_agent(s, None)
    cached = pd.read_parquet(io.BytesIO(s.__dict__.pop("_df_parquet")))
    guard: set = set()
    pii_values.set(guard)
    assert not known_pii_values(cached, s.data_contract)   # the cache alone guards nothing…
    await seed_phi_guard(s, None)                          # …so the source seeds the guard
    assert raw["patient_name"].iloc[3] in guard and "_df_parquet" not in s.__dict__


async def test_phi_then_non_phi_version_skips_the_source_read(monkeypatch):
    s = AnalysisSession(session_id="s2")
    df, _ = datagen.make("employees", 1)
    s.data_contract = _contract(df)
    called = []
    import agents.ingestion_agent as ia
    monkeypatch.setattr(ia, "run_ingestion_agent", lambda *a, **k: called.append(1))
    pii_values.set(set())
    await seed_phi_guard(s, None)
    assert not called


async def test_phi_prepare_fails_closed(encounters_file, monkeypatch):
    import agents.ingestion_agent as ia
    path, _, _ = encounters_file
    monkeypatch.setattr(settings, "encryption_key", "")
    with pytest.raises(IngestionError) as err:
        await run_ingestion_agent(_session(path), None)
    assert "patient records" in err.value.user_message
    monkeypatch.setattr(settings, "encryption_key", "k" * 32)
    monkeypatch.setattr(ia, "prepare_frame", lambda *a: (_ for _ in ()).throw(RuntimeError("x")))
    s = _session(path)
    with pytest.raises(IngestionError):
        await run_ingestion_agent(s, None)
    assert "_df_parquet" not in s.__dict__   # the raw frame never goes on


def test_birth_dates_are_never_a_time_axis_or_a_period():
    from agents.cleaning_agent import _date_ranges
    df = pd.DataFrame({"date_of_birth": pd.to_datetime(["1950-01-01", "1980-06-01"] * 5),
                       "x": range(10)})
    c = _contract(df)
    assert c.date_columns == [] and "date_of_birth" in c.pii_columns
    assert _date_ranges(df, {"date_of_birth"}) == {}


def test_guard_holds_mrns_but_not_birth_dates_on_patient_data():
    df, _ = datagen.make("encounters", 1)
    c = _contract(df)
    vals = known_pii_values(df, c)
    assert df["mrn"].iloc[0] in vals and df["date_of_birth"].iloc[0] not in vals


def test_sql_results_mask_record_numbers():
    from agents.sql_agent import _pii_columns
    from connectors.sql_connector import QueryResult
    res = QueryResult(columns=["mrn", "department"], rows=[["MRN-1", "Cardio"]], truncated=False)
    assert _pii_columns(res) == {"mrn"}


async def test_cleaning_audit_starts_with_the_deidentification(encounters_file):
    from agents.cleaning_agent import run_cleaning_agent
    path, _, _ = encounters_file
    s = _session(path)
    s.data_contract = await run_ingestion_agent(s, None)
    _, audit = await run_cleaning_agent(s)
    assert audit.decisions[0].operation == "withhold" and "de-identified" in audit.summary
    assert "date_of_birth" not in audit.date_ranges


# ── F2: the E2B upload ───────────────────────────────────────────────────────────────────────

def test_e2b_no_pii_unchanged():
    from agents.analysis_agent import e2b_frame
    payload = _parquet(datagen.make("claims", 1)[0])
    assert e2b_frame(payload, []) == (payload, {})


def test_e2b_pseudonymises_pii():
    from agents.analysis_agent import e2b_frame
    df = pd.DataFrame({"customer_name": ["Ana Ito", None, "Ben Moss"], "phone": [5551234567, 1.0, 2.0],
                       "dob": pd.to_datetime(["1950-01-02", None, "1960-03-04"]), "x": [1, 2, 3]})
    out = pd.read_parquet(io.BytesIO(e2b_frame(_parquet(df), ["customer_name", "phone", "dob"])[0]))
    text = out.to_csv(index=False)
    assert list(out.columns) == list(df.columns) and out["x"].tolist() == [1, 2, 3]
    assert not any(v in text for v in ("Ana Ito", "5551234567", "1950-01-02"))
    assert out["customer_name"].isna().tolist() == [False, True, False]


def _builtins(df: pd.DataFrame, case: dict) -> dict:
    from agents.analysis_agent import _build_guaranteed_code, _build_standard_code
    from orchestrator.state import AnalysisTask
    contract = _contract(df)
    result = {"eda_summary": {}, "correlations": {}, "segments": [], "trends": [], "anomalies": [],
              "statistical_tests": [], "distributions": {}, "key_findings": []}
    g = {"df": df.copy(), "pd": pd, "np": np, "result": result, "json": json}
    exec(_build_guaranteed_code(contract) + _build_standard_code(
        contract, AnalysisTask(raw_query=case["question"], **case["task"])), g)
    return json.loads(json.dumps(result, default=str))


def test_old_cases_unaffected_by_pseudonyms():
    """The 46 retail cases: E2B now gets pseudonymised customer emails and names. Every field the
    built-in code computes that a check scores is the same as on the raw upload."""
    from agents.analysis_agent import e2b_frame
    from agents.utils import pii_columns
    from evals.run import dataset, load_cases
    seen = set()
    for case in (c for c in load_cases("analysis", None) if c["domain"] == "retail"):
        key = (case["dataset"]["name"], case["dataset"]["seed"], json.dumps(case["task"]))
        if key in seen:
            continue
        seen.add(key)
        df = dataset(case)[0]
        pii = pii_columns(_contract(df))
        masked = pd.read_parquet(io.BytesIO(e2b_frame(_parquet(df), pii)[0]))
        a, b = _builtins(df, case), _builtins(masked, case)
        for field in ("eda_summary", "correlations", "segments", "trends", "anomalies",
                      "statistical_tests"):
            assert a[field] == b[field], (case["id"], field)
    assert len(seen) > 20


async def test_e2b_failure_message_carries_no_values(monkeypatch, recorded_llm):
    import agents.analysis_agent as aa
    from agents.errors import SandboxError
    from orchestrator.state import AnalysisTask
    from sandbox.executor import ExecutionResult
    df, _ = datagen.make("sales", 1)

    async def boom(code, df_parquet=None):
        return ExecutionResult(stdout="", stderr="", error="KeyError: 'Ana Ito' in row 7")
    monkeypatch.setattr(aa, "run_code", boom)
    recorded_llm.responses.append("result['x'] = 1")
    s = AnalysisSession(session_id="e")
    s.data_contract = _contract(df)
    s.task_plan = AnalysisTask(raw_query="q", analysis_types=["eda"], target_metrics=["revenue"])
    s.__dict__["_df_parquet"] = _parquet(df)
    with pytest.raises(SandboxError) as err:
        await aa.run_analysis_agent(s, recorded_llm)
    assert "Ana Ito" not in err.value.user_message and "KeyError" in err.value.user_message


# ── Hygiene: logs and errors ─────────────────────────────────────────────────────────────────

def _scrubbed_log(msg: str, *args, exc: Exception | None = None) -> str:
    from api.logging_scrub import ScrubFilter
    out = io.StringIO()
    h = logging.StreamHandler(out)
    h.addFilter(ScrubFilter())
    log = logging.getLogger("scrub-test")
    log.addHandler(h)
    log.propagate = False
    try:
        if exc:
            try:
                raise exc
            except Exception:
                log.exception(msg, *args)
        else:
            log.warning(msg, *args)
    finally:
        log.removeHandler(h)
    return out.getvalue()


def test_logs_scrub_guarded_values_and_patterns_in_messages_and_tracebacks():
    pii_values.set({"Ana Ito"})
    text = _scrubbed_log("row for %s (ana@x.io)", "Ana Ito", exc=KeyError("Ana Ito"))
    assert "Ana Ito" not in text and "ana@x.io" not in text and "KeyError" in text


def test_logs_without_a_run_still_mask_patterns():
    pii_values.set(None)
    assert "555-123-4567" not in _scrubbed_log("call 555-123-4567")


def test_a_failing_scrub_still_emits_the_line(monkeypatch):
    import api.logging_scrub as ls
    monkeypatch.setattr(ls, "scrub", lambda t: (_ for _ in ()).throw(RuntimeError()))
    assert ls.SCRUB_FAILED in _scrubbed_log("anything")


def test_install_covers_root_and_uvicorn_handlers():
    from api.logging_scrub import ScrubFilter, install
    h = logging.StreamHandler(io.StringIO())
    logging.getLogger("uvicorn.access").addHandler(h)
    try:
        install()
        install()   # idempotent
        assert sum(isinstance(f, ScrubFilter) for f in h.filters) == 1
    finally:
        logging.getLogger("uvicorn.access").removeHandler(h)


def test_node_failures_show_the_type_not_the_text():
    from orchestrator.graph import _fail
    s = AnalysisSession(session_id="f")
    _fail(s, "Analysis", KeyError("Ana Ito"))
    assert "Ana Ito" not in s.error_message and "KeyError" not in s.error_message


def test_csv_keeps_leading_zero_codes_as_text():
    from connectors.csv_connector import _read_bytes
    df, info = _read_bytes(b"zip,amount\n02134,0.5\n98101,1.25\n", ".csv")
    assert df["zip"].tolist() == ["02134", "98101"] and df["amount"].tolist() == [0.5, 1.25]
    df, _ = _read_bytes(b"zip,amount\n2134,0.5\n98101,1.25\n", ".csv")   # no zero written: as before
    assert df["zip"].tolist() == [2134, 98101]


def test_policyholders_are_people():
    df = pd.DataFrame({"policyholder": ["Ana Ito", "Ben Moss"] * 10, "premium": range(20)})
    assert _contract(df).pii_columns == ["policyholder"]
