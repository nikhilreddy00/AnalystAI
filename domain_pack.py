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


class Term(_Strict):
    """One summand: sum / sum_abs of a role or a 2-role product or difference, nunique of a
    role, or another KPI; over the given row kinds (default: the KPI's)."""
    sum: str | list[str] | None = None
    sum_abs: str | list[str] | None = None
    nunique: str | None = None
    kpi: str | None = None
    rows: list[Literal["sale", "return", "refunded", "cancel", "test", "non_product"]] | None = None
    sign: Literal[1, -1] = 1
    optional: bool = False        # a missing role drops this term, not the KPI

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
        return [] if expr is None else [expr] if isinstance(expr, str) else [expr[0], expr[2]]


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
    signals: dict[str, list[str]]   # detection: a role from EVERY group (two independent signals)
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
    unsupported: dict[str, str] = Field(default_factory=dict)      # concept → what it needs
    caveats: dict[str, str] = Field(default_factory=dict)          # condition → text
    causal_triggers: list[str] = Field(default_factory=list)
    first_questions: list[str] = Field(default_factory=list)

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
                *self.test_roles, *(r for g in self.signals.values() for r in g)]
        errors += [f"unknown role {r}" for r in refs if r not in self.roles]
        if len(self.signals) < 2:
            errors.append("signals needs two groups (two independent signals)")
        names = [k.name for k in self.kpis]
        errors += [f"duplicate kpi {n}" for n in {n for n in names if names.count(n) > 1}]
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
        case "currency":
            return vals.astype(str).str.fullmatch(r"[A-Z]{3}").mean() >= 0.9
        case "category" | "text":
            return not numeric
    return True   # id


def detect(df: pd.DataFrame, date_columns: set[str] = frozenset(),
           pii_columns: set[str] = frozenset()) -> tuple[str | None, dict[str, str]]:
    """(domain, {role: column}) when exactly one pack's every signal group has a role in the
    data; else (None, {}). A column takes the first role whose header and values fit; personal-
    data columns are never an id role (a `customer` column of names)."""
    found = []
    for pack in load_packs():
        roles: dict[str, str] = {}
        for col in df.columns:
            for role, r in pack.roles.items():
                if role in roles or normalize_header(col) not in r.headers:
                    continue
                if r.kind == "id" and col in pii_columns:
                    continue
                if _fits(df[col], r, pack, col in date_columns):
                    roles[role] = str(col)
                    break
        if all(any(r in roles for r in group) for group in pack.signals.values()):
            found.append((pack.domain, roles))
    return found[0] if len(found) == 1 else (None, {})


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
        if role in roles:
            kind[df[roles[role]].astype(str).str.upper().str.startswith(prefix.upper())] = "return"
    if "status" in roles:
        status = df[roles["status"]].astype(str).str.strip().str.lower()
        kind[status.isin([v.lower() for v in pack.status_values.get("return", [])])] = "refunded"
    for role, values in pack.non_product.items():
        if role in roles:
            vals = df[roles[role]].astype(str).str.strip().str.upper()
            kind[vals.isin([v.upper() for v in values])] = "non_product"
    if "status" in roles:
        kind[status.isin([v.lower() for v in pack.status_values.get("cancel", [])])] = "cancel"
    if pack.test_marker:
        for role in pack.test_roles:
            if role in roles:
                hit = df[roles[role]].astype(str).str.strip().str.contains(
                    pack.test_marker, case=False, regex=True, na=False)
                kind[hit] = "test"
    return kind


def main_currency(df: pd.DataFrame, roles: dict[str, str]) -> tuple[pd.Series, str | None, int]:
    """(rows in the main currency, that currency, rows left out). One currency: every row."""
    if "currency" not in roles:
        return pd.Series(True, index=df.index), None, 0
    cur = df[roles["currency"]].astype(str).str.strip().str.upper()
    counts = cur[df[roles["currency"]].notna()].value_counts()
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
    if term.nunique:
        col = part[roles[term.nunique]]
        return col.groupby(part[by]).nunique() if by else col.nunique()
    expr = term.sum if term.sum is not None else term.sum_abs
    if _order_level(df, roles, pack, expr):
        part = part.drop_duplicates(subset=[roles["order_id"]])
    values = _column(part, roles, expr)
    if term.sum_abs is not None:
        values = values.abs()
    return values.groupby(part[by]).sum(min_count=1) if by else values.sum(min_count=1)


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
                by: str | None = None):
    """A KPI's value (or a Series per `by` group) on the main currency's rows; None when the data
    lacks its roles or a denominator is 0 — never a 0 or an infinity standing in for "unknown"."""
    kpi = pack.kpi(name)
    if kpi is None or df.empty:
        return None
    keep, _, _ = main_currency(df, roles)
    df = df[keep]
    value = _kpi_value(df, row_kinds(df, roles, pack), roles, pack, kpi, by)
    if value is None:
        return None
    if by:
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
