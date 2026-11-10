"""The pack: its models, loading (one YAML per domain in domain_packs/), and the primitives every
module reads (headers, words, dates, a role's column, a condition)."""
from __future__ import annotations

import re
from functools import cache
from pathlib import Path
from typing import Literal

import pandas as pd
import yaml
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, StrictFloat, StrictInt, model_validator


PACKS = Path(__file__).parent.parent / "domain_packs"
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
                  "non_product_rule", "money_rows_note", "people_nouns"]
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
    # the words for people a protected value qualifies ("3 Asian employees"): small cells
    "people_nouns": "people",
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
    # A category's numeric meaning in a formula (Pack.columns): a billing interval in months
    numbers: dict[str, float] = Field(default_factory=dict)
    # Headers another meaning shares (a subscription list's next `billing_date`): they fill a
    # spell pack's snapshot role only when each spell repeats over their dates (_snapshot_dates)
    weak_headers: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _weak_are_headers(self):
        if not set(self.weak_headers) <= set(self.headers):
            raise ValueError(f"weak_headers {self.weak_headers} must be among headers")
        return self


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


class Formula(_Strict):
    """A column the product adds to the data (Pack.columns): the product of `times`, net of the
    `net_of` rate, divided by the product of `over` (a subscription's MRR: price × seats ×
    (1 − discount) ÷ (interval months × interval count)). An `optional` role the data lacks, or
    a blank value of one, counts 1 (a rate: 0)."""
    times: list[str]
    over: list[str] = Field(default_factory=list)
    net_of: str | None = None
    optional: list[str] = Field(default_factory=list)


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


class SpellWords(_Strict):
    """A spell's own words, for the frame columns the model's code reads and the sentences the
    product writes: HR's hire / exit / people, a subscription's start / end / customers.
    Shared code never names them (tests/test_one_definition.py)."""
    start: str = "start"
    end: str = "end"
    unknown: str = "end_date_unknown"   # the column marking an ended status with no end date
    plural: str = "entities"            # who the entities are ('885 people', '412 customers')
    after_end: str = "rows after the end"   # an exit record's gloss on snapshots


class Spell(_Strict):
    """Entities in force between two dates: an employee from hire to termination, a subscriber
    from start to end. Rows are spells (a returning entity has a row per spell) or dated
    snapshots (the pack's date role, when it is neither `start` nor `end`). A blank `end` is open;
    a status in `ended` with a blank `end` is over at an unknown date (never in force). `as_of`:
    a report-date role (the extract's own date), else the latest snapshot or start/end date not
    after today."""
    entity: str
    start: str
    end: str
    ended: list[str] = Field(default_factory=list)
    as_of: str | None = None
    words: SpellWords = Field(default_factory=SpellWords)
    # An entity holds several spells at once (a base plan and an add-on): every one in force
    # counts, summed for a role; otherwise only its latest spell started by t (a rehire)
    concurrent: bool = False
    # Snapshots labelled by month on its 1st ('2026-10-01' = October, an MRR export): the as-of
    # is the labelled month's end (clipped to today) when every snapshot date is a 1st
    month_labels: bool = False
    # A later date the spell counts from when filled (a trial's end: a subscription converted
    # from a trial pays from then, not from its start; red-team R3)
    paid_from: str | None = None


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
    spell: Literal["active", "average", "starts", "ends", "opening", "retained", "moves"] | None = None
    # the ledger's terms (Domain 6): `opening` = the entities in force at the window's start (a
    # cohort; with `sum`, their value), `retained` = that cohort's value at its end (`capped`: no
    # entity above its opening value: GRR), `moves` = the month-end changes of one kind summed
    move: Literal["new", "expansion", "contraction", "churn", "reactivation"] | None = None
    capped: bool = False
    rows: list[Literal["sale", "return", "refunded", "cancel", "test", "non_product"]] | None = None
    sign: Literal[1, -1] = 1
    optional: bool = False        # a missing role drops this term (and a blank value counts 0)
    net_of: str | None = None     # a rate role: each row's value × (1 − rate) (a 25% line discount)
    times: StrictInt | StrictFloat = 1   # a constant multiplier: ARR = 12 × MRR (never '12')
    # Only rows meeting one of these groups (each group: all its conditions): NPL = 90+ days
    # past due OR non-accrual, never counted twice.
    where: list[list[Cond]] = Field(default_factory=list)
    # ponytail: the pack's spell roles, set once by Pack._check, so `roles()` (read by
    # computable, used_terms, kpi_roles …) names them; a term outside a pack has none
    _spell_roles: list[str] = PrivateAttr(default_factory=list)

    @model_validator(mode="after")
    def _one(self) -> Term:
        if (self.move is not None) != (self.spell == "moves"):
            raise ValueError("`move` goes with `spell: moves` (which change it sums)")
        if self.capped and self.spell != "retained":
            raise ValueError("`capped` is a retained term's (GRR)")
        if self.spell is not None:
            if self.spell in ("average", "starts", "ends") and self.sum is not None:
                raise ValueError("a spell term sums a role only at dates (active, opening, "
                                 "retained) or over moves")
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
        if self.spell:   # the entity only: snapshots need no start or end column (a ledger's
            own = [*self._spell_roles, *own]   # history terms also need the snapshot date)
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
    # set by Pack._check: every numerator term is a state KPI (ARR = 12 × MRR is as of a date)
    _of_state: bool = PrivateAttr(default=False)

    @property
    def state(self) -> bool:
        """A state at a date (a balance, a headcount), never a flow over the period: THE test
        (`at: latest`, or a spell's entities in force)."""
        return self.at == "latest" or any(t.spell == "active" for alt in self.num for t in alt) \
            or self._of_state

    def written(self, value: str) -> str:
        """A formatted value with its unit word: THE way a KPI's number is written."""
        word = self.unit_word
        if word and value.strip() == "1" and " " not in word and word.endswith("s"):
            word = word[:-1]   # '1 employee', never '1 employees'
        return f"{value} {word}" if word else value

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
    # Special-category attributes and pay (gender, ethnicity, salary): a group by one of these,
    # or one of these measured by any group, of fewer than SMALL_CELL distinct people is never
    # shown (any match, not only patient data; counted in people: the spell entity or snapshot_key)
    protected_roles: list[str] = Field(default_factory=list)
    # The role naming one person, when no spell or snapshot_key does (a policyholder's policy):
    # who a protected small cell counts (without it, the rows counted)
    person_key: str | None = None
    # The entity an average of the pack's money divides by (a claim on claim-line or claimant
    # rows): 'average paid by payer' is per claim, never per line
    average_per: str | None = None
    # A role the data may lack but can derive ({los: [date, days, admit_date]}): the analysis
    # frame gets it as a column named after the role's first header, so the model never invents
    # one ("LOS (derived)" was rejected as an unknown column)
    derived: dict[str, list[str]] = Field(default_factory=dict)
    # A role the product adds to the data at ingestion when the data lacks it (a subscription
    # list's MRR from its price, seats, discount and interval): one column every reader reads
    # (KPIs, charts, filters, the model), never a formula repeated per KPI
    columns: dict[str, Formula] = Field(default_factory=dict)
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
                *self.role_caveats, *self.protected_roles, *(k.per for k in self.kpis if k.per),
                *([self.person_key] if self.person_key else []),
                *([self.spell.entity, self.spell.start, self.spell.end,
                   *([self.spell.as_of] if self.spell.as_of else []),
                   *([self.spell.paid_from] if self.spell.paid_from else [])] if self.spell else []),
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
        for r, f in self.columns.items():
            errors += [f"columns {r}: unknown role {x}" for x in
                       [r, *f.times, *f.over, *([f.net_of] if f.net_of else []), *f.optional]
                       if x not in self.roles]
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
        for k in self.kpis:   # in order: a KPI refers to KPIs written before it
            # ponytail: a ratio of state KPIs isn't marked (banking's avg_balance is a logged
            # follow-up with its own re-measure); a ratio of spell terms is a state already
            k._of_state = k.den is None and all(
                t.kpi and t.kpi in names and self.kpi(t.kpi) and self.kpi(t.kpi).state
                for alt in k.num for t in alt)
            for alt in [*k.num, *(k.den or [])]:
                for t in alt:
                    if t.spell and not self.spell:
                        errors.append(f"kpi {k.name}: a spell term needs the pack's spell")
                    elif t.spell:
                        # a value's history (expansion, retention) exists only on dated snapshots:
                        # a subscription list holds today's price at both ends (red-team R4)
                        history = t.move in ("expansion", "contraction") or t.spell == "retained" \
                            and t.sum is not None
                        t._spell_roles = [self.spell.entity, *(
                            [self.date_role] if history and self.date_role not in
                            (self.spell.start, self.spell.end) else [])]
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


def expand_header(norm: str, pack: Pack) -> str:
    """A normalized header with the pack's abbreviations spelled out: cur_bal → current_balance."""
    return "_".join(pack.abbreviations.get(t, t) for t in norm.split("_"))


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


def snapshot_role(pack: Pack) -> str | None:
    """THE spell's snapshot date role: the pack's date role when it is neither the start nor the
    end (an MRR export's month, HR's snapshot date)."""
    s = pack.spell
    return pack.date_role if s and pack.date_role not in (s.start, s.end) else None


def _snapshot_column(pack: Pack, roles: dict[str, str]) -> str | None:
    """A spell's snapshot date column (snapshot_role, when the data has it): rows are then dated
    snapshots, not spells."""
    return roles.get(snapshot_role(pack)) if snapshot_role(pack) else None


def spell_terms(pack: Pack, name: str) -> list[Term]:
    """The spell terms a KPI reads, through the KPIs it refers to."""
    kpi = pack.kpi(name)
    out: list[Term] = []
    for t in (t for alt in [*kpi.num, *(kpi.den or [])] for t in alt):
        out += [t] if t.spell else spell_terms(pack, t.kpi) if t.kpi else []
    return out


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


def ordinal(n: int) -> str:
    """12 → '12th', 1 → '1st', 22 → '22nd'."""
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def and_list(items: list[str]) -> str:
    """'a, b and c'; '' for none."""
    return ", ".join(items[:-1]) + (" and " if len(items) > 1 else "") + items[-1] if items else ""


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


def defaults_pack(contract) -> Pack | None:
    """The pack whose conventions change default behaviour (tiles, sums by group, weekly months,
    partial weeks, follow-up checks): only on a strong match. A weak match changes nothing by
    default; it says how it read the file, and answers a KPI the user names."""
    if contract is None or contract.domain_confidence != "strong":
        return None
    return pack_for(contract.domain)
