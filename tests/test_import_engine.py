"""The import engine's every outcome by its exact reason and words (5.8c mutation gate: the matrix
said four failures through the route; the rest were reached by no test, or tested as "it fails").
Requests go through the vendor's own host-locked client on the guarded client; only the socket is
fake (tests/fake_network.py)."""
from __future__ import annotations

import copy
import logging

import httpcore
import httpx
import pytest

import connectors.base as base
from connectors import imports as imp
from connectors.imports import ImportFailure
from tests import test_imports as _ti
from tests.fake_network import serve
from tests.test_imports import _connect, _inv, _settle, _sub, _tables, Stripe

client, _key = _ti.client, _ti._key   # the import tests' fixtures
URL = "https://api.stripe.com/v1/x"


async def _fail(coro) -> dict:
    with pytest.raises(ImportFailure) as e:
        await coro
    return e.value.public()


def _answer(*responses):
    """A vendor answering each request with the next response; the requests kept."""
    seen = []

    def handler(request):
        seen.append(request)
        return responses[min(len(seen), len(responses)) - 1]
    return handler, seen


@pytest.fixture
def waits(monkeypatch):
    """The 429 waits, recorded instead of slept."""
    got = []

    async def sleep(seconds):
        got.append(seconds)
    monkeypatch.setattr(imp.asyncio, "sleep", sleep)
    return got


async def _get(monkeypatch, *responses, dns=None, **kw):
    handler, seen = _answer(*responses)
    serve(monkeypatch, handler, dns=dns)
    async with imp.vendor_client(frozenset({"api.stripe.com"})) as c:
        return await imp.get_json(c, URL, what="Invoices", **kw), seen


# ── a failure's form ──────────────────────────────────────────────────────────────────────────

def test_a_failure_is_its_code_and_what():
    assert str(ImportFailure("bad_key")) == "bad_key"
    assert ImportFailure("bad_key").public() == {"code": "bad_key", "what": ""}


def test_a_failure_is_said_in_our_words_with_its_vendor():
    assert imp.said("stripe", {"code": "permission", "what": "Invoices"}) == \
        "The key can't read Invoices. Give it Read access to Invoices and try again."
    assert imp.said("stripe", {"code": "no such code"}) == "Something went wrong reading Stripe."
    assert imp.said("acme", {"code": "bad_key"}).startswith("acme rejected the key")
    assert imp.said("stripe", {"code": "permission"}) == \
        "The key can't read . Give it Read access to  and try again."


def test_only_the_keys_parts_are_secrets():
    assert imp.secret_values({"shop": "a.myshopify.com", "client_id": "cid", "key": "k", "n": 5,
                              "empty": ""}) == ["cid", "k"]


def test_the_vendors_client_gives_a_request_a_minute():
    assert imp.vendor_client(frozenset({"api.stripe.com"})).timeout == httpx.Timeout(60)


# ── each answer's reason ──────────────────────────────────────────────────────────────────────

async def test_a_json_object_is_read_and_sent_as_a_get(monkeypatch, waits):
    got, seen = await _get(monkeypatch, httpx.Response(200, json={"a": 1}))
    assert got == {"a": 1} and seen[0].method == "GET" and waits == []   # a good answer: no wait


@pytest.mark.parametrize("body", [b"<html>", b"[1, 2]"])
async def test_an_answer_that_isnt_a_json_object_is_the_vendor_down(monkeypatch, body):
    got = await _fail(_get(monkeypatch, httpx.Response(200, content=body)))
    assert got == {"code": "vendor_down", "what": "not JSON"}


@pytest.mark.parametrize("status, want", [
    (401, {"code": "bad_key", "what": ""}),
    (403, {"code": "permission", "what": "Invoices"}),
    (500, {"code": "vendor_down", "what": "HTTP 500"}),
])
async def test_a_status_is_its_reason(monkeypatch, status, want):
    assert await _fail(_get(monkeypatch, httpx.Response(status, json={}))) == want


async def test_a_429_is_waited_out_once_by_its_retry_after_at_most_a_minute(monkeypatch, waits):
    ok = httpx.Response(200, json={})
    for header, wait in (("3", 3), ("90", 60), ("soon", imp.RETRY_WAIT_S), (None, imp.RETRY_WAIT_S)):
        waits.clear()
        slow = httpx.Response(429, headers={"Retry-After": header} if header else {})
        _, seen = await _get(monkeypatch, slow, ok)
        assert (waits, len(seen)) == ([wait], 2), header


async def test_a_second_429_or_one_stripe_says_not_to_retry_is_said_as_rate_limited(monkeypatch,
                                                                                    waits):
    slow = httpx.Response(429, json={})
    assert await _fail(_get(monkeypatch, slow, slow)) == {"code": "rate_limited", "what": ""}
    assert waits == [imp.RETRY_WAIT_S]
    waits.clear()
    never = httpx.Response(429, headers={"Stripe-Should-Retry": "false"})
    handler, seen = _answer(never, httpx.Response(200, json={}))
    serve(monkeypatch, handler)
    async with imp.vendor_client(frozenset({"api.stripe.com"})) as c:
        assert await _fail(imp.get_json(c, URL, what="x")) == {"code": "rate_limited", "what": ""}
    assert (waits, len(seen)) == ([], 1)


async def test_an_answer_past_its_cap_is_too_large(monkeypatch):
    handler, _ = _answer(httpx.Response(200, content=b"x" * 100))
    serve(monkeypatch, handler)
    async with imp.vendor_client(frozenset({"api.stripe.com"})) as c:
        assert await _fail(imp.fetch(c, URL, what="x", method="GET", cap=10)) == \
            {"code": "too_large", "what": ""}


async def test_an_answer_in_an_unread_encoding_is_the_vendor_down(monkeypatch):
    odd = httpx.Response(200, content=b"x", headers={"Content-Encoding": "compress"})
    assert await _fail(_get(monkeypatch, odd)) == {"code": "vendor_down", "what": "ValueError"}


async def test_a_private_address_is_the_host_rule_and_no_answer_the_network(monkeypatch):
    ok = httpx.Response(200, json={})
    assert await _fail(_get(monkeypatch, ok, dns={"api.stripe.com": ["10.0.0.1"]})) == \
        {"code": "host", "what": ""}

    class Down(httpcore.AsyncNetworkBackend):
        async def connect_tcp(self, *a, **k):
            raise httpcore.ConnectError("refused")
    serve(monkeypatch, lambda r: ok)
    monkeypatch.setattr(base, "_NETWORK", lambda: Down())
    async with imp.vendor_client(frozenset({"api.stripe.com"})) as c:
        assert await _fail(imp.get_json(c, URL, what="x")) == {"code": "network",
                                                               "what": "ConnectError"}


# ── a declared field ──────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("record", [{}, {"a": {}}, {"a": 1}, {"a": None}])
def test_a_declared_field_missing_is_a_schema_failure_naming_it(record):
    with pytest.raises(ImportFailure) as e:
        imp.need(record, "a", "b")
    assert e.value.public() == {"code": "schema", "what": "a.b"}


def test_a_declared_field_present_is_its_value_null_included():
    assert imp.need({"a": {"b": None}}, "a", "b") is None
    assert imp.need({"a": {"b": 0}}, "a", "b") == 0


# ── the refresh: what it saves as it goes ─────────────────────────────────────────────────────

def _record(monkeypatch, client):
    """The refresh's saves and window writes, recorded as they reach the store."""
    store = client._transport.app.state.session_store
    calls = []
    save, window = store.save_refresh, store.set_import_window

    async def save_refresh(import_id, token, **fields):
        calls.append(("save", copy.deepcopy(fields)))   # the refresh's status: as it was then
        return await save(import_id, token, **fields)

    async def set_import_window(import_id, user_id, value):
        calls.append(("window", user_id, value))
        return await window(import_id, user_id, value)
    monkeypatch.setattr(store, "save_refresh", save_refresh)
    monkeypatch.setattr(store, "set_import_window", set_import_window)
    return calls


def _progress(calls):
    return [{k: v for k, v in f["progress"].items() if k != "started_at"}
            for c, *rest in calls if c == "save" for f in rest if "progress" in f]


async def test_a_refresh_says_each_table_and_its_rows_as_it_goes(client, monkeypatch):
    serve(monkeypatch, Stripe([_sub(1), _sub(2)], [_inv(1)]))
    calls = _record(monkeypatch, client)
    assert (await _connect(client)).status_code == 201
    sub, inv = "Stripe · Subscriptions", "Stripe · Invoices"
    assert _progress(calls) == [
        {"table": 1, "of": 2, "rows": 0, "name": sub},          # begun, before the key's token
        {"table": 1, "of": 2, "rows": 0, "name": sub},
        {"table": 1, "of": 2, "rows": 2, "name": sub},          # fetched, before it is filed
        {"table": 2, "of": 2, "rows": 0, "name": inv},
        {"table": 2, "of": 2, "rows": 1, "name": inv}]
    saves = [f for c, *rest in calls if c == "save" for f in rest]
    assert all("started_at" in f["progress"] for f in saves if "progress" in f)
    # each table's status saved once it is done, before the next begins
    status = [{n: s.get("rows") for n, s in f["status"].items()}
              for f in saves if "status" in f and "state" not in f]
    assert status == [{"subscriptions": 2}, {"subscriptions": 2, "invoices": 1}]
    assert not [c for c in calls if c[0] == "window"]   # no window: none written


async def test_a_window_is_written_only_when_its_day_moves_and_for_its_owner(client, monkeypatch):
    serve(monkeypatch, Stripe([_sub(1)], [_inv(1)]))
    body = (await _connect(client)).json()
    store = client._transport.app.state.session_store
    await store.set_import_window(body["id"], "u1", imp.year_back())   # today's: nothing moves
    import memory.session_store as ss
    monkeypatch.setattr(ss, "REFRESH_GAP_S", 0)
    calls = _record(monkeypatch, client)
    await client.post(f"/api/imports/{body['id']}/refresh")
    await _settle(client)
    assert [c for c in calls if c[0] == "window"] == []
    await store.set_import_window(body["id"], "u1", "2020-01-01")
    calls.clear()
    await client.post(f"/api/imports/{body['id']}/refresh")
    await _settle(client)
    assert [c for c in calls if c[0] == "window"] == [("window", "u1", imp.year_back())]


async def test_a_refresh_that_loses_its_row_writes_nothing_more(client, monkeypatch):
    """Taken over (a delete, a new key): its first refused save is its last write."""
    serve(monkeypatch, Stripe([_sub(1)], [_inv(1)]))
    store = client._transport.app.state.session_store
    real, calls = store.save_refresh, []

    async def save_refresh(import_id, token, **fields):
        calls.append(fields)
        return False if len(calls) == 3 else await real(import_id, token, **fields)
    monkeypatch.setattr(store, "save_refresh", save_refresh)
    await _connect(client)
    assert len(calls) == 3


async def test_a_first_import_whose_second_table_fails_says_so_beside_the_first(client,
                                                                                monkeypatch):
    """A table with no last good copy fails as cleanly as one with."""
    async def down(*_a, **_k):
        raise ImportFailure("vendor_down", "HTTP 503")
    monkeypatch.setattr(imp.STRIPE_INVOICES, "fetch", down)   # the key test passed; the table not
    serve(monkeypatch, Stripe([_sub(1)], [_inv(1)]))
    body = (await _connect(client)).json()
    t = _tables((await client.get(f"/api/imports/{body['id']}")).json())
    assert t["subscriptions"]["error"] is None
    assert t["invoices"]["error"] == {"code": "vendor_down", "what": "HTTP 503"}
    assert t["invoices"]["failed_at"]


async def test_a_failure_before_the_tables_fails_every_table_by_its_reason(client, monkeypatch,
                                                                          caplog):
    """The key's token (an ImportFailure) or anything else before a table is fetched."""
    serve(monkeypatch, Stripe([_sub(1)], [_inv(1)]))
    body = (await _connect(client)).json()
    import memory.session_store as ss
    monkeypatch.setattr(ss, "REFRESH_GAP_S", 0)
    for raised, want in ((ImportFailure("bad_key"), {"code": "bad_key", "what": ""}),
                         (RuntimeError("boom"), {"code": "unexpected", "what": ""})):
        async def auth(*_a, raised=raised):
            raise raised
        monkeypatch.setattr(imp.VENDORS["stripe"], "auth", auth)
        caplog.clear()
        with caplog.at_level(logging.ERROR, logger="connectors.imports"):
            await client.post(f"/api/imports/{body['id']}/refresh")
            await _settle(client)
        t = _tables((await client.get(f"/api/imports/{body['id']}")).json())
        assert [t[n]["error"] for n in ("subscriptions", "invoices")] == [want, want]
        logged = [r.getMessage() for r in caplog.records]
        assert logged == ([] if want["code"] == "bad_key" else ["import refresh failed: stripe"])


async def test_a_deadline_after_the_first_table_keeps_the_first_tables_refresh(client,
                                                                              monkeypatch):
    serve(monkeypatch, Stripe([_sub(1)], [_inv(1)]))
    real = imp.STRIPE_INVOICES.fetch

    async def slow(*a, **k):
        raise TimeoutError
    monkeypatch.setattr(imp.STRIPE_INVOICES, "fetch", slow)
    body = (await _connect(client)).json()
    monkeypatch.setattr(imp.STRIPE_INVOICES, "fetch", real)
    t = _tables((await client.get(f"/api/imports/{body['id']}")).json())
    assert t["subscriptions"]["error"] is None
    assert t["invoices"]["error"] == {"code": "deadline", "what": ""}


async def test_a_tables_unexpected_error_is_logged_by_vendor_and_table(client, monkeypatch, caplog):
    async def broken(*_a, **_k):
        raise KeyError("a field Stripe dropped")
    monkeypatch.setattr(imp.STRIPE_INVOICES, "fetch", broken)
    serve(monkeypatch, Stripe([_sub(1)], [_inv(1)]))
    with caplog.at_level(logging.ERROR, logger="connectors.imports"):
        await _connect(client)
    assert [r.getMessage() for r in caplog.records] == ["import table failed: stripe invoices"]


async def test_the_refreshs_last_write_failing_is_logged(client, monkeypatch, caplog):
    serve(monkeypatch, Stripe([_sub(1)], [_inv(1)]))
    store = client._transport.app.state.session_store
    real = store.save_refresh

    async def save_refresh(import_id, token, **fields):
        if "state" in fields:
            raise ConnectionError("the store went away")
        return await real(import_id, token, **fields)
    monkeypatch.setattr(store, "save_refresh", save_refresh)
    with caplog.at_level(logging.ERROR, logger="connectors.imports"):
        await _connect(client)
    assert [r.getMessage() for r in caplog.records] == ["import refresh's end not saved: stripe"]
