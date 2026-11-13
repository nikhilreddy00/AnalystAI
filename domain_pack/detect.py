"""What the data is: which pack fits, each column's role, each row's kind (sale, return, test …)."""
from __future__ import annotations

import re

import pandas as pd

from . import pack as packs
from .pack import (Pack, Role, _day, as_text, expand_header, normalize_header, pack_for, snapshot_role,
                   words)


# ── Detection ─────────────────────────────────────────────────────────────────────────────────

def _fits(s: pd.Series, role: Role, pack: Pack, is_date: bool, header: str = "") -> bool:
    vals = s.dropna()
    if vals.empty:
        return False
    if role.pattern:   # a capped sample of short values: a pack regex never runs on a whole upload
        text = as_text(vals.head(1000))
        if text.str.len().gt(40).any() or text.str.fullmatch(role.pattern).mean() < 0.9:
            return False
    numeric = pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s)
    if not numeric and role.kind in ("money", "count", "rate"):
        # "£2.55": text at load, a number after Cleaning's type_coerce (the same converter)
        from agents.data_cleaning import try_numeric_conversion
        if (converted := try_numeric_conversion(vals)) is not None:
            vals, numeric = converted[0].dropna(), True
    match role.kind:
        case "money" | "number":
            return numeric
        case "count":
            return numeric and (vals.round() == vals).mean() >= 0.95
        case "rate":   # a declared scale is read as declared (columns.rate_values says what's off)
            return numeric and (header in role.scales or bool(vals.between(0, 1).all()))
        case "date":
            return is_date
        case "status":
            known = set(words(pd.Series([v for vs in pack.status_values.values() for v in vs])))
            return words(vals).isin(known).mean() >= 0.5
        case "currency":   # "usd" as well as "USD"
            return vals.astype(str).str.strip().str.upper().str.fullmatch(r"[A-Z]{3}").mean() >= 0.9
        case "category" | "text":   # a code written as a number (470) fits when its pattern does
            return not numeric or bool(role.pattern)
    return True   # id


def one_series(df: pd.DataFrame, pack: Pack, roles: dict[str, str], level: str) -> bool:
    """No snapshot_key column, and `level` moves by its row's flows on 90% of rows in time order
    (to the cent): the file is one account's series (a bank statement), so its latest row is
    the balance. A file of many accounts never chains this way."""
    rb = pack.running_balance
    date = roles.get(pack.date_role)
    flows = [r for r in [*rb.plus, *rb.minus] if r in roles] if rb else []
    if not (rb and level in rb.levels and flows and date) or pack.snapshot_key in roles:
        return False
    from agents.utils import _in_time_order
    t = pd.to_datetime(df[date], format="mixed", errors="coerce")
    d = _in_time_order(df.loc[t.notna()].assign(_t=t[t.notna()]))
    num = lambda r: pd.to_numeric(d[roles[r]], errors="coerce").fillna(0)   # noqa: E731
    move = sum(num(r) for r in rb.plus if r in roles) - sum(num(r).abs() for r in rb.minus if r in roles)
    bal = pd.to_numeric(d[roles[level]], errors="coerce")
    steps = (bal.diff() - move).abs().iloc[1:]
    return len(steps) >= 3 and bool((steps <= 0.01).mean() >= 0.9)


def _panel(df: pd.DataFrame, pack: Pack, roles: dict[str, str]) -> bool:
    """The pack's strong_panel roles, and one row per snapshot_key per date on 3+ dates each."""
    key, date = roles.get(pack.snapshot_key), roles.get(pack.date_role)
    if not (pack.strong_panel and key and date and all(r in roles for r in pack.strong_panel)):
        return False
    pairs = df[[key, date]].dropna()
    return not pairs.duplicated().any() and _dates_each(df, [key], date) >= 3


def _dates_each(df: pd.DataFrame, keys: list[str], date: str) -> float:
    """THE repeat test of dated rows: the median number of distinct dates per key (0: none)."""
    pairs = df[[*keys, date]].dropna().drop_duplicates()
    return float(pairs.groupby(keys).size().median()) if len(pairs) else 0.0


def _snapshot_dates(df: pd.DataFrame, pack: Pack, roles: dict[str, str], header: str | None) -> bool:
    """Whether a spell pack's snapshot role column holds snapshot dates. A header only the role
    means (month, snapshot_date) is trusted, one snapshot included; a weak one (Role.weak_headers:
    a list's next billing_date) needs each spell (its entity and start, when the file has one) on
    2+ of its dates. Review: a list with a next-billing date was read as snapshots (MRR $476k for
    $869k); dropping the role on row shape alone also dropped real one-month snapshots."""
    snap = snapshot_role(pack) if pack.spell else None
    if snap not in roles or header not in pack.roles[snap].weak_headers or pack.spell.entity not in roles:
        return True
    keys = [roles[r] for r in (pack.spell.entity, pack.spell.start) if r in roles]
    return _dates_each(df, keys, roles[snap]) >= 2


def _matches(alts: list[dict[str, list[str]]], roles: dict[str, str]) -> bool:
    return any(all(any(x in roles for x in group) for group in alt.values()) for alt in alts)


def detect(df: pd.DataFrame, date_columns: set[str] = frozenset(),
           pii_columns: set[str] = frozenset()) -> tuple[str | None, dict[str, str], str | None]:
    """(domain, {role: column}, "strong" | "weak") when exactly one pack matches (Pack.strong:
    shapes only that domain has; Pack.signals: weaker ones); else (None, {}, None).
    Roles are filled in the pack's order, each by the column whose header comes earliest in the
    role's list (Square's `Net Sales` over `Gross Sales`) and whose values fit; a column serves
    one role; personal-data columns are never an id role (a `customer` column of names)."""
    found = []
    norm = {str(c): normalize_header(c) for c in df.columns}
    per_header = [set(n.split("_")) for n in norm.values()]
    tokens = set().union(*per_header) if per_header else set()
    for pack in packs.load_packs():   # through the module: a test's patched loader reaches it
        if any(set(v) <= h for v in pack.exclude_headers for h in per_header) or \
                any(set(v) <= tokens for v in pack.exclude_pairs):
            continue
        roles: dict[str, str] = {}
        matched: dict[str, str] = {}   # role → the header that filled it
        expanded = {c: expand_header(n, pack) for c, n in norm.items()}
        for role, r in pack.roles.items():
            for header in r.headers:
                col = next((c for c, n in expanded.items() if n == header and c not in roles.values()
                            and not (r.kind == "id" and c in pii_columns)
                            and _fits(df[c], r, pack, c in date_columns, header)), None)
                if col is not None:
                    roles[role], matched[role] = col, header
                    break
        snap = snapshot_role(pack) if pack.spell else None
        if snap and not _snapshot_dates(df, pack, roles, matched.get(snap)):
            roles.pop(snap)   # a list's next billing date: no snapshot
        if _matches(pack.strong, roles) or _panel(df, pack, roles):
            found.append((pack.domain, roles, "strong"))
        elif _matches(pack.signals, roles):
            found.append((pack.domain, roles, "weak"))
    # a shape only one domain has beats another domain's weaker match; two equal: neither
    best = [f for f in found if f[2] == "strong"] or found
    if len(best) > 1:
        from llm.client import trace_verdict
        trace_verdict("ingestion", "pack", "domain_ambiguous")
    return best[0] if len(best) == 1 else (None, {}, None)


# ── Row kinds and KPIs ────────────────────────────────────────────────────────────────────────

def row_kinds(df: pd.DataFrame, roles: dict[str, str], pack: Pack) -> pd.Series:
    """Each row's kind. Later rules win: test > cancel > non-product > refunded > return > sale.
    A return is a negative quantity/amount or a credit note; refunded is an order whose status
    says the sale was reversed (its lines stay positive, so it is left out, never netted)."""
    kind = pd.Series("sale", index=df.index, dtype=object)
    for role in pack.negative_is_return:
        if role in roles:
            kind[pd.to_numeric(df[roles[role]], errors="coerce") < 0] = "return"
    for role, prefix in pack.credit_prefix.items():
        if role in roles:   # "C536379" beside "536365": the prefix on an otherwise plain number;
            ids = df[roles[role]].astype(str).str.strip().str.upper()   # never "CA-2025-152156"
            credit = ids.str.fullmatch(rf"{re.escape(prefix.upper())}\d+")
            if ids[~credit].str.fullmatch(r"\d+").mean() >= 0.5:
                kind[credit] = "return"
    if "status" in roles:   # a reversed sale (positive lines); a negative line stays a netted return
        status = words(df[roles["status"]])
        kind[status.isin(set(words(pd.Series(pack.status_values.get("return", [])))))
             & kind.eq("sale")] = "refunded"
    for role, values in pack.non_product.items():
        if role in roles:
            vals = df[roles[role]].astype(str).str.strip().str.upper()
            kind[vals.isin([v.upper() for v in values])] = "non_product"
    if "status" in roles:
        kind[status.isin(set(words(pd.Series(pack.status_values.get("cancel", [])))))] = "cancel"
    kind = _versions(df, roles, pack, kind)
    if pack.test_marker:
        for role in pack.test_roles:
            if role in roles:   # short values only: a pack regex never runs on long text
                text = df[roles[role]].astype(str).str.strip()
                hit = text.str.len().le(80) & text.str.contains(
                    pack.test_marker, case=False, regex=True, na=False)
                kind[hit] = "test"
    return kind


def _versions(df: pd.DataFrame, roles: dict[str, str], pack: Pack, kind: pd.Series) -> pd.Series:
    """The version-chain rule (Pack.version_key): earlier versions → non_product; a chain whose
    last version is a void → cancel. Off unless the pack declares it and the data has the roles."""
    # the key roles the data has, the first (a claim id) required: a header-level extract (no
    # line column) chains on the claim alone
    keys = [roles[r] for r in pack.version_key if r in roles]
    if not pack.version_key or pack.version_key[0] not in roles or pack.version_order not in roles:
        return kind
    # a blank or non-numeric version (an original claim's empty adjustment number) is the
    # earliest: a chain without any version keeps all its rows. A date orders by time (a
    # snapshot's valuation date), never by its digits.
    col = df[roles[pack.version_order]]
    if pack.roles[pack.version_order].kind == "date":
        order = _day(col).rank(method="dense").fillna(-1)
    else:
        order = pd.to_numeric(col, errors="coerce").fillna(-1)
    chain = df.groupby(keys, dropna=False, sort=False).ngroup()
    last = order.eq(order.groupby(chain).transform("max"))
    kind = kind.mask(~last, "non_product")
    void = pd.Series(False, index=df.index)
    for role, values in pack.version_void.items():
        if role in roles:   # THE value comparison (words): '8', 8 and 8.0 are one void code
            void |= words(df[roles[role]]).isin(set(words(pd.Series(values, dtype=str))))
    voided = (void & last).groupby(chain).transform("any")
    return kind.mask(voided, "cancel")


def superseded(df: pd.DataFrame, contract) -> pd.Series:
    """Rows an earlier version of a version chain (a claim's earlier valuation, a replaced claim
    line): never counted or shown. All False without a chain."""
    pack = pack_for(contract.domain) if contract and contract.domain else None
    roles = {r: c for r, c in (contract.roles if contract else {}).items() if c in df.columns}
    base = pd.Series("sale", index=df.index, dtype=object)
    return (_versions(df, roles, pack, base) == "non_product") if pack else base.ne("sale")


def main_currency(df: pd.DataFrame, roles: dict[str, str]) -> tuple[pd.Series, str | None, int]:
    """(rows in the main currency, that currency, rows left out). One currency: every row."""
    if "currency" not in roles:
        return pd.Series(True, index=df.index), None, 0
    cur = df[roles["currency"]].astype(str).str.strip().str.upper()
    code = cur.str.fullmatch(r"[A-Z]{3}") & df[roles["currency"]].notna()
    counts = cur[code].value_counts()   # only real codes: a cell of text is never "the currency"
    if counts.empty:
        return pd.Series(True, index=df.index), None, 0
    main = str(counts.index[0])
    keep = cur.eq(main) | df[roles["currency"]].isna()
    return keep, main, int((~keep).sum())
