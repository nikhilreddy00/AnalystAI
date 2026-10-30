"""
Ingestion eval data (Phase 5 Ingestion): a table written to the bytes a user would upload,
with planted truths: each column's role (date / id / metric / category), its PII kind, and
the literal PII strings that must never reach an LLM.

A case lists columns as `[name, kind]` (or a dict with `nulls`, `role` overrides); the kind
decides the values and the truth. Truth is defined here, independently of the agent: the
scorer never calls the agent's inference. PII is fake (example.com, 555 numbers, generated
names, Luhn-valid test cards).
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

_FIRST = ["Jane", "Omar", "Priya", "Liam", "Sofia", "Chen", "Ada", "Mateo", "Grace", "Kwame",
          "Hannah", "Diego", "Mark", "May", "June", "Olivia", "Ravi", "Elena", "Tomás", "Zoë"]
_LAST = ["Doe", "Haddad", "Patel", "O'Brien", "Rossi", "Wei", "Lovelace", "García", "Hopper",
         "Mensah", "Schmidt", "Novak", "Kowalski", "Fischer", "Nguyen", "Ibrahim", "Silva"]
_STREETS = ["Main St", "Oak Ave", "Maple Dr", "High St", "Elm Rd", "Park Ln", "Cedar Ct"]
_PRODUCTS = ["Blue Widget", "Red Gadget", "Steel Bracket", "Mini Router", "Desk Lamp",
             "Travel Mug", "Field Notebook", "Solar Charger"]
_STATES = ["Washington", "Georgia", "Virginia", "Texas", "Ohio", "Nevada", "Florida", "Maine"]
_TZ = ["America/New_York", "Europe/London", "Asia/Kolkata", "America/Chicago", "Europe/Berlin"]
_PROMOS = ["SAVE10", "SPRING25", "VIP", "FREESHIP", "WELCOME5"]
_WORDS = ["called back", "asked about delivery", "wants a refund", "happy with service",
          "follow up next week", "price too high", "upgraded plan", "no answer"]
CALENDAR_WORDS = {"May", "June", "July", "March", "April", "August"}   # never searched for
TEXT_DATES = {"date_iso": False, "date_us": False, "datetime_iso": False, "dob": False,
              "date_eu": True, "epoch_s": False}   # date kinds → dayfirst


@dataclass
class Column:
    name: str
    values: list
    roles: tuple[str, ...]          # acceptable roles (most columns: exactly one)
    pii: str | None = None          # email | phone | ssn | card | name | address | dob | ip | embedded
    literals: list[str] = field(default_factory=list)   # PII strings that must stay out of prompts
    trap: bool = False              # looks PII-ish but isn't: must not be masked
    kind: str = ""


def _dates(rng, n, lo="2024-01-01", hi="2025-12-31"):
    a, b = pd.Timestamp(lo).value // 10**9, pd.Timestamp(hi).value // 10**9
    return pd.to_datetime(rng.integers(a, b, n), unit="s")


def _luhn_card(rng) -> str:
    digits = [4] + list(rng.integers(0, 10, 14))
    total = 0
    for i, d in enumerate(reversed(digits)):   # check digit position is index 0 after append
        n = d * 2 if i % 2 == 0 else d
        total += n - 9 if n > 9 else n
    digits.append((10 - total % 10) % 10)
    s = "".join(map(str, digits))
    return " ".join(s[i:i + 4] for i in range(0, 16, 4))


def _name(rng) -> str:
    return f"{rng.choice(_FIRST)} {rng.choice(_LAST)}"


def make_column(name: str, kind: str, rng: np.random.Generator, n: int) -> Column:
    """One column of `kind`. Values are Python objects as a correct load should return them."""
    pick = lambda xs: [xs[i] for i in rng.integers(0, len(xs), n)]   # noqa: E731
    if kind == "id_seq":
        return Column(name, list(range(100_001, 100_001 + n)), ("id",))
    if kind == "id_str":
        return Column(name, [f"ORD-{i:05d}" for i in rng.permutation(n) + 1], ("id",))
    if kind == "uuid":
        return Column(name, [f"{rng.integers(16**8):08x}-{rng.integers(16**4):04x}-4"
                             f"{rng.integers(16**3):03x}-a{rng.integers(16**3):03x}-"
                             f"{rng.integers(16**12):012x}" for _ in range(n)], ("id",))
    if kind == "invoice10":   # 10 digits, no separators: not a phone number
        return Column(name, [int(v) for v in rng.choice(np.arange(4_000_000_000, 4_000_900_000),
                                                         n, replace=False)], ("id",), trap=True)
    if kind == "sku":
        return Column(name, [f"{rng.choice(['AB', 'KX', 'TR'])}-{rng.integers(1000, 9999)}"
                             for _ in range(n)], ("id", "category"), trap=True)
    if kind == "date_iso":
        return Column(name, [d.strftime("%Y-%m-%d") for d in _dates(rng, n)], ("date",))
    if kind == "date_us":
        return Column(name, [f"{d.month}/{d.day}/{d.year}" for d in _dates(rng, n)], ("date",))
    if kind == "date_eu":
        return Column(name, [d.strftime("%d.%m.%Y") for d in _dates(rng, n)], ("date",))
    if kind == "datetime_iso":
        return Column(name, [d.strftime("%Y-%m-%d %H:%M:%S") for d in _dates(rng, n)], ("date",))
    if kind == "date_native":   # xlsx / parquet keep real datetimes
        return Column(name, list(_dates(rng, n).normalize()), ("date",))
    if kind == "epoch_s":
        return Column(name, [int(d.value // 10**9) for d in _dates(rng, n)], ("date",))
    if kind == "year_int":
        return Column(name, [int(v) for v in rng.integers(2019, 2026, n)], ("category", "date"))
    if kind == "zip_int":   # leading zeros already lost, as in most exports
        return Column(name, [int(v) for v in rng.choice([2134, 10001, 30301, 60614, 94103, 8540,
                                                          98101, 73301], n)], ("category",))
    if kind == "money":
        return Column(name, [round(float(v), 2) for v in rng.lognormal(4, 0.8, n)], ("metric",))
    if kind == "money_text":
        return Column(name, [f"${v:,.2f}" for v in rng.lognormal(6.5, 0.9, n)], ("metric",))
    if kind == "pct_text":
        return Column(name, [f"{v:.1f}%" for v in rng.uniform(0, 60, n)], ("metric",))
    if kind == "count":
        return Column(name, [int(v) for v in rng.integers(1, 25, n)], ("metric",))
    if kind == "rate01":
        return Column(name, [round(float(v), 3) for v in rng.uniform(0, 0.4, n)], ("metric",))
    if kind == "flag01":
        return Column(name, [int(v) for v in rng.integers(0, 2, n)], ("metric", "category"))
    if kind == "bool_text":
        return Column(name, pick(["true", "false"]), ("category",))
    if kind == "yesno":
        return Column(name, pick(["Yes", "No"]), ("category",))
    if kind == "region":
        return Column(name, pick(["North", "South", "East", "West"]), ("category",))
    if kind == "state":
        return Column(name, pick(_STATES), ("category",), trap=True)
    if kind == "product_name":
        return Column(name, pick(_PRODUCTS), ("category",), trap=True)
    if kind == "company":
        return Column(name, pick(["Acme Corp", "Globex", "Initech", "Umbrella Ltd", "Stark Supply"]),
                      ("category",), trap=True)
    if kind == "timezone":
        return Column(name, pick(_TZ), ("category",))
    if kind == "promo_code":
        return Column(name, pick(_PROMOS), ("category",))
    if kind == "country_code":
        return Column(name, pick(["US", "GB", "IN", "DE", "BR"]), ("category",))
    if kind == "city_accents":
        return Column(name, pick(["Zürich", "Málaga", "Köln", "São Paulo", "Montréal"]), ("category",))
    if kind == "plan":
        return Column(name, pick(["Basic", "Pro", "Enterprise"]), ("category",))
    if kind == "free_text":
        return Column(name, pick(_WORDS), ("category",))
    # ── PII ──
    if kind == "full_name":
        v = [_name(rng) for _ in range(n)]
        return Column(name, v, ("category", "id"), "name", sorted(set(v)))
    if kind == "first_name":
        v = pick(_FIRST)
        return Column(name, v, ("category",), "name", sorted(set(v) - CALENDAR_WORDS))
    if kind == "email":
        v = [f"user{int(i)}@example.com" for i in rng.integers(1, 5000, n)]
        return Column(name, v, ("category", "id"), "email", sorted(set(v)))
    if kind == "phone_us":
        v = [f"(555) {rng.integers(200, 999)}-{rng.integers(1000, 9999)}" for _ in range(n)]
        return Column(name, v, ("category", "id"), "phone", sorted(set(v)))
    if kind == "phone_intl":
        v = [str(rng.choice([f"+44 20 7946 {rng.integers(1000, 9999)}",
                             f"+91 98765 {rng.integers(10000, 99999)}",
                             f"+49 30 {rng.integers(1000000, 9999999)}"])) for _ in range(n)]
        return Column(name, v, ("category", "id"), "phone", sorted(set(v)))
    if kind == "ssn":
        v = [f"{rng.integers(100, 899)}-{rng.integers(10, 99)}-{rng.integers(1000, 9999)}"
             for _ in range(n)]
        return Column(name, v, ("id", "category"), "ssn", sorted(set(v)))
    if kind == "card":
        v = [_luhn_card(rng) for _ in range(n)]
        return Column(name, v, ("id", "category"), "card", sorted(set(v)))
    if kind == "street":
        v = [f"{rng.integers(1, 9999)} {rng.choice(_STREETS)}" for _ in range(n)]
        return Column(name, v, ("category",), "address", sorted(set(v)))
    if kind == "dob":
        v = [d.strftime("%Y-%m-%d") for d in _dates(rng, n, "1950-01-01", "2002-12-31")]
        return Column(name, v, ("date",), "dob", sorted(set(v)))
    if kind == "ip":
        v = [f"203.0.{rng.integers(0, 255)}.{rng.integers(1, 254)}" for _ in range(n)]
        return Column(name, v, ("category", "id"), "ip", sorted(set(v)))
    if kind == "notes_pii":   # free text; ~40% of notes carry an email or phone
        vals, lits = [], []
        for _ in range(n):
            text = str(rng.choice(_WORDS))
            r = rng.random()
            if r < 0.2:
                lit = f"buyer{rng.integers(1, 9999)}@example.com"
            elif r < 0.4:
                lit = f"555-{rng.integers(200, 999)}-{rng.integers(1000, 9999)}"
            else:
                lit = None
            if lit:
                text, lits = f"{text}, reach at {lit}", lits + [lit]
            vals.append(text)
        return Column(name, vals, ("category",), "embedded", sorted(set(lits)))
    raise ValueError(f"unknown column kind {kind!r}")


def build(case: dict) -> tuple[pd.DataFrame, list[Column]]:
    """The case's canonical frame (what a correct load returns) and its column truths."""
    rng = np.random.default_rng(case["seed"])
    n = case.get("rows", 300)
    cols = []
    for spec in case["columns"]:
        name, kind, extra = (spec[0], spec[1], {}) if isinstance(spec, list) else \
            (spec["name"], spec["kind"], spec)
        col = make_column(name, kind, rng, n)
        col.kind = kind
        if extra.get("nulls"):
            hit = rng.random(n) < extra["nulls"]
            col.values = [None if h else v for v, h in zip(col.values, hit)]
            keep = {str(v) for v in col.values if v is not None}
            col.literals = [x for x in col.literals if x in keep or col.pii == "embedded"]
        if extra.get("trap"):
            col.trap = True
        if extra.get("role"):
            col.roles = tuple(extra["role"]) if isinstance(extra["role"], list) else (extra["role"],)
        cols.append(col)
    return pd.DataFrame({c.name: c.values for c in cols}), cols


def to_bytes(df: pd.DataFrame, fmt: dict) -> tuple[bytes, str]:
    """(file bytes, extension) as the user's tool would have written them."""
    kind = fmt.get("type", "csv")
    if kind in ("csv", "tsv"):
        sep = fmt.get("sep", "\t" if kind == "tsv" else ",")
        text = df.to_csv(index=False, sep=sep, decimal=fmt.get("decimal", "."))
        title = "".join(f"{line}\n" for line in fmt.get("title_rows", []))
        return (title + text).encode(fmt.get("encoding", "utf-8")), f".{kind}"
    if kind == "xlsx":
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as xw:
            for sheet in fmt.get("before_sheets", []):   # e.g. a small summary tab first
                pd.DataFrame(sheet["rows"], columns=sheet["columns"]).to_excel(
                    xw, sheet_name=sheet["name"], index=False)
            df.to_excel(xw, sheet_name=fmt.get("sheet", "Data"), index=False)
        return buf.getvalue(), ".xlsx"
    if kind == "parquet":
        out = df.copy()
        for col, dtype in (fmt.get("dtypes") or {}).items():
            out[col] = out[col].astype(dtype)
        buf = io.BytesIO()
        out.to_parquet(buf, index=False)
        return buf.getvalue(), ".parquet"
    if kind == "jsonl":
        lines = (json.dumps({k: v for k, v in r.items()}, default=str, ensure_ascii=False)
                 for r in df.to_dict(orient="records"))
        return ("\n".join(lines) + "\n").encode(), ".json"
    raise ValueError(f"unknown format {kind!r}")
