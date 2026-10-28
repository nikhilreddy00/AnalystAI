"""
Dirty tables for the cleaning eval (docs/ROADMAP.md Cleaning row).

`make(schema, seed, issues)` returns (dirty, expected, manifest):
- `dirty` is what the user uploads. It goes through CSV like the real pipeline. Row labels
  0..n-1 are the clean base rows; injected duplicate and blank rows are appended after them.
- `expected` is what a correct cleaner returns, indexed by the dirty frame's row labels.
- `manifest` lists one entry per planted issue: {issue, column, rows}.

Every base table also carries over-cleaning traps:
- a code column with leading zeros that must stay text;
- rows that differ only by their ID, which are distinct records, not duplicates;
- distinct labels that a too-eager normaliser would merge (bank: "A-1" vs "A1").

The expected values follow the cleaning policy: missing numbers, IDs and dates stay missing,
missing labels become "Unknown", "12.5%" is 0.125, outliers are kept, exact duplicate and
fully blank rows are removed.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

N = 400

SCHEMAS = {
    "orders": {
        "id": "order_id", "code": "store_code", "date": "order_date", "target": "revenue",
        "metric": "units", "rate": "discount", "money": "revenue", "variants": "region",
        "sentinel": "channel", "category": "channel", "optional": "promo",
    },
    "bank": {
        "id": "txn_id", "code": "account_code", "date": "txn_date", "target": "amount",
        "metric": "fee", "rate": "interest_rate", "money": "balance", "variants": "branch",
        "sentinel": "txn_type", "category": "product", "optional": "memo",
    },
}


def _base(schema: str, rng: np.random.Generator) -> tuple[pd.DataFrame, list[int]]:
    days = pd.date_range("2025-11-01", "2026-10-31", freq="D")
    dates = pd.DatetimeIndex(rng.choice(days, size=N)).strftime("%Y-%m-%d")
    # 30 codes, 1 in 20 with a letter prefix: read_csv keeps the column as text, so only the
    # cleaner can strip the leading zeros (the trap).
    codes = [f"S{n:04d}" if i % 20 == 0 else f"{n:04d}"
             for i, n in enumerate(rng.integers(1, 999, size=30))]
    code = rng.choice(codes, size=N)
    if schema == "orders":
        df = pd.DataFrame({
            "order_id": np.arange(5000, 5000 + N),
            "store_code": code,
            "order_date": dates,
            "region": rng.choice(["North", "South", "East", "West"], size=N),
            "channel": rng.choice(["Online", "Retail"], size=N),
            "revenue": rng.uniform(1000, 5000, size=N).round(2),
            "units": rng.integers(1, 9, size=N),
            "discount": rng.uniform(0, 0.3, size=N).round(3),
        })
    else:
        df = pd.DataFrame({
            "txn_id": np.arange(90_000, 90_000 + N),
            "account_code": code,
            "txn_date": dates,
            "branch": rng.choice(["Downtown", "Airport", "Harbor", "Uptown", "Mill Creek"], size=N),
            "txn_type": rng.choice(["Debit", "Credit"], size=N),
            "product": rng.choice(["A-1", "A1", "B-2"], size=N),
            "amount": rng.uniform(1000, 9000, size=N).round(2),
            "fee": rng.uniform(1, 25, size=N).round(2),
            "interest_rate": rng.uniform(0.01, 0.2, size=N).round(4),
            "balance": rng.uniform(1000, 90_000, size=N).round(2),
        })
    # Trap: 5 records identical to another except the ID (two real orders with the same values).
    idc = SCHEMAS[schema]["id"]
    src, dst = rng.choice(N, size=10, replace=False).reshape(2, 5)
    for s, d in zip(src, dst):
        keep = df.at[d, idc]
        df.loc[d] = df.loc[s]
        df.at[d, idc] = keep
    return df, sorted(int(r) for r in (*src, *dst))


def _rows(rng: np.random.Generator, share: float, taken: set[int]) -> list[int]:
    """Distinct base rows, never reused by another cell issue, so issues don't overlap."""
    free = np.array(sorted(set(range(N)) - taken))
    rows = sorted(int(r) for r in rng.choice(free, size=max(1, int(N * share)), replace=False))
    taken.update(rows)
    return rows


def make(schema: str, seed: int, issues: list[str]) -> tuple[pd.DataFrame, pd.DataFrame, list[dict]]:
    rng = np.random.default_rng(seed)
    roles = SCHEMAS[schema]
    base, near_dups = _base(schema, rng)
    manifest: list[dict] = []
    taken: set[int] = set(near_dups)   # cell issues never touch the near-duplicate trap rows

    def plant(issue: str, column: str, rows: list[int]) -> None:
        manifest.append({"issue": issue, "column": column, "rows": rows})

    if "outliers" in issues:   # into the base first: 4 extreme values, flagged and never removed
        c, rows = roles["target"], _rows(rng, 0.01, taken)
        base.loc[rows, c] *= 8
        plant("outliers", c, rows)
    dirty = base.astype(object)
    expected = base.astype(object)
    expected[roles["date"]] = pd.to_datetime(base[roles["date"]])

    for issue in issues:
        if issue == "currency":                  # "$1,234.50" for every value
            c = roles["money"]
            dirty[c] = pd.Series([f"${v:,.2f}" for v in base[c]], dtype=object)
            plant(issue, c, list(range(N)))
        elif issue == "thousands":               # "1,234.5" for every value
            c = roles["money"]
            dirty[c] = pd.Series([f"{v:,.2f}" for v in base[c]], dtype=object)
            plant(issue, c, list(range(N)))
        elif issue == "percent":                 # 0.125 written as "12.5%"
            c = roles["rate"]
            dirty[c] = pd.Series([f"{v * 100:g}%" for v in base[c]], dtype=object)
            plant(issue, c, list(range(N)))
        elif issue == "mixed_dates":             # three unambiguous formats in one column
            c = roles["date"]
            d = pd.to_datetime(base[c])
            fmt = rng.choice(["%Y-%m-%d", "%b %d, %Y", "%d %B %Y"], size=N)
            dirty[c] = pd.Series([x.strftime(f) for x, f in zip(d, fmt)], dtype=object)
            plant(issue, c, list(range(N)))
        elif issue == "sentinels":               # placeholder text meaning "missing"
            c, rows = roles["sentinel"], _rows(rng, 0.06, taken)
            dirty.loc[rows, c] = rng.choice(["-", "?", "--", "N/A"], size=len(rows))
            expected.loc[rows, c] = "Unknown"
            plant(issue, c, rows)
        elif issue == "variants":                # spacing / case variants of the same label
            c, rows = roles["variants"], _rows(rng, 0.15, taken)
            dirty.loc[rows, c] = [rng.choice([f" {v}", f"{v} ", v.upper(), v.lower()])
                                  for v in base.loc[rows, c]]
            plant(issue, c, rows)
        elif issue == "junk_numeric":            # a few non-numbers in a numeric column
            c, rows = roles["metric"], _rows(rng, 0.04, taken)
            dirty[c] = base[c].astype(str).astype(object)
            dirty.loc[rows, c] = rng.choice(["TBD", "n.a.", "pending"], size=len(rows))
            expected.loc[rows, c] = np.nan
            plant(issue, c, rows)
        elif issue in ("target_nulls", "metric_nulls", "id_nulls", "date_nulls"):
            c = roles[{"target_nulls": "target", "metric_nulls": "metric",
                       "id_nulls": "id", "date_nulls": "date"}[issue]]
            rows = _rows(rng, 0.08 if issue != "id_nulls" else 0.02, taken)
            dirty.loc[rows, c] = np.nan
            expected.loc[rows, c] = pd.NaT if c == roles["date"] else np.nan
            plant(issue, c, rows)
        elif issue == "category_nulls":
            c, rows = roles["category"], _rows(rng, 0.07, taken)
            dirty.loc[rows, c] = np.nan
            expected.loc[rows, c] = "Unknown"
            plant(issue, c, rows)
        elif issue == "high_null":               # an optional field, mostly empty: left alone
            c = roles["optional"]
            vals = rng.choice(["SPRING10", "VIP", "WELCOME"], size=N).astype(object)
            vals[rng.random(N) < 0.6] = np.nan
            dirty[c] = vals
            expected[c] = vals
            plant(issue, c, [int(i) for i in np.flatnonzero(pd.isna(vals))])
        elif issue not in ("exact_dups", "blank_rows", "outliers"):
            raise ValueError(f"unknown issue {issue!r}")

    if "mixed_dates" not in issues:              # ISO strings still have to become dates
        plant("iso_dates", roles["date"], list(range(N)))
    manifest.append({"issue": "near_duplicates", "column": "*", "rows": near_dups, "trap": True})

    # Row issues last, appended after the base rows, so their labels are n, n+1, …
    if "exact_dups" in issues:
        src = sorted(int(r) for r in rng.choice(N, size=12, replace=False))
        start = len(dirty)
        dirty = pd.concat([dirty, dirty.loc[src]], ignore_index=True)
        plant("exact_dups", "*", list(range(start, len(dirty))))
    if "blank_rows" in issues:
        start = len(dirty)
        blank = pd.DataFrame([[np.nan] * dirty.shape[1]] * 6, columns=dirty.columns)
        dirty = pd.concat([dirty, blank], ignore_index=True)
        plant("blank_rows", "*", list(range(start, len(dirty))))
    return dirty, expected, manifest
