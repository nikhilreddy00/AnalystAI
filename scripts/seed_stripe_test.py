"""Seed a Stripe TEST-MODE account with the shapes the import must read (5.8c recording session):
active, trialing and canceled subscriptions; USD, EUR, JPY and KWD prices; a subscription with
two prices; a 20% coupon; a yearly plan; an invoice with 12 lines (past the embedded handful).

    STRIPE_TEST_SECRET=rk_test_… uv run python scripts/seed_stripe_test.py   # or in .env

Refuses anything but a test-mode key: a restricted one with Write on Customers, Products,
Prices, Coupons, Subscriptions, Invoices, Payment methods (or sk_test_). Idempotent: a second run finds its
customers by their metadata and adds nothing. Not product code: a recording aid."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx

API = "https://api.stripe.com/v1"
TAG = "analystai-5.8c-seed"


def _key() -> str:
    key = os.environ.get("STRIPE_TEST_SECRET", "")
    env = Path(__file__).resolve().parents[1] / ".env"
    if not key and env.is_file():
        key = next((ln.split("=", 1)[1].strip().strip('"') for ln in env.read_text().splitlines()
                    if ln.startswith("STRIPE_TEST_SECRET=")), "")
    if not key.startswith(("sk_test_", "rk_test_")):
        sys.exit("STRIPE_TEST_SECRET must be a test-mode key (rk_test_… or sk_test_…)")
    return key


def main() -> None:
    c = httpx.Client(base_url=API, auth=(_key(), ""), timeout=60)

    def post(path: str, **data) -> dict:
        r = c.post(path, data=data)
        if r.status_code != 200:
            sys.exit(f"{path}: HTTP {r.status_code} {r.json().get('error', {}).get('message')}")
        return r.json()

    def price(name: str, amount: int, currency: str, interval: str = "month") -> str:
        """A price, reused by its lookup key on a rerun (a run stopped part way resumes)."""
        key = f"seed-{name.lower().replace(' ', '-')}-{currency}-{interval}"
        got = c.get("/prices", params={"lookup_keys[]": key}).json().get("data")
        if got:
            return got[0]["id"]
        prod = post("/products", name=name, **{"metadata[seed]": TAG})
        return post("/prices", product=prod["id"], unit_amount=amount, currency=currency,
                    nickname=name, lookup_key=key, **{"recurring[interval]": interval})["id"]

    pro, team = price("Pro", 2900, "usd"), price("Team", 9900, "eur")
    seats = price("Seats", 1500, "usd")   # one subscription's prices share a currency
    # KWD (three decimals) needs a KWD bank account on the account: ISK and HUF are Stripe's
    # other special cases (research §2), recorded instead
    basic_jpy, iceland = price("Basic JP", 500, "jpy"), price("Iceland", 50000, "isk")
    budapest = price("Budapest", 250000, "huf")
    yearly = price("Pro yearly", 29000, "usd", "year")
    coupon = post("/coupons", percent_off=20, duration="forever", name="Launch 20%")["id"]

    def customer(n: int) -> str:
        email = f"seed{n}@example.com"
        got = c.get("/customers", params={"email": email}).json().get("data")
        if got:
            return got[0]["id"]
        cus = post("/customers", email=email, name=f"Seed Customer {n}",
                   payment_method="pm_card_visa",
                   **{"invoice_settings[default_payment_method]": "pm_card_visa",
                      "metadata[seed]": TAG})
        return cus["id"]

    def sub(cus: str, *prices: str, **extra) -> dict | None:
        """A subscription, unless the customer has one already (a resumed run)."""
        if c.get("/subscriptions", params={"customer": cus, "status": "all"}).json().get("data"):
            return None
        items = {f"items[{i}][price]": p for i, p in enumerate(prices)}
        return post("/subscriptions", customer=cus, **items, **extra)

    sub(customer(1), pro)                                            # active, USD
    sub(customer(2), team, **{"items[0][quantity]": 3, "trial_period_days": 14})   # trialing, EUR
    if canceled := sub(customer(3), pro):
        c.delete(f"/subscriptions/{canceled['id']}")                 # canceled
    sub(customer(4), pro, seats)                                     # two prices, one subscription
    sub(customer(5), pro, **{"discounts[0][coupon]": coupon})        # 20% off
    sub(customer(6), basic_jpy)                                      # zero-decimal
    sub(customer(7), iceland)                                        # ISK: two decimals, 00
    sub(customer(10), budapest)                                      # HUF: two on invoices
    sub(customer(8), yearly)                                         # yearly
    many = customer(9)
    if c.get("/invoices", params={"customer": many}).json().get("data"):
        print("seeded (resumed): every step done")
        return
    for k in range(12):                                              # 12 lines on one invoice
        post("/invoiceitems", customer=many, amount=100 * (k + 1), currency="usd",
             description=f"Seat {k + 1}")
    inv = post("/invoices", customer=many, pending_invoice_items_behavior="include")
    post(f"/invoices/{inv['id']}/finalize")
    print("seeded: 10 customers, 9 subscriptions (1 canceled), 1 invoice with 12 lines")


if __name__ == "__main__":
    main()
