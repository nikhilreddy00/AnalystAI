"""Record a vendor's real API pages for the import tests (5.8c, D1-38: tests replay real pages):
the production fetchers run against the vendor, through the production client, and every answer
is saved, scrubbed, as `tests/fixtures/imports/<vendor>/pages.json`. tests/test_imports.py
replays them under the guarded client's fake network.

    STRIPE_TEST_RESTRICTED=rk_test_… uv run python scripts/record_import.py stripe

Keys come from the environment or .env (never an argument). Scrubbed before writing: people's
names, emails, phones and addresses become placeholders; the file is refused if any key, or a
token-shaped value, is still in it. Not product code: a recording aid."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

ENV = {"stripe": {"key": "STRIPE_TEST_RESTRICTED"},
       "shopify": {"key": "SHOPIFY_CLIENT_SECRET", "client_id": "SHOPIFY_CLIENT_ID",
                   "shop": "SHOPIFY_SHOP"}}
_PERSONAL = {"name", "phone", "address", "shipping", "line1", "line2", "postal_code",
             "first_name", "last_name", "firstName", "lastName",
             # an invoice's copy of its customer (the first recording kept customer_name)
             "customer_name", "customer_phone", "customer_address", "customer_shipping",
             "billing_details", "shipping_details"}
_THINGS = {"product", "coupon", "price", "plan", "account"}   # objects whose `name` is no person
_TOKENS = re.compile(r"\b(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{8,}|shpat_\w+|shpss_\w+|pat-\w+")


def _env(name: str) -> str:
    if v := os.environ.get(name):
        return v
    env = ROOT / ".env"
    lines = env.read_text().splitlines() if env.is_file() else []
    return next((ln.split("=", 1)[1].strip().strip('"') for ln in lines
                 if ln.startswith(f"{name}=")), "")


def email(value: str) -> str:
    """An address as its placeholder, the same from every file (the recording and the vendor's
    own export of one customer match, for the oracle): customer<hash>@mail.invalid, never
    @example.com, which the SaaS pack's test_marker reads as a test account."""
    return f"customer{hashlib.sha256(value.strip().lower().encode()).hexdigest()[:8]}@mail.invalid"


def scrub(value, emails: dict):
    """People out, structure kept: an email becomes its placeholder, a name, phone or address
    `[scrubbed]` (an address's parts too)."""
    if isinstance(value, dict):
        # a product's or a coupon's name is no person's (the first oracle run: every Product
        # read "[scrubbed]")
        personal = _PERSONAL - ({"name"} if value.get("object") in _THINGS else set())
        return {k: ("[scrubbed]" if k in personal and isinstance(v, str) and v
                    else None if k in personal and isinstance(v, dict) else scrub(v, emails))
                for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(v, emails) for v in value]
    if isinstance(value, str) and re.fullmatch(r"[^@\s]+@[^@\s]+\.\w+", value):
        return emails.setdefault(value, email(value))
    return value


def scrub_csv(path: Path) -> None:
    """A vendor's own export, scrubbed in place by the same rules: an email column's values
    become their placeholders, a name, description, phone or address column `[scrubbed]`."""
    import pandas as pd
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    for c in df.columns:
        low = c.lower()
        if "email" in low:
            df[c] = df[c].map(lambda v: email(v) if v else v)
        elif any(w in low for w in ("name", "description", "phone", "address", "shipping")) \
                and not low.startswith(("product", "plan")):
            df[c] = df[c].map(lambda v: "[scrubbed]" if v else v)
    text = df.to_csv(index=False)
    if hit := _TOKENS.search(text):
        sys.exit(f"refused: a key or token is in {path.name} ({hit.group()[:6]}…)")
    path.write_text(text)
    print(f"scrubbed {path}")


async def record(vendor_name: str) -> Path:
    import httpx
    from connectors import imports as imp

    secrets = {k: _env(v) for k, v in ENV[vendor_name].items()}
    if not all(secrets.values()):
        sys.exit(f"set {', '.join(ENV[vendor_name].values())} (environment or .env)")
    vendor = imp.VENDORS[vendor_name]
    vendor.check(secrets)
    pages, emails = [], {}

    read = imp.capped_body

    async def keep(response: httpx.Response, cap: int) -> bytes:
        """The production read, then the page as it was read (never a second read of it)."""
        body = await read(response, cap)
        url = response.request.url
        pages.append({"method": response.request.method, "path": url.path,
                      "query": sorted(url.params.multi_items()), "status": response.status_code,
                      "headers": {k: v for k, v in response.headers.items()
                                  if k.lower() in ("retry-after", "stripe-should-retry",
                                                   "content-type")},
                      "body": scrub(json.loads(body), emails) if body else None})
        return body

    imp.capped_body = keep
    client = vendor.client(secrets)
    async with client:
        account, test_mode = await vendor.test(client, secrets)
        headers = await vendor.auth(client, secrets)
        counts = {}
        for table in vendor.tables:
            records, cut = await table.fetch(client, headers, None)
            counts[table.name] = (len(records), cut)
    if not test_mode:
        sys.exit("refused: a live-mode account. Record a test account only.")
    text = json.dumps({"vendor": vendor_name, "account": "[scrubbed]", "pages": pages}, indent=1,
                      sort_keys=True)
    if (hit := _TOKENS.search(text)) or any(v and v in text for v in secrets.values()):
        sys.exit(f"refused: a key or token is in the recording ({hit.group()[:6] if hit else 'key'}…)")
    out = ROOT / "tests/fixtures/imports" / vendor_name / "pages.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text + "\n")
    print(f"{account} ({'test' if test_mode else 'live'}): {len(pages)} pages → {out}")
    for name, (n, cut) in counts.items():
        print(f"  {name}: {n} records{f', cut from {cut}' if cut else ''}")
    return out


if __name__ == "__main__":
    if sys.argv[1:2] == ["scrub-csv"]:   # record_import.py scrub-csv <export.csv> …
        for f in sys.argv[2:]:
            scrub_csv(Path(f))
    else:
        asyncio.run(record(sys.argv[1] if len(sys.argv) > 1 else "stripe"))
