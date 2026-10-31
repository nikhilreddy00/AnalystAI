"""Phase 5.6b workspace store: threads (create/save fence, tombstones, sidebar fields) and datasets
(upload → version rules, split, live sources, cascade). Same contract in memory and on Postgres
(when TEST_PG_URL is set)."""
import asyncio
import os
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text

from api.config import settings
from memory.session_store import (DatasetConflict, InMemorySessionStore, SessionStore,
                                  make_title)
from orchestrator.state import AnalysisSession, ConnectorConfig, ConnectorType

PG = os.environ.get("TEST_PG_URL")
TABLES = "analysis_sessions, dashboard_charts, datasets, dataset_versions"
COLS = ["date", "region", "revenue"]


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setattr(settings, "encryption_key", Fernet.generate_key().decode())


@pytest.fixture
def uploads(tmp_path, monkeypatch):
    from connectors import csv_connector
    monkeypatch.setattr(csv_connector, "_UPLOADS_DIR", tmp_path)
    return tmp_path


@pytest.fixture(params=["memory", "postgres"])
async def store(request):
    if request.param == "memory":
        yield InMemorySessionStore()
        return
    if not PG:
        pytest.skip("TEST_PG_URL not set")
    s = SessionStore(PG)
    async with s._engine.begin() as conn:
        await conn.execute(text(f"DROP TABLE IF EXISTS {TABLES}"))
    await s.init()
    yield s
    async with s._engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {TABLES}"))
    await s._engine.dispose()


def _session(sid="s1", uid="u1", **kw) -> AnalysisSession:
    return AnalysisSession(session_id=sid, user_id=uid, **kw)


def _writer(uploads, name="f.csv", uid="u1"):
    calls = []

    def write() -> str:
        d = uploads / uid
        d.mkdir(exist_ok=True)
        p = d / f"{len(calls)}-{name}"
        p.write_text("x")
        calls.append(p)
        return str(p)
    return write, calls


async def _upload(store, uploads, h, name="orders", cols=COLS, uid="u1", dataset_id=None):
    write, calls = _writer(uploads, uid=uid)
    got = await store.register_upload(uid, name=name, content_hash=h, columns=cols, rows=10,
                                      write_file=write, dataset_id=dataset_id)
    return got, calls


# ── Threads ────────────────────────────────────────────────────────────────────

async def test_save_never_creates_a_thread(store):
    """Only POST /chat inserts: a worker's late save can't bring a deleted thread back."""
    assert await store.save(_session()) is False
    assert await store.load("s1") is None
    await store.create(_session(), title="t")
    assert await store.save(_session(status="completed")) is True
    assert (await store.load("s1")).status == "completed"


async def test_a_run_save_keeps_rename_pin_and_archive(store):
    await store.create(_session(), title="auto")
    await store.update_thread("s1", "u1", title="Board numbers", pinned=True, archived=True)
    await store.save(_session(status="completed", dataset_name="orders"))
    [t] = await store.list_sessions("u1")
    assert (t.title, t.pinned, t.archived, t.dataset_name, t.status) == (
        "Board numbers", True, True, "orders", "completed")


async def test_a_question_is_searchable_and_unarchives(store):
    await store.create(_session(), title="t")
    await store.update_thread("s1", "u1", archived=True)
    await store.note_question("s1", "revenue by region?")
    await store.note_question("s1", "and by month?")
    [t] = await store.list_sessions("u1")
    assert t.questions == "revenue by region?\nand by month?" and t.archived is False


async def test_a_tombstoned_thread_is_hidden_everywhere(store):
    from orchestrator.state import ChartConfig, ChartType, InsightReport
    s = _session(insight_report=InsightReport(
        executive_summary="x", technical_findings=[], recommended_actions=[], anomaly_alerts=[],
        proactive_questions=[], methodology_notes=""))
    await store.create(s, title="t")
    chart = ChartConfig(id="c1", chart_type=ChartType.BAR, title="c", x_key="a", y_keys=["b"],
                        data=[], source_finding="f")
    await store.pin_chart("u1", "s1", chart.model_dump())
    assert await store.get_dashboard("u1") and await store.get_latest_analysis("u1")
    assert await store.tombstone("s1", "u2") is False   # not yours
    assert await store.tombstone("s1", "u1") is True
    assert await store.tombstone("s1", "u1") is False   # already gone
    assert await store.load("s1") is None
    assert await store.get_summary("s1", "u1") is None
    assert await store.list_sessions("u1") == []
    assert await store.get_dashboard("u1") == []
    assert await store.get_latest_analysis("u1") is None
    assert await store.save(s) is False
    assert await store.update_thread("s1", "u1", title="x") is False
    assert [sid for sid, _, _ in await store.tombstoned()] == ["s1"]
    assert "s1" in await store.session_ids()   # still a row until purged
    await store.purge_session("s1")
    assert await store.count_rows(session_id="s1") == {
        "analysis_sessions": 0, "dashboard_charts": 0, "datasets": 0, "dataset_versions": 0}


async def test_summaries_are_utc_and_scoped(store):
    await store.create(_session(), title="t")
    await store.create(_session("s2", uid="u2"), title="t")
    [t] = await store.list_sessions("u1")
    assert t.updated_at.tzinfo is not None and t.updated_at.utcoffset() == timedelta(0)
    assert await store.get_summary("s2", "u1") is None


async def test_idle_threads(store):
    await store.create(_session(), title="t")
    assert await store.idle_threads(datetime.now(timezone.utc) - timedelta(days=1)) == []
    assert await store.idle_threads(datetime.now(timezone.utc) + timedelta(seconds=5)) == [
        ("s1", "u1")]


def test_titles_by_code():
    assert make_title("orders", "What drove revenue last quarter?") == \
        "orders · What drove revenue last quarter?"
    assert make_title("orders", None) == "orders"
    assert make_title(None, "  hi \n there ") == "Analysis · hi there"
    long = make_title("orders", "x" * 200)
    assert long.endswith("…") and len(long) == len("orders · ") + 60


# ── Datasets ───────────────────────────────────────────────────────────────────

async def test_same_bytes_link_to_the_existing_version_without_a_new_file(store, uploads):
    (ds, v, status), calls = await _upload(store, uploads, "H1")
    assert status == "new_dataset" and v.version == 1 and len(calls) == 1
    (ds2, v2, status2), calls2 = await _upload(store, uploads, "H1", name="renamed")
    assert (status2, ds2.id, v2.id, calls2) == ("existing", ds.id, v.id, [])


async def test_same_name_and_columns_is_a_new_version(store, uploads):
    (ds, _, _), _ = await _upload(store, uploads, "H1")
    (ds2, v2, status), _ = await _upload(store, uploads, "H2", name="ORDERS",
                                         cols=list(reversed(COLS)))
    assert (status, ds2.id, v2.version) == ("new_version", ds.id, 2)
    assert [v.version for v in (await store.get_dataset(ds.id, "u1")).versions] == [1, 2]
    # An older version's bytes again: that version, not a third one.
    (_, v1, status), _ = await _upload(store, uploads, "H1")
    assert (status, v1.version) == ("existing", 1)


async def test_same_name_other_columns_is_a_new_dataset(store, uploads):
    (ds, _, _), _ = await _upload(store, uploads, "H1")
    (ds2, v2, status), _ = await _upload(store, uploads, "H2", cols=["a", "b"])
    assert status == "new_dataset" and ds2.id != ds.id and v2.version == 1


async def test_explicit_new_version_and_ownership(store, uploads):
    (ds, _, _), _ = await _upload(store, uploads, "H1")
    (ds2, v2, status), _ = await _upload(store, uploads, "H2", name="other", cols=["a"],
                                         dataset_id=ds.id)
    assert (status, ds2.id, v2.version) == ("new_version", ds.id, 2)
    got, calls = await _upload(store, uploads, "H3", uid="u2", dataset_id=ds.id)
    assert got is None and calls == []   # another user's dataset: nothing written


async def test_a_hash_only_in_a_deleted_dataset_makes_a_new_one(store, uploads):
    (ds, _, _), _ = await _upload(store, uploads, "H1")
    assert await store.tombstone_dataset(ds.id, "u1") == []
    (ds2, _, status), calls = await _upload(store, uploads, "H1")
    assert status == "new_dataset" and ds2.id != ds.id and len(calls) == 1


async def test_two_identical_uploads_at_once_make_one_version(store, uploads):
    results = await asyncio.gather(*[_upload(store, uploads, "H1") for _ in range(4)])
    statuses = sorted(r[0][2] for r in results)
    assert statuses == ["existing"] * 3 + ["new_dataset"]
    assert sum(len(calls) for _, calls in results) == 1
    [(ds, n)] = await store.list_datasets("u1")
    assert len(ds.versions) == 1 and n == 0


async def test_split_undo(store, uploads):
    (ds, v1, _), _ = await _upload(store, uploads, "H1")
    (_, v2, _), _ = await _upload(store, uploads, "H2")
    with pytest.raises(DatasetConflict):
        await store.split_version(v1.id, "u1")   # version 1 is already its own dataset
    assert await store.split_version(v2.id, "u2") is None
    new = await store.split_version(v2.id, "u1")
    assert new.id != ds.id and [v.version for v in new.versions] == [1]
    assert [v.id for v in (await store.get_dataset(ds.id, "u1")).versions] == [v1.id]
    # A version with an analysis can't move: its reports cite it.
    (_, v3, _), _ = await _upload(store, uploads, "H3")
    await store.create(_session(dataset_id=ds.id, dataset_version_id=v3.id), title="t")
    with pytest.raises(DatasetConflict):
        await store.split_version(v3.id, "u1")


async def test_live_sources_are_one_dataset_per_connection(store):
    a = ConnectorConfig(type=ConnectorType.SQL, connection_string="postgresql://h1/db",
                        table_name="orders")
    b = a.model_copy(update={"connection_string": "postgresql://h2/db"})
    d1 = await store.ensure_live_dataset("u1", "orders", a)
    assert (await store.ensure_live_dataset("u1", "orders", a)).id == d1.id
    d2 = await store.ensure_live_dataset("u1", "orders", b)
    assert d2.id != d1.id   # same table name, another database
    got = await store.get_dataset(d1.id, "u1")
    assert got.kind == "live" and got.versions == []
    assert got.config.connection_string == "postgresql://h1/db"   # decrypted on read
    assert await store.get_dataset(d1.id, "u2") is None


async def test_deleting_a_dataset_cascades_to_its_threads(store, uploads):
    (ds, v, _), calls = await _upload(store, uploads, "H1")
    await store.create(_session("a", dataset_id=ds.id, dataset_version_id=v.id), title="t")
    await store.create(_session("b", dataset_id=ds.id, dataset_version_id=v.id), title="t")
    await store.create(_session("c"), title="other data")
    assert [n for _, n in await store.list_datasets("u1")] == [2]
    assert await store.tombstone_dataset(ds.id, "u2") is None
    assert sorted(await store.tombstone_dataset(ds.id, "u1")) == ["a", "b"]
    assert [t.session_id for t in await store.list_sessions("u1")] == ["c"]
    assert await store.get_dataset(ds.id, "u1") is None
    assert await store.purge_dataset(ds.id) is False   # its threads still have rows
    for sid in ("a", "b"):
        await store.purge_session(sid)
    assert await store.purge_dataset(ds.id) is True
    assert not calls[0].exists()
    assert await store.count_rows(dataset_id=ds.id) == {
        "analysis_sessions": 0, "dashboard_charts": 0, "datasets": 0, "dataset_versions": 0}


async def test_purge_never_deletes_a_file_outside_the_users_uploads(store, uploads, tmp_path):
    outside = tmp_path.parent / "keep-me.csv"
    outside.write_text("x")
    got = await store.register_upload("u1", name="x", content_hash="H", columns=["a"], rows=1,
                                      write_file=lambda: str(outside))
    ds = got[0]
    await store.tombstone_dataset(ds.id, "u1")
    assert await store.purge_dataset(ds.id) is True
    assert outside.exists()
