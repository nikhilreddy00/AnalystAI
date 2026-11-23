"""SaaS imports through the route (5.8c, ADR-0010 amendment): connect with a key, the tables filed
as dataset versions, Get latest data, and every way a refresh fails said in its own words with the
last good copy kept. The production path end to end: the route, the vendor's host-locked client on
the guarded client, `QueryResult.frame`, the one file reader, the store. Only the socket is fake
(tests/fake_network.py) and answers as the vendor's API does.

The Stripe pages here are shaped from Stripe's API reference (docs/research/2026-11-22-5.8c-sources
.md §2); the recorded test-mode pages join them in the recording session (D1-38)."""
from __future__ import annotations

import asyncio
import json
import logging
from urllib.parse import parse_qs, urlparse

import httpx
import pandas as pd
import pytest
from cryptography.fernet import Fernet

from api.config import settings
from tests.fake_network import serve

KEY = "rk_test_51CANARYimportKEY0000000000"   # fake: a canary every sink is searched for
T = 1_731_000_000                              # 2024-11-07 17:20 UTC


@pytest.fixture(autouse=True)
def _key(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "encryption_key", Fernet.generate_key().decode())
    from connectors import csv_connector
    monkeypatch.setattr(csv_connector, "_UPLOADS_DIR", tmp_path)


@pytest.fixture
async def client():
    from api.rate_limit import limiter
    from orchestrator.runner import in_memory_deps
    from tests.test_workspace_api import _client
    limiter.reset()
    async with _client(in_memory_deps()) as c:
        yield c


# ── a Stripe answering as its API does ─────────────────────────────────────────────────────────

def _sub(i: int, *, status="active", ended=None, currency="usd", amount=2900, items=1,
         created=None, customer=None):
    return {"id": f"sub_{i:04d}", "object": "subscription", "livemode": False, "status": status,
            "customer": customer or {"id": f"cus_{i:04d}", "email": f"c{i}@example.com",
                                     "name": "Ann Example"},
            "created": created or T - i * 86400, "start_date": created or T - i * 86400,
            "trial_start": None, "trial_end": None, "cancel_at": None, "canceled_at": ended,
            "ended_at": ended, "cancel_at_period_end": False, "discount": None,
            "cancellation_details": {"reason": "cancellation_requested" if ended else None,
                                     "feedback": None},
            "items": {"object": "list", "has_more": False, "data": [
                {"id": f"si_{i:04d}_{k}", "quantity": 1 + k,
                 "price": {"id": f"price_{k}", "nickname": ["Pro", "Seats"][k % 2],
                           "product": f"prod_{k}", "unit_amount": amount, "currency": currency,
                           "recurring": {"interval": "month", "interval_count": 1}}}
                for k in range(items)]}}


PRODUCTS = [{"id": "prod_0", "name": "Pro"}, {"id": "prod_1", "name": "Seats"}]


def _inv(i: int, *, amount=2900, currency="usd", discount=0):
    return {"id": f"in_{i:04d}", "number": f"INV-{i:04d}", "customer": f"cus_{i:04d}",
            "customer_email": f"c{i}@example.com", "customer_name": "Ann Example",
            "status": "paid", "created": T - i * 3600, "currency": currency,
            "collection_method": "charge_automatically", "charge": f"ch_{i:04d}",
            "due_date": None, "period_start": T - i * 3600, "period_end": T - i * 3600,
            "subscription": f"sub_{i:04d}", "subtotal": amount, "total": amount - discount,
            "total_discount_amounts": [{"amount": discount}] if discount else [],
            "amount_due": amount - discount, "amount_paid": amount - discount,
            "starting_balance": 0, "ending_balance": 0,
            "status_transitions": {"paid_at": T - i * 3600, "finalized_at": T - i * 3600}}


class Stripe:
    """The objects, newest first, paged on starting_after as Stripe does. `fail`: path → status."""

    def __init__(self, subs=(), invoices=(), *, key=KEY, fail=None, customers=True):
        self.subs, self.invoices, self.key = list(subs), list(invoices), key
        self.fail, self.seen = dict(fail or {}), []
        self.customers = customers   # the key may read Customers (an expanded customer needs it)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = urlparse(str(request.url))
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        self.seen.append((url.path, q))
        if request.headers.get("authorization") != f"Bearer {self.key}":
            return httpx.Response(401, json={"error": {"message": f"Invalid API Key provided: "
                                                       f"{request.headers.get('authorization')}"}})
        for prefix, status in self.fail.items():
            if url.path.startswith(prefix) and (status != 500 or q.get("starting_after")):
                return httpx.Response(status, json={"error": {"message": "nope"}})
        if not self.customers and "data.customer" in q.get("expand[]", ""):
            return httpx.Response(403, json={"error": {"message": "requires rak_customer_read"}})
        if url.path == "/v1/account":
            return httpx.Response(200, json={"settings": {"dashboard": {"display_name": "Acme"}}})
        if url.path == "/v1/subscriptions":
            objs = self.subs if q.get("status") == "all" else [s for s in self.subs
                                                                if s["status"] != "canceled"]
        elif url.path == "/v1/invoices":
            since = int(q.get("created[gte]", 0))
            objs = [i for i in self.invoices if i["created"] >= since]
        elif url.path == "/v1/products":
            objs = PRODUCTS
        else:
            return httpx.Response(404, json={})
        start = 0
        if after := q.get("starting_after"):
            start = next(n for n, o in enumerate(objs) if o["id"] == after) + 1
        page = objs[start:start + int(q.get("limit", 10))]
        return httpx.Response(200, json={"object": "list", "url": url.path, "data": page,
                                         "has_more": start + len(page) < len(objs)})


async def _settle(client) -> None:
    """Every refresh task this app started, run to its end."""
    tasks = getattr(client._transport.app.state, "import_tasks", set())
    while tasks:
        await asyncio.gather(*list(tasks))


async def _connect(client, key=KEY):
    r = await client.post("/api/imports", json={"vendor": "stripe", "key": key})
    if r.status_code == 201:
        await _settle(client)
    return r


async def _frame(client, dataset_id: str) -> pd.DataFrame:
    ds = (await client.get(f"/api/datasets/{dataset_id}")).json()
    store = client._transport.app.state.session_store
    v = await store.get_version(ds["latest"]["id"], "u1")
    from connectors.csv_connector import _read_bytes
    from pathlib import Path
    return _read_bytes(Path(v[1].file_path).read_bytes(), ".parquet")[0]


def _tables(body) -> dict:
    return {t["name"]: t for t in body["tables"]}


FIXTURES = __import__("pathlib").Path(__file__).parent / "fixtures" / "imports"


def recorded(vendor: str):
    """A vendor answering with its recorded real pages (scripts/record_import.py), each by its
    exact path and query: a request the recording never made is a 404, so a changed fetcher fails
    here instead of passing on pages it no longer asks for."""
    pages = json.loads((FIXTURES / vendor / "pages.json").read_text())["pages"]
    by: dict = {}
    for p in pages:   # a call made twice (a page asked again) is answered in recorded order
        by.setdefault(_call(p.get("method", "GET"), p["path"], p["query"], p.get("sent")),
                      []).append(p)

    def handler(request: httpx.Request) -> httpx.Response:
        sent = json.loads(request.content) if request.method == "POST" and \
            not request.url.path.endswith("/oauth/access_token") else None
        answers = by.get(_call(request.method, request.url.path,
                               sorted(request.url.params.multi_items()), sent))
        if not answers:
            return httpx.Response(404, json={"error": {"message": "not in the recording"}})
        page = answers.pop(0) if len(answers) > 1 else answers[0]
        return httpx.Response(page["status"], json=page["body"], headers=page["headers"])
    return handler


def _call(method: str, path: str, query, sent) -> tuple:
    return method, path, tuple(map(tuple, query)), json.dumps(sent, sort_keys=True)


# ── the recorded real account first (D1-38: normal pages before attacks) ───────────────────────

async def _recorded_import(client, monkeypatch) -> dict:
    serve(monkeypatch, recorded("stripe"))
    r = await _connect(client)
    assert r.status_code == 201, r.text
    body = (await client.get(f"/api/imports/{r.json()['id']}")).json()
    assert body["state"] == "idle" and body["test_mode"] and body["account"] == "Retail sandbox"
    return {name: await _frame(client, t["dataset_id"]) for name, t in _tables(body).items()}


async def test_every_recorded_normal_page_imports(client, monkeypatch):
    """Stripe test mode, recorded 2026-11-23 (scripts/seed_stripe_test.py): every status,
    a two-price subscription, a 20% coupon, USD/EUR/JPY/ISK/HUF, an invoice of 12 lines."""
    got = await _recorded_import(client, monkeypatch)
    subs, invoices = got["subscriptions"], got["invoices"]
    assert (len(subs), len(invoices)) == (10, 10)
    assert set(subs["Status"]) == {"active", "canceled", "trialing"}
    amounts = {(c, a) for c, a in zip(subs["Currency"], subs["Amount"])}
    # Stripe's currency list: JPY none; ISK and HUF two decimals (ISK's always 00)
    assert {("jpy", 500), ("isk", 500), ("huf", 2500), ("usd", 290), ("usd", 29),
            ("usd", 15), ("eur", 99)} == amounts
    assert subs["Percent Off"].dropna().tolist() == [20.0]
    assert subs["id"].value_counts().max() == 2            # the two-price subscription: 2 rows
    assert subs["Customer Email"].str.fullmatch(r"customer[0-9a-f]{8}@mail\.invalid").all()
    totals = dict(zip(invoices["Currency"] + " " + invoices["Number"].str[-4:], invoices["Total"]))
    assert 78.0 in invoices["Total"].tolist()             # the 12-line invoice: its own total
    assert sorted(invoices.loc[invoices["Currency"].ne("usd"), "Total"]) == [0.0, 500.0, 500.0,
                                                                            2500.0], totals


@pytest.mark.parametrize(("table", "export"), [("subscriptions", "export_subscriptions.csv"),
                                               ("invoices", "export_invoices.csv")])
async def test_the_import_reads_as_stripes_own_export(client, monkeypatch, table, export):
    """The source oracle for an import (ADR-0010: the same table from every source reads the
    same): the recorded import and Stripe's own export of the same account, through the one file
    reader, give the same values in every column both have, and the same SaaS roles. Percent Off
    is the import's own (the export has only the coupon's id)."""
    from connectors.csv_connector import _read_bytes
    from domain_pack.detect import detect
    got = (await _recorded_import(client, monkeypatch))[table]
    want, _ = _read_bytes((FIXTURES / "stripe" / export).read_bytes(), ".csv")
    shared = [c for c in got.columns if c in want.columns]
    assert set(got.columns) - set(shared) <= {"Percent Off"}
    key = ["id", "Plan"] if table == "subscriptions" else ["id"]
    a = got[shared].sort_values(key).reset_index(drop=True)
    b = want[shared].sort_values(key).reset_index(drop=True)
    pd.testing.assert_frame_equal(a, b, check_dtype=False)
    dates = lambda f: {c for c in f.columns if "(UTC)" in c}   # noqa: E731
    gd, groles, _ = detect(got, dates(got))
    wd, wroles, _ = detect(want, dates(want))
    assert gd == wd == "saas"
    assert {r: c for r, c in groles.items() if r != "discount_pct"} == wroles


SHOP = {"vendor": "shopify", "shop": "analystai-test.myshopify.com", "client_id": "cid-recorded",
        "key": "shpss_CANARYclientSECRET0000000000"}


async def _recorded_shop(client, monkeypatch):
    serve(monkeypatch, recorded("shopify"))
    r = await client.post("/api/imports", json=SHOP)
    assert r.status_code == 201, r.text
    await _settle(client)
    body = (await client.get(f"/api/imports/{r.json()['id']}")).json()
    assert body["state"] == "idle" and body["test_mode"], body
    return await _frame(client, _tables(body)["orders"]["dataset_id"])


async def test_every_recorded_shopify_page_imports(client, monkeypatch):
    """A development store, recorded 2026-11-23 (scripts/seed_shopify_test.py): 8 orders of 1 to
    3 lines, one cancelled, one pending, one test, one discounted. Each order's own fields on its
    first line only (red-team 2: a total on every line multiplied it)."""
    orders = await _recorded_shop(client, monkeypatch)
    assert len(orders) == 12 and orders["Name"].nunique() == 8
    assert orders["Total"].sum() == pytest.approx(444.5)   # each order counted once
    assert orders["Total"].notna().sum() == 8
    assert orders["Test"].dropna().tolist().count(True) == 1
    assert orders["Cancelled at"].notna().sum() == 1
    assert set(orders["Financial Status"].dropna()) == {"paid", "pending"}


async def test_the_shopify_import_reads_as_shopifys_own_export(client, monkeypatch):
    """The source oracle for an import: the recorded store and Shopify's own Orders export of it,
    through the one file reader, give the same values in every column both have (the export's
    Email and people's columns aren't imported; Test is the import's own) and the same retail
    roles, bar email."""
    from connectors.csv_connector import _read_bytes
    from domain_pack.detect import detect
    got = await _recorded_shop(client, monkeypatch)
    want, _ = _read_bytes((FIXTURES / "shopify" / "export_orders.csv").read_bytes(), ".csv")
    shared = [c for c in got.columns if c in want.columns]
    assert set(got.columns) - set(shared) == {"Test"}
    # Source is text by Shopify's declaration ("web", "pos", an app's id): the CSV reads an app's
    # all-digit id as a number. The import keeps the declared type (5.8b root cause 2); the values
    # are the same as text
    assert (got["Source"].dropna().tolist()
            == want["Source"].dropna().astype("int64").astype(str).tolist())
    same = [c for c in shared if c != "Source"]
    pd.testing.assert_frame_equal(got[same].reset_index(drop=True),
                                  want[same].reset_index(drop=True), check_dtype=False)
    dates = lambda f: {c for c in f.columns if c.endswith(" at")}   # noqa: E731
    gd, groles, gs = detect(got, dates(got))
    wd, wroles, _ = detect(want, dates(want))
    assert (gd, gs) == ("retail", "strong") and wd == "retail"
    assert groles == {r: c for r, c in wroles.items() if r != "email"}


async def test_a_store_that_is_always_throttled_is_said_never_waited_on_forever(client, monkeypatch):
    """Security review: Shopify's throttle was waited out without end, and a key's test runs in
    the request under no refresh's deadline."""
    import connectors.imports as imports
    monkeypatch.setattr(imports, "THROTTLE_WAIT_S", 0)

    def shop(request):
        if request.url.path.endswith("/oauth/access_token"):
            return httpx.Response(200, json={"access_token": "minted-token-0000000000"})
        return httpx.Response(200, json={"errors": [{"extensions": {"code": "THROTTLED"}}]})
    wire = serve(monkeypatch, shop)
    r = await client.post("/api/imports", json=SHOP)
    assert r.status_code == 400 and "slow down" in r.json()["detail"]
    assert len(wire.requests) == 1 + imports.THROTTLE_TRIES


async def test_a_key_test_past_its_deadline_is_said(client, monkeypatch):
    import api.routes.imports as routes
    import connectors.imports as imports
    monkeypatch.setattr(routes, "KEY_TEST_S", 0.05)

    async def slow(client, secrets):
        await asyncio.sleep(1)
    monkeypatch.setattr(imports.VENDORS["stripe"], "test", slow)
    r = await _connect(client)
    assert r.status_code == 400 and "within a minute" in r.json()["detail"]


@pytest.mark.parametrize("pasted", ["analystai-test", "AnalystAI-Test.myshopify.com",
                                    "https://analystai-test.myshopify.com/admin/orders",
                                    "admin.shopify.com/store/analystai-test"])
def test_a_store_address_is_read_as_people_paste_it(pasted):
    from connectors.imports import shop_host
    assert shop_host(pasted) == "analystai-test.myshopify.com"


@pytest.mark.parametrize("pasted", ["shop.example.com", "", "evil.com/x.myshopify.com",
                                    "a.myshopify.com.evil.com", "my_store.myshopify.com"])
def test_anything_but_a_store_address_is_refused(pasted):
    from connectors.imports import ImportFailure, shop_host
    with pytest.raises(ImportFailure):
        shop_host(pasted)


# ── normal accounts first (D1-38) ──────────────────────────────────────────────────────────────

async def test_a_stripe_account_imports_as_two_tables(client, monkeypatch):
    stripe = Stripe([_sub(1), _sub(2, status="canceled", ended=T - 86400), _sub(3, items=2)],
                    [_inv(1), _inv(2, amount=5800, discount=580)])
    serve(monkeypatch, stripe)
    r = await _connect(client)
    assert r.status_code == 201, r.text
    body = (await client.get(f"/api/imports/{r.json()['id']}")).json()
    assert body["account"] == "Acme" and body["test_mode"] and body["state"] == "idle"
    t = _tables(body)
    assert t["subscriptions"]["rows"] == 4 and t["invoices"]["rows"] == 2
    assert t["subscriptions"]["error"] is None and t["subscriptions"]["filed"] == "new_version"
    subs = await _frame(client, t["subscriptions"]["dataset_id"])
    # every status: the API's default leaves canceled ones out (churn would read 0)
    assert sorted(subs["Status"].unique()) == ["active", "canceled"]
    assert subs["Amount"].tolist() == [29.0] * 4            # minor units → the export's dollars
    assert pd.api.types.is_datetime64_any_dtype(subs["Created (UTC)"])
    assert subs["Created (UTC)"].iloc[0] == pd.Timestamp("2024-11-06 17:20:00")   # UTC, minutes
    assert (subs["id"] == "sub_0003").sum() == 2            # one row a subscription item
    assert set(subs["Product"]) == {"Pro", "Seats"}         # the product's name, as exported
    assert "Ann Example" not in subs.to_csv()               # the customer's name isn't imported
    inv = await _frame(client, t["invoices"]["dataset_id"])
    assert sorted(zip(inv["Subtotal"], inv["Total Discount Amount"], inv["Total"])) == [
        (29.0, 0.0, 29.0), (58.0, 5.8, 52.2)]
    assert "Ann Example" not in inv.to_csv()
    ds = (await client.get(f"/api/datasets/{t['subscriptions']['dataset_id']}")).json()
    assert ds["kind"] == "import" and ds["name"] == "Stripe · Subscriptions"


@pytest.mark.parametrize(("currency", "minor", "major"), [
    ("usd", 1999, 19.99), ("jpy", 500, 500.0), ("kwd", 1250, 1.25), ("krw", 15000, 15000.0)])
async def test_amounts_are_the_major_unit_by_stripes_own_currency_list(client, monkeypatch,
                                                                      currency, minor, major):
    serve(monkeypatch, Stripe([_sub(1, currency=currency, amount=minor)], []))
    t = _tables((await _connect(client)).json())
    body = (await client.get("/api/imports")).json()[0]
    subs = await _frame(client, _tables(body)["subscriptions"]["dataset_id"])
    assert subs["Amount"].tolist() == [major] and subs["Currency"].tolist() == [currency]
    assert t


async def test_the_row_cap_counts_rows_and_says_where_it_cut(client, monkeypatch):
    """The cap is on the table's rows (a subscription with three items is three), newest kept,
    the first day kept said (review: the cap counted subscriptions, not their rows)."""
    import connectors.imports as imports
    monkeypatch.setattr(imports, "ROW_CAP", 5)
    serve(monkeypatch, Stripe([_sub(i, items=3) for i in range(1, 6)],
                              [_inv(i) for i in range(1, 8)]))
    body = (await _connect(client)).json()
    t = _tables((await client.get(f"/api/imports/{body['id']}")).json())
    assert t["subscriptions"]["rows"] == 5 and t["invoices"]["rows"] == 5
    assert t["subscriptions"]["cut_from"] == "2024-11-05" and t["invoices"]["cut_from"]


async def test_an_account_with_no_subscriptions_says_so_and_isnt_a_failure(client, monkeypatch):
    serve(monkeypatch, Stripe([], [_inv(1)]))
    body = (await _connect(client)).json()
    body = (await client.get(f"/api/imports/{body['id']}")).json()
    t = _tables(body)
    assert body["state"] == "idle" and t["subscriptions"]["rows"] == 0
    assert t["subscriptions"]["filed"] == "empty" and t["invoices"]["rows"] == 1


async def test_a_window_keeps_subscriptions_in_force_whenever_they_started(client, monkeypatch):
    """Red-team 1: a date cut by `created` dropped old active subscriptions (MRR understated)."""
    old_active = _sub(1, created=T - 900 * 86400)
    old_ended = _sub(2, status="canceled", created=T - 900 * 86400, ended=T - 800 * 86400)
    serve(monkeypatch, Stripe([old_active, old_ended, _sub(3)], [_inv(1)]))
    body = (await _connect(client)).json()
    store = client._transport.app.state.session_store
    await store.set_import_window(body["id"], "u1", "2024-01-01")
    import memory.session_store as ss
    monkeypatch.setattr(ss, "REFRESH_GAP_S", 0)
    assert (await client.post(f"/api/imports/{body['id']}/refresh")).status_code == 202
    await _settle(client)
    body = (await client.get(f"/api/imports/{body['id']}")).json()
    subs = await _frame(client, _tables(body)["subscriptions"]["dataset_id"])
    assert sorted(subs["id"]) == ["sub_0001", "sub_0003"]


# ── the key ────────────────────────────────────────────────────────────────────────────────────

async def test_a_live_secret_key_is_refused_before_any_request(client, monkeypatch):
    wire = serve(monkeypatch, Stripe())
    r = await _connect(client, key="sk_live_" + "x" * 30)
    assert r.status_code == 400 and "restricted key" in r.json()["detail"]
    assert wire.requests == []


@pytest.mark.parametrize(("fail", "words"), [
    ({"/v1/": 401}, "rejected the key"),
    ({"/v1/invoices": 403}, "can't read Invoices"),
])
async def test_a_key_that_cant_read_is_refused_in_words_and_never_saved(client, monkeypatch,
                                                                        fail, words):
    serve(monkeypatch, Stripe([_sub(1)], [_inv(1)], fail=fail))
    r = await _connect(client)
    assert r.status_code == 400 and words in r.json()["detail"]
    assert KEY not in r.text and (await client.get("/api/imports")).json() == []


async def test_a_key_the_table_would_be_refused_is_refused_at_connect(client, monkeypatch):
    """Recorded session (2026-11-23): the key test read /subscriptions bare, passed, and the
    refresh then hit a 403 on the expanded customer. The test sends each table's own request."""
    serve(monkeypatch, Stripe([_sub(1)], [_inv(1)], customers=False))
    r = await _connect(client)
    assert r.status_code == 400 and "Subscriptions and Customers" in r.json()["detail"]


async def test_a_key_test_reads_each_table_and_the_mode_from_stripes_answer(client, monkeypatch):
    stripe = Stripe([dict(_sub(1), livemode=True)], [])
    serve(monkeypatch, stripe)
    body = (await _connect(client)).json()
    assert body["test_mode"] is False   # the answer says live, whatever the key's prefix
    assert {"/v1/subscriptions", "/v1/invoices"} <= {p for p, _ in stripe.seen}


# ── a refresh: once at a time, and every failure keeps the last good copy ─────────────────────

async def _good_then(client, monkeypatch, stripe_after: Stripe):
    serve(monkeypatch, Stripe([_sub(1)], [_inv(1)]))
    body = (await _connect(client)).json()
    before = {n: t["dataset_id"] for n, t in _tables(body).items()}
    versions = {n: (await client.get(f"/api/datasets/{d}")).json()["latest"]["id"]
                for n, d in before.items()}
    import memory.session_store as ss
    monkeypatch.setattr(ss, "REFRESH_GAP_S", 0)
    serve(monkeypatch, stripe_after)
    r = await client.post(f"/api/imports/{body['id']}/refresh")
    assert r.status_code == 202, r.text
    await _settle(client)
    after = (await client.get(f"/api/imports/{body['id']}")).json()
    kept = {n: (await client.get(f"/api/datasets/{d}")).json()["latest"]["id"] == versions[n]
            for n, d in before.items()}
    return after, kept


async def test_a_revoked_key_keeps_every_last_good_copy_and_says_why(client, monkeypatch):
    after, kept = await _good_then(client, monkeypatch, Stripe([_sub(1)], [_inv(1)], key="other"))
    t = _tables(after)
    assert after["state"] == "failed" and kept == {"subscriptions": True, "invoices": True}
    assert t["subscriptions"]["error"]["code"] == "bad_key"
    assert "rejected the key" in t["invoices"]["said"]
    assert t["subscriptions"]["as_of"]   # the last good copy's time, still shown


async def test_a_vendor_error_mid_pagination_fails_that_table_only(client, monkeypatch):
    subs = [_sub(i) for i in range(1, 150)]
    after, kept = await _good_then(client, monkeypatch, Stripe(subs, [_inv(1), _inv(2)],
                                                               fail={"/v1/subscriptions": 500}))
    t = _tables(after)
    assert after["state"] == "failed"
    assert t["subscriptions"]["error"] == {"code": "vendor_down", "what": "HTTP 500"}
    assert kept["subscriptions"] and not kept["invoices"]   # the other table is fresh


async def test_a_refresh_past_its_deadline_files_nothing_and_says_so(client, monkeypatch):
    import connectors.imports as imports
    serve(monkeypatch, Stripe([_sub(1)], [_inv(1)]))
    body = (await _connect(client)).json()
    import memory.session_store as ss
    monkeypatch.setattr(ss, "REFRESH_GAP_S", 0)
    monkeypatch.setattr(imports, "REFRESH_DEADLINE_S", 0)
    await client.post(f"/api/imports/{body['id']}/refresh")
    await _settle(client)
    t = _tables((await client.get(f"/api/imports/{body['id']}")).json())
    assert {x["error"]["code"] for x in t.values()} == {"deadline"}
    assert all("too slow" in x["said"] for x in t.values())


async def test_nothing_new_is_said_as_unchanged(client, monkeypatch):
    after, kept = await _good_then(client, monkeypatch, Stripe([_sub(1)], [_inv(1)]))
    assert after["state"] == "idle" and kept == {"subscriptions": True, "invoices": True}
    assert {t["filed"] for t in after["tables"]} == {"unchanged"}


async def test_a_second_refresh_is_said_never_started(client, monkeypatch):
    serve(monkeypatch, Stripe([_sub(1)], [_inv(1)]))
    body = (await _connect(client)).json()
    r = await client.post(f"/api/imports/{body['id']}/refresh")
    assert r.status_code == 409 and "less than 30 minutes ago" in r.json()["detail"]


async def test_the_vendors_client_reaches_its_own_hosts_only():
    """Red-team 6: every request, a retry or a download alike, checked before it is sent."""
    from connectors.imports import ImportFailure, vendor_client
    async with vendor_client(frozenset({"api.stripe.com"})) as c:
        for url in ("https://evil.example/v1/x", "http://api.stripe.com/v1/x"):
            with pytest.raises(ImportFailure) as exc:
                await c.get(url)
            assert exc.value.code == "host"


async def test_another_users_import_is_not_found(client, monkeypatch):
    serve(monkeypatch, Stripe([_sub(1)], [_inv(1)]))
    body = (await _connect(client)).json()
    from tests.test_workspace_api import _app
    other = httpx.AsyncClient(transport=httpx.ASGITransport(
        app=_app(client._transport.app.state.deps, "u2")), base_url="http://t")
    async with other:
        for call in (other.get(f"/api/imports/{body['id']}"),
                     other.post(f"/api/imports/{body['id']}/refresh"),
                     other.delete(f"/api/imports/{body['id']}")):
            assert (await call).status_code == 404


async def test_delete_takes_the_tables_when_asked(client, monkeypatch):
    serve(monkeypatch, Stripe([_sub(1)], [_inv(1)]))
    body = (await _connect(client)).json()
    ids = [t["dataset_id"] for t in body["tables"]]
    assert (await client.delete(f"/api/imports/{body['id']}?tables=true")).status_code == 200
    assert [(await client.get(f"/api/datasets/{d}")).status_code for d in ids] == [404, 404]


# ── the key in no sink (P58-06, red-team 7) ────────────────────────────────────────────────────

async def test_the_key_is_in_no_sink(client, monkeypatch, caplog, tmp_path):
    caplog.set_level(logging.DEBUG)
    after, _ = await _good_then(client, monkeypatch, Stripe([_sub(1)], [_inv(1)], key="other"))
    store = client._transport.app.state.session_store
    sinks = [caplog.text, json.dumps(after), (await client.get("/api/imports")).text,
             json.dumps((await store.list_imports("u1"))[0].status)]
    sinks += [p.read_bytes().decode("latin-1") for p in tmp_path.rglob("*.parquet")]
    assert not [s for s in sinks if KEY in s]
