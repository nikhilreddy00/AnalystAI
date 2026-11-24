"""Shopify's fetchers by what they send and keep (5.8c mutation gate: the recorded store held 8
orders of at most 3 lines, so no order page past the first, no line page, no window, no cut and no
GraphQL error was ever read). A store answering GraphQL as Shopify does, on the fake socket under
the host-locked client."""
from __future__ import annotations

import json
from urllib.parse import parse_qs

import httpx
import pytest

import connectors.base as base
from connectors import imports as imp
from connectors.imports import ImportFailure
from tests.fake_network import serve

HOST = "mystore.myshopify.com"
SECRETS = {"shop": HOST, "client_id": "cid-0000", "key": "shpss_secret000000"}
TOKEN = "shpat_minted0000000000"


def _line(o: int, n: int) -> dict:
    money = {"shopMoney": {"amount": "10.00"}}
    return {"id": f"gid://shopify/LineItem/{o}{n:03d}", "name": f"Item {n}", "sku": f"S{n}",
            "quantity": 1, "vendor": "Acme", "requiresShipping": True, "taxable": True,
            "unfulfilledQuantity": 0, "originalUnitPriceSet": money, "totalDiscountSet": money}


def _order(o: int, lines: int = 1, created: str = "2026-11-20T10:00:00Z") -> dict:
    money = {"shopMoney": {"amount": "10.00"}}
    return {"id": f"gid://shopify/Order/{o}", "name": f"#{o}", "createdAt": created,
            "cancelledAt": None, "test": False, "currencyCode": "USD",
            "displayFinancialStatus": "PAID", "displayFulfillmentStatus": "FULFILLED",
            **{k: money for k in ("subtotalPriceSet", "totalShippingPriceSet", "totalTaxSet",
                                  "totalPriceSet", "totalDiscountsSet", "totalRefundedSet",
                                  "totalOutstandingSet")},
            "discountCodes": [], "tags": [], "sourceName": "web", "retailLocation": None,
            "_lines": [_line(o, n) for n in range(lines)]}


def _page(items: list, after: str | None, size: int, wrap) -> dict:
    start = int(after) if after else 0
    chunk = items[start:start + size]
    more = start + size < len(items)
    return {"pageInfo": {"hasNextPage": more, "endCursor": str(start + len(chunk)) if more else None},
            "edges": [{"node": wrap(x)} for x in chunk]}


class Shop:
    """A store: the token mint, the shop's zone, its orders newest first paged on endCursor, an
    order's lines past the first page paged on their own. `deny`: a query that answers
    ACCESS_DENIED; `errors`: GraphQL errors for every query."""

    def __init__(self, orders=(), *, tz="Asia/Kolkata", deny=None, errors=None, dev=True):
        self.orders, self.tz, self.deny, self.errors, self.dev = list(orders), tz, deny, errors, dev
        self.asked, self.minted = [], []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/admin/oauth/access_token":
            self.minted.append((request.method, {k: v[0] for k, v in
                                                 parse_qs(request.content.decode()).items()}))
            return httpx.Response(200, json={"access_token": TOKEN})
        assert request.url.path == f"/admin/api/{imp.SHOPIFY_API}/graphql.json"
        assert request.headers["x-shopify-access-token"] == TOKEN and "_shop" not in request.headers
        sent = json.loads(request.content)
        q, v = sent["query"], sent["variables"]
        kind = ("zone" if "ianaTimezone" in q else "test" if "partnerDevelopment" in q
                else "lines" if "order(id:" in q else "orders")
        self.asked.append((kind, v))
        if self.errors:
            return httpx.Response(200, json={"errors": self.errors})
        if kind == self.deny:
            return httpx.Response(200, json={"errors": [{"extensions": {"code": "ACCESS_DENIED"}}]})
        if kind == "zone":
            return httpx.Response(200, json={"data": {"shop": {"ianaTimezone": self.tz}}})
        if kind == "test":
            return httpx.Response(200, json={"data": {"shop": {"name": "My Store", "plan": {
                "partnerDevelopment": self.dev}}, "orders": {"edges": []}}})
        if kind == "lines":
            o = next(x for x in self.orders if x["id"] == v["id"])
            return httpx.Response(200, json={"data": {"order": {"lineItems": _page(
                o["_lines"], v["after"], imp.LINES_PAGE, lambda x: x)}}})
        return httpx.Response(200, json={"data": {"orders": _page(
            self.orders, v["after"], imp.ORDERS_PAGE,
            lambda o: {**{k: x for k, x in o.items() if k != "_lines"},
                       "lineItems": _page(o["_lines"], None, imp.LINES_PAGE, lambda x: x)})}})


async def _with(monkeypatch, shop, call):
    serve(monkeypatch, shop)
    async with imp.vendor_client(frozenset({HOST})) as c:
        return await call(c)


async def _orders(monkeypatch, shop, window=None):
    async def go(c):
        return await imp.SHOPIFY_ORDERS.fetch(c, await imp._shopify_auth(c, SECRETS), window)
    return await _with(monkeypatch, shop, go)


async def _failure(monkeypatch, shop, call) -> dict:
    with pytest.raises(ImportFailure) as e:
        await _with(monkeypatch, shop, call)
    return e.value.public()


# ── the store's address and the app's key ─────────────────────────────────────────────────────

@pytest.mark.parametrize("pasted", ["MyStore", "https://mystore.myshopify.com/admin?x=1",
                                    "mystore.myshopify.com?x=1", "mystore.myshopify.com/",
                                    "admin.shopify.com/store/mystore/orders"])
def test_a_store_address_reads_as_its_myshopify_host(pasted):
    assert imp.shop_host(pasted) == HOST


def test_a_store_name_of_sixty_characters_reads_and_sixty_one_doesnt():
    assert imp.shop_host("a" * 60) == "a" * 60 + ".myshopify.com"
    with pytest.raises(ImportFailure) as e:
        imp.shop_host("a" * 61)
    assert e.value.public() == {"code": "bad_shop", "what": ""}


@pytest.mark.parametrize("secrets, code", [
    ({"client_id": "c", "key": "k"}, "bad_shop"),                  # no store
    ({"shop": HOST, "client_id": "c"}, "not_a_key"),               # no secret
    ({"shop": HOST, "key": "k"}, "not_a_key"),                     # no client id
])
def test_an_apps_key_missing_a_part_is_refused_by_its_reason(secrets, code):
    with pytest.raises(ImportFailure) as e:
        imp._shopify_check(secrets)
    assert e.value.public() == {"code": code, "what": ""}


# ── the token ─────────────────────────────────────────────────────────────────────────────────

async def test_a_token_is_minted_by_client_credentials_and_registered_a_secret(monkeypatch):
    shop = Shop()
    token = base.credential_scope()
    try:
        headers = await _with(monkeypatch, shop, lambda c: imp._shopify_auth(c, SECRETS))
        assert TOKEN in base._registry().secrets[0]
    finally:
        base._SCOPE.reset(token)
    assert headers == {"X-Shopify-Access-Token": TOKEN, "_shop": HOST}
    assert shop.minted == [("POST", {"grant_type": "client_credentials", "client_id": "cid-0000",
                                     "client_secret": "shpss_secret000000"})]


@pytest.mark.parametrize("status, code", [(400, "bad_key"), (401, "bad_key"), (403, "bad_key"),
                                          (500, "vendor_down")])
async def test_a_refused_mint_is_said_as_the_key_and_a_failing_one_as_the_store(monkeypatch,
                                                                              status, code):
    got = await _failure(monkeypatch, lambda r: httpx.Response(status, json={}),
                         lambda c: imp._shopify_auth(c, SECRETS))
    assert got["code"] == code


@pytest.mark.parametrize("answer", [{}, {"access_token": ""}, {"access_token": 5}])
async def test_a_mint_without_a_token_is_said_as_the_key(monkeypatch, answer):
    got = await _failure(monkeypatch, lambda r: httpx.Response(200, json=answer),
                         lambda c: imp._shopify_auth(c, SECRETS))
    assert got == {"code": "bad_key", "what": ""}


# ── GraphQL's answers ─────────────────────────────────────────────────────────────────────────

async def test_a_throttle_is_waited_out_then_read(monkeypatch):
    waits, answers = [], [{"errors": [{"extensions": {"code": "THROTTLED"}}]},
                          {"data": {"shop": {"ianaTimezone": "UTC"}}}]

    async def sleep(s):
        waits.append(s)
    monkeypatch.setattr(imp.asyncio, "sleep", sleep)
    got = await _with(monkeypatch, lambda r: httpx.Response(200, json=answers.pop(0)),
                      lambda c: imp._gql(c, {"_shop": HOST}, "{ q }", "Orders"))
    assert got == {"shop": {"ianaTimezone": "UTC"}} and waits == [imp.THROTTLE_WAIT_S]


@pytest.mark.parametrize("errors, want", [
    ([{"extensions": {"code": "ACCESS_DENIED", "documentation":
                      "https://shopify.dev/docs/apps/launch/protected-customer-data"}}],
     {"code": "protected_data", "what": "Orders"}),
    ([{"extensions": {"code": "ACCESS_DENIED"}}], {"code": "permission", "what": "Orders"}),
    ([{"message": "Internal error"}], {"code": "vendor_down", "what": "GraphQL"}),
])
async def test_a_graphql_error_inside_a_200_is_its_reason(monkeypatch, errors, want):
    got = await _failure(monkeypatch, lambda r: httpx.Response(200, json={"errors": errors}),
                         lambda c: imp._gql(c, {"_shop": HOST}, "{ q }", "Orders"))
    assert got == want


@pytest.mark.parametrize("answer", [{}, {"data": None}, {"data": [1]}])
async def test_an_answer_without_data_is_a_schema_failure(monkeypatch, answer):
    got = await _failure(monkeypatch, lambda r: httpx.Response(200, json=answer),
                         lambda c: imp._gql(c, {"_shop": HOST}, "{ q }", "Orders"))
    assert got == {"code": "schema", "what": "data"}


async def test_a_refused_graphql_request_names_what_it_read(monkeypatch):
    got = await _failure(monkeypatch, lambda r: httpx.Response(403, json={}),
                         lambda c: imp._gql(c, {"_shop": HOST}, "{ q }", "Orders"))
    assert got == {"code": "permission", "what": "Orders"}


@pytest.mark.parametrize("deny", ["zone", "orders", "lines"])
async def test_every_query_the_orders_table_sends_names_orders_when_denied(monkeypatch, deny):
    shop = Shop([_order(1, lines=60)], deny=deny)
    with pytest.raises(ImportFailure) as e:
        await _orders(monkeypatch, shop)
    assert e.value.public() == {"code": "permission", "what": "Orders"}


# ── the key's test ────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("dev", [True, False])
async def test_the_key_test_says_the_stores_name_and_a_development_store_as_test_data(monkeypatch,
                                                                                    dev):
    got = await _with(monkeypatch, Shop(dev=dev), lambda c: imp._shopify_test(c, SECRETS))
    assert got == ("My Store", dev)


async def test_the_key_test_names_orders_when_denied(monkeypatch):
    got = await _failure(monkeypatch, Shop(deny="test"), lambda c: imp._shopify_test(c, SECRETS))
    assert got == {"code": "permission", "what": "Orders"}


# ── orders and their lines ────────────────────────────────────────────────────────────────────

async def test_orders_are_paged_on_their_cursor_to_the_last(monkeypatch):
    shop = Shop([_order(o) for o in range(60, 0, -1)])
    rows, cut = await _orders(monkeypatch, shop)
    assert [r["order"]["id"] for r in rows] == [f"gid://shopify/Order/{o}" for o in range(60, 0, -1)]
    assert cut is None
    assert [v["after"] for k, v in shop.asked if k == "orders"] == [None, "25", "50"]
    assert {r["tz"] for r in rows} == {"Asia/Kolkata"}


async def test_an_orders_lines_past_the_first_page_are_paged_on_their_own(monkeypatch):
    shop = Shop([_order(2, lines=1), _order(1, lines=120)])
    rows, _ = await _orders(monkeypatch, shop)
    big = [r for r in rows if r["order"]["id"] == "gid://shopify/Order/1"]
    assert [r["line"]["id"] for r in big] == [f"gid://shopify/LineItem/1{n:03d}" for n in range(120)]
    assert [(r["first"], r["i"]) for r in big[:2]] == [(True, 0), (False, 1)] and big[-1]["i"] == 119
    assert [(v["id"], v["after"]) for k, v in shop.asked if k == "lines"] == \
        [("gid://shopify/Order/1", "50"), ("gid://shopify/Order/1", "100")]


@pytest.mark.parametrize("window, q", [("2025-11-24", "created_at:>=2025-11-24"), (None, None)])
async def test_a_window_is_asked_of_shopify_as_its_first_day(monkeypatch, window, q):
    shop = Shop([_order(1)])
    await _orders(monkeypatch, shop, window)
    assert [v["q"] for k, v in shop.asked if k == "orders"] == [q]


async def test_orders_at_the_row_cap_are_said_cut_from_the_last_orders_day(monkeypatch):
    monkeypatch.setattr(imp, "ROW_CAP", 4)
    shop = Shop([_order(3, 2, "2026-11-21T00:30:00Z"), _order(2, 2, "2026-11-20T23:30:00Z"),
                 _order(1, 2, "2026-11-19T10:00:00Z")])
    rows, cut = await _orders(monkeypatch, shop)
    assert len(rows) == 4 and cut == "2026-11-20"


# ── the columns ───────────────────────────────────────────────────────────────────────────────

async def test_an_orders_moments_are_in_the_shops_zone_and_its_own_fields_on_its_first_line(
        monkeypatch):
    o = _order(1, lines=2, created="2026-11-20T20:00:00Z")
    o["totalRefundedSet"] = {"shopMoney": {"amount": None}}
    rows, _ = await _orders(monkeypatch, Shop([o]))
    frame = imp.SHOPIFY_ORDERS.frame(rows)
    assert frame["Created at"].astype(str).tolist() == ["2026-11-21 01:30:00+05:30"] * 2
    assert frame["Total"].isna().tolist() == [False, True]
    assert frame["Refunded Amount"].isna().all()   # a money field Shopify sends as null
