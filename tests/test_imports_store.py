"""SaaS imports in the store (5.8c, ADR-0010 amendment): an import's tables are datasets of their
own kind, versions only ever move forward, one refresh holds the row at a time, and the key is
never plain at rest. Same contract in memory and on Postgres (when TEST_PG_URL is set)."""
import os
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text

from api.config import settings
from memory.session_store import (REFRESH_DEADLINE_S, InMemorySessionStore, SessionStore)

PG = os.environ.get("TEST_PG_URL")
TABLES = "analysis_sessions, dashboard_charts, datasets, dataset_versions, imports"
KEY = "rk_test_51CANARYimportsSTOREkey000"
T0 = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)


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


async def _stripe(store, uid="u1"):
    return await store.create_import(uid, vendor="stripe", account="Acme (test)", test_mode=True,
                                     secrets={"key": KEY},
                                     tables={"subscriptions": "Stripe · Subscriptions",
                                             "invoice_lines": "Stripe · Invoice lines"})


def _writer(uploads, uid="u1"):
    n = [0]

    def write() -> str:
        d = uploads / uid
        d.mkdir(exist_ok=True)
        n[0] += 1
        p = d / f"t{n[0]}.parquet"
        p.write_bytes(b"x")
        return str(p)
    return write


async def test_an_import_has_a_dataset_of_its_own_kind_per_table(store):
    imp = await _stripe(store)
    assert set(imp.tables) == {"subscriptions", "invoice_lines"}
    names = {}
    for t, did in imp.tables.items():
        ds = await store.get_dataset(did, "u1")
        assert ds.kind == "import" and ds.versions == []
        names[t] = ds.name
    assert names == {"subscriptions": "Stripe · Subscriptions",
                     "invoice_lines": "Stripe · Invoice lines"}
    assert (await store.get_import(imp.id, "u1")).secrets == {"key": KEY}
    assert await store.get_import(imp.id, "u2") is None
    assert KEY not in str(imp.public(T0))


async def test_the_key_is_never_plain_at_rest(store):
    imp = await _stripe(store)
    if isinstance(store, InMemorySessionStore):
        raw = str(store._imports[imp.id])
    else:
        async with store._engine.begin() as conn:
            raw = str((await conn.execute(text("SELECT * FROM imports"))).all())
    assert KEY not in raw and "enc:" in raw


async def test_versions_move_forward_and_nothing_new_is_the_latest_only(store, uploads):
    """Red-team 3: back to an older copy's bytes is a new version (the latest), never 'existing'
    pointing at the old one while the latest stays the stale copy."""
    imp = await _stripe(store)
    did, write = imp.tables["subscriptions"], _writer(uploads)
    add = lambda h: store.add_import_version("u1", did, content_hash=h, columns=["id"],  # noqa: E731
                                             rows=1, write_file=write)
    v1, s1 = await add("a" * 64)
    same, s2 = await add("a" * 64)
    v2, s3 = await add("b" * 64)
    v3, s4 = await add("a" * 64)
    assert (s1, s2, s3, s4) == ("new_version", "unchanged", "new_version", "new_version")
    assert same.id == v1.id and v3.version == 3
    ds = await store.get_dataset(did, "u1")
    assert ds.latest.id == v3.id and [v.version for v in ds.versions] == [1, 2, 3]


async def test_an_upload_never_lands_in_an_imports_dataset(store, uploads):
    """Red-team 3: the same bytes, or the same name and columns, as an import's table."""
    imp = await _stripe(store)
    did = imp.tables["subscriptions"]
    await store.add_import_version("u1", did, content_hash="c" * 64, columns=["id"], rows=1,
                                   write_file=_writer(uploads))
    ds, v, status = await store.register_upload(
        "u1", name="Stripe · Subscriptions", content_hash="c" * 64, columns=["id"], rows=1,
        write_file=_writer(uploads))
    assert status == "new_dataset" and ds.id != did and ds.kind == "upload"
    assert await store.register_upload("u1", name="x", content_hash="d" * 64, columns=["id"],
                                       rows=1, write_file=_writer(uploads),
                                       dataset_id=did) is None
    assert await store.add_import_version("u1", ds.id, content_hash="e" * 64, columns=["id"],
                                          rows=1, write_file=_writer(uploads)) is None


async def test_one_refresh_holds_the_row(store):
    imp = await _stripe(store)
    assert await store.claim_refresh(imp.id, "u1", "tok-a", T0) == "claimed"
    assert await store.claim_refresh(imp.id, "u1", "tok-b", T0 + timedelta(minutes=1)) == "running"
    assert await store.claim_refresh(imp.id, "u2", "tok-c", T0) is None
    # the holder writes; anyone else's write changes nothing
    assert not await store.save_refresh(imp.id, "tok-b", status={"x": 1})
    assert await store.save_refresh(imp.id, "tok-a", status={"subscriptions": {"rows": 3}},
                                    progress={"table": 1, "of": 2})
    got = await store.get_import(imp.id, "u1")
    assert got.status == {"subscriptions": {"rows": 3}} and got.progress == {"table": 1, "of": 2}
    assert await store.save_refresh(imp.id, "tok-a", state="idle", now=T0 + timedelta(minutes=2))
    got = await store.get_import(imp.id, "u1")
    assert got.state == "idle" and got.owner is None and got.progress is None
    assert got.last_success_at == T0 + timedelta(minutes=2)


async def test_a_good_refresh_waits_30_minutes_a_failed_one_doesnt(store):
    imp = await _stripe(store)
    await store.claim_refresh(imp.id, "u1", "t1", T0)
    await store.save_refresh(imp.id, "t1", state="idle", now=T0)
    assert await store.claim_refresh(imp.id, "u1", "t2", T0 + timedelta(minutes=29)) == "too_soon"
    assert await store.claim_refresh(imp.id, "u1", "t2", T0 + timedelta(minutes=31)) == "claimed"
    await store.save_refresh(imp.id, "t2", state="failed")
    assert await store.claim_refresh(imp.id, "u1", "t3", T0 + timedelta(minutes=32)) == "claimed"


async def test_at_most_three_attempts_an_hour(store):
    imp = await _stripe(store)
    for i in range(3):
        assert await store.claim_refresh(imp.id, "u1", f"t{i}", T0 + timedelta(minutes=i)) \
            == "claimed"
        await store.save_refresh(imp.id, f"t{i}", state="failed")
    assert await store.claim_refresh(imp.id, "u1", "t3", T0 + timedelta(minutes=10)) == "too_many"
    assert await store.claim_refresh(imp.id, "u1", "t3", T0 + timedelta(minutes=61)) == "claimed"


async def test_a_refresh_whose_process_died_is_interrupted_never_running_forever(store):
    """Red-team 4: no startup sweep (a second process starting would kill a live refresh): a
    refresh past its deadline and a margin reads as interrupted, and the next claim takes it;
    the dead one's late writes change nothing."""
    imp = await _stripe(store)
    await store.claim_refresh(imp.id, "u1", "dead", T0)
    late = T0 + timedelta(seconds=REFRESH_DEADLINE_S + 60)
    assert await store.claim_refresh(imp.id, "u1", "new", late) == "running"
    later = T0 + timedelta(seconds=REFRESH_DEADLINE_S + 3 * 60)
    got = await store.get_import(imp.id, "u1")
    assert got.public(later)["state"] == "failed" and got.public(later)["interrupted"]
    assert got.public(late)["state"] == "refreshing"
    assert await store.claim_refresh(imp.id, "u1", "new", later) == "claimed"
    assert not await store.save_refresh(imp.id, "dead", state="idle", now=later)


async def test_replace_key_keeps_the_tables_and_delete_takes_the_key_now(store, uploads):
    imp = await _stripe(store)
    assert await store.replace_import_key(imp.id, "u1", secrets={"key": "rk_test_NEWKEY123"},
                                          account="Acme", test_mode=False)
    got = await store.get_import(imp.id, "u1")
    assert got.tables == imp.tables and got.secrets == {"key": "rk_test_NEWKEY123"}
    assert not got.test_mode and got.account == "Acme"
    assert not await store.replace_import_key(imp.id, "u2", secrets={}, account="x",
                                              test_mode=True)
    kept = await _stripe(store)
    assert await store.delete_import(kept.id, "u1", with_tables=False)
    assert await store.get_import(kept.id, "u1") is None
    assert all([await store.get_dataset(d, "u1") for d in kept.tables.values()])
    assert await store.delete_import(imp.id, "u1", with_tables=True)
    assert not any([await store.get_dataset(d, "u1") for d in imp.tables.values()])
    assert [i.id for i in await store.list_imports("u1")] == []


async def test_the_window_is_the_users_choice(store):
    imp = await _stripe(store)
    assert await store.set_import_window(imp.id, "u1", "2025-11-22")
    assert (await store.get_import(imp.id, "u1")).window_from == "2025-11-22"
    assert await store.set_import_window(imp.id, "u1", None)
    assert (await store.get_import(imp.id, "u1")).window_from is None
