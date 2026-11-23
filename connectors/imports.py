"""SaaS imports (5.8c, ADR-0010 amendment): a vendor account's tables copied into dataset versions,
refreshed on demand. Our own small fetchers, never dlt: every request goes through the guarded
client (`public_client`, public addresses only) and a host list per vendor checked before it is
sent; only a cursor is read from an answer, never a URL to follow (the one exception, a Shopify
bulk result, is a host-checked URL registered as a secret).

Each table is typed by its catalog (the vendor's declared types, as its own CSV export shows them:
money in major units by the vendor's currency list, a moment in the export's zone), never by what
values look like (5.8b hardening, root cause 2), and becomes a frame through `QueryResult.frame`,
the one place values become a frame. A refresh files each table on its own: a table that fails,
times out or is interrupted keeps its last good copy and is said stale (GLOSSARY: Stale, Cut).
Every failure is one reason code from a fixed set, said in our words; a vendor's own text is never
shown or stored (it can echo the key)."""
from __future__ import annotations

import asyncio
import datetime as dt
import decimal
import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Awaitable, Callable

import httpx
import pandas as pd

from connectors.base import (_SCOPE, BodyTooLarge, RefusedHost, capped_body, credential_scope,
                             public_client, register_secrets)
from connectors.sql_connector import QueryResult
from memory.session_store import REFRESH_DEADLINE_S, ImportRecord

log = logging.getLogger(__name__)

ROW_CAP = 100_000             # per table, newest kept; sized from measured seconds a page (WP3)
PAGE_BYTES = 20 * 2**20       # one answer
RETRY_WAIT_S = 20             # a 429's wait when it names none (neither vendor sends Retry-After)


# ── Failures, in our words ─────────────────────────────────────────────────────────────────────

REASONS = {
    "bad_key": "{vendor} rejected the key: was it deleted or rotated? Replace the key to continue.",
    "permission": "The key can't read {what}. Give it Read access to {what} and try again.",
    "rate_limited": "{vendor} asked us to slow down. Try again in a few minutes.",
    "vendor_down": "{vendor} didn't answer properly ({what}). Try again later.",
    "network": "Couldn't reach {vendor} from this server. Check its internet connection.",
    "deadline": "{vendor} was too slow: the refresh stopped after "
                f"{REFRESH_DEADLINE_S // 60} minutes. Try again later, or import the last 12 "
                "months only.",
    "interrupted": "The refresh was interrupted (the server restarted).",
    "schema": "{vendor}'s answer is missing {what}, so this table wasn't updated.",
    "too_large": "One of {vendor}'s answers was larger than this server reads.",
    "host": "{vendor} pointed us at another server, which isn't allowed.",
    "unexpected": "Something went wrong reading {vendor}.",
    # the key's form, before any request (Stripe)
    "live_secret_key": "That's a live secret key, which can also move money. Create a restricted "
                       "key with Read access instead (Stripe Dashboard → Developers → API keys → "
                       "Create restricted key) and paste that. We recommend read-only.",
    "not_a_key": "That doesn't look like a {vendor} key. Paste a restricted key (rk_live_… or "
                 "rk_test_…).",
}
ACCOUNT_WIDE = {"bad_key", "network", "rate_limited", "host"}   # the next table would fail too


class ImportFailure(Exception):
    """A refresh or key test that failed, as a reason code (REASONS) and a `what` from our own
    vocabulary (a table's or a permission's name, an HTTP status), never the vendor's text."""

    def __init__(self, code: str, what: str = "") -> None:
        super().__init__(code)
        self.code, self.what = code, what

    def public(self) -> dict:
        return {"code": self.code, "what": self.what}


def said(vendor: str, error: dict) -> str:
    """A stored failure in words, with the last good copy's fate (Q1: kept, said)."""
    label = VENDORS[vendor].label if vendor in VENDORS else vendor
    return REASONS.get(error.get("code"), REASONS["unexpected"]).format(
        vendor=label, what=error.get("what") or "")


# ── The vendor's own client ────────────────────────────────────────────────────────────────────

def vendor_client(hosts: frozenset[str]) -> httpx.AsyncClient:
    """`public_client` (public addresses, no redirects, no proxies) and the vendor's host list,
    checked before each request is sent: a page, a retry or a bulk download alike (red-team 6:
    the rule in one place, never an `if` per fetcher)."""
    client = public_client(timeout=60)

    async def only_the_vendors(request: httpx.Request) -> None:
        if request.url.scheme != "https" or request.url.host not in hosts:
            raise ImportFailure("host")
    client.event_hooks["request"] = [only_the_vendors]
    return client


async def get_json(client: httpx.AsyncClient, url: str, *, what: str, **kw) -> dict:
    """One answer as JSON, its failure as a reason code. A 429 is waited out once (its
    Retry-After if any, else RETRY_WAIT_S), never when the vendor says not to retry."""
    for attempt in (1, 2):
        try:
            async with client.stream("GET", url, **kw) as r:
                if r.status_code == 429 and attempt == 1 and \
                        r.headers.get("stripe-should-retry") != "false":
                    wait = r.headers.get("retry-after", "")
                    await asyncio.sleep(min(float(wait) if wait.isdigit() else RETRY_WAIT_S, 60))
                    continue
                if r.status_code == 401:
                    raise ImportFailure("bad_key")
                if r.status_code == 403:
                    raise ImportFailure("permission", what)
                if r.status_code == 429:
                    raise ImportFailure("rate_limited")
                if r.status_code != 200:
                    raise ImportFailure("vendor_down", f"HTTP {r.status_code}")
                body = await capped_body(r, PAGE_BYTES)
        except ImportFailure:
            raise
        except RefusedHost:
            raise ImportFailure("host") from None
        except BodyTooLarge:
            raise ImportFailure("too_large") from None
        except (httpx.TransportError, ValueError) as exc:   # ValueError: capped_body's refusals
            raise ImportFailure("network" if isinstance(exc, httpx.TransportError)
                                else "vendor_down", type(exc).__name__) from None
        try:
            return json.loads(body)
        except ValueError:
            raise ImportFailure("vendor_down", "not JSON") from None
    raise ImportFailure("rate_limited")


# ── Typing: the vendor's declared types, as its export shows them ─────────────────────────────

def need(record: dict, *path: str):
    """A field the catalog declares: missing is a schema failure naming it (red-team 10: never
    a blank column). A present null is a blank."""
    value = record
    for key in path:
        if not isinstance(value, dict) or key not in value:
            raise ImportFailure("schema", ".".join(path))
        value = value[key]
    return value


def instant(unix: int | None) -> dt.datetime | None:
    """A Unix time as Stripe's exports write it: the UTC wall time, to the minute
    (`2026-11-23 15:17`: a second's difference never makes the import and the export differ)."""
    return None if unix is None else dt.datetime.fromtimestamp(
        unix - unix % 60, dt.timezone.utc).replace(tzinfo=None)


@dataclass(frozen=True)
class Column:
    name: str
    get: Callable[[dict], object]     # the record → its typed value (raises ImportFailure)


@dataclass
class Table:
    name: str                          # stable key (imports.tables)
    label: str                         # the dataset's name: "Stripe · Subscriptions"
    what: str                          # the permission it needs, in the vendor's words
    columns: list[Column]
    fetch: Callable[..., Awaitable[tuple[list[dict], str | None]]]   # (records, cut_from)
    key: Callable[[dict], tuple] = lambda r: ()   # a full sort key: equal data, equal bytes

    def frame(self, records: list[dict]) -> pd.DataFrame:
        """The table as its export reads: typed by the catalog, through QueryResult.frame."""
        records = sorted(records, key=self.key)
        rows = [tuple(c.get(r) for c in self.columns) for r in records]
        return QueryResult(columns=[c.name for c in self.columns], rows=rows,
                           truncated=False).frame()


@dataclass
class Vendor:
    name: str
    label: str
    hosts: Callable[[dict], frozenset[str]]
    check: Callable[[dict], None]                       # the key's form, before any request
    test: Callable[..., Awaitable[tuple[str, bool]]]    # (account name, test mode)
    auth: Callable[..., Awaitable[dict]]                # request headers (a minted token too)
    tables: list[Table] = field(default_factory=list)

    def client(self, secrets: dict) -> httpx.AsyncClient:
        return vendor_client(self.hosts(secrets))


VENDORS: dict[str, Vendor] = {}


def secret_values(secrets: dict) -> list[str]:
    """Each part of a stored key (red-team 7: a JSON of parts registered whole masked none)."""
    return [v for v in secrets.values() if isinstance(v, str) and v]


# ── The refresh ────────────────────────────────────────────────────────────────────────────────

FileTable = Callable[[str, str, pd.DataFrame], Awaitable[str]]   # (user, dataset, frame) → status


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


async def refresh(store, rec: ImportRecord, token: str, file_table: FileTable) -> None:
    """Fetch every table of `rec` and file each whole one as a new version. Runs as a task
    holding the row (`token`) under its own credential scope: the key masks its own lines only,
    and everything is caught and logged inside it (red-team 7)."""
    scope = credential_scope()
    vendor = VENDORS[rec.vendor]
    status: dict = {t: dict(s) for t, s in rec.status.items()}
    done: set[str] = set()
    started = _now()

    def fail(tables, error: ImportFailure) -> None:
        for t in tables:
            status[t] = {**status.get(t, {}), "error": error.public(), "failed_at": _now().isoformat()}

    try:
        register_secrets(*secret_values(rec.secrets))
        async with asyncio.timeout(REFRESH_DEADLINE_S):
            async with vendor.client(rec.secrets) as client:
                headers = await vendor.auth(client, rec.secrets)
                for i, table in enumerate(vendor.tables, 1):
                    progress = {"table": i, "of": len(vendor.tables), "name": table.label,
                                "rows": 0, "started_at": started.isoformat()}
                    await store.save_refresh(rec.id, token, progress=progress)
                    try:
                        records, cut_from = await table.fetch(client, headers, rec.window_from)
                        frame = table.frame(records)
                        filed = await file_table(rec.user_id, rec.tables[table.name], frame) \
                            if len(frame) else "empty"
                        status[table.name] = {"as_of": _now().isoformat(), "rows": len(frame),
                                              "cut_from": cut_from, "filed": filed, "error": None}
                    except ImportFailure as f:
                        fail([table.name], f)
                        if f.code in ACCOUNT_WIDE:
                            fail([t.name for t in vendor.tables[i:]], f)
                            break
                    done.add(table.name)
                    await store.save_refresh(rec.id, token, status=status)
    except ImportFailure as f:   # the key test or the token mint
        fail([t.name for t in vendor.tables if t.name not in done], f)
    except TimeoutError:
        fail([t.name for t in vendor.tables if t.name not in done], ImportFailure("deadline"))
    except asyncio.CancelledError:   # a graceful shutdown: said, the last good copies kept
        fail([t.name for t in vendor.tables if t.name not in done], ImportFailure("interrupted"))
        await _end(store, rec, token, status)
        raise
    except Exception:
        log.exception("import refresh failed: %s", rec.vendor)
        fail([t.name for t in vendor.tables if t.name not in done], ImportFailure("unexpected"))
    finally:
        if not asyncio.current_task().cancelling():
            await _end(store, rec, token, status)
        _SCOPE.reset(scope)


async def _end(store, rec: ImportRecord, token: str, status: dict) -> None:
    ok = all(not s.get("error") for s in status.values()) and set(status) >= set(rec.tables)
    await store.save_refresh(rec.id, token, status=status, state="idle" if ok else "failed",
                             now=_now())


def new_token() -> str:
    return uuid.uuid4().hex


# ── Stripe ─────────────────────────────────────────────────────────────────────────────────────
# Docs: docs/research/2026-11-22-5.8c-sources.md §2. Amounts are in a currency's smallest unit;
# Stripe's own currency list says which have none (zero-decimal) and which three. The special
# cases (ISK, HUF, TWD, UGX) are settled from a test-mode recording (research correction 2).

STRIPE_API = "https://api.stripe.com/v1"
STRIPE_VERSION = "2024-06-20"   # pinned: an account's default version changes field shapes
_ZERO_DECIMAL = frozenset("BIF CLP DJF GNF JPY KMF KRW MGA PYG RWF UGX VND VUV XAF XOF XPF".split())
_THREE_DECIMAL = frozenset("BHD JOD KWD OMR TND".split())


def stripe_money(amount: int | None, currency: str | None) -> decimal.Decimal | None:
    """Minor units as the major amount Stripe's exports show (500 JPY is 500; 1999 USD is
    19.99; 1000 KWD thousandths is 1.000)."""
    if amount is None:
        return None
    code = (currency or "").upper()
    places = 0 if code in _ZERO_DECIMAL else 3 if code in _THREE_DECIMAL else 2
    return decimal.Decimal(amount).scaleb(-places)


def _stripe_check(secrets: dict) -> None:
    key = secrets.get("key", "")
    if key.startswith("sk_live_"):
        raise ImportFailure("live_secret_key")
    if not key.startswith(("rk_live_", "rk_test_", "sk_test_")):
        raise ImportFailure("not_a_key")


async def _stripe_auth(client, secrets: dict) -> dict:
    return {"Authorization": f"Bearer {secrets['key']}", "Stripe-Version": STRIPE_VERSION}


async def _stripe_list(client, headers: dict, path: str, *, what: str, params: dict,
                       keep: Callable[[dict], bool] = lambda o: True,
                       cap: int | None = None) -> tuple[list[dict], bool]:
    """A Stripe list, newest first, page by page on `starting_after` (only the cursor is read):
    (objects, cut). Stops at `cap` objects kept (ROW_CAP, read when called)."""
    cap = ROW_CAP if cap is None else cap
    out, after = [], None
    while True:
        page = await get_json(client, f"{STRIPE_API}{path}", what=what, headers=headers,
                              params={**params, "limit": 100,
                                      **({"starting_after": after} if after else {})})
        data = need(page, "data")
        out.extend(o for o in data if keep(o))
        if len(out) >= cap:
            return out[:cap], True
        if not need(page, "has_more") or not data:
            return out, False
        after = need(data[-1], "id")


def _since(window_from: str | None) -> int | None:
    return int(dt.datetime.fromisoformat(window_from).replace(
        tzinfo=dt.timezone.utc).timestamp()) if window_from else None


def _cut_date(objects: list[dict], cut: bool, field_name: str = "created") -> str | None:
    """The first day a cut table still holds (newest first: the last one kept)."""
    return instant(objects[-1][field_name]).date().isoformat() if cut and objects else None


# Each list request a table makes, read by its fetch and by the key test alike: the test sends the
# very request the table will (recorded session: the test read /subscriptions bare, the table
# expands the customer, which needs Customers too, and only the refresh found it)
_STRIPE_FIRST = {
    "subscriptions": ("/subscriptions", "Subscriptions and Customers",
                      {"status": "all", "expand[]": "data.customer"}),
    "products": ("/products", "Products", {}),
    "invoices": ("/invoices", "Invoices", {}),
}


async def _stripe_subscriptions(client, headers, window_from) -> tuple[list[dict], str | None]:
    """One row per subscription item, as Stripe's own export (a subscription with two prices is
    two rows), every status (the API's default leaves canceled ones out: churn would read 0), the
    product by its name (the export's Product: its id is too deep to expand, so one product list).
    A window keeps subscriptions in force at some time inside it, whenever they started
    (red-team 1)."""
    since = _since(window_from)
    path, what, params = _STRIPE_FIRST["subscriptions"]
    subs, cut = await _stripe_list(
        client, headers, path, what=what, params=params,
        keep=lambda s: since is None or s.get("ended_at") is None or s["ended_at"] >= since)
    path, what, params = _STRIPE_FIRST["products"]
    products, _ = await _stripe_list(client, headers, path, what=what, params=params)
    names = {p["id"]: p.get("name") for p in products}
    rows = []
    for s in subs:
        items = need(s, "items", "data")
        if need(s, "items", "has_more"):   # more than the embedded items: page them
            items, _ = await _stripe_list(client, headers, "/subscription_items",
                                          what="Subscriptions", params={"subscription": s["id"]},
                                          cap=ROW_CAP - len(rows))
        rows += [{"sub": s, "item": it, "names": names} for it in items]
        if len(rows) >= ROW_CAP:   # the cap is on rows, never on subscriptions (review)
            return rows[:ROW_CAP], _cut_date([s], True)
    return rows, _cut_date(subs, cut)


def _customer(sub: dict, key: str):
    c = sub.get("customer")
    return c.get(key) if isinstance(c, dict) else (c if key == "id" else None)


def _percent_off(sub: dict):
    d = sub.get("discount")
    return (d or {}).get("coupon", {}).get("percent_off") if isinstance(d, dict) else None


def _coupon(sub: dict):
    d = sub.get("discount")
    return (d or {}).get("coupon", {}).get("id") if isinstance(d, dict) else None


_SUB = lambda r, *p: need(r["sub"], *p)    # noqa: E731
_ITEM = lambda r, *p: need(r["item"], *p)  # noqa: E731
_RECURRING = lambda r: _ITEM(r, "price", "recurring") or {}   # noqa: E731

# Stripe's Subscriptions export (Billing → Subscriptions → Export, all columns), its own names and
# order (tests/fixtures/imports/stripe/export_subscriptions.csv); its people (Customer Name,
# Customer Description) left out, and Percent Off added: the export names a coupon only by its id,
# so MRR read from it can't take the discount off.
STRIPE_SUBSCRIPTIONS = Table(
    name="subscriptions", label="Stripe · Subscriptions", what="Subscriptions",
    fetch=_stripe_subscriptions,
    key=lambda r: (r["sub"]["id"], r["item"]["id"]),
    columns=[
        Column("id", lambda r: _SUB(r, "id")),
        Column("Customer ID", lambda r: _customer(r["sub"], "id")),
        Column("Customer Email", lambda r: _customer(r["sub"], "email")),
        Column("Plan", lambda r: _ITEM(r, "price", "id")),
        Column("Product", lambda r: r["names"].get(_ITEM(r, "price", "product"))),
        Column("Product ID", lambda r: _ITEM(r, "price", "product")),
        Column("Quantity", lambda r: _ITEM(r, "quantity")),
        Column("Currency", lambda r: _ITEM(r, "price", "currency")),
        Column("Cancellation Reason",
               lambda r: (r["sub"].get("cancellation_details") or {}).get("reason")),
        Column("Cancellation Feedback",
               lambda r: (r["sub"].get("cancellation_details") or {}).get("feedback")),
        Column("Interval", lambda r: _RECURRING(r).get("interval")),
        Column("Amount", lambda r: stripe_money(_ITEM(r, "price", "unit_amount"),
                                                _ITEM(r, "price", "currency"))),
        Column("Status", lambda r: _SUB(r, "status")),
        Column("Created (UTC)", lambda r: instant(_SUB(r, "created"))),
        Column("Start Date (UTC)", lambda r: instant(_SUB(r, "start_date"))),
        Column("Current Period Start (UTC)",
               lambda r: instant(r["item"].get("current_period_start")
                                 or r["sub"].get("current_period_start"))),
        Column("Current Period End (UTC)",
               lambda r: instant(r["item"].get("current_period_end")
                                 or r["sub"].get("current_period_end"))),
        Column("Trial Start (UTC)", lambda r: instant(_SUB(r, "trial_start"))),
        Column("Trial End (UTC)", lambda r: instant(_SUB(r, "trial_end"))),
        Column("Coupon", lambda r: _coupon(r["sub"])),
        Column("Canceled At (UTC)", lambda r: instant(_SUB(r, "canceled_at"))),
        Column("Cancel At Period End", lambda r: _SUB(r, "cancel_at_period_end")),
        Column("Ended At (UTC)", lambda r: instant(_SUB(r, "ended_at"))),
        Column("Percent Off", lambda r: _percent_off(r["sub"])),
    ])


async def _stripe_invoices(client, headers, window_from) -> tuple[list[dict], str | None]:
    """One row per invoice, as Stripe's Invoices export: its totals are the invoice's own, so no
    line is fetched (an invoice's lines past the embedded handful never leave a total short)."""
    since = _since(window_from)
    path, what, params = _STRIPE_FIRST["invoices"]
    invoices, cut = await _stripe_list(
        client, headers, path, what=what,
        params={**params, **({"created[gte]": since} if since else {})})
    return invoices, _cut_date(invoices, cut)


def _inv_money(field_name: str):
    return lambda i: stripe_money(need(i, field_name), need(i, "currency"))


def _transition(i: dict, key: str):
    return instant((i.get("status_transitions") or {}).get(key))


# Stripe's Invoices export (Billing → Invoices → Export, all columns): the columns it fills, its
# names and order (tests/fixtures/imports/stripe/export_invoices.csv); the customer's name and
# address left out.
STRIPE_INVOICES = Table(
    name="invoices", label="Stripe · Invoices", what="Invoices",
    fetch=_stripe_invoices,
    key=lambda i: (i["id"],),
    columns=[
        Column("id", lambda i: need(i, "id")),
        Column("Amount Due", _inv_money("amount_due")),
        Column("Billing", lambda i: need(i, "collection_method")),
        Column("Charge", lambda i: i.get("charge")),
        Column("Currency", lambda i: need(i, "currency")),
        Column("Customer", lambda i: need(i, "customer")),
        Column("Date (UTC)", lambda i: instant(need(i, "created"))),
        Column("Due Date (UTC)", lambda i: instant(i.get("due_date"))),
        Column("Ending Balance", _inv_money("ending_balance")),
        Column("Number", lambda i: need(i, "number")),
        Column("Paid", lambda i: need(i, "status") == "paid"),
        Column("Paid At (UTC)", lambda i: _transition(i, "paid_at")),
        Column("Finalized At (UTC)", lambda i: _transition(i, "finalized_at")),
        Column("Period Start (UTC)", lambda i: instant(need(i, "period_start"))),
        Column("Period End (UTC)", lambda i: instant(need(i, "period_end"))),
        Column("Starting Balance", _inv_money("starting_balance")),
        Column("Subscription", lambda i: i.get("subscription")),
        Column("Subtotal", _inv_money("subtotal")),
        Column("Total Discount Amount",
               lambda i: stripe_money(sum(d.get("amount", 0) for d in
                                          i.get("total_discount_amounts") or []),
                                      need(i, "currency"))),
        Column("Total", _inv_money("total")),
        Column("Customer Email", lambda i: i.get("customer_email")),
        Column("Amount Paid", _inv_money("amount_paid")),
        Column("Status", lambda i: need(i, "status")),
    ])


async def _stripe_test(client, secrets: dict) -> tuple[str, bool]:
    """Each table's endpoint read once (a missing permission named), then the account's name
    if the key may read it. Test or live from Stripe's answer, never from the key's prefix."""
    headers = await _stripe_auth(client, secrets)
    live = None
    for path, what, params in _STRIPE_FIRST.values():
        page = await get_json(client, f"{STRIPE_API}{path}", what=what, headers=headers,
                              params={**params, "limit": 1})
        if need(page, "data") and "livemode" in page["data"][0]:
            live = bool(page["data"][0]["livemode"])
    name = "Stripe account"
    try:
        acct = await get_json(client, f"{STRIPE_API}/account", what="Account", headers=headers)
        name = (acct.get("settings") or {}).get("dashboard", {}).get("display_name") or \
            (acct.get("business_profile") or {}).get("name") or name
    except ImportFailure as f:
        if f.code != "permission":   # a key that can't read the account is fine
            raise
    if live is None:   # an account with no objects yet: Stripe's own key forms say the mode
        live = secrets["key"].startswith("rk_live_")
    return name, not live


VENDORS["stripe"] = Vendor(
    name="stripe", label="Stripe", hosts=lambda s: frozenset({"api.stripe.com"}),
    check=_stripe_check, test=_stripe_test, auth=_stripe_auth,
    tables=[STRIPE_SUBSCRIPTIONS, STRIPE_INVOICES])
