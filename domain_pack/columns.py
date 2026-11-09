"""Pack columns: columns the product adds from a pack formula (a subscription list's MRR), and
the layout of blanks that are the file's shape, not missing values."""
from __future__ import annotations

import pandas as pd

from .pack import Pack, _column, first_line_only, pack_for, words


def filled_statuses(df: pd.DataFrame, pack: Pack, roles: dict[str, str], role: str) -> pd.Series:
    """The rows whose status says a conditional field is filled (a charge-off date on a
    charged-off loan); none when the data lacks the status or the field."""
    if role not in roles or "status" not in roles:
        return pd.Series(False, index=df.index)
    return words(df[roles["status"]]).isin(set(words(pd.Series(pack.conditional_fields[role]))))


def layout_columns(df: pd.DataFrame, pack: Pack | None, roles: dict[str, str]) -> dict[str, str]:
    """{column: "order" | "paired" | "conditional" | "marker"} for columns whose blanks are the file's layout,
    not missing values: written on an order's first line only (Shopify's status, total, email),
    one of a declared pair each row fills one of (Debit or Credit, on 95% of rows), or a
    declared conditional field filled only for some statuses (a charge-off date on charged-off
    loans, at least 95% of its values there). The one rule every place that reports blanks
    uses (ingestion's summary, cleaning, evidence notes, alerts, suggested questions)."""
    if pack is None:
        return {}
    out: dict[str, str] = {}
    key = roles.get(pack.order_key) if pack.order_key else None
    if key is not None and key in df.columns:
        out |= {c: "order" for c in df.columns if c != key and df[c].isna().any()
                and first_line_only(df, df[key], c)}
    for group in pack.paired_fields:   # each row fills one of them (Debit or Credit)
        cols = [roles[r] for r in group if roles.get(r) in df.columns]
        if len(cols) >= 2 and df[cols].isna().any().any() \
                and (df[cols].notna().sum(axis=1) == 1).mean() >= 0.95:
            out |= {c: "paired" for c in cols if c not in out and df[c].isna().any()}
    for role in pack.conditional_fields:
        col = roles.get(role)
        if col in df.columns and col not in out and df[col].isna().any():
            filled = df[col].notna()
            if filled.any() and (filled & filled_statuses(df, pack, roles, role)).sum() >= 0.95 * filled.sum():
                out[col] = "conditional"
    for col in marker_columns(pack, roles):   # blank = none (no event): the layout, never missing
        if col in df.columns and col not in out and df[col].isna().any():
            out[col] = "marker"
    return out


def marker_columns(pack: Pack | None, roles: dict[str, str]) -> list[str]:
    """The pack's marker-field columns (blank = no event): the one branch of layout_columns that
    needs no rows, so a contract alone can tell it (the no-frame fallback chart)."""
    return [roles[r] for r in (pack.marker_fields if pack else []) if r in roles]


def layout_blanks(df: pd.DataFrame, contract) -> list[str]:
    """The columns of layout_columns, for a contract."""
    pack = pack_for(contract.domain) if contract is not None else None
    return list(layout_columns(df, pack, {r: c for r, c in (contract.roles if pack else {}).items()
                                          if c in df.columns}))


def pack_columns(df: pd.DataFrame, pack: Pack | None, roles: dict[str, str]
                 ) -> tuple[pd.DataFrame, list[str]]:
    """(the data with each Pack.columns role it lacks, a note per row left blank). Raw values are
    parsed as cleaning parses numbers ('1,200'); a category reads its Role.numbers (an unknown
    one leaves the row blank, said). Needs every non-optional role; the user's own column of
    that role is never replaced."""
    from agents.data_cleaning import try_numeric_conversion
    if pack is None:
        return df, []
    notes: list[str] = []
    for r, f in pack.columns.items():
        name = pack.roles[r].headers[0]
        needed = [x for x in [*f.times, *f.over, *([f.net_of] if f.net_of else [])]
                  if x not in f.optional]
        if r in roles or name in df.columns or any(x not in roles for x in needed):
            continue

        def missing(role: str) -> pd.Series:   # blank or only spaces: no value given
            s = df[roles[role]]
            return s.isna() | (s.astype(str).str.strip() == "")

        def value(role: str, blank: float) -> pd.Series:
            if role not in roles:
                return pd.Series(blank, index=df.index)
            s, nums = df[roles[role]], pack.roles[role].numbers
            if nums:
                v = words(s).map({k.casefold(): x for k, x in nums.items()})
            else:
                parsed = try_numeric_conversion(s.astype(str).where(s.notna())) \
                    if pd.api.types.is_string_dtype(s) else None
                v = pd.to_numeric(parsed[0] if parsed else s, errors="coerce")
            # an optional role's blank takes its default; an unreadable value never does
            return v.mask(missing(role), blank) if role in f.optional else v

        out = pd.Series(1.0, index=df.index)
        for x in f.times:
            out = out * value(x, 1.0)
        if f.net_of:   # a rate: 0.2, or Stripe's percent_off 20 (any value over 1: percent)
            rate = value(f.net_of, 0.0)
            given = rate[rate > 0]
            # percent (Stripe's 20 or 12.5 = 20%, 12.5%): most values over 1, or some over 1 and
            # all whole numbers (1 = 1%); a rate (0.2) otherwise. Rows on the other scale: blank
            percent = bool(len(given)) and bool((given > 1).mean() > 0.5 or (
                (given > 1).any() and (given == given.round()).all()))
            ones = bool(len(given)) and bool((given == 1).all())
            if ones:   # 1 = 100% off or 1%: no scale to read it by
                notes.append(f"{name}: {roles[f.net_of]} holds only 1s: its scale (100% or 1%) is "
                             "unclear, so those rows are left blank.")
            wrong = (rate == 1) if ones else (rate > 1) if not percent else ((rate > 0) & (rate < 1))
            if (odd := int(wrong.sum())) and not ones:
                notes.append(f"{name}: {odd:,} row{'s' * (odd != 1)} of {roles[f.net_of]} disagree "
                             f"with the column's {'percent' if percent else 'rate'} scale: left blank.")
            if odd:
                out = out.where(~wrong)
            out = out * (1 - (rate / 100 if percent else rate))
        for x in f.over:
            out = out / value(x, 1.0)
        inputs = [x for x in [*f.times, *f.over, *([f.net_of] if f.net_of else [])] if x in roles]
        for x in inputs:   # every input: a required one's blank rows, then any unreadable ones
            raw = df[roles[x]].mask(missing(x))
            for bad, what in ((raw.isna() & (x not in f.optional), f"no {roles[x]}"),
                              (raw.notna() & value(x, 1.0).isna(),
                               f"a value of {roles[x]} the formula doesn't know")):
                if n := int(bad.sum()):
                    seen = "" if raw[bad].isna().all() else \
                        f" ({', '.join(sorted(map(str, raw[bad].unique()))[:3])})"
                    notes.append(f"{name}: {n:,} row{'s' * (n != 1)} {'have' if n != 1 else 'has'} "
                                 f"{what}{seen}: left blank.")
        df = df.assign(**{name: out})
    return df, notes


PACK_COLUMN_NOTE = "Added by the product from the {domain} pack's formula: {names}."


def formula_pairs(contract) -> set[frozenset]:
    """{column the product added, one of its formula's inputs} pairs: they move together by
    construction (MRR is computed from seats), so a correlation of them is no finding (Domain 6
    live run: 'revenue growth is tightly linked to adding seats')."""
    pack = pack_for(contract.domain) if contract is not None else None
    notes = (contract.source_evidence.assumptions if contract is not None and contract.source_evidence
             else [])
    head = PACK_COLUMN_NOTE.split("{names}")[0].format(domain=pack.domain) if pack else None
    added = {n.strip() for a in notes if head and a.startswith(head)
             for n in a[len(head):].rstrip(".").split(",")}
    out = set()
    for r, f in (pack.columns.items() if pack else []):
        col = contract.roles.get(r)
        if col in added:
            out |= {frozenset((col, contract.roles[x])) for x in [*f.times, *f.over,
                    *([f.net_of] if f.net_of else [])] if x in contract.roles}
    return out


def with_derived(df: pd.DataFrame, contract) -> tuple[pd.DataFrame, list[str]]:
    """The frame with each derivable role the data lacks (Pack.derived) as a column named after
    the role's first header (length_of_stay = discharge − admit in days), and the names added.
    Never replaces a column; no pack or nothing to derive: the frame unchanged."""
    pack = pack_for(contract.domain) if contract is not None else None
    if pack is None:
        return df, []
    roles = {r: c for r, c in contract.roles.items() if c in df.columns}
    added = []
    for r, expr in pack.derived.items():
        name = pack.roles[r].headers[0]
        if r in roles or name in df.columns or expr[0] not in roles or expr[2] not in roles:
            continue
        df = df.assign(**{name: _column(df, roles, expr)})
        added.append(name)
    return df, added
