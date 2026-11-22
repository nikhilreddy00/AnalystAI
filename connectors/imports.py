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
    """A Unix time as the export writes it: the UTC wall time (Stripe's exports are UTC)."""
    return None if unix is None else dt.datetime.fromtimestamp(unix, dt.timezone.utc).replace(
        tzinfo=None)


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
                       cap: int = ROW_CAP) -> tuple[list[dict], bool]:
    """A Stripe list, newest first, page by page on `starting_after` (only the cursor is read):
    (objects, cut). Stops at `cap` objects kept."""
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


async def _stripe_subscriptions(client, headers, window_from) -> tuple[list[dict], str | None]:
    """One row per subscription item (a subscription with two prices is two rows: red-team 11),
    every status (the API's default leaves canceled ones out: churn would read 0). A window keeps
    subscriptions in force at some time inside it, whenever they started (red-team 1)."""
    since = _since(window_from)
    subs, cut = await _stripe_list(
        client, headers, "/subscriptions", what="Subscriptions",
        params={"status": "all", "expand[]": "data.customer"},
        keep=lambda s: since is None or s.get("ended_at") is None or s["ended_at"] >= since)
    rows = []
    for s in subs:
        items = need(s, "items", "data")
        if need(s, "items", "has_more"):   # more than the embedded items: page them
            items, _ = await _stripe_list(client, headers, "/subscription_items",
                                          what="Subscriptions", params={"subscription": s["id"]},
                                          cap=ROW_CAP - len(rows))
        rows += [{"sub": s, "item": it} for it in items]
        if len(rows) >= ROW_CAP:   # the cap is on rows, never on subscriptions (review)
            return rows[:ROW_CAP], _cut_date([s], True)
    return rows, _cut_date(subs, cut)


def _customer(sub: dict, key: str):
    c = sub.get("customer")
    return c.get(key) if isinstance(c, dict) else (c if key == "id" else None)


def _percent_off(sub: dict):
    d = sub.get("discount")
    return (d or {}).get("coupon", {}).get("percent_off") if isinstance(d, dict) else None


_SUB = lambda r, *p: need(r["sub"], *p)    # noqa: E731
_ITEM = lambda r, *p: need(r["item"], *p)  # noqa: E731

STRIPE_SUBSCRIPTIONS = Table(
    name="subscriptions", label="Stripe · Subscriptions", what="Subscriptions",
    fetch=_stripe_subscriptions,
    key=lambda r: (r["sub"]["id"], r["item"]["id"]),
    columns=[
        Column("id", lambda r: _SUB(r, "id")),
        Column("Customer ID", lambda r: _customer(r["sub"], "id")),
        Column("Customer Email", lambda r: _customer(r["sub"], "email")),
        Column("Status", lambda r: _SUB(r, "status")),
        Column("Product", lambda r: _ITEM(r, "price", "product")
               if isinstance(_ITEM(r, "price", "product"), str) else None),
        Column("Plan", lambda r: _ITEM(r, "price", "nickname") or _ITEM(r, "price", "id")),
        Column("Quantity", lambda r: _ITEM(r, "quantity")),
        Column("Interval", lambda r: (_ITEM(r, "price", "recurring") or {}).get("interval")),
        Column("Interval Count",
               lambda r: (_ITEM(r, "price", "recurring") or {}).get("interval_count")),
        Column("Amount", lambda r: stripe_money(_ITEM(r, "price", "unit_amount"),
                                                _ITEM(r, "price", "currency"))),
        Column("Currency", lambda r: _ITEM(r, "price", "currency")),
        Column("Percent Off", lambda r: _percent_off(r["sub"])),
        Column("Created (UTC)", lambda r: instant(_SUB(r, "created"))),
        Column("Start Date (UTC)", lambda r: instant(_SUB(r, "start_date"))),
        Column("Trial End (UTC)", lambda r: instant(_SUB(r, "trial_end"))),
        Column("Cancel At (UTC)", lambda r: instant(_SUB(r, "cancel_at"))),
        Column("Canceled At (UTC)", lambda r: instant(_SUB(r, "canceled_at"))),
        Column("Ended At (UTC)", lambda r: instant(_SUB(r, "ended_at"))),
    ])


async def _stripe_invoice_lines(client, headers, window_from) -> tuple[list[dict], str | None]:
    """One row per invoice line; an invoice's lines past the embedded ones paged on their own
    endpoint (red-team 11: never the first handful only)."""
    since = _since(window_from)
    invoices, cut = await _stripe_list(
        client, headers, "/invoices", what="Invoices",
        params={"created[gte]": since} if since else {})
    rows = []
    for inv in invoices:
        lines = need(inv, "lines", "data")
        if need(inv, "lines", "has_more"):
            lines, _ = await _stripe_list(client, headers, f"/invoices/{need(inv, 'id')}/lines",
                                          what="Invoices", params={}, cap=ROW_CAP - len(rows))
        rows += [{"inv": inv, "line": ln} for ln in lines]
        if len(rows) >= ROW_CAP:
            return rows[:ROW_CAP], _cut_date([inv], True)
    return rows, _cut_date(invoices, cut)


_INV = lambda r, *p: need(r["inv"], *p)    # noqa: E731
_LINE = lambda r, *p: need(r["line"], *p)  # noqa: E731

STRIPE_INVOICE_LINES = Table(
    name="invoice_lines", label="Stripe · Invoice lines", what="Invoices",
    fetch=_stripe_invoice_lines,
    key=lambda r: (r["inv"]["id"], r["line"]["id"]),
    columns=[
        Column("Invoice ID", lambda r: _INV(r, "id")),
        Column("Invoice Number", lambda r: _INV(r, "number")),
        Column("Customer ID", lambda r: _INV(r, "customer")),
        Column("Customer Email", lambda r: _INV(r, "customer_email")),
        Column("Invoice Status", lambda r: _INV(r, "status")),
        Column("Invoice Date (UTC)", lambda r: instant(_INV(r, "created"))),
        Column("Line ID", lambda r: _LINE(r, "id")),
        Column("Description", lambda r: _LINE(r, "description")),
        Column("Subscription ID", lambda r: r["line"].get("subscription")),
        Column("Quantity", lambda r: _LINE(r, "quantity")),
        Column("Amount", lambda r: stripe_money(_LINE(r, "amount"), _LINE(r, "currency"))),
        Column("Currency", lambda r: _LINE(r, "currency")),
        Column("Period Start (UTC)", lambda r: instant(_LINE(r, "period", "start"))),
        Column("Period End (UTC)", lambda r: instant(_LINE(r, "period", "end"))),
    ])


async def _stripe_test(client, secrets: dict) -> tuple[str, bool]:
    """Each table's endpoint read once (a missing permission named), then the account's name
    if the key may read it. Test or live from Stripe's answer, never from the key's prefix."""
    headers = await _stripe_auth(client, secrets)
    live = None
    for path, what in (("/subscriptions", "Subscriptions"), ("/invoices", "Invoices")):
        page = await get_json(client, f"{STRIPE_API}{path}", what=what, headers=headers,
                              params={"limit": 1, **({"status": "all"} if "sub" in path else {})})
        if need(page, "data"):
            live = bool(page["data"][0].get("livemode"))
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
    tables=[STRIPE_SUBSCRIPTIONS, STRIPE_INVOICE_LINES])
