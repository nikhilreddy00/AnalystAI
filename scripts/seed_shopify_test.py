"""Seed a Shopify DEVELOPMENT store with the orders the import must read (5.8c recording session):
several lines an order, paid and pending, one cancelled, one test order, one with a discount,
spread over the last 50 days (inside read_orders' 60). Custom line items (a title, a price, a
SKU), so no product setup is needed.

    uv run python scripts/seed_shopify_test.py   # SHOPIFY_SHOP / _CLIENT_ID / _CLIENT_SECRET in .env

Refuses a store that isn't a development store. Idempotent: a store with orders is left alone.
Not product code: a recording aid."""
from __future__ import annotations

import datetime as dt
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
API = "2026-01"


def _env(name: str) -> str:
    lines = (ROOT / ".env").read_text().splitlines()
    return next((ln.split("=", 1)[1].strip().strip('"') for ln in lines
                 if ln.startswith(f"{name}=")), "")


def main() -> None:
    shop = _env("SHOPIFY_SHOP")
    r = httpx.post(f"https://{shop}/admin/oauth/access_token", timeout=30,
                   data={"grant_type": "client_credentials", "client_id": _env("SHOPIFY_CLIENT_ID"),
                         "client_secret": _env("SHOPIFY_CLIENT_SECRET")})
    if r.status_code != 200:
        sys.exit(f"token: HTTP {r.status_code}")
    headers = {"X-Shopify-Access-Token": r.json()["access_token"]}

    def gql(query: str, **variables) -> dict:
        got = httpx.post(f"https://{shop}/admin/api/{API}/graphql.json", headers=headers,
                         json={"query": query, "variables": variables}, timeout=60).json()
        if got.get("errors"):
            sys.exit(f"graphql: {got['errors']}")
        return got["data"]

    info = gql("""{ shop { plan { partnerDevelopment } }
                    orders(first: 100) { edges { node { id tags cancelledAt } } } }""")
    if not info["shop"]["plan"]["partnerDevelopment"]:
        sys.exit("refused: not a development store")
    # resumable: each seed order carries its number (a first run tagged its one order bare)
    done = {int(t.rsplit("-", 1)[1]) if t != "seed-5.8c" else 1
            for e in info["orders"]["edges"] for t in e["node"]["tags"] if t.startswith("seed-5.8c")}
    now = dt.datetime.now(dt.timezone.utc)
    create = """mutation($order: OrderCreateOrderInput!, $options: OrderCreateOptionsInput) {
      orderCreate(order: $order, options: $options) {
        order { id name } userErrors { field message } } }"""

    def line(title: str, price: str, qty: int, sku: str) -> dict:
        return {"title": title, "quantity": qty, "sku": sku,
                "priceSet": {"shopMoney": {"amount": price, "currencyCode": "USD"}}}

    def order(n: int, days_ago: int, lines: list, *, status="PAID", test=False,
              discount: str | None = None) -> str | None:
        if n in done:
            return None
        o = {"email": f"buyer{n}@mail.invalid", "currency": "USD", "lineItems": lines,
             "financialStatus": status, "test": test,
             "processedAt": (now - dt.timedelta(days=days_ago)).isoformat(),
             "tags": [f"seed-5.8c-{n}"]}
        if discount:
            o["discountCode"] = {"itemFixedDiscountCode": {
                "code": "SAVE5", "amountSet": {"shopMoney": {"amount": discount,
                                                             "currencyCode": "USD"}}}}
        for _ in range(5):   # a dev store throttles order creation ("Too many attempts")
            got = gql(create, order=o, options={"sendReceipt": False})["orderCreate"]
            if not any("Too many attempts" in e["message"] for e in got["userErrors"]):
                break
            print("throttled: waiting 60 s", flush=True)
            time.sleep(60)
        if got["userErrors"]:
            sys.exit(f"orderCreate: {got['userErrors']}")
        time.sleep(10)
        return got["order"]["id"]

    order(1, 50, [line("Classic Tee", "20.00", 2, "TEE-001"), line("Mug", "12.50", 1, "MUG-001")])
    order(2, 40, [line("Hoodie", "55.00", 1, "HOO-001")])
    order(3, 30, [line("Classic Tee", "20.00", 1, "TEE-001"), line("Cap", "18.00", 3, "CAP-001"),
                  line("Sticker", "3.00", 5, "STK-001")], discount="5.00")
    order(4, 21, [line("Mug", "12.50", 4, "MUG-001")], status="PENDING")
    cancelled = order(5, 14, [line("Hoodie", "55.00", 2, "HOO-001")])
    order(6, 7, [line("Classic Tee", "20.00", 1, "TEE-001")], test=True)
    order(7, 3, [line("Cap", "18.00", 1, "CAP-001"), line("Mug", "12.50", 2, "MUG-001")])
    order(8, 1, [line("Sticker", "3.00", 10, "STK-001")])
    if cancelled is None:   # created by an earlier run: cancelled only if it isn't yet
        cancelled = next((e["node"]["id"] for e in info["orders"]["edges"]
                          if "seed-5.8c-5" in e["node"]["tags"] and not e["node"]["cancelledAt"]),
                         None)
    if cancelled:
        got = gql("""mutation($id: ID!) { orderCancel(orderId: $id, reason: CUSTOMER, refund: false,
                     restock: false, notifyCustomer: false) {
                     orderCancelUserErrors { message } } }""", id=cancelled)
        if errs := got["orderCancel"]["orderCancelUserErrors"]:
            sys.exit(f"orderCancel: {errs}")
    print("seeded: 8 orders (1 cancelled, 1 test, 1 pending, 1 discounted), 13 lines"
          f"{f'; {len(done)} already there' if done else ''}")


if __name__ == "__main__":
    main()
