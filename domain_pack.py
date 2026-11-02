"""
Domain packs (Phase 5.7): a domain's knowledge as data, one YAML file per domain in domain_packs/.
A pack names the domain's column roles by their header variants, its KPIs as structured formulas,
its glossary, its sentinels and caveat text. The code here is domain-neutral: detection, the row
kinds (sale / return / refunded / cancel / test / non-product), the KPI arithmetic. Adding a
domain is a YAML file, never an agent edit. An invalid pack stops the API and the worker at boot.
"""
from __future__ import annotations

import re
from functools import cache
from pathlib import Path
from typing import Literal

import pandas as pd
import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

PACKS = Path(__file__).parent / "domain_packs"
ROW_KINDS = ("sale", "return", "refunded", "cancel", "test", "non_product")
ROW_KIND_COLUMN = "row_kind"   # each row's kind, in the frame analysis code gets
# The caveat conditions code can check (pack_caveats); a pack writes the words for each.
CONDITIONS = ("read_as_domain", "read_as_domain_weak", "repeated_rows_kept", "partial_last_period", "returns_present", "refunded_excluded", "cancelled_excluded",
              "months_lt_13", "mixed_currency", "test_excluded", "non_product_excluded")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Role(_Strict):
    headers: list[str]
    # How the values must look for a header match to count (a `discount` of 0.1 is a rate, of
    # 12.50 an amount): money/count numeric (count whole), rate within 0–1, status known words.
    kind: Literal["money", "count", "rate", "id", "category", "date", "status", "currency", "text"]
    # "order": one value per order repeated on its lines (Shopify's discount); summed once per
    # order when it is constant within every order.
    level: Literal["row", "order"] = "row"
    pattern: str | None = None    # the values' shape (an order number), checked on 90% of them
    # False for a per-unit amount (a unit price, a unit cost): never summed across rows.
    additive: bool = True


class Term(_Strict):
    """One summand: sum / sum_abs of a role or a 2-role product or difference, nunique of a
    role, or another KPI; over the given row kinds (default: the KPI's)."""
    sum: str | list[str] | None = None
    sum_abs: str | list[str] | None = None
    nunique: str | None = None
    kpi: str | None = None
    rows: list[Literal["sale", "return", "refunded", "cancel", "test", "non_product"]] | None = None
    sign: Literal[1, -1] = 1
    optional: bool = False        # a missing role drops this term (and a blank value counts 0)
    net_of: str | None = None     # a rate role: each row's value × (1 − rate) (a 25% line discount)

    @model_validator(mode="after")
    def _one(self) -> Term:
        if sum(x is not None for x in (self.sum, self.sum_abs, self.nunique, self.kpi)) != 1:
            raise ValueError("a term needs exactly one of sum, sum_abs, nunique, kpi")
        expr = self.sum if self.sum is not None else self.sum_abs
        if isinstance(expr, list) and (len(expr) != 3 or expr[1] not in ("*", "-")):
            raise ValueError(f"a term's expression is [role, '*' or '-', role], not {expr}")
        return self

    def roles(self) -> list[str]:
        expr = self.sum if self.sum is not None else self.sum_abs
        if self.nunique:
            return [self.nunique]
        own = [] if expr is None else [expr] if isinstance(expr, str) else [expr[0], expr[2]]
        return own + ([self.net_of] if self.net_of else [])


class KPI(_Strict):
    name: str
    label: str
    # Alternatives in order: the first whose (non-optional) roles the data has is used.
    num: list[list[Term]]
    den: list[list[Term]] | None = None
    rows: list[Literal["sale", "return", "refunded", "cancel", "test", "non_product"]] = ["sale"]
    unit: Literal["money", "percent", "count", "number"] = "number"
    tile: bool = False            # one of the domain's KPI tiles (when computable)


class Pack(_Strict):
    domain: str
    # Detection: the data is this domain when, for one alternative, every group has a role in it
    # (independent signals: a product AND a money column, or an orders export's own shape) and
    # no header carries a veto token (another domain's word: patient, claim, ticker, vendor …).
    signals: list[dict[str, list[str]]]
    strong: list[dict[str, list[str]]] = Field(default_factory=list)   # shapes only this domain has
    exclude_headers: list[list[str]] = Field(default_factory=list)   # words, all in one header
    exclude_pairs: list[list[str]] = Field(default_factory=list)     # words, each in any header
    week_ends: str = "SUN"          # a calendar week's last day for weekly buckets of daily data
    roles: dict[str, Role]
    status_values: dict[Literal["ok", "return", "cancel"], list[str]] = Field(default_factory=dict)
    credit_prefix: dict[str, str] = Field(default_factory=dict)   # role → prefix ("C536379")
    non_product: dict[str, list[str]] = Field(default_factory=dict)   # role → values (POST)
    test_marker: str | None = None  # regex on the raw order / customer / email values
    test_roles: list[str] = Field(default_factory=list)
    sentinels: dict[str, list[float]] = Field(default_factory=dict)   # role → codes for "unknown"
    ranges: dict[str, dict[Literal["gt", "ge", "lt", "le"], float]] = Field(default_factory=dict)
    # Fields an export writes on an order's first line only (Shopify): carried down the order.
    order_fields: list[str] = Field(default_factory=list)
    kpis: list[KPI] = Field(default_factory=list)
    glossary: dict[str, list[str]] = Field(default_factory=dict)   # KPI or concept → its words
    definitions: dict[str, str] = Field(default_factory=dict)      # a non-KPI concept → its rule
    consumed: list[str] = Field(default_factory=list)   # "sales tax": uses its words, means none
    unsupported: dict[str, str] = Field(default_factory=dict)      # concept → what it needs
    caveats: dict[str, str] = Field(default_factory=dict)          # condition → text
    causal_triggers: list[str] = Field(default_factory=list)
    # Plain text, or {q, needs: [KPI…]}: offered only when the data supports what it needs
    first_questions: list[str | dict[str, str | list[str]]] = Field(default_factory=list)

    def kpi(self, name: str) -> KPI | None:
        return next((k for k in self.kpis if k.name == name), None)

    @model_validator(mode="after")
    def _check(self) -> Pack:
        errors, seen = [], {}
        for role, r in self.roles.items():
            for h in r.headers:
                if normalize_header(h) != h:
                    errors.append(f"role {role}: header {h!r} is not normalized "
                                  f"({normalize_header(h)!r})")
                # one header may serve two roles only if their value checks tell them apart
                if (other := seen.get(h)) and self.roles[other].kind == r.kind:
                    errors.append(f"header {h!r} is in roles {other} and {role} of the same kind")
                seen.setdefault(h, role)
        refs = [*self.order_fields, *self.sentinels, *self.ranges, *self.credit_prefix, *self.non_product,
                *self.test_roles,
                *(r for alt in [*self.signals, *self.strong] for g in alt.values() for r in g)]
        errors += [f"unknown role {r}" for r in refs if r not in self.roles]
        if not self.signals or any(len(alt) < 2 for alt in [*self.signals, *self.strong]):
            errors.append("each signals alternative needs two groups (two independent signals)")
        for where, rx in [*((f"role {r}", x.pattern) for r, x in self.roles.items()),
                          ("test_marker", self.test_marker)]:
            try:
                re.compile(rx or "")
            except re.error as exc:
                errors.append(f"{where}: invalid regex {rx!r}: {exc}")
        errors += [f"caveat {c}: no such condition (one of {CONDITIONS})" for c in self.caveats
                   if c not in CONDITIONS]
        if "read_as_domain_weak" not in self.caveats:   # a weak match's only effect is this hedge
            errors.append("caveats: read_as_domain_weak is required")
        names = [k.name for k in self.kpis]
        errors += [f"glossary {c}: neither a KPI, a definition nor unsupported" for c in self.glossary
                   if c not in names and c not in self.definitions and c not in self.unsupported]
        errors += [f"duplicate kpi {n}" for n in {n for n in names if names.count(n) > 1}]
        if self.week_ends not in ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"):
            errors.append(f"week_ends {self.week_ends!r}: one of MON … SUN")
        errors += [f"first question {q!r}: needs a 'q'" for q in self.first_questions
                   if isinstance(q, dict) and not q.get("q")]
        errors += [f"kpi {k.name}: net_of {t.net_of} is not a rate role" for k in self.kpis
                   for alt in [*k.num, *(k.den or [])] for t in alt
                   if t.net_of and t.net_of in self.roles and self.roles[t.net_of].kind != "rate"]
        errors += [f"first question needs {n}: not a KPI, a role, a row kind or 'year'"
                   for q in self.first_questions if isinstance(q, dict)
                   for need in q.get("needs", []) for n in need.split("|")   # "sku|product": either
                   if n not in names and n not in self.roles and n not in ROW_KINDS and n != "year"]
        for k in self.kpis:
            for alt in [*k.num, *(k.den or [])]:
                for t in alt:
                    errors += [f"kpi {k.name}: unknown role {r}" for r in t.roles() if r not in self.roles]
                    if t.kpi and t.kpi not in names:
                        errors.append(f"kpi {k.name}: unknown kpi {t.kpi}")
            if self._cycle(k.name, ()):
                errors.append(f"kpi {k.name}: refers to itself")
        if errors:
            raise ValueError(f"invalid domain pack {self.domain}:\n- " + "\n- ".join(errors))
        return self

    def _cycle(self, name: str, path: tuple[str, ...]) -> bool:
        if name in path:
            return True
        k = self.kpi(name)
        return bool(k) and any(self._cycle(t.kpi, (*path, name)) for alt in [*k.num, *(k.den or [])]
                               for t in alt if t.kpi)


def normalize_header(name: str) -> str:
    """'Lineitem quantity' → lineitem_quantity; 'Net Sales ($)' → net_sales; 'NET_SALES' →
    net_sales. Units in brackets are dropped; case, spaces, dashes and dots don't matter."""
    name = re.sub(r"[(\[].*?[)\]]", " ", str(name)).lower()
    return re.sub(r"[^a-z0-9]+", "_", name).strip("_")


@cache
def load_packs() -> tuple[Pack, ...]:
    """Every pack in domain_packs/, loaded once (at boot by the API and the worker)."""
    return tuple(Pack.model_validate(yaml.safe_load(p.read_text()))
                 for p in sorted(PACKS.glob("*.yaml")))


def pack_for(domain: str | None) -> Pack | None:
    return next((p for p in load_packs() if p.domain == domain), None) if domain else None


# ── Detection ─────────────────────────────────────────────────────────────────────────────────

def _fits(s: pd.Series, role: Role, pack: Pack, is_date: bool) -> bool:
    vals = s.dropna()
    if vals.empty:
        return False
    if role.pattern:   # a capped sample of short values: a pack regex never runs on a whole upload
        text = vals.head(1000).astype(str)
        if text.str.len().gt(40).any() or text.str.fullmatch(role.pattern).mean() < 0.9:
            return False
    numeric = pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s)
    if not numeric and role.kind in ("money", "count", "rate"):
        # "£2.55": text at load, a number after Cleaning's type_coerce (the same converter)
        from agents.data_cleaning import try_numeric_conversion
        if (converted := try_numeric_conversion(vals)) is not None:
            vals, numeric = converted[0].dropna(), True
    match role.kind:
        case "money":
            return numeric
        case "count":
            return numeric and (vals.round() == vals).mean() >= 0.95
        case "rate":
            return numeric and bool(vals.between(0, 1).all())
        case "date":
            return is_date
        case "status":
            known = {v.lower() for vs in pack.status_values.values() for v in vs}
            return vals.astype(str).str.strip().str.lower().isin(known).mean() >= 0.5
        case "currency":   # "usd" as well as "USD"
            return vals.astype(str).str.strip().str.upper().str.fullmatch(r"[A-Z]{3}").mean() >= 0.9
        case "category" | "text":
            return not numeric
    return True   # id


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
    words = set().union(*per_header) if per_header else set()
    for pack in load_packs():
        if any(set(v) <= h for v in pack.exclude_headers for h in per_header) or \
                any(set(v) <= words for v in pack.exclude_pairs):
            continue
        roles: dict[str, str] = {}
        for role, r in pack.roles.items():
            for header in r.headers:
                col = next((c for c, n in norm.items() if n == header and c not in roles.values()
                            and not (r.kind == "id" and c in pii_columns)
                            and _fits(df[c], r, pack, c in date_columns)), None)
                if col is not None:
                    roles[role] = col
                    break
        if _matches(pack.strong, roles):
            found.append((pack.domain, roles, "strong"))
        elif _matches(pack.signals, roles):
            found.append((pack.domain, roles, "weak"))
    return found[0] if len(found) == 1 else (None, {}, None)


# ── Row kinds and KPIs ────────────────────────────────────────────────────────────────────────

def row_kinds(df: pd.DataFrame, roles: dict[str, str], pack: Pack) -> pd.Series:
    """Each row's kind. Later rules win: test > cancel > non-product > refunded > return > sale.
    A return is a negative quantity/amount or a credit note; refunded is an order whose status
    says the sale was reversed (its lines stay positive, so it is left out, never netted)."""
    kind = pd.Series("sale", index=df.index, dtype=object)
    for role in ("quantity", "revenue"):
        if role in roles:
            kind[pd.to_numeric(df[roles[role]], errors="coerce") < 0] = "return"
    for role, prefix in pack.credit_prefix.items():
        if role in roles:   # "C536379" beside "536365": the prefix on an otherwise plain number;
            ids = df[roles[role]].astype(str).str.strip().str.upper()   # never "CA-2025-152156"
            credit = ids.str.fullmatch(rf"{re.escape(prefix.upper())}\d+")
            if ids[~credit].str.fullmatch(r"\d+").mean() >= 0.5:
                kind[credit] = "return"
    if "status" in roles:   # a reversed sale (positive lines); a negative line stays a netted return
        status = df[roles["status"]].astype(str).str.strip().str.lower()
        kind[status.isin([v.lower() for v in pack.status_values.get("return", [])])
             & kind.eq("sale")] = "refunded"
    for role, values in pack.non_product.items():
        if role in roles:
            vals = df[roles[role]].astype(str).str.strip().str.upper()
            kind[vals.isin([v.upper() for v in values])] = "non_product"
    if "status" in roles:
        kind[status.isin([v.lower() for v in pack.status_values.get("cancel", [])])] = "cancel"
    if pack.test_marker:
        for role in pack.test_roles:
            if role in roles:   # short values only: a pack regex never runs on long text
                text = df[roles[role]].astype(str).str.strip()
                hit = text.str.len().le(80) & text.str.contains(
                    pack.test_marker, case=False, regex=True, na=False)
                kind[hit] = "test"
    return kind


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


def _column(df: pd.DataFrame, roles: dict[str, str], expr: str | list[str]) -> pd.Series:
    if isinstance(expr, str):
        return pd.to_numeric(df[roles[expr]], errors="coerce")
    a, op, b = (pd.to_numeric(df[roles[expr[0]]], errors="coerce"), expr[1],
                pd.to_numeric(df[roles[expr[2]]], errors="coerce"))
    return a * b if op == "*" else a - b


def _order_level(df: pd.DataFrame, roles: dict[str, str], pack: Pack, expr) -> bool:
    """A single order-level role (Shopify's discount) whose value is constant within every
    order: counted once per order. A per-line discount differs within an order: summed."""
    if not isinstance(expr, str) or pack.roles[expr].level != "order" or "order_id" not in roles:
        return False
    per = df.groupby(df[roles["order_id"]], dropna=True)[roles[expr]].nunique(dropna=False)
    return bool(len(per)) and int(per.max()) <= 1


def _term(df, kinds, roles, pack, kpi, term: Term, by):
    rows = kinds.isin(term.rows or kpi.rows)
    if term.kpi:
        return _kpi_value(df, kinds, roles, pack, pack.kpi(term.kpi), by)
    part = df[rows]
    # no rows of these kinds here (no returns this month, in this store) while the data has them
    # elsewhere: 0, and a group keeps its place (a 0% return rate ranks lowest); none anywhere in
    # the data (returns may not be exported at all): unknown
    if part.empty and not set(term.rows or kpi.rows) & kinds.attrs.get("present", set(kinds)):
        return None
    # the groups this KPI's rows have (a postage-only code or a test customer is no group at $0)
    counted = kinds.isin({"sale", "return", "refunded", *kpi.rows})
    groups = pd.unique(df.loc[counted, by].dropna()) if by else None
    if term.nunique:
        col = part[roles[term.nunique]]
        return col.groupby(part[by]).nunique().reindex(groups, fill_value=0) if by else col.nunique()
    if part.empty and not by:
        return 0.0
    expr = term.sum if term.sum is not None else term.sum_abs
    values = _column(part, roles, expr)
    if _order_level(df, roles, pack, expr):
        # One value per order on each of its lines: counted once. Grouped by anything inside an
        # order (a product, a category), each line takes its share of the order's line value.
        order = part[roles["order_id"]]
        weight = (_column(part, roles, ["quantity", "*", "unit_price"])
                  if {"quantity", "unit_price"} <= roles.keys() else pd.Series(1.0, index=part.index))
        weight = weight.where(weight > 0, 0).fillna(0)
        total = weight.groupby(order).transform("sum")
        n = order.groupby(order).transform("size")
        values = values * (weight / total).where(total > 0, 1 / n)
    if term.net_of:
        values = values * (1 - pd.to_numeric(part[roles[term.net_of]], errors="coerce").fillna(0))
    if term.sum_abs is not None:
        values = values.abs()
    if term.optional:   # a blank discount is no discount
        values = values.fillna(0)
    if by:
        return values.groupby(part[by]).sum(min_count=1).reindex(groups, fill_value=0)
    return values.sum(min_count=1)


def _side(df, kinds, roles, pack, kpi, alternatives, by):
    for alt in alternatives:
        usable = [t for t in alt if all(r in roles for r in t.roles())]
        if any(not t.optional and t not in usable for t in alt):
            continue
        total = None
        for t in usable:
            v = _term(df, kinds, roles, pack, kpi, t, by)
            if v is None:
                break
            v = v * t.sign
            total = v if total is None else (total.add(v, fill_value=0) if by else total + v)
        else:
            if total is not None:
                return total
    return None


def _kpi_value(df, kinds, roles, pack, kpi: KPI, by):
    num = _side(df, kinds, roles, pack, kpi, kpi.num, by)
    if num is None or kpi.den is None:
        return num
    den = _side(df, kinds, roles, pack, kpi, kpi.den, by)
    if den is None:
        return None
    if by:
        den = den.where(den != 0)
        return (num / den).dropna()
    return None if not den or pd.isna(den) else num / den


def compute_kpi(df: pd.DataFrame, roles: dict[str, str], pack: Pack, name: str,
                by: str | None = None, full: pd.DataFrame | None = None):
    """A KPI's value (or a Series per `by` group) on the main currency's rows; None when the data
    lacks its roles or a denominator is 0 — never a 0 or an infinity standing in for "unknown".
    `full`: the whole data when `df` is a period of it (a month with no returns has a 0% return
    rate when the data has returns at all)."""
    kpi = pack.kpi(name)
    if kpi is None or df.empty:
        return None
    keep, _, _ = main_currency(df, roles)
    df = df[keep]
    kinds = row_kinds(df, roles, pack)
    whole = kinds if full is None else row_kinds(full, roles, pack)
    kinds.attrs["present"] = set(whole.unique())
    value = _kpi_value(df, kinds, roles, pack, kpi, by)
    if value is None:
        return None
    if by:   # a group with no values is no group (a NaN would break the answer's JSON)
        value = value.dropna()
        return value if len(value) else None
    return None if pd.isna(value) else float(value)


_ROWS_TEXT = {"sale": "sales", "return": "returns", "refunded": "refunded orders",
              "cancel": "cancelled orders", "test": "test orders", "non_product": "non-product lines"}


def used_terms(pack: Pack, kpi: KPI, roles: dict[str, str], side: str = "num") -> list[Term]:
    """The terms of the alternative compute_kpi uses for this data (the first whose roles exist)."""
    for alt in getattr(kpi, side) or []:
        usable = [t for t in alt if all(r in roles for r in t.roles())]
        if all(t.optional or t in usable for t in alt):
            return usable
    return []


def computable(pack: Pack, name: str, roles: dict[str, str]) -> bool:
    """The data has what this KPI needs, through every KPI it refers to (AOV needs orders, which
    needs an order id)."""
    kpi = pack.kpi(name)

    def side(alts) -> bool:
        return any(all(t.optional or (all(r in roles for r in t.roles())
                                      and (not t.kpi or computable(pack, t.kpi, roles)))
                       for t in alt) for alt in alts)
    return kpi is not None and side(kpi.num) and (kpi.den is None or side(kpi.den))


def additive(pack: Pack, name: str) -> bool:
    """A sum of rows (net sales, units): adds up over weeks. A distinct count (orders, customers)
    does not: a month's customers are not 4 weeks of customers."""
    kpi = pack.kpi(name)
    return kpi.den is None and all(not t.nunique and (not t.kpi or additive(pack, t.kpi))
                                   for alt in kpi.num for t in alt)


def kpi_roles(pack: Pack, name: str) -> set[str]:
    """Every role a KPI can read, through the KPIs it refers to."""
    kpi = pack.kpi(name)
    out: set[str] = set()
    for alt in [*kpi.num, *(kpi.den or [])]:
        for t in alt:
            out |= set(t.roles()) | (kpi_roles(pack, t.kpi) if t.kpi else set())
    return out


def describe_kpi(pack: Pack, name: str, roles: dict[str, str],
                 present: set[str] = frozenset(ROW_KINDS)) -> str:
    """How a KPI was computed on this data, in words, from the alternative that applied. Only
    the row kinds the data has (`present`) are named as left out."""
    kpi = pack.kpi(name)

    def term(t: Term) -> str:
        if t.kpi:
            return pack.kpi(t.kpi).label.lower()
        if t.nunique:
            return f"distinct {roles[t.nunique]}"
        expr = t.sum if t.sum is not None else t.sum_abs
        cols = roles[expr] if isinstance(expr, str) else (
            f"{roles[expr[0]]} {'×' if expr[1] == '*' else '−'} {roles[expr[2]]}")
        once = " (once per order)" if isinstance(expr, str) and pack.roles[expr].level == "order" \
            and "order_id" in roles else ""
        return f"{'sum of |' + cols + '|' if t.sum_abs is not None else 'sum of ' + cols}{once}"

    def side(name_: str) -> str:
        terms = used_terms(pack, kpi, roles, name_)
        return " ".join(("less " if t.sign < 0 else ("plus " if i else "")) + term(t)
                        for i, t in enumerate(terms))

    text = side("num") + (f", divided by {side('den')}" if kpi.den else "")
    if any(not t.kpi for t in used_terms(pack, kpi, roles)):   # a ratio of KPIs says it there
        left_out = [_ROWS_TEXT[k] for k in ROW_KINDS if k not in kpi.rows and k in present]
        text += f"; over {' and '.join(_ROWS_TEXT[k] for k in kpi.rows)}"
        text += f" ({', '.join(left_out)} left out)" if left_out else ""
    return text[0].upper() + text[1:] + "."


def glossary_hits(pack: Pack, question: str, columns: list[str]) -> list[tuple[str, str]]:
    """[(concept, the words used)] for each glossary concept the question names as whole words,
    unless the words are just one of the data's own column names ("revenue" in a file with a
    revenue column needs no glossary)."""
    q = question.lower()
    own = {normalize_header(c) for c in columns}
    found = [(m.start(), m.end(), concept, w)
             for concept, words in [*pack.glossary.items(), (None, pack.consumed)]
             for w in words for m in re.finditer(rf"(?<![\w-]){re.escape(w.lower())}(?![\w-])", q)]
    taken: list[tuple[int, int]] = []
    hits: dict[str, str] = {}
    # the longest phrase consumes its words ("gross sales", "sales tax" never also mean "sales")
    for start, end, concept, w in sorted(found, key=lambda f: f[0] - f[1]):
        if any(start < e and s < end for s, e in taken):
            continue
        taken.append((start, end))
        if concept and normalize_header(w) not in own and concept not in hits:
            hits[concept] = w
    return list(hits.items())


def row_rules(pack: Pack, roles: dict[str, str], present: set[str] = frozenset(ROW_KINDS)) -> str:
    """Which rows are not plain sales, in the pack's words, for the kinds this data has."""
    rules = []
    if "return" in present and (any(r in roles for r in ("quantity", "revenue"))
                                or any(r in roles for r in pack.credit_prefix)):
        neg = " or ".join(r for r in ("quantity", "revenue") if r in roles)
        credit = [f"{r} starting {p!r}" for r, p in pack.credit_prefix.items() if r in roles]
        rules.append("a return (netted in net sales): " + " or ".join(
            ([f"a negative {neg}"] if neg else []) + credit))
    sv = pack.status_values
    if "status" in roles and "refunded" in present:
        rules.append(f"refunded (left out): status in {sv.get('return', [])}")
    if "status" in roles and "cancel" in present:
        rules.append(f"cancelled (left out): status in {sv.get('cancel', [])}")
    if "non_product" in present:
        rules += [f"non-product (left out): {r} in {v}" for r, v in pack.non_product.items() if r in roles]
    test = [r for r in pack.test_roles if r in roles]
    if pack.test_marker and test and "test" in present:
        rules.append(f"a test order (left out): {' or '.join(test)} matching {pack.test_marker!r}")
    return "; ".join(rules)


def pack_brief(pack: Pack, roles: dict[str, str], concepts: list[str], grain: str | None = None,
               present: set[str] = frozenset(), currency: str | None = None,
               kind_column: str = ROW_KIND_COLUMN) -> list[str]:
    """The pack's rules, in role names: which rows are not sales (the kinds `present` in this
    data), the grain, and each concept (a KPI or a definition) asked about. Roles, never column
    names: the caller shows the role → column map inside its untrusted-data block."""
    named = {r: r for r in roles}
    lines = []
    if present - {"sale"} or concepts:   # rows to leave out, or a KPI to compute
        lines.append(f"Each row's kind is in the column '{kind_column}' (sale, return, refunded, "
                     "cancel, test, non_product, other_currency), computed by these rules: filter on "
                     "it, never re-derive it. Personal columns (email, names) reach you as "
                     "pseudonyms, so test orders cannot be found from them: only this column marks "
                     "them. Sales figures use sale rows; net figures sale and return rows.")
    if rules := row_rules(pack, roles, present):
        lines.append(f"Row kinds: {rules}.")
    if currency and "currency" in roles:
        lines.append(f"Every figure uses {currency} rows only: rows in another currency are left "
                     "out (never added across currencies).")
    if grain == "line":
        lines.append("One row is an order line: count orders as distinct order_id, never rows; "
                     "an average per order divides by orders.")
    for c in dict.fromkeys(concepts):
        if kpi := pack.kpi(c):
            if computable(pack, c, roles):
                lines.append(f"{kpi.label} ({c}): {describe_kpi(pack, c, named, present)}")
            else:
                lines.append(f"{kpi.label} ({c}): this data lacks the columns it needs; say so, "
                             "never approximate it with another column.")
        elif c in pack.definitions:
            lines.append(f"{c}: {pack.definitions[c]}")
        elif c in pack.unsupported:
            lines.append(f"{c}: needs {pack.unsupported[c]}; it cannot be computed here.")
    return lines


def ordinal(n: int) -> str:
    """12 → '12th', 1 → '1st', 22 → '22nd'."""
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def pack_caveats(pack: Pack, audit, roles: dict[str, str], confidence: str | None = None,
                 always: bool = False) -> list[str]:
    """What the report must say, from the cleaning audit (the data's own facts): each caveat
    whose condition holds, in the pack's words. Deterministic: never left to the model."""
    if audit is None and not always:
        return []
    flags = {d.strategy: d for d in (audit.decisions if audit else []) if d.operation == "flag"}
    # the roles that made the match first (an order id, a date), in plain words, no headers
    deciding = [r for alt in [*pack.strong, *pack.signals] for g in alt.values() for r in g]
    ordered = sorted(roles, key=lambda r: deciding.index(r) if r in deciding else len(deciding))
    words = [r.replace("_", " ") for r in ordered[:5]]
    shown = ", ".join(words[:-1]) + (" and " if len(words) > 1 else "") + words[-1] if words else ""
    # as the tiles show them: a KPI of non-sale rows only (return rate) when the data has some
    kinds = {s for s in flags if s in ROW_KINDS}
    kpis = ", ".join(k.label.lower() for k in pack.kpis if k.tile and computable(pack, k.name, roles)
                     and ("sale" in k.rows or set(k.rows) & kinds))
    date = roles.get("date")
    span = audit.date_ranges.get(date) if date and audit else None
    facts: dict[str, dict] = {}
    if confidence == "weak":
        facts["read_as_domain_weak"] = {"roles": shown}
    elif confidence == "strong":
        facts["read_as_domain"] = {"kpis": kpis or "its sales columns"}
    if "repeated_rows" in flags:
        facts["repeated_rows_kept"] = {"n": f"{flags['repeated_rows'].rows_affected:,}"}
    if audit and date and (p := audit.partial_months.get(date)):
        facts["partial_last_period"] = {"label": pd.Period(p[0], "M").strftime("%b %Y"),
                                        "through": ordinal(p[1])}
    for cond, kind in (("returns_present", "return"), ("refunded_excluded", "refunded"),
                       ("cancelled_excluded", "cancel"), ("test_excluded", "test"),
                       ("non_product_excluded", "non_product")):
        if kind in flags:
            facts[cond] = {"n": f"{flags[kind].rows_affected:,}"}
    full = span[2] - bool(audit.partial_months.get(date)) if span else 0   # a partial month isn't one
    if 1 < full < 13:
        facts["months_lt_13"] = {"months": full}
    if "other_currency" in flags and audit and audit.main_currency:
        facts["mixed_currency"] = {"n": f"{flags['other_currency'].rows_affected:,}",
                                   "currency": audit.main_currency}
    return [pack.caveats[c].format(**facts[c]) for c in CONDITIONS if c in facts and c in pack.caveats]



def with_row_kind(df: pd.DataFrame, contract) -> tuple[pd.DataFrame, str | None]:
    """The frame with each row's kind as a column (sale, return, refunded, cancel, test,
    non_product), for code the model writes: it filters on the column, never re-derives the
    rules (the analysis smoke run kept 12 test orders it had been told to leave out). The name
    never replaces a column of the user's; no pack: the frame unchanged."""
    pack = pack_for(contract.domain) if contract is not None else None
    if pack is None:
        return df, None
    name = next(n for n in (ROW_KIND_COLUMN, f"_{ROW_KIND_COLUMN}", f"pack_{ROW_KIND_COLUMN}")
                if n not in df.columns)   # never a user's own column
    roles = {r: c for r, c in contract.roles.items() if c in df.columns}
    kinds = row_kinds(df, roles, pack)
    keep, _, _ = main_currency(df, roles)
    return df.assign(**{name: kinds.where(keep, "other_currency")}), name


def money_rows(df: pd.DataFrame, contract, column: str) -> tuple[pd.DataFrame, str]:
    """(rows, how) for a strong-matched pack's additive money column: sale and return rows in the
    main currency, as its tiles count them. The one filter every chart and answer of that column
    uses (one number per name); anything else: every row, no note."""
    pack = defaults_pack(contract)
    role = next((r for r, c in contract.roles.items() if c == column), None) if pack else None
    if not (role and pack.roles[role].kind == "money" and pack.roles[role].additive):
        return df, ""
    roles = {r: c for r, c in contract.roles.items() if c in df.columns}
    keep, currency, _ = main_currency(df, roles)
    kept = keep & row_kinds(df, roles, pack).isin(["sale", "return"])
    if kept.all():
        return df, ""
    return df[kept], (f"Sale and return rows{f' in {currency}' if currency else ''} only: "
                      f"{int((~kept).sum()):,} other rows (test, cancelled, other currencies …) left out.")


def defaults_pack(contract) -> Pack | None:
    """The pack whose conventions change default behaviour (tiles, sums by group, weekly months,
    partial weeks, follow-up checks): only on a strong match. A weak match changes nothing by
    default; it says how it read the file, and answers a KPI the user names."""
    if contract is None or contract.domain_confidence != "strong":
        return None
    return pack_for(contract.domain)


def first_questions(pack: Pack, roles: dict[str, str], df: pd.DataFrame | None = None) -> list[str]:
    """The pack's first questions this data can answer. `needs` names KPIs (computable: no AOV
    question without order ids), roles (a store column), row kinds (returns present) and "year"
    (13 full months, for a same-period-last-year question); the last two need the frame.
    "a|b" is either."""
    kinds = set(row_kinds(df, roles, pack).unique()) if df is not None else None

    def has(n: str) -> bool:
        if pack.kpi(n):
            return computable(pack, n, roles)
        if n in pack.roles:
            return n in roles
        if df is None:
            return True   # not knowable without the data: offered, as before
        if n == "year":
            t = pd.to_datetime(df[roles["date"]], errors="coerce").dropna() if "date" in roles else None
            if t is None or t.empty:
                return False
            lo, hi = t.min(), t.max()   # complete months: a few days' slack at either end
            months = (hi.to_period("M") - lo.to_period("M")).n + 1
            months -= (lo.day > 3) + (hi.normalize() < (hi + pd.offsets.MonthEnd(0)).normalize()
                                      - pd.Timedelta(days=3))
            return months >= 13
        return n in kinds
    return [q if isinstance(q, str) else str(q["q"]) for q in pack.first_questions
            if isinstance(q, str) or all(any(has(a) for a in n.split("|")) for n in q.get("needs", []))]
