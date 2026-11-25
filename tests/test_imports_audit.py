"""The 5.8c audit's findings on imports, each through the route (audit pass 1: a senior audit and
five reviewers). The fixtures are tests/test_imports.py's: the route, the store, the one reader,
a Stripe answering on the fake socket."""
from __future__ import annotations

import httpx
import pytest

from tests import test_imports as _ti
from tests.fake_network import serve
from tests.test_imports import _connect, _good_then, _inv, _settle, _sub, _tables, Stripe

client, _key = _ti.client, _ti._key   # the import tests' fixtures


def _no_gap(monkeypatch):
    import memory.session_store as ss
    monkeypatch.setattr(ss, "REFRESH_GAP_S", 0)


async def _latest(client, dataset_ids) -> dict:
    return {d: (await client.get(f"/api/datasets/{d}")).json()["latest"]["id"]
            for d in dataset_ids}


async def test_a_deleted_import_files_nothing_more_and_reads_no_more(client, monkeypatch):
    """Four reviewers: the running refresh kept the deleted import's key and filed new versions
    into the tables the user kept."""
    serve(monkeypatch, Stripe([_sub(1)], [_inv(1)]))
    body = (await _connect(client)).json()
    ids = [t["dataset_id"] for t in body["tables"]]
    before = await _latest(client, ids)
    _no_gap(monkeypatch)
    later = Stripe([_sub(1), _sub(2)], [_inv(1), _inv(2)])   # new data: a refresh would file it
    serve(monkeypatch, later)
    assert (await client.post(f"/api/imports/{body['id']}/refresh")).status_code == 202
    assert (await client.delete(f"/api/imports/{body['id']}?tables=false")).status_code == 200
    await _settle(client)
    assert await _latest(client, ids) == before
    assert "/v1/invoices" not in {p for p, _ in later.seen}   # no table begun after the delete


async def test_a_new_key_while_a_refresh_runs_is_refused(client, monkeypatch):
    serve(monkeypatch, Stripe([_sub(1)], [_inv(1)]))
    body = (await _connect(client)).json()
    _no_gap(monkeypatch)
    await client.post(f"/api/imports/{body['id']}/refresh")
    r = await client.put(f"/api/imports/{body['id']}/key",
                         json={"vendor": "stripe", "key": "rk_test_" "51CANARYimportKEY0000000000"})
    assert r.status_code == 409 and "refresh is running" in r.json()["detail"]
    await _settle(client)


async def test_a_new_key_clears_the_old_keys_failure_and_its_lockout(client, monkeypatch):
    """Senior audit m1: after a revoked key's three tries, the replaced key was refused for an
    hour and the card still said the key was rejected."""
    after, _ = await _good_then(client, monkeypatch, Stripe([_sub(1)], [_inv(1)], key="other"))
    for _ in range(2):
        await client.post(f"/api/imports/{after['id']}/refresh")
        await _settle(client)
    r = await client.put(f"/api/imports/{after['id']}/key",
                         json={"vendor": "stripe", "key": "rk_test_" "51CANARYimportKEY0000000000"})
    serve(monkeypatch, Stripe([_sub(1)], [_inv(1)]))
    assert r.status_code == 400   # the fake still wants "other": the new key is tested first
    serve(monkeypatch, Stripe([_sub(1)], [_inv(1)], key="rk_test_" "51NEWKEY0000000000000000"))
    r = await client.put(f"/api/imports/{after['id']}/key",
                         json={"vendor": "stripe", "key": "rk_test_" "51NEWKEY0000000000000000"})
    assert r.status_code == 200 and r.json()["state"] == "idle"
    assert not any(t["said"] for t in r.json()["tables"])
    assert (await client.post(f"/api/imports/{after['id']}/refresh")).status_code == 202
    await _settle(client)


@pytest.mark.parametrize("how", ["delete", "split"])
async def test_an_imports_table_is_changed_through_its_import_only(client, monkeypatch, how):
    """Senior audit M1 / code review 2: a table deleted or a version split off in the Datasets
    view left the import failing 'unexpected' on every refresh after."""
    serve(monkeypatch, Stripe([_sub(1)], [_inv(1)]))
    body = (await _connect(client)).json()
    did = body["tables"][0]["dataset_id"]
    if how == "delete":
        r = await client.delete(f"/api/datasets/{did}")
    else:
        v = (await client.get(f"/api/datasets/{did}")).json()["latest"]["id"]
        r = await client.post(f"/api/datasets/versions/{v}/split")
    assert r.status_code == 409 and "Stripe" in r.json()["detail"]


async def test_one_tables_unexpected_error_leaves_the_next_table_its_refresh(client, monkeypatch):
    """Senior audit m4: an error outside the reason codes in Subscriptions marked Invoices
    'unexpected' too, never fetched."""
    from connectors import imports as imp

    async def broken(*_a, **_k):
        raise KeyError("a field Stripe dropped")
    serve(monkeypatch, Stripe([_sub(1)], [_inv(1)]))
    body = (await _connect(client)).json()
    ids = {t["name"]: t["dataset_id"] for t in body["tables"]}
    before = await _latest(client, ids.values())
    _no_gap(monkeypatch)
    monkeypatch.setattr(imp.STRIPE_SUBSCRIPTIONS, "fetch", broken)
    serve(monkeypatch, Stripe([_sub(1), _sub(2)], [_inv(1), _inv(2)]))
    await client.post(f"/api/imports/{body['id']}/refresh")
    await _settle(client)
    t = _tables((await client.get(f"/api/imports/{body['id']}")).json())
    assert t["subscriptions"]["error"]["code"] == "unexpected" and t["invoices"]["error"] is None
    now = await _latest(client, ids.values())
    assert now[ids["subscriptions"]] == before[ids["subscriptions"]]
    assert now[ids["invoices"]] != before[ids["invoices"]]


async def test_a_failed_table_says_its_failure_never_its_last_runs_filing(client, monkeypatch):
    """Type review 1: 'no changes since the last copy' stood beside the failure it followed."""
    after, _ = await _good_then(client, monkeypatch, Stripe([_sub(1)], [_inv(1)], key="other"))
    assert all("filed" not in t for t in after["tables"])


async def test_an_answer_that_isnt_an_object_is_said_in_words(client, monkeypatch):
    """Silent-failure hunt 4: a JSON list where an object belongs was a bare 500."""
    def odd(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=["not", "an", "object"])
    serve(monkeypatch, odd)
    r = await _connect(client)
    assert r.status_code == 400 and "Stripe" in r.json()["detail"]


async def test_a_user_has_at_most_five_imports(client, monkeypatch):
    """Security review 1: each import is a refresh of up to the row cap in memory."""
    serve(monkeypatch, Stripe([_sub(1)], [_inv(1)]))
    for _ in range(5):
        assert (await _connect(client)).status_code == 201
    r = await _connect(client)
    assert r.status_code == 409 and "five" in r.json()["detail"]


async def test_last_12_months_moves_with_each_refresh(client, monkeypatch):
    """Senior audit m2: the window's first day was stored once, so a year on it held two."""
    import datetime as dt
    serve(monkeypatch, Stripe([_sub(1)], [_inv(1)]))
    body = (await _connect(client)).json()
    store = client._transport.app.state.session_store
    await store.set_import_window(body["id"], "u1", "2020-01-01")   # set long ago
    _no_gap(monkeypatch)
    later = Stripe([_sub(1)], [_inv(1)])
    serve(monkeypatch, later)
    await client.post(f"/api/imports/{body['id']}/refresh")
    await _settle(client)
    year = (dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=365)).isoformat()   # UTC day
    since = {q.get("created[gte]") for p, q in later.seen if p == "/v1/invoices"}
    assert since == {str(int(dt.datetime.fromisoformat(year).replace(
        tzinfo=dt.timezone.utc).timestamp()))}
    assert (await client.get(f"/api/imports/{body['id']}")).json()["window_from"] == year


async def test_last_12_months_is_a_year_back_and_off_clears_it(client, monkeypatch):
    """Retro 5.8c: the route took the local day and the refresh the UTC one, so an evening in
    New York stored one day and the next refresh another. One rule, by UTC; the clock is set to
    a moment where the two days differ."""
    import datetime as dt
    from connectors import imports as imp
    monkeypatch.setattr(imp, "_now", lambda: dt.datetime(2026, 11, 24, 4, 30, tzinfo=dt.timezone.utc))
    serve(monkeypatch, Stripe([_sub(1)], [_inv(1)]))
    body = (await _connect(client)).json()
    on = await client.put(f"/api/imports/{body['id']}/window", json={"last_12_months": True})
    assert on.json()["window_from"] == "2025-11-24"
    off = await client.put(f"/api/imports/{body['id']}/window", json={"last_12_months": False})
    assert off.json()["window_from"] is None
