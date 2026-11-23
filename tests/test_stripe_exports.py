"""Stripe's real dashboard exports read as SaaS data (5.8c recording session, 2026-11-23). The SaaS
pack was built on API-style names its own research marked unverified (docs/research/2026-11-05-
saas.md §6: `subscription_id`, `unit_amount`) and a generated fixture of the same names, so every
SaaS gate passed while Stripe's own export (`id`, `Amount`, `Created (UTC)`) was read as generic
data (D1-38: no gate held a real normal input). These are real exports of a Stripe test account,
scrubbed of people; every SaaS rule change must keep them read as Stripe means them."""
from __future__ import annotations

from pathlib import Path

import pytest

from connectors.csv_connector import _read_bytes
from domain_pack.detect import detect

STRIPE = Path(__file__).parent / "fixtures" / "imports" / "stripe"


def _export(name: str):
    df, _ = _read_bytes((STRIPE / name).read_bytes(), ".csv")
    return df, {c for c in df.columns if "(UTC)" in c}


def test_stripes_subscription_export_is_a_subscription_list():
    df, dates = _export("export_subscriptions.csv")
    domain, roles, strength = detect(df, dates)
    assert (domain, strength) == ("saas", "strong"), roles
    assert roles["subscription"] == "id" and roles["customer"] == "Customer ID"
    # a subscription list's Amount is the price per interval, never billed money
    assert roles["unit_amount"] == "Amount" and "amount" not in roles
    assert roles["created"] == "Created (UTC)" and roles["ended_at"] == "Ended At (UTC)"
    assert roles["canceled_at"] == "Canceled At (UTC)" and roles["trial_end"] == "Trial End (UTC)"
    assert roles["plan"] == "Product"   # the readable name; Plan holds the price's id
    assert {roles["interval"], roles["quantity"], roles["status"], roles["currency"]} == {
        "Interval", "Quantity", "Status", "Currency"}


def test_stripes_invoice_export_is_billing():
    df, dates = _export("export_invoices.csv")
    domain, roles, strength = detect(df, dates)
    assert (domain, strength) == ("saas", "strong"), roles
    assert roles["invoice"] == "id" and roles["customer"] == "Customer"
    assert roles["invoice_date"] == "Date (UTC)"
    assert roles["amount"] == "Subtotal" and "unit_amount" not in roles   # billed money
    assert (roles["period_start"], roles["period_end"]) == ("Period Start (UTC)",
                                                            "Period End (UTC)")


@pytest.mark.parametrize("name", ["export_subscriptions.csv", "export_invoices.csv"])
def test_a_bare_id_or_amount_alone_is_no_saas_signal(name):
    """The conditional headers read only beside the shape that gives them their meaning: an
    `id` and an `Amount` with no billing interval or amount due are anyone's table."""
    df, dates = _export(name)
    keep = [c for c in df.columns if c in ("id", "Amount", "Currency", "Status")]
    assert detect(df[keep], set())[0] is None
