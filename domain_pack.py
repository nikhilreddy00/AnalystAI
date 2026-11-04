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
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, model_validator

PACKS = Path(__file__).parent / "domain_packs"
ROW_KINDS = ("sale", "return", "refunded", "cancel", "test", "non_product")
ROW_KIND_COLUMN = "row_kind"
# Role names shared code may use: a role of these kinds is named after its kind in every pack.
RESERVED_ROLES = ("status", "currency")   # each row's kind, in the frame analysis code gets
# The caveat conditions code can check (pack_caveats); a pack writes the words for each.
CONDITIONS = ("read_as_domain", "read_as_domain_weak", "repeated_rows_kept", "partial_last_period", "returns_present", "refunded_excluded", "cancelled_excluded",
              "months_lt_13", "mixed_currency", "test_excluded", "non_product_excluded", "level_as_of",
              "recent_incomplete", "developing_periods", "developing_periods_undated")


RowKind = Literal["sale", "return", "refunded", "cancel", "test", "non_product"]
# A pack's own sentences; each has a domain-neutral default (TEXT_DEFAULTS).
TextKey = Literal["rows_brief", "grain_line", "unsupported_alternatives", "kpis_default",
                  "causal_removed", "return_rule", "refunded_rule", "cancel_rule",
                  "non_product_rule", "money_rows_note"]
TEXT_DEFAULTS = {   # fit every domain: a pack says its own words (tests/test_pseudo_pack.py)
    "rows_brief": "Figures use the rows these rules keep; net figures also the netted rows.",
    "grain_line": "One row is a line of a larger record: count records as distinct {key}, never "
                  "rows; an average per record divides by records.",
    "unsupported_alternatives": "I can answer with what the data has. Which would help?",
    "kpis_default": "its main columns",
    "causal_removed": "{n} sentence(s) saying an action caused a change were removed: without a "
                      "control group that can't be shown.",
    "return_rule": "netted (in net figures)",
    "refunded_rule": "reversed (left out)",
    "cancel_rule": "excluded (left out)",
    "non_product_rule": "not an item (left out)",
    "money_rows_note": "Only the rows the figures count{currency}: {n} other rows left out.",
}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Role(_Strict):
    headers: list[str]
    # How the values must look for a header match to count (a `discount` of 0.1 is a rate, of
    # 12.50 an amount): money/count numeric (count whole), rate within 0–1, status known words.
    # number: any numeric value (an interest rate of 5.25), never summed
    kind: Literal["money", "count", "rate", "number", "id", "category", "date", "status", "currency",
                  "text"]
    # "order": one value per order repeated on its lines (Shopify's discount); summed once per
    # order when it is constant within every order.
    level: Literal["row", "order"] = "row"
    pattern: str | None = None    # the values' shape (an order number), checked on 90% of them
    # False for a per-unit amount (a unit price, a unit cost): never summed across rows.
    additive: bool = True
    # "last": a level (a balance): adds across the snapshot_key's entities at one date, never
    # across dates; each entity's latest snapshot per period (the ColumnInfo level hint).
    over_time: Literal["sum", "last"] = "sum"


class Cond(_Strict):
    """One condition on a role's value: words (`in` / `not_in`, case, spaces, dashes and
    underscores ignored) or a number (`gt` … `le`)."""
    role: str
    in_: list[str] | None = Field(None, alias="in")
    not_in: list[str] | None = None
    gt: float | None = None
    ge: float | None = None
    lt: float | None = None
    le: float | None = None

    @model_validator(mode="after")
    def _one(self) -> Cond:
        if sum(x is not None for x in (self.in_, self.not_in, self.gt, self.ge, self.lt, self.le)) != 1:
            raise ValueError(f"condition on {self.role}: exactly one of in, not_in, gt, ge, lt, le")
        return self


class SignRule(_Strict):
    """An unsigned money role whose direction is another role's value (a Dr/Cr column)."""
    indicator: str
    negative: list[str]           # the indicator's values for money out (compared lower-case)


class RunningBalance(_Strict):
    """A level that moves by the flows on its row (a statement's Balance after Debit / Credit):
    with no snapshot_key column, a level that chains this way is ONE series (one account)."""
    levels: list[str]
    plus: list[str] = Field(default_factory=list)
    minus: list[str] = Field(default_factory=list)


class Runout(_Strict):
    """Records keep arriving after the period they belong to (claims paid months after service):
    with `role` (the arrival date) in the data, the last `months` months are incomplete."""
    role: str
    months: int = Field(gt=0)


class Maturity(_Strict):
    """Periods keep developing after they end (a year's figures still move at later valuations):
    each period year younger than `months` at the latest `role` date is named with its age.
    Nothing is cut: every period is shown, the young ones flagged. `measures`: the roles that
    develop (losses, claim counts); a period answer about anything else says nothing."""
    role: str
    months: int = Field(gt=0)
    measures: list[str] = Field(default_factory=list)
    # measures that develop only on these date headers (earned premium by policy effective date:
    # a policy-year view still earning; by a calendar month it is final)
    dated: dict[str, list[str]] = Field(default_factory=dict)


class NeverSay(_Strict):
    """A claim the domain's analyst never makes (a still-developing year's losses called final):
    the quality check flags a sentence matching `pattern` that doesn't hedge."""
    pattern: str
    reason: str


class Within(_Strict):
    """Rows followed by another row of the same entity starting (0, days] after this row's end
    (a 30-day readmission). Follow-ups are found anywhere in the data and must meet `follow`;
    a row whose window runs past the data's last start is censored (in neither count). `base`:
    count the uncensored rows instead (the rate's denominator)."""
    entity: str
    start: str
    end: str
    days: int = Field(gt=0)
    follow: list[list[Cond]] = Field(default_factory=list)
    base: bool = False


class Spell(_Strict):
    """Entities in force between two dates: an employee from hire to termination, a subscriber
    from start to cancellation. Rows are spells (a returning entity has a row per spell) or dated
    snapshots (the pack's date role, when it is neither `start` nor `end`). A blank `end` is open;
    a status in `ended` with a blank `end` is over at an unknown date (never in force). `as_of`:
    a report-date role (the extract's own date), else the latest snapshot or start/end date not
    after today."""
    entity: str
    start: str
    end: str
    ended: list[str] = Field(default_factory=list)
    as_of: str | None = None


class Term(_Strict):
    """One summand: sum / sum_abs of a role or a 2-role product or difference, nunique of a
    role, or another KPI; over the given row kinds (default: the KPI's). `spell` (the pack's
    spell): `active` = the entities in force at the as-of or each period's end (with `sum`: a
    role summed over them, one row each), `average` = the mean of the month-end active counts
    in the window or period, `starts` / `ends` = spells starting / ending in it (events)."""
    sum: str | list[str] | None = None
    sum_abs: str | list[str] | None = None
    nunique: str | None = None
    kpi: str | None = None
    within: Within | None = None
    spell: Literal["active", "average", "starts", "ends"] | None = None
    rows: list[Literal["sale", "return", "refunded", "cancel", "test", "non_product"]] | None = None
    sign: Literal[1, -1] = 1
    optional: bool = False        # a missing role drops this term (and a blank value counts 0)
    net_of: str | None = None     # a rate role: each row's value × (1 − rate) (a 25% line discount)
    # Only rows meeting one of these groups (each group: all its conditions): NPL = 90+ days
    # past due OR non-accrual, never counted twice.
    where: list[list[Cond]] = Field(default_factory=list)
    # ponytail: the pack's spell roles, set once by Pack._check, so `roles()` (read by
    # computable, used_terms, kpi_roles …) names them; a term outside a pack has none
    _spell_roles: list[str] = PrivateAttr(default_factory=list)

    @model_validator(mode="after")
    def _one(self) -> Term:
        if self.spell is not None:
            if self.spell != "active" and self.sum is not None:
                raise ValueError("a spell term sums a role only for `active` (at a date)")
            if any(x is not None for x in (self.sum_abs, self.nunique, self.kpi, self.within)):
                raise ValueError("a spell term counts its entities (or sums one role): no "
                                 "sum_abs, nunique, kpi or within")
        elif sum(x is not None for x in (self.sum, self.sum_abs, self.nunique, self.kpi,
                                         self.within)) != 1:
            raise ValueError("a term needs exactly one of sum, sum_abs, nunique, kpi, within, spell")
        expr = self.sum if self.sum is not None else self.sum_abs
        if isinstance(expr, list) and (len(expr) != 3 or expr[1] not in ("*", "-", "days")):
            raise ValueError(f"a term's expression is [role, '*', '-' or 'days', role], not {expr}")
        return self

    def roles(self) -> list[str]:
        expr = self.sum if self.sum is not None else self.sum_abs
        own = [self.nunique] if self.nunique else [] if expr is None else [expr] if isinstance(
            expr, str) else [expr[0], expr[2]]
        if w := self.within:
            own = [w.entity, w.start, w.end, *(c.role for g in w.follow for c in g)]
        if self.spell:   # the entity only: snapshots need no start or end column
            own = [*self._spell_roles[:1], *own]
        return list(dict.fromkeys([*own, *([self.net_of] if self.net_of else []),
                                   *(c.role for g in self.where for c in g)]))


class KPI(_Strict):
    name: str
    label: str
    # Alternatives in order: the first whose (non-optional) roles the data has is used.
    num: list[list[Term]]
    den: list[list[Term]] | None = None
    rows: list[Literal["sale", "return", "refunded", "cancel", "test", "non_product"]] = ["sale"]
    unit: Literal["money", "percent", "count", "number"] = "number"
    unit_word: str | None = None  # a number's unit, written after it ("3.72 days")
    tile: bool = False            # one of the domain's KPI tiles (when computable)
    # "latest": a state at a date (outstanding balance, accounts, NPL): each snapshot_key's latest
    # row in the period, of any kind, THEN the kind / currency / where filters (a loan charged off
    # this month is out, never its last month's balance)
    at: Literal["all", "latest"] = "all"
    # a role whose values the KPI never adds across (exposure units differ by line): computed
    # within one value only; asked without a group, answered by this role
    per: str | None = None

    @property
    def state(self) -> bool:
        """A state at a date (a balance, a headcount), never a flow over the period: THE test
        (`at: latest`, or a spell's entities in force)."""
        return self.at == "latest" or any(t.spell == "active" for alt in self.num for t in alt)

    def written(self, value: str) -> str:
        """A formatted value with its unit word: THE way a KPI's number is written."""
        return f"{value} {self.unit_word}" if self.unit_word else value

    @property
    def phrase(self) -> str:
        """The label inside a sentence: 'Net sales' → 'net sales'; an acronym stays one ('NPL
        ratio', 'AOV')."""
        first = self.label.split(" ")[0]
        return self.label if first[1:2].isupper() or first[1:2].isdigit() else \
            self.label[0].lower() + self.label[1:]


class Pack(_Strict):
    domain: str
    # Patient data: any match of this pack (strong or weak) turns PHI mode on, whatever the
    # headers say (fail closed: a claims extract has member ids and codes, no MRN or name)
    phi: bool = False
    # Detection: the data is this domain when, for one alternative, every group has a role in it
    # (independent signals: a product AND a money column, or an orders export's own shape) and
    # no header carries a veto token (another domain's word: patient, claim, ticker, vendor …).
    signals: list[dict[str, list[str]]]
    strong: list[dict[str, list[str]]] = Field(default_factory=list)   # shapes only this domain has
    # With these roles, a panel of snapshots (snapshot_key × date unique, 3+ dates per key) is
    # strong: a book of accounts at each date (a deposit book's EOD balances)
    strong_panel: list[str] = Field(default_factory=list)
    exclude_headers: list[list[str]] = Field(default_factory=list)   # words, all in one header
    exclude_pairs: list[list[str]] = Field(default_factory=list)     # words, each in any header
    # Header tokens as exports shorten them ("Cur Bal" → current_balance), expanded before the
    # exact match against a role's headers (plan v2 R3: one vocabulary, never per-file patches).
    abbreviations: dict[str, str] = Field(default_factory=dict)
    week_ends: str = "SUN"          # a calendar week's last day for weekly buckets of daily data
    roles: dict[str, Role]
    status_values: dict[Literal["ok", "return", "cancel"], list[str]] = Field(default_factory=dict)
    credit_prefix: dict[str, str] = Field(default_factory=dict)   # role → prefix ("C536379")
    non_product: dict[str, list[str]] = Field(default_factory=dict)   # role → values (POST)
    test_marker: str | None = None  # regex on the raw order / customer / email values
    test_roles: list[str] = Field(default_factory=list)
    sentinels: dict[str, list[float]] = Field(default_factory=dict)   # role → codes for "unknown"
    # role → its sign from an indicator: Cleaning signs the values once (every consumer reads them)
    signed_by: dict[str, SignRule] = Field(default_factory=dict)
    ranges: dict[str, dict[Literal["gt", "ge", "lt", "le"], float]] = Field(default_factory=dict)
    # Fields an export writes on an order's first line only (Shopify): carried down the order.
    order_fields: list[str] = Field(default_factory=list)
    # The row layout, in this domain's roles (none: the behaviour is off, never borrowed):
    negative_is_return: list[str] = Field(default_factory=list)   # a negative value: a return
    order_key: str | None = None    # the role that groups rows into one order (orders, dedup)
    item_roles: list[str] = Field(default_factory=list)   # a line's item: order + item = one line
    line_weight: list[str] | None = None   # [role, '*', role]: a line's share of an order value
    cost_roles: list[str] = Field(default_factory=list)   # margin or profit needs one of these
    # An overview opens with the first money tile KPI by month and by the first of these the
    # data has (the charts the domain's analyst reads first)
    overview_groups: list[str] = Field(default_factory=list)
    # The KPIs an overview charts (the first by month and by group, the rest by group); unset:
    # the first money tile and a state ratio. A hospital opens on stays and readmission, not charges.
    overview_kpis: list[str] = Field(default_factory=list)
    location_role: str | None = None   # sites that open and close (comparable-store growth)
    snapshot_key: str | None = None    # the role naming one level's series (an account, a loan)
    # A column filled only on rows of these statuses (a charge-off date): its blanks are the
    # layout, not missing values
    conditional_fields: dict[str, list[str]] = Field(default_factory=dict)
    # Roles a row fills one of (Debit or Credit): the other's blank is the layout, never missing
    paired_fields: list[list[str]] = Field(default_factory=list)
    # A marker filled only on the rows it applies to (a catastrophe event code): blank = none
    marker_fields: list[str] = Field(default_factory=list)
    running_balance: RunningBalance | None = None
    runout: Runout | None = None
    maturity: Maturity | None = None
    spell: Spell | None = None
    # A caution said whenever the data has the role (a protected attribute: described, never a
    # rating recommendation), strong or weak match
    role_caveats: dict[str, str] = Field(default_factory=dict)
    # The entity an average of the pack's money divides by (a claim on claim-line or claimant
    # rows): 'average paid by payer' is per claim, never per line
    average_per: str | None = None
    # A role the data may lack but can derive ({los: [date, days, admit_date]}): the analysis
    # frame gets it as a column named after the role's first header, so the model never invents
    # one ("LOS (derived)" was rejected as an unknown column)
    derived: dict[str, list[str]] = Field(default_factory=dict)
    # A version chain (a claim line adjudicated, replaced, voided): the rows sharing these roles'
    # values, ordered by `version_order`; only the last counts, earlier ones are non_product, and
    # a chain whose last row carries a `version_void` value is cancelled (every row)
    version_key: list[str] = Field(default_factory=list)
    version_order: str | None = None
    version_void: dict[str, list[str]] = Field(default_factory=dict)
    # Words: each row kind's name ("refunded orders") and its cleaning flag; `text` for the rest.
    row_labels: dict[RowKind, str] = Field(default_factory=dict)
    # The codes the model's frame and brief use for each kind (a banking model filters on
    # 'on_book', never a retail 'sale'); unset: the internal names
    kind_codes: dict[RowKind, str] = Field(default_factory=dict)
    row_flags: dict[RowKind, str] = Field(default_factory=dict)
    text: dict[TextKey, str] = Field(default_factory=dict)
    kpis: list[KPI] = Field(default_factory=list)
    glossary: dict[str, list[str]] = Field(default_factory=dict)   # KPI or concept → its words
    definitions: dict[str, str] = Field(default_factory=dict)      # a non-KPI concept → its rule
    consumed: list[str] = Field(default_factory=list)   # "sales tax": uses its words, means none
    unsupported: dict[str, str] = Field(default_factory=dict)      # concept → what it needs
    caveats: dict[str, str] = Field(default_factory=dict)          # condition → text
    causal_triggers: list[str] = Field(default_factory=list)
    never_say: list[NeverSay] = Field(default_factory=list)
    # On patient data: a reported count of 1–10 of these (patients, deaths …) is a small cell
    small_cell_nouns: list[str] = Field(default_factory=list)
    # Plain text, or {q, needs: [KPI…]}: offered only when the data supports what it needs
    first_questions: list[str | dict[str, str | list[str]]] = Field(default_factory=list)

    def kpi(self, name: str) -> KPI | None:
        return next((k for k in self.kpis if k.name == name), None)

    def value_words(self) -> dict[str, set[str]]:
        """role → the written values a pack rule reads (a KPI's `where` words, status words,
        version voids, non-product codes), as `words()` compares them: pooling these values
        would change the answers (5 'expired' pooled → mortality 0)."""
        out: dict[str, set[str]] = {}
        for k in self.kpis:
            for t in (t for alt in [*k.num, *(k.den or [])] for t in alt):
                for c in (c for g in t.where for c in g):
                    out.setdefault(c.role, set()).update(c.in_ or c.not_in or [])
        status = [v for vs in self.status_values.values() for v in vs]
        for r in (r for r, x in self.roles.items() if x.kind == "status"):
            out.setdefault(r, set()).update(status)
        for r, vs in [*self.version_void.items(), *self.non_product.items()]:
            out.setdefault(r, set()).update(vs)
        return {r: set(words(pd.Series(sorted(vs), dtype=str))) for r, vs in out.items() if vs}

    @property
    def date_role(self) -> str:
        """The domain's time role: the first role of kind date (the validator requires one)."""
        return next(r for r, x in self.roles.items() if x.kind == "date")

    def code(self, kind: str) -> str:
        return self.kind_codes.get(kind, kind)

    def label(self, kind: str) -> str:
        return self.row_labels.get(kind, kind.replace("_", "-") + " rows")

    def name_of(self, concept: str) -> str:
        """A concept in a sentence: its first glossary word, capitalized ('Net interest margin',
        'Sell-through'), never its key ('Nim')."""
        n = (self.glossary.get(concept) or [concept.replace("_", " ")])[0]
        return n[0].upper() + n[1:]

    def say(self, which: str, **kw) -> str:
        return self.text.get(which, TEXT_DEFAULTS[which]).format(**kw)

    @model_validator(mode="after")
    def _check(self) -> Pack:
        errors, seen = [], {}
        for role, r in self.roles.items():
            for h in r.headers:
                if normalize_header(h) != h:
                    errors.append(f"role {role}: header {h!r} is not normalized "
                                  f"({normalize_header(h)!r})")
                elif expand_header(h, self) != h:   # never matched: headers are expanded first
                    errors.append(f"role {role}: header {h!r} is abbreviated "
                                  f"(write {expand_header(h, self)!r})")
                # one header may serve two roles only if their value checks tell them apart
                if (other := seen.get(h)) and self.roles[other].kind == r.kind:
                    errors.append(f"header {h!r} is in roles {other} and {role} of the same kind")
                seen.setdefault(h, role)
        refs = [*self.order_fields, *self.sentinels, *self.ranges, *self.credit_prefix, *self.non_product,
                *self.test_roles, *self.negative_is_return, *self.item_roles, *self.cost_roles, *self.overview_groups,
                *(r for r in (self.order_key, self.location_role, self.snapshot_key,
                              self.version_order) if r),
                *self.version_key, *self.version_void, *self.conditional_fields, *self.marker_fields,
                *([self.runout.role] if self.runout else []),
                *([self.maturity.role, *self.maturity.measures, *self.maturity.dated]
                  if self.maturity else []),
                *self.role_caveats, *(k.per for k in self.kpis if k.per),
                *([self.spell.entity, self.spell.start, self.spell.end,
                   *([self.spell.as_of] if self.spell.as_of else [])] if self.spell else []),
                *([self.average_per] if self.average_per else []), *(r for g in self.paired_fields for r in g),
                *([*self.running_balance.levels, *self.running_balance.plus, *self.running_balance.minus]
                  if self.running_balance else []),
                *self.signed_by, *(x.indicator for x in self.signed_by.values()),
                *(self.line_weight[::2] if self.line_weight else []),
                *(r for alt in [*self.signals, *self.strong] for g in alt.values() for r in g),
                *self.strong_panel]
        errors += [f"unknown role {r}" for r in refs if r not in self.roles]
        for r, expr in self.derived.items():
            if r not in self.roles or len(expr) != 3 or expr[1] not in ("*", "-", "days") \
                    or expr[0] not in self.roles or expr[2] not in self.roles:
                errors.append(f"derived {r}: a role and [role, '*', '-' or 'days', role]")
        if bool(self.version_key) != bool(self.version_order):
            errors.append("version_key and version_order go together")
        if self.line_weight and (len(self.line_weight) != 3 or self.line_weight[1] not in ("*", "-")):
            errors.append(f"line_weight is [role, '*' or '-', role], not {self.line_weight}")
        errors += [f"role {r}: a {x.kind} role is named {x.kind!r} (reserved)" for r, x in
                   self.roles.items() if x.kind in RESERVED_ROLES and r != x.kind]
        if self.snapshot_key is None:
            errors += [f"role {r}: over_time last needs snapshot_key" for r, x in self.roles.items()
                       if x.over_time == "last"]
            errors += [f"kpi {k.name}: at latest needs snapshot_key" for k in self.kpis if k.at == "latest"]
        codes = [self.code(k) for k in ROW_KINDS]
        if len(set(codes)) != len(codes) or "other_currency" in codes or \
                not all(re.fullmatch(r"[a-z][a-z0-9_]*", c) for c in codes):
            errors.append(f"kind_codes: distinct snake_case codes, never other_currency: {codes}")
        if self.conditional_fields and "status" not in self.roles:
            errors.append("conditional_fields: needs a status role")
        if not any(r.kind == "date" for r in self.roles.values()):
            errors.append("roles: one role of kind date is needed (periods, partial months)")
        if not self.signals or any(len(alt) < 2 for alt in [*self.signals, *self.strong]):
            errors.append("each signals alternative needs two groups (two independent signals)")
        for where, rx in [*((f"role {r}", x.pattern) for r, x in self.roles.items()),
                          ("test_marker", self.test_marker),
                          *(("never_say", n.pattern) for n in self.never_say)]:
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
                    if t.spell and not self.spell:
                        errors.append(f"kpi {k.name}: a spell term needs the pack's spell")
                    elif t.spell:
                        t._spell_roles = [self.spell.entity]
                        if k.at == "latest":
                            errors.append(f"kpi {k.name}: at latest and a spell term are two "
                                          "state rules")
                    errors += [f"kpi {k.name}: unknown role {r}" for r in t.roles() if r not in self.roles]
                    if t.kpi and t.kpi not in names:
                        errors.append(f"kpi {k.name}: unknown kpi {t.kpi}")
            if self._cycle(k.name, ()):
                errors.append(f"kpi {k.name}: refers to itself")
        errors += [f"overview_kpis: unknown kpi {n}" for n in self.overview_kpis if n not in names]
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
    packs = tuple(Pack.model_validate(yaml.safe_load(p.read_text()))
                  for p in sorted(PACKS.glob("*.yaml")))
    return packs


def pack_for(domain: str | None) -> Pack | None:
    return next((p for p in load_packs() if p.domain == domain), None) if domain else None


# ── Detection ─────────────────────────────────────────────────────────────────────────────────

def _fits(s: pd.Series, role: Role, pack: Pack, is_date: bool) -> bool:
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
        case "rate":
            return numeric and bool(vals.between(0, 1).all())
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


def expand_header(norm: str, pack: Pack) -> str:
    """A normalized header with the pack's abbreviations spelled out: cur_bal → current_balance."""
    return "_".join(pack.abbreviations.get(t, t) for t in norm.split("_"))


def _panel(df: pd.DataFrame, pack: Pack, roles: dict[str, str]) -> bool:
    """The pack's strong_panel roles, and one row per snapshot_key per date on 3+ dates each."""
    key, date = roles.get(pack.snapshot_key), roles.get(pack.date_role)
    if not (pack.strong_panel and key and date and all(r in roles for r in pack.strong_panel)):
        return False
    pairs = df[[key, date]].dropna()
    return len(pairs) > 0 and not pairs.duplicated().any() and pairs.groupby(key).size().median() >= 3


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
    for pack in load_packs():
        if any(set(v) <= h for v in pack.exclude_headers for h in per_header) or \
                any(set(v) <= tokens for v in pack.exclude_pairs):
            continue
        roles: dict[str, str] = {}
        expanded = {c: expand_header(n, pack) for c, n in norm.items()}
        for role, r in pack.roles.items():
            for header in r.headers:
                col = next((c for c, n in expanded.items() if n == header and c not in roles.values()
                            and not (r.kind == "id" and c in pii_columns)
                            and _fits(df[c], r, pack, c in date_columns)), None)
                if col is not None:
                    roles[role] = col
                    break
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


def _column(df: pd.DataFrame, roles: dict[str, str], expr: str | list[str]) -> pd.Series:
    if isinstance(expr, str):
        return pd.to_numeric(df[roles[expr]], errors="coerce")
    if expr[1] == "days":   # whole days from b to a (a stay: discharge − admit; same day = 0)
        d = (_day(df[roles[expr[0]]]) - _day(df[roles[expr[2]]])).dt.days
        return d.where(d >= 0).astype(float)
    a, op, b = (pd.to_numeric(df[roles[expr[0]]], errors="coerce"), expr[1],
                pd.to_numeric(df[roles[expr[2]]], errors="coerce"))
    return a * b if op == "*" else a - b


def _day(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, format="mixed", errors="coerce").dt.normalize()


def follow_up_complete(pack: Pack, name: str, df: pd.DataFrame, roles: dict[str, str]):
    """(the last date whose follow-up window is complete, the window in days) for a KPI with a
    `within` term (a 30-day readmission: the data's last start − 30 days), else None. A period
    ending after it is mostly censored rows: never a rate to show or compare (THE rule _within
    censors by)."""
    kpi = pack.kpi(name)
    terms = [t for alt in [*kpi.num, *(kpi.den or [])] for t in alt]
    windows = [t.within for t in terms if t.within and t.within.start in roles]
    windows += [w for t in terms if t.kpi for w in [follow_up_complete(pack, t.kpi, df, roles)] if w]
    cut = [(_day(df[roles[w.start]]).max() - pd.Timedelta(days=w.days), w.days)
           for w in windows if isinstance(w, Within)] + [w for w in windows if isinstance(w, tuple)]
    return min(cut) if cut else None


def _within(df: pd.DataFrame, whole: pd.DataFrame, roles: dict[str, str], w: Within) -> pd.Series:
    """1.0 per `df` row counted by `w` (a hit, or with `base` an uncensored row), else 0.0."""
    end = _day(df[roles[w.end]])
    starts = _day(whole[roles[w.start]])
    keep = (end <= starts.max() - pd.Timedelta(days=w.days)) & end.notna()   # follow_up_complete
    if w.base:
        return keep.astype(float)
    follow = _meets(whole, roles, w.follow) if w.follow else pd.Series(True, index=whole.index)
    nxt = pd.DataFrame({"e": whole.loc[follow, roles[w.entity]].astype(str),
                        "t": starts[follow]}).dropna().sort_values("t", kind="stable")
    idx = pd.DataFrame({"e": df[roles[w.entity]].astype(str), "t": end, "i": df.index}
                       ).dropna(subset=["t"]).sort_values("t", kind="stable")
    hit = pd.merge_asof(idx, nxt.rename(columns={"t": "n"}), left_on="t", right_on="n", by="e",
                        direction="forward", allow_exact_matches=False)
    hit = hit.set_index("i")["n"].sub(hit.set_index("i")["t"]).dt.days.le(w.days)
    return (keep & hit.reindex(df.index, fill_value=False)).astype(float)


def _snapshot_column(pack: Pack, roles: dict[str, str]) -> str | None:
    """A spell's snapshot date column (the pack's date role, when it is neither the start nor the
    end and the data has it): rows are then dated snapshots, not spells."""
    s = pack.spell
    return roles.get(pack.date_role) if s and pack.date_role not in (s.start, s.end) else None


def spell_as_of(pack: Pack, df: pd.DataFrame, roles: dict[str, str]) -> pd.Timestamp | None:
    """THE date a spell KPI is measured at: the report-date role, else the latest snapshot, else
    the latest start or end date not after today (a notice period's exit, a pre-boarded hire:
    not yet). Always judged on all rows (a filter never moves it)."""
    from agents.utils import today
    s = pack.spell
    for col in [roles.get(s.as_of) if s.as_of else None, _snapshot_column(pack, roles)]:
        if col and pd.notna(t := _day(df[col]).max()):
            return t
    dates = pd.concat([_day(df[roles[r]]) for r in (s.start, s.end) if r in roles] or [pd.Series(dtype="datetime64[ns]")])
    dates = dates[dates <= today()]
    return dates.max() if dates.notna().any() else None


def spell_from(pack: Pack, df: pd.DataFrame, roles: dict[str, str]) -> pd.Timestamp | None:
    """The first date a spell KPI is shown from: the first snapshot, else the earliest end (a
    roster lists only recent leavers: before its first exit the headcount is understated), else
    the earliest start."""
    s = pack.spell
    for col in [_snapshot_column(pack, roles), roles.get(s.end), roles.get(s.start)]:
        if col and pd.notna(t := _day(df[col]).min()):
            return t
    return None


def spell_window(pack: Pack, df: pd.DataFrame, roles: dict[str, str],
                 window: tuple | None = None) -> tuple[pd.Timestamp, pd.Timestamp, int] | None:
    """THE window of a spell KPI asked without a period grouping: (lo, as_of, months), flows
    counted in (lo, as_of] and the average over its month-ends. The 12 months to the as-of, or
    the shorter span the data covers; an asked window (lo, hi) clips both ends."""
    as_of, first = spell_as_of(pack, df, roles), spell_from(pack, df, roles)
    if as_of is None or first is None:
        return None
    if window and window[1] is not None:
        as_of = min(as_of, pd.Timestamp(window[1]).normalize())
    lo = as_of - pd.DateOffset(months=12)
    if window and window[0] is not None:
        lo = pd.Timestamp(window[0]).normalize() - pd.Timedelta(days=1)
    lo = max(lo, first - pd.Timedelta(days=1))
    return lo, as_of, len(_month_ends(lo, as_of))


def _month_ends(a: pd.Timestamp, b: pd.Timestamp) -> list[pd.Timestamp]:
    """The month-ends in (a, b], the last one clipped to b."""
    if b <= a:
        return []
    ends = [min(m.end_time.normalize(), b) for m in pd.period_range(a, b, freq="M")]
    return [e for e in dict.fromkeys(ends) if a < e <= b]


def window_words(pack: Pack, df: pd.DataFrame, roles: dict[str, str], name: str,
                 window: tuple | None = None) -> str:
    """'as of 31 Oct 2026' (a state) or '12 months to 31 Oct 2026' (a flow, a rate): how a spell
    KPI's answer names its period; '' for other KPIs."""
    kpi = pack.kpi(name)
    if not pack.spell or not kpi or not spell_terms(pack, name):
        return ""
    w = spell_window(pack, df, roles, window)
    if w is None:
        return ""
    lo, as_of, months = w
    if kpi.state:
        return f"as of {as_of:%-d %b %Y}"
    return f"{months} month{'s' * (months != 1)} to {as_of:%-d %b %Y}"


def spell_terms(pack: Pack, name: str) -> list[Term]:
    """The spell terms a KPI reads, through the KPIs it refers to."""
    kpi = pack.kpi(name)
    out: list[Term] = []
    for t in (t for alt in [*kpi.num, *(kpi.den or [])] for t in alt):
        out += [t] if t.spell else spell_terms(pack, t.kpi) if t.kpi else []
    return out


def _in_force(whole: pd.DataFrame, roles: dict[str, str], pack: Pack, t: pd.Timestamp) -> pd.DataFrame:
    """THE state rule: each entity's row at `t` if it is in force then. Snapshots: its latest
    snapshot on or before t (agents.utils.snapshot_rows: absent from the newest snapshot = gone);
    spells: its latest spell started by t. Either way out when its end is on or before t, or its
    status says ended with no end date."""
    s, snap = pack.spell, _snapshot_column(pack, roles)
    ent = roles[s.entity]
    if snap:
        from agents.utils import snapshot_rows
        rows = snapshot_rows(whole[_day(whole[snap]) <= t], [ent], snap)
    else:
        start = _day(whole[roles[s.start]])
        rows = whole.loc[start[start <= t].sort_values(kind="stable").index].groupby(ent).tail(1)
    end = _day(rows[roles[s.end]]) if s.end in roles else pd.Series(pd.NaT, index=rows.index)
    out = end.notna() & (end <= t)
    if s.ended and "status" in roles:
        out |= words(rows[roles["status"]]).isin(set(words(pd.Series(s.ended)))) & end.isna()
    return rows[~out]


def _spell_events(whole: pd.DataFrame, roles: dict[str, str], pack: Pack, which: str,
                  as_of: pd.Timestamp) -> tuple[pd.DataFrame, pd.Series]:
    """(each spell's row, its event date) for `starts` or `ends` up to the as-of: an event is
    described by its spell's own first row (a hire) or last row (an exit: the department left,
    not today's). On snapshots an exit with no end date is dated at the first snapshot the entity
    is missing from; the first snapshot holds no hires."""
    s, snap = pack.spell, _snapshot_column(pack, roles)
    ent = roles[s.entity]
    if snap:
        d = whole.assign(__t=_day(whole[snap])).dropna(subset=["__t"]).sort_values("__t", kind="stable")
        g = d.groupby([ent, *([roles[s.start]] if s.start in roles else [])], sort=False, dropna=False)
        if which == "starts":
            rows = g.head(1)
            when = (_day(rows[roles[s.start]]) if s.start in roles
                    else rows["__t"].where(rows["__t"] > d["__t"].min()))
        else:
            rows = g.tail(1)
            when = _day(rows[roles[s.end]]) if s.end in roles else pd.Series(pd.NaT, index=rows.index)
            dates = pd.Series(sorted(d["__t"].unique()))
            pos = dates.searchsorted(rows["__t"], side="right")
            missing = pd.Series([dates[p] if p < len(dates) else pd.NaT for p in pos], index=rows.index)
            when = when.where(when.notna(), missing)
        rows = rows.drop(columns="__t")
    else:
        col = roles.get(s.start if which == "starts" else s.end)
        if col is None:
            return whole.iloc[:0], pd.Series(dtype="datetime64[ns]")
        when = _day(whole[col])
        rows = whole[when.notna()]
        rows = rows[~pd.DataFrame({"e": rows[ent].astype(str), "t": when[rows.index]}).duplicated()]
        when = when[rows.index]
    keep = when.notna() & (when <= as_of)
    return rows[keep], when[keep]


def _spell(df, kinds, roles, pack, kpi, term: Term, by):
    """A spell term's value (or a Series per `by` group or period). The entities are chosen on
    ALL rows (the state at a date, an exit's own row), then counted when the chosen row is in
    `df` (a filter: Sales counts who was in Sales at that date), of the KPI's row kinds and
    meeting `where`. No period grouping: `active` at the as-of, the rest over the 12 months to it
    (or the shorter span the data covers); by period: each period of a gap-free axis from
    `spell_from` to the as-of, the last clipped to it."""
    s = pack.spell
    whole = kinds.attrs.get("frame", df)
    if s.entity not in roles or not (_snapshot_column(pack, roles) or s.start in roles):
        return None
    w = spell_window(pack, whole, roles, kinds.attrs.get("window"))
    if w is None:
        return None
    lo, as_of, _ = w
    first = spell_from(pack, whole, roles)
    allk = kinds.attrs.get("kinds", kinds)
    ok = set(term.rows or kpi.rows)
    periodic = by is not None and isinstance(df[by].dtype, pd.PeriodDtype)
    ent = roles[s.entity]

    def kept(rows: pd.DataFrame) -> pd.DataFrame:
        m = rows.index.isin(df.index) & allk.reindex(rows.index).isin(ok).to_numpy()
        if term.where:
            m &= _meets(rows, roles, term.where).to_numpy()
        return rows[m]

    def count(rows: pd.DataFrame, key):
        """People in force: distinct entities (or a role summed over their one row each);
        events: rows (a returning entity's two exits are two)."""
        if term.sum is not None:
            v = _column(rows, roles, term.sum)
            return v.sum(min_count=1) if key is None else v.groupby(key).sum(min_count=1)
        if term.spell in ("starts", "ends"):
            return float(len(rows)) if key is None else rows.groupby(key).size()
        return float(rows[ent].nunique()) if key is None else rows[ent].groupby(key).nunique()

    if periodic:
        freq = df[by].dtype.freq
        axis = pd.period_range(first, as_of, freq=freq)
        groups = None
    else:
        axis = None
        groups = pd.unique(df[by].dropna()) if by else None

    if term.spell in ("starts", "ends"):
        rows, when = _spell_events(whole, roles, pack, term.spell, as_of)
        rows = kept(rows)
        when = when[rows.index]
        if periodic:
            n = count(rows, when.dt.to_period(freq))
            return n.reindex(axis, fill_value=0)
        rows = rows[when[rows.index] > lo]
        if by:
            return count(rows, rows[by]).reindex(groups, fill_value=0)
        return count(rows, None)

    def active_at(t: pd.Timestamp):
        rows = kept(_in_force(whole, roles, pack, t))
        return count(rows, rows[by] if by and not periodic else None)

    if term.spell == "active":
        if periodic:
            return pd.Series({p: active_at(min(p.end_time.normalize(), as_of)) for p in axis},
                             dtype=float).reindex(axis)
        v = active_at(as_of)
        return v.reindex(groups, fill_value=0) if by else v
    # average: the mean of the month-end active counts (a group absent at a month-end counts 0)
    if periodic:   # ponytail: one in-force pass per month-end, fine below ~50k rows × 120 months
        out = {}
        for p in axis:
            pts = _month_ends(max(p.start_time, first) - pd.Timedelta(days=1),
                              min(p.end_time.normalize(), as_of))
            out[p] = sum(active_at(t) for t in pts) / len(pts) if pts else float("nan")
        return pd.Series(out, dtype=float).reindex(axis)
    pts = _month_ends(lo, as_of)
    if not pts:
        return None
    vals = [active_at(t) for t in pts]
    if by:
        return (pd.concat(vals, axis=1).fillna(0).sum(axis=1) / len(pts)).reindex(groups, fill_value=0)
    return sum(vals) / len(pts)


def _order_level(df: pd.DataFrame, roles: dict[str, str], pack: Pack, expr) -> bool:
    """A single order-level role (Shopify's discount) whose value is constant within every
    order: counted once per order. A per-line discount differs within an order: summed."""
    if not isinstance(expr, str) or pack.roles[expr].level != "order" or pack.order_key not in roles:
        return False
    per = df.groupby(df[roles[pack.order_key]], dropna=True)[roles[expr]].nunique(dropna=False)
    return bool(len(per)) and int(per.max()) <= 1


def as_text(s: pd.Series) -> pd.Series:
    """Values as written: a whole number read as float (a code column with a blank: DRG 313.0)
    is its digits, '313'."""
    if pd.api.types.is_float_dtype(s) and (s.dropna() % 1 == 0).all():
        return s.astype("Int64").astype(str)
    return s.astype(str)


def words(s: pd.Series) -> pd.Series:
    """'Non Accrual', 'non-accrual', 'NON_ACCRUAL' → 'non accrual': a value as a word list. THE
    comparison of a data value with a pack's words (statuses, conditions, indicators)."""
    return as_text(s).str.strip().str.lower().str.replace(r"[\s_\-]+", " ", regex=True)


def _meets(df: pd.DataFrame, roles: dict[str, str], where: list[list[Cond]]) -> pd.Series:
    """The rows meeting one of the groups (each group: all its conditions). A blank meets none."""
    hit = pd.Series(False, index=df.index)
    for group in where:
        ok = pd.Series(True, index=df.index)
        for c in group:
            col = df[roles[c.role]]
            if c.in_ is not None or c.not_in is not None:
                wanted = words(pd.Series(c.in_ if c.in_ is not None else c.not_in))
                inside = words(col).isin(set(wanted))
                ok &= col.notna() & (inside if c.in_ is not None else ~inside)
            else:
                v = pd.to_numeric(col, errors="coerce")
                op, bound = next((o, b) for o, b in (("gt", c.gt), ("ge", c.ge), ("lt", c.lt),
                                                      ("le", c.le)) if b is not None)
                ok &= {"gt": v > bound, "ge": v >= bound, "lt": v < bound, "le": v <= bound}[op]
        hit |= ok
    return hit


def _term(df, kinds, roles, pack, kpi, term: Term, by):
    rows = kinds.isin(term.rows or kpi.rows)
    if term.kpi:
        return _kpi_value(df, kinds, roles, pack, pack.kpi(term.kpi), by)
    if term.spell:
        return _spell(df, kinds, roles, pack, kpi, term, by)
    met = _meets(df, roles, term.where) if term.where else None
    if met is not None:
        rows &= met
        whole = kinds.attrs.get("frame", df)
        if not met.any() and not _meets(whole, roles, term.where).any():
            return None   # no interest rows anywhere in the data: unknown, never a $0.00
    part = df[rows]
    # no rows of these kinds here (no returns this month, in this store) while the data has them
    # elsewhere: 0, and a group keeps its place (a 0% return rate ranks lowest); none anywhere in
    # the data (returns may not be exported at all): unknown
    if part.empty and not set(term.rows or kpi.rows) & kinds.attrs.get("present", set(kinds)):
        return None
    # the groups this KPI's rows have (a postage-only code or a test customer is no group at $0)
    counted = kinds.isin({"sale", "return", "refunded", *kpi.rows})
    periodic = by is not None and isinstance(df[by].dtype, pd.PeriodDtype)
    if met is not None and kpi.den is None and not periodic:   # spend by category: no "Transfer"
        counted &= met   # at $0; but a month with no charge-offs is a month at $0
    groups = pd.unique(df.loc[counted, by].dropna()) if by else None
    if term.within:   # follow-ups anywhere in the data, never only in this month or group
        values = _within(part, kinds.attrs.get("frame", df), roles, term.within)
        return values.groupby(part[by]).sum().reindex(groups, fill_value=0) if by else values.sum()
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
        order = part[roles[pack.order_key]]
        lw = pack.line_weight
        weight = (_column(part, roles, lw) if lw and {lw[0], lw[2]} <= roles.keys()
                  else pd.Series(1.0, index=part.index))
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
    if kpi.at == "latest":   # a state: each entity's latest row of any kind, then the filters
        from agents.utils import snapshot_rows
        key, date = roles.get(pack.snapshot_key), roles.get(pack.date_role)
        if key is None or (date is None and df[key].duplicated().any()):
            return None   # undated snapshots of one account: which is the latest is unknown
        present = kinds.attrs.get("present")
        # by period: each account's latest row in each period (its month-end); by a group: its
        # latest row overall, in the group it is in now
        periodic = by is not None and isinstance(df[by].dtype, pd.PeriodDtype)
        if date is not None:   # undated: one row per account already (a one-date extract)
            df = snapshot_rows(df, [key], date, per=by if periodic else None)
        kinds = kinds.loc[df.index]
        if present is not None:
            kinds.attrs["present"] = present
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
                by: str | None = None, full: pd.DataFrame | None = None,
                window: tuple | None = None):
    """A KPI's value (or a Series per `by` group) on the main currency's rows; None when the data
    lacks its roles or a denominator is 0 — never a 0 or an infinity standing in for "unknown".
    `full`: the whole data when `df` is a period of it (a month with no returns has a 0% return
    rate when the data has returns at all). `window` (lo, hi): an asked period for a spell KPI,
    which clips its window and as-of instead of cutting rows (rows dated in Q3 are the people
    hired in Q3, not the people employed then)."""
    kpi = pack.kpi(name)
    if kpi is None or df.empty:
        return None
    if mixes(df, roles, kpi, by):   # car-years and house-years never add up
        return None
    keep, _, _ = main_currency(df, roles)
    # another currency is a kind of row no KPI counts (tiles side by side read the same rows:
    # net sales / orders = AOV), unless the question splits BY currency: then each currency is its
    # own group, never added to another. A state's latest row is judged first either way.
    # a row's kind can depend on rows outside `df` (a claim line replaced or voided later): with
    # the whole data at hand, kinds are judged on it (a filtered part never revives an old version)
    whole = None if full is None else row_kinds(full, roles, pack)
    kinds = whole.reindex(df.index) if whole is not None and df.index.isin(full.index).all() \
        else row_kinds(df, roles, pack)
    if not (by and by == roles.get("currency")):
        kinds = kinds.where(keep, "other_currency")
    whole = kinds if whole is None else whole
    kinds.attrs["present"] = set(whole.unique())
    kinds.attrs["frame"] = df if full is None else full
    if whole is not kinds:   # the kinds of every row (a spell's entity may be chosen outside df)
        kinds.attrs["kinds"] = whole
    if window:
        kinds.attrs["window"] = window
    value = _kpi_value(df, kinds, roles, pack, kpi, by)
    if value is None:
        return None
    if by:   # a group with no values is no group (a NaN would break the answer's JSON)
        value = value.dropna()
        return value if len(value) else None
    return None if pd.isna(value) else float(value)


def mixes(df: pd.DataFrame, roles: dict[str, str], kpi: KPI, by: str | None = None) -> bool:
    """Would this KPI add across values of its `per` role (one total, or a group that isn't it)?"""
    col = roles.get(kpi.per) if kpi.per else None
    return bool(col and col in df.columns and by != col and df[col].nunique() > 1)


def money_columns(pack: Pack, roles: dict[str, str]) -> set[str]:
    """The data's columns in the pack's money roles (where a currency symbol says the unit)."""
    return {c for r, c in roles.items() if r in pack.roles and pack.roles[r].kind == "money"}


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

    def has(t: Term) -> bool:
        return all(r in roles for r in t.roles()) and (not t.kpi or computable(pack, t.kpi, roles))

    def side(alts) -> bool:   # every needed term, and at least one term there (all optional: any)
        return any(all(t.optional or has(t) for t in alt) and any(has(t) for t in alt)
                   for alt in alts)
    # a state needs its accounts (and their dates, or one row each: _kpi_value checks)
    state = kpi is not None and (kpi.at == "all" or pack.snapshot_key in roles)
    return state and side(kpi.num) and (kpi.den is None or side(kpi.den))


def additive(pack: Pack, name: str) -> bool:
    """A sum of rows (net sales, units): adds up over weeks. A distinct count (orders, customers)
    does not: a month's customers are not 4 weeks of customers."""
    kpi = pack.kpi(name)
    # spell events (hires, exits) add up over weeks; people in force and their average do not
    return kpi.den is None and all(not t.nunique and t.spell not in ("active", "average")
                                   and (not t.kpi or additive(pack, t.kpi))
                                   for alt in kpi.num for t in alt)


def kpi_measures(pack: Pack, name: str) -> set[str]:
    """The roles a KPI measures (summed, counted, followed up), through the KPIs it refers to;
    never the roles its filters only name (Policies counts policies where a premium exists: it
    doesn't measure premium)."""
    kpi = pack.kpi(name)
    out: set[str] = set()
    for t in (t for alt in [*kpi.num, *(kpi.den or [])] for t in alt):
        expr = t.sum if t.sum is not None else t.sum_abs
        out |= {t.nunique} if t.nunique else set(expr if isinstance(expr, list) else [expr]) - {
            None, "*", "-", "days"} if expr is not None else set()
        out |= {t.within.entity} if t.within else set()
        out |= {pack.spell.entity} if t.spell and pack.spell else set()
        out |= kpi_measures(pack, t.kpi) if t.kpi else set()
    return out


def average_entity(contract, column: str) -> str | None:
    """The column an average of `column` divides by (Pack.average_per, a strong match, `column`
    one of the pack's money roles), else None: a plain mean over rows."""
    pack = defaults_pack(contract)
    if not pack or not pack.average_per or pack.average_per not in contract.roles:
        return None
    money = {r for r, x in pack.roles.items() if x.kind == "money"}
    return contract.roles[pack.average_per] if roles_of(contract, column) & money else None


def roles_of(contract, column: str) -> set[str]:
    """The pack roles a data column fills."""
    return {r for r, c in contract.roles.items() if c == column}


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
            return pack.kpi(t.kpi).phrase
        if t.nunique:
            return f"distinct {roles[t.nunique]}" + (f" where {where(t.where)}" if t.where else "")
        if t.spell:
            sp, snap = pack.spell, _snapshot_column(pack, roles)
            ent = roles[sp.entity]
            at = (f"each {ent}'s latest {snap} snapshot on or before the date" if snap else
                  f"{roles.get(sp.start, sp.start)} on or before the date")
            gone = f"{roles.get(sp.end, sp.end)} blank or after it"
            text = {"active": f"distinct {ent} in force at the date ({at}, {gone})",
                    "average": f"the mean of the month-end counts of distinct {ent} in force "
                               f"({at}, {gone})",
                    "starts": f"spells starting in the period (by {roles.get(sp.start, 'first snapshot')})",
                    "ends": f"exits in the period (events: by {roles.get(sp.end, sp.end)}"
                            + (", or the first snapshot an entity is missing from" if snap else "")
                            + f"; a returning {ent}'s exits count separately)"}[t.spell]
            if t.sum is not None:
                text = f"sum of {roles[t.sum] if isinstance(t.sum, str) else t.sum} over the " + text
            return text + (f" where {where(t.where)}" if t.where else "")
        if w := t.within:
            then = f" (the next must have {where(w.follow)})" if w.follow else ""
            rows = (f"rows whose {roles[w.end]} is at least {w.days} days before the last "
                    f"{roles[w.start]}")
            return (rows if w.base else f"{rows} followed by another row of the same "
                    f"{roles[w.entity]} starting 1–{w.days} days after {roles[w.end]}{then}") + (
                f" where {where(t.where)}" if t.where else "")
        expr = t.sum if t.sum is not None else t.sum_abs
        cols = roles[expr] if isinstance(expr, str) else (
            f"days from {roles[expr[2]]} to {roles[expr[0]]}" if expr[1] == "days" else
            f"{roles[expr[0]]} {'×' if expr[1] == '*' else '−'} {roles[expr[2]]}")
        once = " (once per order)" if isinstance(expr, str) and pack.roles[expr].level == "order" \
            and pack.order_key in roles else ""
        return f"{'sum of |' + cols + '|' if t.sum_abs is not None else 'sum of ' + cols}{once}" + (
            f" where {where(t.where)}" if t.where else "")

    def where(groups: list[list[Cond]]) -> str:
        def cond(c: Cond) -> str:
            if c.not_in == []:   # "not in nothing": the row has a value
                return f"{roles[c.role]} has a value"
            if c.in_ is not None or c.not_in is not None:
                # each spelling once ('non-accrual', not also 'nonaccrual' and 'non accrual')
                vals, seen = [], set()
                for v in c.in_ if c.in_ is not None else c.not_in:
                    if (key := re.sub(r"[^a-z0-9+]", "", v.lower())) not in seen:
                        seen.add(key)
                        vals.append(v)
                return f"{roles[c.role]} {'is' if c.in_ is not None else 'is not'} " + " / ".join(vals)
            op, v = next((o, v) for o, v in (("> ", c.gt), ("≥ ", c.ge), ("< ", c.lt), ("≤ ", c.le))
                         if v is not None)
            return f"{roles[c.role]} {op}{v:g}"
        return " or ".join(" and ".join(cond(c) for c in g) for g in groups)

    def side(name_: str) -> str:
        terms = used_terms(pack, kpi, roles, name_)
        return " ".join(("less " if t.sign < 0 else ("plus " if i else "")) + term(t)
                        for i, t in enumerate(terms))

    text = side("num") + (f", divided by {side('den')}" if kpi.den else "")
    if kpi.at == "latest" and pack.snapshot_key in roles:
        text += f", at each {roles[pack.snapshot_key]}'s latest snapshot"
    if any(not t.kpi for t in used_terms(pack, kpi, roles)):   # a ratio of KPIs says it there
        left_out = [pack.label(k) for k in ROW_KINDS if k not in kpi.rows and k in present]
        text += f"; over {' and '.join(pack.label(k) for k in kpi.rows)}"
        text += f" ({', '.join(left_out)} left out)" if left_out else ""
    return text[0].upper() + text[1:] + "."


def glossary_hits(pack: Pack, question: str, columns: list[str]) -> list[tuple[str, str]]:
    """[(concept, the words used)] for each glossary concept the question names as whole words,
    unless the words are just one of the data's own column names ("revenue" in a file with a
    revenue column needs no glossary)."""
    q = question.lower()
    own = {normalize_header(c) for c in columns}
    found = [(m.start(), m.end(), concept, w)
             for concept, said in [*pack.glossary.items(), (None, pack.consumed)]
             for w in said for m in re.finditer(rf"(?<![\w-]){re.escape(w.lower())}(?![\w-])", q)]
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
    if "return" in present and (any(r in roles for r in pack.negative_is_return)
                                or any(r in roles for r in pack.credit_prefix)):
        neg = " or ".join(r for r in pack.negative_is_return if r in roles)
        credit = [f"{r} starting {p!r}" for r, p in pack.credit_prefix.items() if r in roles]
        rules.append(pack.say("return_rule") + ": " + " or ".join(
            ([f"a negative {neg}"] if neg else []) + credit))
    sv = pack.status_values
    if "status" in roles and "refunded" in present and sv.get("return"):
        rules.append(f"{pack.say('refunded_rule')}: status in {sv['return']}")
    if "status" in roles and "cancel" in present and sv.get("cancel"):
        rules.append(f"{pack.say('cancel_rule')}: status in {sv['cancel']}")
    chain = pack.version_order in roles and all(r in roles for r in pack.version_key)
    if chain and "cancel" in present and (void := {r: v for r, v in pack.version_void.items()
                                                   if r in roles}):
        rules.append(f"{pack.say('cancel_rule')}: every row of a " + " + ".join(pack.version_key)
                     + " whose last " + pack.version_order + " has "
                     + " or ".join(f"{r} in {v}" for r, v in void.items()))
    if "non_product" in present:
        rules += [f"{pack.say('non_product_rule')}: {r} in {v}" for r, v in pack.non_product.items()
                  if r in roles]
        if chain:
            rules.append(f"{pack.say('non_product_rule')}: a row with a later {pack.version_order} "
                         "for the same " + " + ".join(pack.version_key))
    test = [r for r in pack.test_roles if r in roles]
    if pack.test_marker and test and "test" in present:
        rules.append(f"a {pack.label('test').removesuffix('s')} (left out): {' or '.join(test)} "
                     f"matching {pack.test_marker!r}")
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
        lines.append(f"`df` has one column more than the schema shows: '{kind_column}', each row's "
                     f"kind ({', '.join(pack.code(k) for k in ROW_KINDS)}, other_currency), "
                     "computed by these rules: filter on "
                     "it, never re-derive it. Personal columns (email, names) reach you as "
                     f"pseudonyms, so {pack.label('test')} cannot be found from them: only this "
                     f"column marks them. {pack.say('rows_brief')} Column "
                     "profiles (eda_summary, distributions) describe every row, as loaded.")
    if rules := row_rules(pack, roles, present):
        lines.append(f"Row kinds: {rules}.")
    if currency and "currency" in roles:
        lines.append(f"Every figure uses {currency} rows only: rows in another currency are left "
                     "out (never added across currencies).")
    if grain == "line" and pack.order_key:
        lines.append(pack.say("grain_line", key=pack.order_key))
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
    for r, expr in pack.derived.items():   # with_derived adds these to the frame code reads
        if r not in roles and expr[0] in roles and expr[2] in roles:
            how = (f"days from {named[expr[2]]} to {named[expr[0]]} (same day = 0)" if expr[1] == "days"
                   else f"{named[expr[0]]} {'×' if expr[1] == '*' else '−'} {named[expr[2]]}")
            lines.append(f"Column {pack.roles[r].headers[0]} is in the data for you: {how}. Use it; "
                         "never compute your own, and never report a KPI as if it were a column.")
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
    named = [r.replace("_", " ") for r in ordered[:5]]
    shown = and_list(named)
    # as the tiles show them: a KPI of non-sale rows only (return rate) when the data has some
    kinds = {s for s in flags if s in ROW_KINDS}
    kpis = ", ".join(k.phrase for k in pack.kpis if k.tile and computable(pack, k.name, roles)
                     and ("sale" in k.rows or set(k.rows) & kinds))
    date = roles.get(pack.date_role)
    span = audit.date_ranges.get(date) if date and audit else None
    facts: dict[str, dict] = {}
    if confidence == "weak":
        facts["read_as_domain_weak"] = {"roles": shown}
    elif confidence == "strong":
        facts["read_as_domain"] = {"kpis": kpis or pack.say("kpis_default")}
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
    states = [k for k in pack.kpis if k.at == "latest" and computable(pack, k.name, roles)]   # spells: window_words
    if span and states:   # a balance is a state at a date (a ledger's running balance is no KPI)
        facts["level_as_of"] = {"date": pd.Timestamp(span[1]).strftime("%-d %b %Y")}
    if span and pack.runout and pack.runout.role in roles:   # claims still arriving
        last = pd.Period(span[1], "M")
        facts["recent_incomplete"] = {"from_month": (last - pack.runout.months + 1).strftime("%b %Y"),
                                      "months": pack.runout.months}
    if span and pack.maturity:
        valued = audit.date_ranges.get(roles.get(pack.maturity.role, ""))
        facts.update(_developing(pack.maturity, span[0], span[1], valued[1] if valued else None))
    if "other_currency" in flags and audit and audit.main_currency:
        facts["mixed_currency"] = {"n": f"{flags['other_currency'].rows_affected:,}",
                                   "currency": audit.main_currency}
    said = [pack.caveats[c].format(**facts[c]) for c in CONDITIONS if c in facts and c in pack.caveats]
    return said + [text for role, text in pack.role_caveats.items() if role in roles]



def version_roles(pack: Pack | None) -> set[str]:
    """The roles of a version chain (a claim's line and adjustment number): labels, never measures."""
    return {*pack.version_key, *([pack.version_order] if pack.version_order else [])} if pack else set()


def version_grain(contract) -> tuple[list[str], str] | None:
    """(the version chain's key columns, its order column) in this data (a strong match), or None:
    one row is one key at one order value (a claim and claimant at one valuation)."""
    pack = defaults_pack(contract)
    keys = [contract.roles[r] for r in (pack.version_key if pack else []) if r in contract.roles]
    order = contract.roles.get(pack.version_order) if pack and pack.version_order else None
    return (keys, order) if keys and order else None


def and_list(items: list[str]) -> str:
    """'a, b and c'; '' for none."""
    return ", ".join(items[:-1]) + (" and " if len(items) > 1 else "") + items[-1] if items else ""


def _developing(maturity: Maturity, first, last, valued) -> dict:
    """The period years (first..last) younger than maturity.months at `valued`, the latest
    valuation date (None: at the last period date): {condition: {as_of, periods}}; none → {}."""
    as_of = pd.Timestamp(valued if valued is not None else last)
    ages = [(y, (as_of.year - y) * 12 + as_of.month)
            for y in range(as_of.year, pd.Timestamp(first).year - 1, -1)]
    young = [f"{y} ({a} month{'s' if a != 1 else ''})" for y, a in ages if a < maturity.months]
    if not young:
        return {}
    key = "developing_periods" if valued is not None else "developing_periods_undated"
    return {key: {"as_of": as_of.strftime("%-d %b %Y"), "periods": and_list(young)}}


def developing_trend(contract, df: pd.DataFrame, col: str) -> str:
    """The developing-periods note for a period comparison of `col` ('' when it doesn't develop):
    THE rule that skips a built-in MoM / YoY (analysis_agent and the eval harness)."""
    return developing_note(contract, df, roles_of(contract, col))


def developing_note(contract, df: pd.DataFrame, about: set[str]) -> str:
    """' <the pack's developing-periods caveat>' for a period answer over this frame about these
    roles (strong match, Pack.maturity, a measure that develops), else '': a drop in a young
    year is development, not a trend; written premium doesn't develop."""
    pack = defaults_pack(contract)
    col = contract.roles.get(pack.date_role) if pack else None
    if not pack or not pack.maturity or col not in df.columns:
        return ""
    m = pack.maturity
    header = expand_header(normalize_header(col), pack)   # as detection reads it ('Eff Date')
    developing = {*m.measures, *(r for r, heads in m.dated.items() if header in heads)}
    if not about & developing:
        return ""
    t = _day(df[col]).dropna()
    valued = contract.roles.get(pack.maturity.role)
    v = _day(df[valued]).max() if valued in df.columns else None
    facts = _developing(pack.maturity, t.min(), t.max(), v) if not t.empty else {}
    return "".join(f" {pack.caveats[k].format(**f)}" for k, f in facts.items() if k in pack.caveats)


def runout_months(contract) -> int:
    """Months whose records are still arriving (Pack.runout, when its arrival date is in the
    data): every period answer leaves them out (utils.cut_partial)."""
    pack = defaults_pack(contract)
    return pack.runout.months if pack and pack.runout and pack.runout.role in contract.roles else 0


def first_line_only(df: pd.DataFrame, by: pd.Series, col: str) -> bool:
    """The export writes `col` on an order's first line and leaves the rest blank, in at least 80%
    of the orders with several lines (Shopify): a layout, not missing data."""
    multi = by.notna() & by.duplicated(keep=False)
    if not multi.any():
        return False
    first = ~by[multi].duplicated()
    filled = df.loc[multi, col].notna()
    per = pd.DataFrame({"o": by[multi], "first_only": filled.eq(first)}).groupby("o")["first_only"].all()
    return bool(per.mean() >= 0.8)


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
    for role in pack.marker_fields:   # blank = none (no event): the layout, never missing
        col = roles.get(role)
        if col in df.columns and col not in out and df[col].isna().any():
            out[col] = "marker"
    return out


def layout_blanks(df: pd.DataFrame, contract) -> list[str]:
    """The columns of layout_columns, for a contract."""
    pack = pack_for(contract.domain) if contract is not None else None
    return list(layout_columns(df, pack, {r: c for r, c in (contract.roles if pack else {}).items()
                                          if c in df.columns}))


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
    return df.assign(**{name: kinds.map(pack.code).where(keep, "other_currency")}), name


def counted(pack: Pack, role: str) -> bool:
    """An additive amount or count (net sales, units): its totals use the KPI rows (sale and
    return rows, the main currency), so a trend and its tile are one number."""
    return pack.roles[role].kind in ("money", "count") and pack.roles[role].additive


def rows_for(df: pd.DataFrame, contract, column: str,
             full: pd.DataFrame | None = None) -> tuple[pd.DataFrame, str]:
    """(rows, how): THE rows every answer, chart and drill-down of `column` reads (plan v2 R1:
    one entry point; tests/test_one_definition.py guards it). On a strong-matched pack's additive
    money or count column: sale and return rows in the main currency, as its tiles count them;
    a level (a balance) keeps every row with its value blanked where the row is left out, so
    `utils.aggregate` / `latest_rows` judge each entity's latest row first. Anything else: every
    row, no note."""
    pack = defaults_pack(contract)
    role = next((r for r, c in contract.roles.items() if c == column), None) if pack else None
    if not (role and counted(pack, role)):
        return df, ""
    roles = {r: c for r, c in contract.roles.items() if c in df.columns}
    keep, currency, _ = main_currency(df, roles)
    # `full`: the whole data when `df` is a filtered part of it (a row's kind can depend on rows
    # outside the part: a claim line replaced later)
    kinds = row_kinds(full, roles, pack).reindex(df.index) if full is not None \
        and df.index.isin(full.index).all() else row_kinds(df, roles, pack)
    kept = keep & kinds.isin(["sale", "return"])
    if kept.all():
        return df, ""
    # a level (a balance) keeps every row, its value blank where the row is left out: each
    # account's latest row is judged first (a loan charged off this month is out, never its last
    # month's balance; utils.latest_rows), exactly as the tiles do (KPI.at latest)
    out = df.assign(**{column: df[column].where(kept)}) if pack.roles[role].over_time == "last" \
        else df[kept]
    return out, pack.say("money_rows_note", currency=f" in {currency}" if currency else "",
                              n=f"{int((~kept).sum()):,}")


def snapshot_entity(contract, columns) -> tuple[list[str], str] | None:
    """(entity columns, time column) when the data is snapshots: a level's (a balance's)."""
    lv = next((c for c in (contract.columns if contract is not None else [])
               if c.aggregation in ("last", "first") and c.aggregation_by and c.aggregation_time
               and all(x in columns for x in [*c.aggregation_by, c.aggregation_time])), None)
    return (lv.aggregation_by, lv.aggregation_time) if lv else None


def entity_rows(df: pd.DataFrame, contract) -> pd.DataFrame:
    """Rows for a statistic over entities (a correlation) on snapshot data: each entity's latest
    snapshot (any kind first), then only rows the tiles count (on the books, main currency);
    every snapshot would count an account 14 times. An account that stopped reporting keeps its
    last row (dropping it would bias toward survivors). Not snapshot data: the frame without a
    version chain's earlier versions (a claim's earlier valuations; none without a chain).
    Mirrored in the analysis sandbox's built-in correlation."""
    ent = snapshot_entity(contract, df.columns)
    if ent is None:
        return df[~superseded(df, contract)] if contract is not None else df
    from agents.utils import snapshot_rows
    rows = snapshot_rows(df, ent[0], ent[1], current=False)
    pack = defaults_pack(contract)   # row kinds on a strong match only, as rows_for
    if pack is None:
        return rows
    roles = {r: c for r, c in contract.roles.items() if c in rows.columns}
    keep, _, _ = main_currency(rows, roles)
    return rows[keep & row_kinds(rows, roles, pack).isin(["sale", "return"])]


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
        if n == "year":   # 13 complete months, by the one coverage rule answers use
            from agents.utils import _local, cut_partial
            date = roles.get(pack.date_role)
            t = _local(pd.to_datetime(df[date], errors="coerce")).dropna() if date else None
            if t is None or t.empty:
                return False
            months = t.groupby(t.dt.to_period("M")).size().sort_index()
            return len(cut_partial(months, t, "M")[0]) >= 13
        return n in kinds
    return [q if isinstance(q, str) else str(q["q"]) for q in pack.first_questions
            if isinstance(q, str) or all(any(has(a) for a in n.split("|")) for n in q.get("needs", []))]
