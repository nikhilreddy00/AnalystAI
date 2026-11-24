"""Stripe's fetchers by what they send and keep (5.8c mutation gate: the recorded account never
paged, never paged a subscription's items, and never named a refused permission). Through the
host-locked client on the fake socket; the Stripe here answers as its API does (tests/test_imports
.py's, plus the subscription-items list)."""
from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from connectors import imports as imp
from connectors.imports import ImportFailure
from tests import test_imports as _ti
from tests.fake_network import serve
from tests.test_imports import KEY, _connect, _inv, _sub, Stripe

client, _key = _ti.client, _ti._key
HEADERS = {"Authorization": f"Bearer {KEY}", "Stripe-Version": imp.STRIPE_VERSION}


class StripeItems(Stripe):
    """Stripe with a subscription's items past the embedded ones: `/v1/subscription_items`."""

    def __init__(self, *a, items=None, **k):
        super().__init__(*a, **k)
        self.items = items or {}

    def __call__(self, request):
        url = urlparse(str(request.url))
        if url.path != "/v1/subscription_items" or url.path in self.fail or \
                request.headers.get("authorization") != f"Bearer {self.key}":
            return super().__call__(request)   # its key and its refusals as every list's
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        self.seen.append((url.path, q))
        objs = self.items[q["subscription"]]
        start = next((n + 1 for n, o in enumerate(objs) if o["id"] == q.get("starting_after")), 0)
        page = objs[start:start + int(q["limit"])]
        return httpx.Response(200, json={"data": page, "has_more": start + len(page) < len(objs)})


async def _fetch(monkeypatch, table, handler, window=None):
    serve(monkeypatch, handler)
    async with imp.vendor_client(frozenset({"api.stripe.com"})) as c:
        return await table.fetch(c, HEADERS, window)


# ── the key's form and its test ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("key, code", [("sk_live_x", "live_secret_key"), ("pk_live_x", "not_a_key"),
                                       ("", "not_a_key"), (None, "not_a_key")])
def test_a_key_of_the_wrong_form_is_refused_by_its_reason(key, code):
    with pytest.raises(ImportFailure) as e:
        imp._stripe_check({} if key is None else {"key": key})
    assert e.value.public() == {"code": code, "what": ""}


@pytest.mark.parametrize("key", ["rk_live_x", "rk_test_x", "sk_test_x"])
def test_a_restricted_or_test_key_passes_its_form(key):
    imp._stripe_check({"key": key})


async def test_every_request_carries_the_key_and_the_pinned_version(monkeypatch):
    stripe = Stripe([_sub(1)], [_inv(1)])
    seen = []
    serve(monkeypatch, lambda r: seen.append(r) or stripe(r))
    async with imp.vendor_client(frozenset({"api.stripe.com"})) as c:
        await imp._stripe_test(c, {"key": KEY})
    assert {(r.headers["authorization"], r.headers["stripe-version"]) for r in seen} == \
        {(f"Bearer {KEY}", imp.STRIPE_VERSION)}


@pytest.mark.parametrize("account, name", [
    ({"settings": {"dashboard": {"display_name": "Acme"}}}, "Acme"),
    ({"settings": {}, "business_profile": {"name": "Acme Ltd"}}, "Acme Ltd"),
    ({"settings": None, "business_profile": None}, "Stripe account"),
])
async def test_the_accounts_name_is_its_dashboards_else_its_business_name(monkeypatch, account,
                                                                          name):
    stripe = Stripe([_sub(1)], [_inv(1)])
    serve(monkeypatch, lambda r: httpx.Response(200, json=account)
          if r.url.path == "/v1/account" else stripe(r))
    async with imp.vendor_client(frozenset({"api.stripe.com"})) as c:
        assert (await imp._stripe_test(c, {"key": KEY}))[0] == name


async def test_a_key_that_cant_read_the_account_is_fine_and_an_account_that_fails_isnt(monkeypatch):
    stripe = Stripe([_sub(1)], [_inv(1)])
    for status, want in ((403, ("Stripe account", True)), (500, "vendor_down")):
        serve(monkeypatch, lambda r, s=status: httpx.Response(s, json={})
              if r.url.path == "/v1/account" else stripe(r))
        async with imp.vendor_client(frozenset({"api.stripe.com"})) as c:
            if isinstance(want, tuple):
                assert await imp._stripe_test(c, {"key": KEY}) == want
            else:
                with pytest.raises(ImportFailure, match=want):
                    await imp._stripe_test(c, {"key": KEY})


@pytest.mark.parametrize("key, test_mode", [("rk_live_x", False), ("rk_test_x", True)])
async def test_an_empty_accounts_mode_is_said_by_its_keys_form(monkeypatch, key, test_mode):
    serve(monkeypatch, Stripe(key=key))
    async with imp.vendor_client(frozenset({"api.stripe.com"})) as c:
        assert await imp._stripe_test(c, {"key": key}) == ("Acme", test_mode)


@pytest.mark.parametrize("path, what", [("/v1/subscriptions", "Subscriptions and Customers"),
                                        ("/v1/products", "Products"), ("/v1/invoices", "Invoices")])
async def test_a_refused_permission_is_named_at_the_key_test(monkeypatch, path, what):
    serve(monkeypatch, Stripe([_sub(1)], [_inv(1)], fail={path: 403}))
    async with imp.vendor_client(frozenset({"api.stripe.com"})) as c:
        with pytest.raises(ImportFailure) as e:
            await imp._stripe_test(c, {"key": KEY})
    assert e.value.public() == {"code": "permission", "what": what}


# ── the tables ────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("table, path, what", [
    (imp.STRIPE_INVOICES, "/v1/invoices", "Invoices"),
    (imp.STRIPE_SUBSCRIPTIONS, "/v1/subscriptions", "Subscriptions and Customers"),
    (imp.STRIPE_SUBSCRIPTIONS, "/v1/products", "Products"),
])
async def test_a_refused_permission_is_named_at_the_refresh(monkeypatch, table, path, what):
    with pytest.raises(ImportFailure) as e:
        await _fetch(monkeypatch, table, Stripe([_sub(1)], [_inv(1)], fail={path: 403}))
    assert e.value.public() == {"code": "permission", "what": what}


async def test_a_list_is_paged_on_its_last_objects_id_to_its_end(monkeypatch):
    invoices = [_inv(i) for i in range(250)]
    stripe = Stripe(invoices=invoices)
    got, cut = await _fetch(monkeypatch, imp.STRIPE_INVOICES, stripe)
    assert [i["id"] for i in got] == [i["id"] for i in invoices] and cut is None
    assert [q.get("starting_after") for p, q in stripe.seen] == [None, "in_0099", "in_0199"]


async def test_a_list_at_its_cap_is_said_cut_from_its_last_kept_day(monkeypatch):
    """At the cap the table is said cut, from the last object it keeps (newest first); whether
    more exist isn't asked (a request fewer)."""
    monkeypatch.setattr(imp, "ROW_CAP", 2)
    got, cut = await _fetch(monkeypatch, imp.STRIPE_INVOICES, Stripe(invoices=[_inv(1), _inv(30)]))
    assert len(got) == 2 and cut == imp.instant(_inv(30)["created"]).date().isoformat()
    monkeypatch.setattr(imp, "ROW_CAP", 3)
    assert (await _fetch(monkeypatch, imp.STRIPE_INVOICES,
                         Stripe(invoices=[_inv(1), _inv(30)])))[1] is None


async def test_a_subscriptions_items_past_the_embedded_ones_are_paged(monkeypatch):
    sub = _sub(1)
    sub["items"]["has_more"] = True
    more = [{"id": f"si_x{n:03d}", "quantity": 1,
             "price": {"id": "price_0", "product": "prod_0", "unit_amount": 100, "currency": "usd",
                       "recurring": {"interval": "month"}}} for n in range(120)]
    stripe = StripeItems([sub], items={"sub_0001": more})
    rows, cut = await _fetch(monkeypatch, imp.STRIPE_SUBSCRIPTIONS, stripe)
    assert [r["item"]["id"] for r in rows] == [m["id"] for m in more] and cut is None
    asked = [q for p, q in stripe.seen if p == "/v1/subscription_items"]
    assert [(q["subscription"], q.get("starting_after")) for q in asked] == \
        [("sub_0001", None), ("sub_0001", "si_x099")]


async def test_a_subscriptions_items_are_cut_at_the_rows_left(monkeypatch):
    monkeypatch.setattr(imp, "ROW_CAP", 3)
    first, second = _sub(1, items=2), _sub(2)
    second["items"]["has_more"] = True
    more = [dict(first["items"]["data"][0], id=f"si_y{n}") for n in range(5)]
    rows, cut = await _fetch(monkeypatch, imp.STRIPE_SUBSCRIPTIONS,
                             StripeItems([first, second], items={"sub_0002": more}))
    assert [r["item"]["id"] for r in rows] == ["si_0001_0", "si_0001_1", "si_y0"]
    assert cut == imp.instant(second["created"]).date().isoformat()


async def test_a_subscriptions_items_are_paged_no_further_than_the_rows_left(monkeypatch):
    """The items list stops at the rows the table has left (its cap), not at the table's cap:
    a request fewer for each page past it."""
    monkeypatch.setattr(imp, "ROW_CAP", 150)
    first, second = _sub(1, items=60), _sub(2)
    second["items"]["has_more"] = True
    more = [dict(first["items"]["data"][0], id=f"si_y{n:03d}") for n in range(250)]
    stripe = StripeItems([first, second], items={"sub_0002": more})
    rows, _ = await _fetch(monkeypatch, imp.STRIPE_SUBSCRIPTIONS, stripe)
    assert len(rows) == 150 and rows[-1]["item"]["id"] == "si_y089"
    assert [p for p, _ in stripe.seen].count("/v1/subscription_items") == 1


async def test_a_subscriptions_paged_items_are_asked_with_the_key_and_named_when_refused(
        monkeypatch):
    sub = _sub(1)
    sub["items"]["has_more"] = True
    with pytest.raises(ImportFailure) as e:
        await _fetch(monkeypatch, imp.STRIPE_SUBSCRIPTIONS,
                     StripeItems([sub], items={"sub_0001": []},
                                 fail={"/v1/subscription_items": 403}))
    assert e.value.public() == {"code": "permission", "what": "Subscriptions"}
    rows, _ = await _fetch(monkeypatch, imp.STRIPE_SUBSCRIPTIONS,   # a 401 if sent keyless
                           StripeItems([sub], items={"sub_0001": sub["items"]["data"]}))
    assert [r["item"]["id"] for r in rows] == ["si_0001_0"]


async def test_a_cut_subscription_list_is_said_cut_when_its_rows_fall_short(monkeypatch):
    """Subscriptions cut by their own count but fewer rows than the cap (one with no items):
    still said cut, from the last subscription kept."""
    monkeypatch.setattr(imp, "ROW_CAP", 2)
    rows, cut = await _fetch(monkeypatch, imp.STRIPE_SUBSCRIPTIONS,
                             Stripe([_sub(1, items=0), _sub(2), _sub(3)]))
    assert [r["sub"]["id"] for r in rows] == ["sub_0002"]
    assert cut == imp.instant(_sub(2)["created"]).date().isoformat()


async def test_subscriptions_are_cut_by_their_own_count_too(monkeypatch):
    monkeypatch.setattr(imp, "ROW_CAP", 2)
    rows, cut = await _fetch(monkeypatch, imp.STRIPE_SUBSCRIPTIONS,
                             Stripe([_sub(1), _sub(2), _sub(3)]))
    assert [r["sub"]["id"] for r in rows] == ["sub_0001", "sub_0002"]
    assert cut == imp.instant(_sub(2)["created"]).date().isoformat()


async def test_a_window_keeps_a_subscription_that_ended_on_its_first_moment(monkeypatch):
    since = imp._since("2024-11-01")
    rows, _ = await _fetch(monkeypatch, imp.STRIPE_SUBSCRIPTIONS,
                           Stripe([_sub(1, status="canceled", ended=since),
                                   _sub(2, status="canceled", ended=since - 1)]), "2024-11-01")
    assert [r["sub"]["id"] for r in rows] == ["sub_0001"]


# ── a subscription's own fields ───────────────────────────────────────────────────────────────

def test_a_customer_is_read_expanded_or_as_its_id():
    assert imp._customer({"customer": {"id": "cus_1", "email": "a@b.c"}}, "email") == "a@b.c"
    assert imp._customer({"customer": "cus_1"}, "id") == "cus_1"
    assert imp._customer({"customer": "cus_1"}, "email") is None


@pytest.mark.parametrize("discount, coupon, percent", [
    (None, None, None),
    ({}, None, None),                                     # a discount with no coupon
    ({"coupon": {"id": "SAVE20", "percent_off": 20}}, "SAVE20", 20),
    ("di_123", None, None),                               # a discount not expanded
])
def test_a_discount_is_read_whatever_its_shape(discount, coupon, percent):
    sub = {"discount": discount}
    assert (imp._coupon(sub), imp._percent_off(sub)) == (coupon, percent)


def test_an_invoices_money_is_its_currencys_major_unit():
    assert str(imp._inv_money({"total": 1999, "currency": "usd"}, "total")) == "19.99"
    assert str(imp._inv_money({"total": 500, "currency": "jpy"}, "total")) == "500"
    with pytest.raises(ImportFailure, match="schema"):
        imp._inv_money({"total": 5}, "total")


def test_an_amount_with_no_currency_has_two_places():
    assert str(imp.stripe_money(1999, None)) == "19.99"


async def test_a_whole_import_is_never_said_cut(client, monkeypatch):
    serve(monkeypatch, Stripe([_sub(1)], [_inv(1)]))
    body = (await _connect(client)).json()
    tables = (await client.get(f"/api/imports/{body['id']}")).json()["tables"]
    assert [t["cut_from"] for t in tables] == [None, None]
