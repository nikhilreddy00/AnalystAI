"""
Domain packs (Phase 5.7): a domain's knowledge as data, one YAML file per domain in domain_packs/.
A pack names the domain's column roles by their header variants, its KPIs as structured formulas,
its glossary, its sentinels and caveat text. The code here is domain-neutral: detection, the row
kinds (sale / return / refunded / cancel / test / non-product), the KPI arithmetic. Adding a
domain is a YAML file, never an agent edit. An invalid pack stops the API and the worker at boot.
"""
from __future__ import annotations

import re
import threading
from functools import cache
from pathlib import Path
from typing import Literal

import pandas as pd
import yaml
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, StrictFloat, StrictInt, model_validator

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
    product writes: HR's hire / exit / people, a subscription's start / cancellation / customers.
    Shared code never names them (tests/test_one_definition.py)."""
    start: str = "start"
    end: str = "end"
    unknown: str = "end_date_unknown"   # the column marking an ended status with no end date
    plural: str = "entities"            # who the entities are ('885 people', '412 customers')
    after_end: str = "rows after the end"   # an exit record's gloss on snapshots


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


# A spell series reaches back at most this many years and has at most this many periods (one
# in-force pass per period: a weekly axis over a decade is 520 passes).
# ponytail: one pass per period; an interval sweep over start/end events if rosters pass ~500k rows
SPELL_YEARS = 10
SPELL_PERIODS = 520


def snapshot_role(pack: Pack) -> str | None:
    """THE spell's snapshot date role: the pack's date role when it is neither the start nor the
    end (an MRR export's month, HR's snapshot date)."""
    s = pack.spell
    return pack.date_role if s and pack.date_role not in (s.start, s.end) else None


def _snapshot_column(pack: Pack, roles: dict[str, str]) -> str | None:
    """A spell's snapshot date column (snapshot_role, when the data has it): rows are then dated
    snapshots, not spells."""
    return roles.get(snapshot_role(pack)) if snapshot_role(pack) else None


def spell_as_of(pack: Pack, df: pd.DataFrame, roles: dict[str, str]) -> pd.Timestamp | None:
    """THE date a spell KPI is measured at: the report-date role, else the latest snapshot, else
    the latest start or end date not after today (a notice period's exit, a pre-boarded hire:
    not yet). Always judged on all rows (a filter never moves it)."""
    from agents.utils import today
    return _memo(df, ("as_of", *_spell_key(pack, roles), today()),
                 lambda: _spell_as_of(pack, df, roles))


def _spell_as_of(pack: Pack, df: pd.DataFrame, roles: dict[str, str]) -> pd.Timestamp | None:
    """spell_as_of's rule, uncached."""
    from agents.utils import today
    s = pack.spell
    snap = _snapshot_column(pack, roles)
    for col in [roles.get(s.as_of) if s.as_of else None, snap]:
        if col and pd.notna(t := _day(df[col]).max()):
            if col == snap and s.month_labels and (_day(df[col]).dropna().dt.day == 1).all():
                return min(t + pd.offsets.MonthEnd(0), today())
            return t
    dates = pd.concat([_day(df[roles[r]]) for r in (s.start, s.end, s.paid_from) if r in roles]
                      or [pd.Series(dtype="datetime64[ns]")])
    dates = dates[dates <= today()]
    return dates.max() if dates.notna().any() else None


def spell_from(pack: Pack, df: pd.DataFrame, roles: dict[str, str]) -> pd.Timestamp | None:
    """The first date a spell KPI is shown from: the first snapshot, else the earliest end (a
    roster lists only recent leavers: before its first exit the headcount is understated), else
    the earliest start."""
    s = pack.spell
    for col in [_snapshot_column(pack, roles), roles.get(s.end), roles.get(s.start)]:
        if col and pd.notna(t := _day(df[col]).min()):
            as_of = spell_as_of(pack, df, roles)   # bounded: a 1900 typo never means 6,000 months
            return max(t, as_of - pd.DateOffset(years=SPELL_YEARS)) if as_of is not None else t
    return None


def spell_window(pack: Pack, df: pd.DataFrame, roles: dict[str, str],
                 window: tuple | None = None) -> tuple[pd.Timestamp, pd.Timestamp, list] | None:
    """THE window of a spell KPI asked without a period grouping: (lo, as_of, month-ends), flows
    counted in (lo, as_of] and the average over the month-ends. The 12 months to the as-of, or
    the shorter span the data covers (month-ends on or after its first date: a stock there is
    complete); an asked window (lo, hi) clips both ends."""
    as_of, first = spell_as_of(pack, df, roles), spell_from(pack, df, roles)
    if as_of is None or first is None:
        return None
    if window and window[1] is not None:
        as_of = min(as_of, pd.Timestamp(window[1]).normalize())
    lo = as_of - pd.DateOffset(months=12)
    ends = _month_ends(lo, as_of)
    if window and window[0] is not None:   # an asked start: its own month's end counts ('since
        lo = pd.Timestamp(window[0]).normalize() - pd.Timedelta(days=1)   # 15 Mar' keeps 31 Mar)
        ends = [e for e in _month_ends(lo - pd.offsets.MonthEnd(1), as_of) if e > lo]
    return max(lo, first - pd.Timedelta(days=1)), as_of, [e for e in ends if e >= first]


def spell_covered(pack: Pack, df: pd.DataFrame, roles: dict[str, str]) -> pd.Series:
    """The dates a spell series covers, as month-end points from its first date to its as-of
    (cut_partial reads the spacing: a partial last year or month is cut and named)."""
    first, as_of = spell_from(pack, df, roles), spell_as_of(pack, df, roles)
    if first is None or as_of is None:
        return pd.Series(dtype="datetime64[ns]")
    return pd.Series([min(m.end_time.normalize(), as_of) for m in pd.period_range(first, as_of, freq="M")])


def _month_ends(a: pd.Timestamp, b: pd.Timestamp) -> list[pd.Timestamp]:
    """The month-ends of the months after a's up to b's, the last one clipped to b: the 12
    month-end headcounts of the 12 months to 24 Oct are Nov … Sep and 24 Oct (never also 31 Oct
    of the year before, a 13th)."""
    if b <= a:
        return []
    months = pd.period_range(a.to_period("M") + 1, b.to_period("M"), freq="M")
    return list(dict.fromkeys(min(m.end_time.normalize(), b) for m in months))


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
    lo, as_of, pts = w
    months = len(pts)
    if kpi.state:
        return f"as of {as_of:%-d %b %Y}"
    spells = {t.spell for t in spell_terms(pack, name)}
    if "moves" in spells:   # moves start after the first known state (pass 6: a month too many)
        known = first_known(pack, roles, spell_from(pack, df, roles))
        months = sum(p > known for p in pts)
        if months < 1:
            return ""
    if spells & {"opening", "retained"}:   # whatever period is asked: the cohort ending there
        start = cohort_start(as_of, first_known(pack, roles, spell_from(pack, df, roles)))
        if spells == {"opening"}:   # a level at the cohort's start (Opening MRR)
            return f"on {start:%-d %b %Y}"
        n = round((as_of - start).days / 30.44)
        return f"{n}-month cohort to {as_of:%-d %b %Y}" if n >= 1 else ""
    return f"{months} month{'s' * (months != 1)} to {as_of:%-d %b %Y}"


MOVES = ("new", "expansion", "contraction", "churn", "reactivation")


def ledger_kpi(pack: Pack, roles: dict[str, str], spell: str, move: str | None = None,
               unit: str = "money") -> KPI | None:
    """THE KPI made of one ledger term (new MRR = moves/new; churned customers = moves/churn as a
    count; opening MRR = opening) the data can compute: the story and the facts read it."""
    return next((k for k in pack.kpis if computable(pack, k.name, roles) and k.den is None
                 and k.unit == unit and len(alt := k.num[0]) == 1 and alt[0].spell == spell
                 and alt[0].move == move), None)


def stock_kpi(pack: Pack, roles: dict[str, str], unit: str | None = None) -> KPI | None:
    """THE stock tile: the first tile that is a state made of spell terms the data can compute
    (headcount, MRR); `unit` narrows it to a count (who is in force) or a money one (audit pass 1:
    the choice was copied six times)."""
    return next((k for k in pack.kpis if k.tile and k.state and (unit is None or k.unit == unit)
                 and spell_terms(pack, k.name) and computable(pack, k.name, roles)), None)


def spell_terms(pack: Pack, name: str) -> list[Term]:
    """The spell terms a KPI reads, through the KPIs it refers to."""
    kpi = pack.kpi(name)
    out: list[Term] = []
    for t in (t for alt in [*kpi.num, *(kpi.den or [])] for t in alt):
        out += [t] if t.spell else spell_terms(pack, t.kpi) if t.kpi else []
    return out


def _amount(pack: Pack, df: pd.DataFrame, roles: dict[str, str], kpi: KPI, v: float) -> str:
    """A spell fact's number in its KPI's unit: money with the main currency ('$851,102'), a
    count or number as before ('885')."""
    if kpi.unit == "money":
        from agents.visualization_agent import _format_currency
        return _format_currency(v, main_currency(df, roles)[1])
    return f"{v:,.0f}"


def spell_facts(pack: Pack, df: pd.DataFrame, roles: dict[str, str]) -> list[str]:
    """The product's own spell numbers for a model to divide by, never rebuild (Domain 5
    red-team B6: the model re-deriving a row rule was the one root cause of Domain 4's analysis
    failures): the as-of, the window, the month-end counts, their mean, and what is dated after
    the as-of."""
    s = pack.spell
    stock = stock_kpi(pack, roles)
    mean = next((k for k in pack.kpis if any(t.spell == "average" for alt in k.num for t in alt)
                 and computable(pack, k.name, roles)), None)
    w = spell_window(pack, df, roles)
    if not s or stock is None or w is None:
        return []
    lo, as_of, pts = w
    months = len(pts)
    frame = df.assign(_period=pd.Series(pd.NaT, index=df.index).astype("period[M]"))
    series = compute_kpi(frame, roles, pack, stock.name, by="_period", full=df)
    shown = ", ".join(f"{t:%b %Y} {_amount(pack, df, roles, stock, series.get(t.to_period('M')))}" for t in pts
                      if series is not None and pd.notna(series.get(t.to_period("M"))))
    lines = [f"As of {as_of:%-d %b %Y}: the window is the {months} months "
             f"{(lo + pd.Timedelta(days=1)):%-d %b %Y} – {as_of:%-d %b %Y}."]
    if shown:
        lines.append(f"{stock.label} at its month-ends: {shown}.")
    if mean and (v := compute_kpi(df, roles, pack, mean.name)) is not None:
        lines.append(f"{mean.label} = {v:,.1f} (their mean): divide the window's counts by it "
                     "for a rate; never recount it from rows.")
        frame_y = df.assign(_period=pd.Series(pd.NaT, index=df.index).astype("period[Y]"))
        years = compute_kpi(frame_y, roles, pack, mean.name, by="_period", full=df)
        if years is not None and len(years):   # a year's rate divides by that year's own base
            lines.append(f"{mean.label} by year (each year's month-ends; the last year to "
                         f"{as_of:%-d %b %Y} only): " + ", ".join(
                             f"{p} {x:,.1f}" for p, x in years.items() if pd.notna(x)) + ".")
        for g in [g for g in pack.overview_groups if g in roles][:2]:   # a group's own base
            by = compute_kpi(df, roles, pack, mean.name, by=roles[g])
            if by is not None and len(by) <= 15:
                lines.append(f"{mean.label} by {roles[g]} (a group's rate divides by its own): "
                             + ", ".join(f"{k} {x:,.1f}" for k, x in by.items()) + ".")
    return lines + _ledger_facts(pack, df, roles, lo, as_of) + spell_story(pack, df, roles)


def _ledger_facts(pack: Pack, df: pd.DataFrame, roles: dict[str, str], lo, as_of) -> list[str]:
    """The ledger's numbers by month and by group, for a model to quote, never rebuild (Domain 6
    analysis gate: re-deriving a cohort by segment or a month's churn went wrong in 4 of 33
    runs): each money move by month, the paying count at each month-end and the monthly churn
    (churned at a month-end ÷ in force at the one before), each cohort rate tile by the first
    two overview groups. Only for KPIs made of ledger terms (no other pack has them)."""
    out: list[str] = []
    frame = df.assign(_period=pd.Series(pd.NaT, index=df.index).astype("period[M]"))
    # months that start inside the window (a 29 Oct as-of: Nov … Oct, never the 2-day sliver
    # of the October before shown as a whole month); `upto` also the month-end before them
    months = lambda s: [p for p in s.index if p.start_time.normalize() > lo]   # noqa: E731
    upto = lambda s: [p for p in s.index if (p + 1).start_time.normalize() > lo]   # noqa: E731
    for k in (k for u in ("money", "count") for m in MOVES if (k := ledger_kpi(pack, roles, "moves", m, u))):
        if (s := compute_kpi(frame, roles, pack, k.name, by="_period", full=df)) is not None:
            out.append(f"{k.label} by month (12 months to {as_of:%-d %b %Y}): " + ", ".join(
                f"{p} {_amount(pack, df, roles, k, s[p])}" for p in months(s)) + ".")
    heads = stock_kpi(pack, roles, "count")
    lost = ledger_kpi(pack, roles, "moves", "churn", "count")
    if heads and lost:
        n = compute_kpi(frame, roles, pack, heads.name, by="_period", full=df)
        c = compute_kpi(frame, roles, pack, lost.name, by="_period", full=df)
        if n is not None and c is not None:
            out.append(f"{heads.label} at each month-end: " + ", ".join(
                f"{p} {n[p]:,.0f}" for p in upto(n)) + ".")
            rate = {p: c.get(p, 0) / n[p - 1] for p in months(n) if (p - 1) in n.index and n[p - 1]}
            out.append(f"Monthly churn ({lost.phrase} at a month-end ÷ {heads.phrase} at the month-end "
                       "before; never × 12 into an annual rate): " + ", ".join(
                           f"{p} {v:.2%}" for p, v in rate.items()) + ".")
    for k in (k for k in pack.kpis if k.tile and k.unit == "percent" and computable(pack, k.name, roles)
              and any(t.spell in ("opening", "retained") for t in spell_terms(pack, k.name))):
        for g in [g for g in pack.overview_groups if g in roles][:2]:
            if (by := compute_kpi(df, roles, pack, k.name, by=roles[g])) is not None and len(by) <= 15:
                out.append(f"{k.label} by {roles[g]} (each group's own cohort, placed by its row on "
                           f"{lo:%-d %b %Y}): " + ", ".join(f"{x} {v:.1%}" for x, v in by.items()) + ".")
    return out


def spell_story(pack: Pack, df: pd.DataFrame, roles: dict[str, str]) -> list[str]:
    """The file's story in the product's own numbers (Domain 5 audit: overviews missed a layoff,
    a model's YoY contradicted the product's, rows were read as people): the tiles' rates now and
    the same window a year earlier (in points), the stock's peak in the window against now, the
    largest month of ends against the usual month and the group most of it came from, people
    against rows, and what is dated after the as-of. Computed facts for the analysis and the
    insight, never re-derived by a model."""
    if not has_spell(pack, roles) or (w := spell_window(pack, df, roles)) is None:
        return []
    lo, as_of, pts = w
    out: list[str] = []
    stock = stock_kpi(pack, roles)
    ends = next((k for k in pack.kpis if k.tile and k.unit == "count" and computable(pack, k.name, roles)
                 and [t.spell for t in used_terms(pack, k, roles)] == ["ends"]), None)
    back = (None, as_of - pd.DateOffset(years=1))   # the default window a year earlier (12 points)
    prior_ok = (pw := spell_window(pack, df, roles, back)) is not None and len(pw[2]) >= len(pts)
    for k in pack.kpis:   # each rate tile now and a year earlier, the change in points
        if not (k.tile and k.unit == "percent" and spell_terms(pack, k.name) and computable(pack, k.name, roles)):
            continue
        now = compute_kpi(df, roles, pack, k.name)
        cohort = any(t.spell in ("opening", "retained") for t in spell_terms(pack, k.name))
        whole_year = not cohort or (back[1] - pd.DateOffset(months=12)).to_period("M") >= first_known(
            pack, roles, spell_from(pack, df, roles)).to_period("M")   # an 11-month cohort is no 12
        then = compute_kpi(df, roles, pack, k.name, window=back) if prior_ok and whole_year else None
        if now is not None:
            out.append(f"{k.label} ({window_words(pack, df, roles, k.name)}): {now:.1%}" + (
                f"; a year earlier ({window_words(pack, df, roles, k.name, back)}): {then:.1%}, "
                f"{(now - then) * 100:+.1f} points" if then is not None
                else "; no full year-earlier window in the data") + ".")
    frame = df.assign(_period=pd.Series(pd.NaT, index=df.index).astype("period[M]"))
    if stock is not None and (s_ := compute_kpi(frame, roles, pack, stock.name, by="_period", full=df)) is not None:
        inside = s_[[p for p in s_.index if p.end_time.normalize() > lo]].dropna()
        if len(inside) > 1 and inside.idxmax() != inside.index[-1] \
                and inside.iloc[-1] <= 0.95 * inside.max():   # a material fall only (5%+)
            peak, now = inside.max(), inside.iloc[-1]
            out.append(f"{stock.label} peaked at {_amount(pack, df, roles, stock, peak)} "
                       f"({inside.idxmax().strftime('%b %Y')}) and is {_amount(pack, df, roles, stock, now)} "
                       f"at {as_of:%-d %b %Y} ({(now / peak - 1) * 100:+.1f}%).")
    if ends is not None and (e := compute_kpi(frame, roles, pack, ends.name, by="_period", full=df)) is not None \
            and len(e) >= 6:
        top, usual = e.idxmax(), float(e.drop(e.idxmax()).median())
        if e[top] >= 10 and e[top] >= 2.5 * max(usual, 1):
            line = (f"The largest month of {ends.phrase}: {top.strftime('%b %Y')} with {e[top]:,.0f} (a usual month: "
                    f"{usual:,.0f})")
            group = next((roles[g] for g in pack.overview_groups if g in roles), None)
            if group and (g := compute_kpi(df, roles, pack, ends.name, by=group, full=df,
                                           window=(top.start_time, top.end_time.normalize()))) is not None \
                    and len(g) and g.max() >= 0.5 * e[top]:
                line += f", {g.max():,.0f} of them in {g.idxmax()} ({group})"
            out.append(line + ".")
    out += _ledger_story(pack, df, roles, lo, as_of)
    col = _person_col(pack, roles)
    heads = stock_kpi(pack, roles, "count")
    if col:
        people = df[col].nunique()
        now_n = compute_kpi(df, roles, pack, heads.name) if heads else None
        out.append(f"{len(df):,} rows are {people:,} {pack.spell.words.plural} ({int((df[col].value_counts() > 1).sum()):,} with "
                   "more than one row)" + (f"; {now_n:,.0f} in force at {as_of:%-d %b %Y}" if now_n else "")
                   + ".")
    if later := not_counted_yet(pack, df, roles, as_of):
        out.append(later)
    undated = int(_ended_undated(df, roles, pack).sum())
    if undated:
        out.append(f"{undated:,} rows have an ended status but no end date.")
    return out


def _ledger_story(pack: Pack, df: pd.DataFrame, roles: dict[str, str], lo, as_of) -> list[str]:
    """The ledger's facts (Domain 6 S9), from the pack's KPIs of one moves or opening term each:
    the bridge (opening + new + expansion + reactivation − contraction − churned = now), the
    churned-customer count beside the cohort rate (they differ: new and returning customers'
    churns are in the count only), and the largest month of churned value against a usual
    month, with its largest single entity's share and group. No line without its KPIs."""
    out: list[str] = []
    stock = stock_kpi(pack, roles, "money")
    parts = {m: ledger_kpi(pack, roles, "moves", m) for m in ("new", "expansion", "reactivation",
                                                              "contraction", "churn")}
    opening = ledger_kpi(pack, roles, "opening")
    say = lambda k, v: _amount(pack, df, roles, k, v)   # noqa: E731
    if stock and opening and parts["new"] and parts["churn"]:
        val = {m: compute_kpi(df, roles, pack, k.name) for m, k in parts.items() if k}
        start, now = compute_kpi(df, roles, pack, opening.name), compute_kpi(df, roles, pack, stock.name)
        if start is not None and now is not None and None not in val.values():
            terms = [f"+ {k.phrase} {say(k, val[m])}" if m not in ("contraction", "churn")
                     else f"− {k.phrase} {say(k, val[m])}" for m, k in parts.items() if k]
            rest = now - start - sum(v if m not in ("contraction", "churn") else -v for m, v in val.items())
            if not parts["expansion"] and abs(rest) >= 0.005:   # it ties, always (pass 3: \$198 off)
                terms.append(f"{'+' if rest > 0 else '−'} net expansion less contraction (not split in "
                             f"this file) {say(stock, abs(rest))}")
            begin = cohort_start(as_of, first_known(pack, roles, spell_from(pack, df, roles)))
            span = window_words(pack, df, roles, parts["new"].name)   # the moves' own words
            if not span or begin >= as_of:
                return out   # no month of moves: no bridge (pass 5: '0 months to 26 Oct')
            out.append(f"{stock.label} bridge, {span}: {say(stock, start)} on "
                       f"{begin:%-d %b %Y} " + " ".join(terms) + f" = {say(stock, now)}."
                       + ("" if parts["expansion"] else " This file has no monthly values per "
                          f"{pack.spell.words.plural[:-1] if pack.spell.words.plural.endswith('s') else pack.spell.words.plural}, "
                          "so expansion and contraction are not split."))
    lost, rate = ledger_kpi(pack, roles, "moves", "churn", "count"), next(
        (k for k in pack.kpis if k.tile and k.unit == "percent" and computable(pack, k.name, roles)
         and any(t.spell == "retained" and t.sum is None for alt in k.num for t in alt)), None)
    if lost and rate and (n := compute_kpi(df, roles, pack, lost.name)) is not None \
            and compute_kpi(df, roles, pack, rate.name) is not None:   # never without its rate
        who = pack.spell.words.plural
        out.append(f"{n:,.0f} {who} left at a month-end in the window ({lost.phrase}); {rate.phrase} "
                   f"counts only the {who} in force on {cohort_start(as_of, first_known(pack, roles, spell_from(pack, df, roles))):%-d %b %Y} "
                   "who are not in force now, so "
                   f"the two differ: {who} who joined in the window or came back are in the count only.")
    if (k := parts["churn"]) is not None:
        frame = df.assign(_period=pd.Series(pd.NaT, index=df.index).astype("period[M]"))
        e = compute_kpi(frame, roles, pack, k.name, by="_period", full=df)
        e = e[[p for p in e.index if p.end_time.normalize() > lo]] if e is not None else None
        if e is not None and len(e) >= 6 and e.max() > 0:
            top, usual = e.idxmax(), float(e.drop(e.idxmax()).median())
            if e[top] >= 2.5 * max(usual, 1):
                line = (f"The largest month of {k.phrase}: {top.strftime('%b %Y')} with "
                        f"{say(k, e[top])} (a usual month: {say(k, usual)})")
                ent, prev = roles[pack.spell.entity], (top - 1).end_time.normalize()
                rows = _in_force(df, roles, pack, prev)
                gone = rows[~rows[ent].isin(_in_force(df, roles, pack, top.end_time.normalize())[ent])]
                if len(gone) and (m := _column(gone, roles, k.num[0][0].sum)).notna().any():
                    big = m.groupby(gone[ent]).sum()
                    line += f", {big.max() / e[top]:.0%} of it one {ent}"
                    group = next((roles[g] for g in pack.overview_groups if g in roles), None)
                    if group:
                        line += f" in {gone.loc[gone[ent] == big.idxmax(), group].iloc[0]} ({group})"
                out.append(line + ".")
    return out


# ponytail: per-frame memo of the spell rules a dashboard asks again and again (_in_force for
# each month-end of each ledger KPI, the as-of: 146 + 96 calls, ~4 s on a 10k-row MRR export).
# Keyed on the frame's identity, held weakly (dies with the frame); a frame changed in place
# after a call would be stale (nothing here mutates a frame it passes in).
_MEMO: dict[int, tuple] = {}
_MEMO_LOCK = threading.Lock()   # runs build dashboards in threads (asyncio.to_thread)


def _memo(whole: pd.DataFrame, key: tuple, fn):
    import weakref
    key = (*key, len(whole))
    with _MEMO_LOCK:
        for k in [k for k, (ref, _) in _MEMO.items() if ref() is None]:
            del _MEMO[k]
        ref, memo = _MEMO.get(id(whole), (None, None))
        if ref is None or ref() is not whole:
            memo = {}
            _MEMO[id(whole)] = (weakref.ref(whole), memo)
        if key in memo:
            return memo[key]
    value = fn()   # outside the lock: two threads may both compute it once, never wrongly
    with _MEMO_LOCK:
        memo[key] = value
    return value


def _spell_key(pack: Pack, roles: dict[str, str]) -> tuple:
    return pack.domain, pack.date_role, pack.spell.model_dump_json(), tuple(sorted(roles.items()))


def _in_force(whole: pd.DataFrame, roles: dict[str, str], pack: Pack, t: pd.Timestamp) -> pd.DataFrame:
    """THE state rule: each entity's row at `t` if it is in force then. Snapshots: its latest
    snapshot on or before t (agents.utils.snapshot_rows: absent from the newest snapshot = gone);
    spells: its latest spell started by t. Either way out when its end is on or before t, or its
    status says ended with no end date."""
    idx = _memo(whole, ("in_force", *_spell_key(pack, roles), pd.Timestamp(t)),
                lambda: _in_force_at(whole, roles, pack, t).index)
    return whole.loc[idx]


def _in_force_at(whole: pd.DataFrame, roles: dict[str, str], pack: Pack, t) -> pd.DataFrame:
    """_in_force's rule, uncached."""
    s, snap = pack.spell, _snapshot_column(pack, roles)
    ent = roles[s.entity]
    if snap:
        from agents.utils import snapshot_rows
        upto = whole[_day(whole[snap]) <= t]
        rows = snapshot_rows(upto, [ent], snap)
        if s.concurrent:   # every row of that snapshot (an export with one row per subscription)
            chosen = pd.MultiIndex.from_arrays([rows[ent], _day(rows[snap])])
            rows = upto[pd.MultiIndex.from_arrays([upto[ent], _day(upto[snap])]).isin(chosen)]
    else:
        start = _started(whole, roles, pack)
        rows = whole.loc[start[start <= t].sort_values(kind="stable").index]
        if not s.concurrent:
            rows = rows.groupby(ent).tail(1)
    return rows[~_ended(rows, roles, pack, t)]


def _ended(rows: pd.DataFrame, roles: dict[str, str], pack: Pack, t) -> pd.Series:
    """THE ended test of a spell's row at `t` (a date, or each row's own date): its end on or
    before t, or an `ended` status with no end date (over at an unknown date). _in_force and
    ended_records both read it."""
    s = pack.spell
    end = _day(rows[roles[s.end]]) if s.end in roles else pd.Series(pd.NaT, index=rows.index)
    return (end.notna() & (end <= t)) | _ended_undated(rows, roles, pack)


def not_counted_yet(pack: Pack, df: pd.DataFrame, roles: dict[str, str], as_of) -> str:
    """THE sentence about start or end dates after the as-of (notice periods, pre-boarding):
    still in force / not yet started, so not counted yet. The file's story and every spell
    answer say it."""
    s = pack.spell
    later = sum(int((_day(df[roles[r]]) > as_of).sum()) for r in (s.start, s.end, s.paid_from)
                if r in roles)
    return (f"{later:,} start or end date{'s fall' if later != 1 else ' falls'} after "
            f"{as_of:%-d %b %Y}: not counted yet.") if later else ""


def _ended_undated(rows: pd.DataFrame, roles: dict[str, str], pack: Pack) -> pd.Series:
    """THE over-at-an-unknown-date test: an `ended` status with no end date (never in force at
    any date). _ended, the frame's marker and the file's story all read it."""
    s = pack.spell
    if not (s.ended and "status" in roles):
        return pd.Series(False, index=rows.index)
    end = _day(rows[roles[s.end]]) if s.end in roles else pd.Series(pd.NaT, index=rows.index)
    return words(rows[roles["status"]]).isin(set(words(pd.Series(s.ended)))) & end.isna()


def _started(rows: pd.DataFrame, roles: dict[str, str], pack: Pack) -> pd.Series:
    """THE date a spell row counts from: its start, or its `paid_from` when later (a converted
    trial pays from the trial's end). _in_force, the start events and the population read it."""
    s = pack.spell
    start = _day(rows[roles[s.start]])
    if s.paid_from in roles:
        start = pd.concat([start, _day(rows[roles[s.paid_from]])], axis=1).max(axis=1)
    return start


def has_spell(pack: Pack | None, roles: dict[str, str]) -> bool:
    """The data can be read as spells: the pack's entity, and a start date or snapshot dates (a
    leavers-only file with no hire date can't). Every spell reader asks this first."""
    s = pack.spell if pack else None
    return bool(s and s.entity in roles and (s.start in roles or _snapshot_column(pack, roles)))


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
        when = _started(whole, roles, pack) if which == "starts" else _day(whole[col])
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
    if not has_spell(pack, roles):
        return None
    w = spell_window(pack, whole, roles, kinds.attrs.get("window"))
    if w is None:
        return None
    lo, as_of, window_pts = w
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
        axis = pd.period_range(first, as_of, freq=freq)[-SPELL_PERIODS:]
        groups = None
    else:   # the groups its population has (never 'Contractor: 0' for an employees' KPI)
        axis = None
        pop = df[_meets(df, roles, w)] if by and (w := _population(pack, term.where)) else df
        # and rows of the KPI's kinds: a country billed only in euros is no '$0' group (audit 1)
        pop = pop[allk.reindex(pop.index).isin(ok).to_numpy()] if by else pop
        groups = pd.unique(pop[by].dropna()) if by else None

    if term.spell in ("opening", "retained", "moves"):
        return _ledger_term(df, whole, roles, pack, term, allk, ok, by, lo, as_of, first,
                            axis if periodic else None, groups)

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
            pts = [e for e in _month_ends(p.start_time - pd.Timedelta(days=1),
                                          min(p.end_time.normalize(), as_of)) if e >= first]
            out[p] = sum(active_at(t) for t in pts) / len(pts) if pts else float("nan")
        return pd.Series(out, dtype=float).reindex(axis)
    pts = window_pts
    if not pts:
        return None
    vals = [active_at(t) for t in pts]
    if by:
        return (pd.concat(vals, axis=1).fillna(0).sum(axis=1) / len(pts)).reindex(groups, fill_value=0)
    return sum(vals) / len(pts)


def first_known(pack: Pack, roles: dict[str, str], first: pd.Timestamp) -> pd.Timestamp:
    """THE first date the data knows each entity's state: snapshots, the first snapshot (a month
    label on the 1st: that month's end, as spell_as_of reads it); spells, the day before the first
    date (in force by start dates). The ledger's opening and moves start there at the earliest."""
    if not _snapshot_column(pack, roles):
        return first - pd.Timedelta(days=1)
    return first + pd.offsets.MonthEnd(0) if pack.spell.month_labels and first.day == 1 else first


def cohort_start(as_of, known):
    """THE start of a cohort KPI's window: 12 months before its end, or the data's first known
    state when it covers less: the opening is a real value, never a 0 before the data."""
    return max(as_of - pd.DateOffset(months=12), known)


def _ledger(whole, roles, pack, term: Term, allk, ok, points, part, by):
    """THE ledger: (values, in the part, group), each an entity × date frame. An entity's value
    at t is its rows in force at t (on ALL rows: _in_force) of the KPI's kinds meeting `where`,
    the term's role summed (concurrent spells add up) or 1 for a count (the entity is there,
    however many of its spells are); 0 when none. `in part`:
    one of those rows is in `part` (a filter); `group`: the `by` value of its first row."""
    ent = roles[pack.spell.entity]
    vals, inside, grp = {}, {}, {}
    for t in points:
        rows = _in_force(whole, roles, pack, t)
        m = allk.reindex(rows.index).isin(ok).to_numpy()
        if term.where:
            m = m & _meets(rows, roles, term.where).to_numpy()
        rows = rows[m]
        if term.sum is not None:
            vals[t] = _column(rows, roles, term.sum).groupby(rows[ent]).sum()
        else:   # a count: the entity is there (two concurrent subscriptions are one customer)
            vals[t] = pd.Series(1.0, index=pd.unique(rows[ent]))
        inside[t] = pd.Series(rows.index.isin(part), index=rows.index).groupby(rows[ent]).any()
        if by:
            grp[t] = rows[by].groupby(rows[ent]).first()
    values = pd.DataFrame(vals, columns=points).fillna(0.0).clip(lower=0)
    return (values, pd.DataFrame(inside, columns=points).reindex(values.index).eq(True),
            pd.DataFrame(grp, columns=points).reindex(values.index) if by else None)


def _ledger_term(df, whole, roles, pack, term: Term, allk, ok, by, lo, as_of, first, axis, groups):
    """An opening / retained / moves term from the ledger. Moves: each pair of consecutive
    month-ends classes each entity's change (0→x new, or reactivation when it was in force at an
    earlier month-end; up expansion; down contraction; x→0 churn), counted where its later row
    is (a churn: its earlier row), so a segment's bridge adds up and a mover's growth counts in
    its new segment. Opening / retained: the cohort in force at the window's start (by period:
    12 months before each period's end), placed by its row then; retained values it at the end
    (an entity back after a churn is retained). A start or end before the data counts 0."""
    known = first_known(pack, roles, first)
    lo = max(lo, known)   # moves never start before the data knows a state (an asked window of
    # 20 months keeps its 20: pass 3); the 12-month cohort is the opening's own rule

    # from the month-end before the first period, so the first period's moves have a start
    before = (first.to_period("M") - 1).end_time.normalize()
    history = [before, *(t for t in _month_ends(before, as_of) if t <= as_of)]
    if term.spell == "moves":
        points = sorted({*history, lo, as_of})
    else:
        ends = [min(p.end_time.normalize(), as_of) for p in axis] if axis is not None else [as_of]
        # always the 12-month cohort ending there, an asked period's end included (audit 1: a
        # 6-month period made a 6-month 'Logo churn (12 months)')
        starts = [e - pd.DateOffset(months=12) for e in ends] if axis is not None \
            else [cohort_start(as_of, known)]
        points = sorted({*ends, *starts})
    points = [t for t in points if t <= as_of]
    by = by if axis is None else None   # a period axis is the ledger's own dates, never a group
    v, inside, g = _ledger(whole, roles, pack, term, allk, ok, points, df.index, by)

    def total(x: pd.Series, at, key=None):
        """x summed over the entities in the part at `at` (by group or one number)."""
        keep = inside[at]
        x = x[keep & (x != 0)]
        if by:
            return x.groupby(g.loc[x.index, at]).sum().reindex(groups, fill_value=0)
        return float(x.sum())

    if term.spell == "moves":
        prev_ever = (v > 0).cummax(axis=1).shift(1, axis=1, fill_value=False)
        out = {}
        for a, b in zip(points, points[1:]):
            x, y = v[a], v[b]
            kind = term.move
            amount = {"new": (x == 0) & (y > 0) & ~prev_ever[a],
                      "reactivation": (x == 0) & (y > 0) & prev_ever[a],
                      "expansion": (x > 0) & (y > x), "contraction": (y > 0) & (y < x),
                      "churn": (x > 0) & (y == 0)}[kind]
            if term.sum is None:
                delta = amount.astype(float)
            else:
                delta = {"new": y, "reactivation": y, "expansion": y - x, "contraction": x - y,
                         "churn": x}[kind].where(amount, 0.0)
            out[b] = total(delta, a if kind == "churn" else b)
        if axis is not None:
            per = {}
            for b, n in out.items():
                per[pd.Period(b, freq=axis.freq)] = per.get(pd.Period(b, freq=axis.freq), 0.0) + n
            series = pd.Series(per, dtype=float).reindex(axis, fill_value=0.0)
            if _snapshot_column(pack, roles):   # snapshots: the first period has no month before
                # it in the data, so its moves are unknown, never a \$0 (audit 1)
                series[series.index <= pd.Period(first, freq=axis.freq)] = float("nan")
            return series
        inside_window = [b for b in out if b > lo]
        if not inside_window:   # no month of moves (one month of data): unknown, never $0 (pass 7)
            return None
        if by:
            return sum((out[b] for b in inside_window), pd.Series(0.0, index=groups)) \
                if inside_window else pd.Series(0.0, index=groups)
        return float(sum(out[b] for b in inside_window))

    def cohort(start, end):
        if start not in v.columns or end not in v.columns or start >= end:
            return float("nan")   # a window after the data (a date filter past the as-of)
        base = v[start]
        if term.spell == "opening":
            return total(base, start)
        later = v[end].where(base > 0, 0.0)
        return total(later.clip(upper=base) if term.capped else later, start)

    if axis is not None:
        return pd.Series({p: cohort(s_, e) if s_.to_period("M") >= known.to_period("M") else float("nan")
                          for p, s_, e in zip(axis, starts, ends)}, dtype=float).reindex(axis)
    value = cohort(cohort_start(as_of, known), as_of)
    return None if isinstance(value, float) and pd.isna(value) else value


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
            v = v * t.sign * t.times
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
                window: tuple | None = None, cut=()):
    """A KPI's value (or a Series per `by` group) on the main currency's rows; None when the data
    lacks its roles or a denominator is 0 — never a 0 or an infinity standing in for "unknown".
    `full`: the whole data when `df` is a period of it (a month with no returns has a 0% return
    rate when the data has returns at all). `cut`: the columns `df` was filtered by (a dashboard
    filter, a named value): a protected one makes it a protected group. `window` (lo, hi): an
    asked period for a spell KPI,
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
        if whole is not None:   # the whole data's kinds too: a spell's entity is chosen on them
            # (Domain 6: a month's MRR counted the EUR subscriptions the tile leaves out)
            whole = whole.where(main_currency(full, roles)[0], "other_currency")
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
        hidden = small_groups(df, roles, pack, name, by, kinds.attrs["frame"], value.index, window, cut)
        value = value.drop(hidden, errors="ignore").dropna()
        value.attrs["hidden"] = list(hidden)   # said in the answer ('Not shown: …')
        return value if len(value) else None
    if full is not None and protected_value(df, full, roles, pack, name, window, cut):
        return None   # a protected group of fewer than 11 people (pay in a 6-person department)
    return None if pd.isna(value) else float(value)


def _person_col(pack: Pack, roles: dict[str, str]) -> str | None:
    """The column naming one person (the spell entity, the snapshot key, else the pack's
    person_key): who a small cell counts."""
    for r in (pack.spell.entity if pack.spell else None, pack.snapshot_key, pack.person_key):
        if r and r in roles:
            return roles[r]
    return None


def _population(pack: Pack, where: list | None) -> list:
    """A spell term's people: its `where` without conditions on the event's own fields
    (marker_fields: an exit's category). Retirements' people are the employees, never the
    retirees: who a small cell counts, and the groups a count of no such exits shows as 0."""
    groups = [[c for c in g if c.role not in pack.marker_fields] for g in where or []]
    return [] if any(not g for g in groups) else groups


def pay_columns(pack: Pack, roles: dict[str, str]) -> set[str]:
    """The protected money columns (pay): grouping by their values shows one person's pay."""
    return {roles[r] for r in pack.protected_roles if r in roles and pack.roles[r].kind == "money"}


def _kpi_where(pack: Pack, name: str | None, roles: dict[str, str]) -> list:
    """The population filter of a KPI's spell terms (contractors out), through the KPIs it
    refers to: who its small cells count."""
    if not name or not pack.kpi(name):
        return []
    for t in used_terms(pack, pack.kpi(name), roles):
        if t.spell and t.where:
            return _population(pack, t.where)
        if t.kpi and (w := _kpi_where(pack, t.kpi, roles)):
            return w
    return []


def people_in(part: pd.DataFrame, roles: dict[str, str], pack: Pack, state: bool,
              whole: pd.DataFrame | None = None, window: tuple | None = None, by: str | None = None,
              where: list | None = None):
    """THE count of the people a number is about (the small-cell rule's): on spell data, for a
    state the people in force at the window's end, for a flow or rate the people in force at any
    point of its window; elsewhere the distinct people (person column) among the rows counted
    (a chain's earlier versions never count), else those rows. Restricted to `part` (a filter or a
    group). With `by`: a Series per group (0 for none)."""
    whole = part if whole is None else whole
    col = _person_col(pack, roles)
    if has_spell(pack, roles) and (w := spell_window(pack, whole, roles, window)):
        lo, as_of, _ = w
        if state:
            rows = _in_force(whole, roles, pack, as_of)
        else:   # in force at some point of (lo, as_of]: started by its end, not ended by its start
            snap = _snapshot_column(pack, roles)
            if snap:
                t = _day(whole[snap])
                rows = whole[(t > lo) & (t <= as_of)]
            else:
                rows = whole[(_started(whole, roles, pack) <= as_of) & ~_ended(whole, roles, pack, lo)]
        rows = rows.loc[rows.index.intersection(part.index)]
        if where:   # the KPI's own population (a contractor is no employee in a group of 10)
            rows = rows[_meets(rows, roles, where)]
    else:
        rows = part[row_kinds(part, roles, pack) != "non_product"] if pack.version_key else part
    if by is None:
        return rows[col].nunique() if col else len(rows)
    n = rows.groupby(by)[col].nunique() if col else rows.groupby(by).size()
    return n.reindex(part.groupby(by).size().index, fill_value=0)   # a group nobody counts: 0


def protected_cut(cut, roles: dict[str, str], pack: Pack) -> bool:
    """A part was asked by a protected role (`cut`: the columns the user filtered or named:
    women, one ethnicity): every number on it is about that protected group. Decided from what
    was asked, never from the part's values (a part hidden because it happens to be all one
    gender would say so: audit pass 2)."""
    prot = {roles.get(r) for r in pack.protected_roles} - {None}   # a pay value: one person's pay
    return bool(set(cut or ()) & prot)


def protected_groups(df: pd.DataFrame, contract, keys, column: str, label: str = "", cut=()) -> list:
    """The groups of a by-group column statistic the protected rule hides: grouped by a protected
    role (or a label naming one: a 'gender pay gap' by level), or of a protected measure (pay),
    with fewer than SMALL_CELL people (people_in, on the rows the statistic reads). small_groups'
    rule for column statistics (utils.aggregate, chart bars, the model's findings)."""
    from agents.utils import level_rows
    from guardrails import SMALL_CELL
    pack = pack_for(contract.domain) if contract is not None else None
    if not pack or not pack.protected_roles:
        return []
    keys = keys if isinstance(keys, list) else [keys]
    names = [k for k in keys if isinstance(k, str)]
    if not names or len(names) != len(keys) or any(k not in df.columns for k in names):
        return []
    roles = {r: c for r, c in contract.roles.items() if c in df.columns}
    prot = {roles[r]: r for r in pack.protected_roles if r in roles}
    said = set(normalize_header(label or "").split("_"))   # whole words: 'Sex' is not 'Essex'
    crossed = [c for c in prot if pack.roles[prot[c]].kind != "money" and c not in names
               and set(normalize_header(c).split("_")) <= said]
    if set(names) & pay_columns(pack, roles):   # grouped by pay values: every group is one pay
        return sorted(set(df[names].dropna().itertuples(index=False, name=None))
                      if len(names) > 1 else pd.unique(df[names[0]].dropna()), key=str)
    asked = protected_cut(cut, roles, pack)   # a part that is itself a protected group
    if not (set(names) & set(prot) or column in prot or crossed or asked):
        return []
    rows = level_rows(df, column, contract) if column in df.columns else df
    key = names if len(names) > 1 else names[0]
    if crossed:   # a gap by level compares its two largest groups: both need 11 people
        cells = people_in(rows, roles, pack, False, df, by=names + crossed[:1])
        top2 = cells.groupby(level=list(range(len(names)))).apply(
            lambda c: sorted(c.tolist(), reverse=True)[1] if len(c) > 1 else 0)
        return sorted([k for k, v in top2.items() if v < SMALL_CELL], key=str)
    n = people_in(rows, roles, pack, False, df, by=key)
    by_protected = bool(set(names) & set(prot))
    return sorted(_with_complement([k for k, v in n.items() if v < SMALL_CELL], list(n.index), n,
                                   column in prot and not by_protected), key=str)


def small_groups(df: pd.DataFrame, roles: dict[str, str], pack: Pack, name: str, by: str,
                 whole: pd.DataFrame | None = None, groups=None, window: tuple | None = None,
                 cut=()) -> list:
    """The `by` groups a KPI never shows: fewer than SMALL_CELL people (people_in), when `by` is
    a protected role, the KPI measures one (pay by team), or `df` is itself a protected group
    (headcount by department among non-binary staff). THE small-cell rule outside patient data
    (which pools at ingestion: ADR-0008)."""
    from guardrails import SMALL_CELL
    whole = df if whole is None else whole
    if not pack.protected_roles or by not in df.columns:
        return []
    if not ({r for r, c in roles.items() if c == by} & set(pack.protected_roles)) and not (
            kpi_roles(pack, name) & set(pack.protected_roles)) and not protected_cut(cut, roles, pack):
        return []
    state = pack.kpi(name).state
    groups = pd.unique(df[by].dropna()) if groups is None else groups
    if isinstance(df[by].dtype, pd.PeriodDtype):   # a period's people: its own window, never
        # the rows dated in it (pay in a month with 3 people in force is 3 people's pay)
        if not has_spell(pack, roles):
            return []
        return [p for p in groups if people_in(df, roles, pack, state, whole,
                                               (p.start_time, p.end_time.normalize()),
                                               where=_kpi_where(pack, name, roles)) < SMALL_CELL]
    n = people_in(df, roles, pack, state, whole, window, by=by, where=_kpi_where(pack, name, roles))
    by_protected = bool({r for r, c in roles.items() if c == by} & set(pack.protected_roles))
    return _with_complement([g for g in groups if n.get(g, 0) < SMALL_CELL], groups, n,
                            bool(kpi_roles(pack, name) & set(pack.protected_roles)) and not by_protected)


def _with_complement(hidden: list, groups, n, measure_protected: bool) -> list:
    """The hidden groups, plus one more when a protected measure (pay) by an ordinary group
    (departments) hides exactly one: the total and the others would give it back by
    subtraction, so the next smallest is hidden with it. By a protected role itself (pay by
    gender) no complement: hiding women's pay because a non-binary cell is small would hide the
    comparison asked; the small cell is hidden alone (its residual differencing against a total
    is a stated limit). THE complement rule (small_groups, protected_groups)."""
    hidden = list(hidden)
    rest = [g for g in groups if g not in hidden]
    if measure_protected and len(hidden) == 1 and len(rest) > 1:
        hidden.append(min(rest, key=lambda g: (n.get(g, 0), str(g))))
    return hidden


def protected_value(df: pd.DataFrame, whole: pd.DataFrame, roles: dict[str, str], pack: Pack,
                    name: str, window: tuple | None = None, cut=()) -> bool:
    """A single KPI value on `df` the protected rule hides: `df` is a protected group (one gender)
    or the KPI measures one (pay in Legal), with fewer than SMALL_CELL people (people_in)."""
    from guardrails import SMALL_CELL
    if not pack.protected_roles or len(df) == len(whole):
        return False
    if not (kpi_roles(pack, name) & set(pack.protected_roles)) and not protected_cut(cut, roles, pack):
        return False
    return people_in(df, roles, pack, pack.kpi(name).state, whole, window,
                     where=_kpi_where(pack, name, roles)) < SMALL_CELL


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
    return kpi.den is None and all(not t.nunique and t.spell not in ("active", "average", "opening",
                                                                     "retained")
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
                 present: set[str] = frozenset(ROW_KINDS), parts: bool = False) -> str:
    """How a KPI was computed on this data, in words, from the alternative that applied. Only
    the row kinds the data has (`present`) are named as left out. `parts`: a ratio of spell KPIs
    also says how each part was counted (an answer's 'How': the 12 month-ends, exits as events,
    who is out), never just 'separations ÷ headcount'."""
    kpi = pack.kpi(name)

    def term(t: Term) -> str:
        if t.kpi:
            how = describe_kpi(pack, t.kpi, roles, present).rstrip(".")
            return (f"{t.times:g} × " if t.times != 1 else "") + pack.kpi(t.kpi).phrase + (
                f" ({how[:1].lower() + how[1:]})" if parts and kpi.den is not None
                and spell_terms(pack, t.kpi) else "")
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
                    "opening": f"distinct {ent} in force 12 months before the date ({at}, {gone})",
                    "retained": f"same {ent} (in force 12 months before) at the date"
                                + (", each capped at its value then" if t.capped else ""),
                    "moves": f"{t.move} moves: each {ent} whose value went "
                             + {"new": "from 0 (never in force before) to above 0",
                                "reactivation": "from 0 back above 0 (in force before)",
                                "expansion": "up", "contraction": "down (not to 0)",
                                "churn": "to 0"}.get(t.move or "", "")
                             + " between consecutive month-ends in the period",
                    "ends": f"{sp.words.end}s in the period (events: by {roles.get(sp.end, sp.end)}"
                            + (", or the first snapshot an entity is missing from" if snap else "")
                            + f"; a returning {ent}'s {sp.words.end}s count separately)"}[t.spell]
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


def named_kpis(pack: Pack, contract, message: str) -> list[str]:
    """THE KPIs a question names, in its order: a glossary word, unless it is a column's own name
    and not the KPI's ("average revenue" with a revenue column is that column), or the pack's
    `consumed` words. Intake fills the KPI from it; a question naming two ('MRR and paying
    customers', 'NRR and GRR') is answered for each (targeted_analysis_agent)."""
    by_name = {normalize_header(c): r for r, c in contract.roles.items()}
    own = {normalize_header(x.name) for x in contract.columns}
    return [c for c, w in glossary_hits(pack, message, []) if pack.kpi(c)
            and (normalize_header(w) not in own
                 or (normalize_header(w) in {c, normalize_header(pack.kpi(c).label)}
                     and by_name.get(normalize_header(w)) in kpi_roles(pack, c)))]


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
    # rows to leave out, a KPI to compute, or spell markers in the frame (with_spell_events: a
    # question naming no KPI, 'average base salary', still reads them; Domain 5 gate: hr-8)
    if present - {"sale"} or concepts or (pack.spell and pack.spell.entity in roles):
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
            return v.fillna(blank) if role in f.optional else v

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
        for x in (x for x in f.over if pack.roles[x].numbers and x in roles):
            unknown = df[roles[x]].notna() & value(x, 1.0).isna()
            if n := int(unknown.sum()):
                seen = ", ".join(sorted(map(str, df.loc[unknown, roles[x]].unique()))[:3])
                notes.append(f"{name}: {n:,} row{'s' * (n != 1)} {'have' if n != 1 else 'has'} a "
                             f"{roles[x]} the formula doesn't know ({seen}): left blank.")
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
    out = df.assign(**{name: kinds.map(pack.code).where(keep, "other_currency")})
    return with_spell_events(out, pack, roles), name


def spell_event_columns(pack: Pack) -> tuple[str, str, str, str, str]:
    """THE names of the spell markers in the model's frame, from the pack's words: (start event
    date, end event date, end in window, in force now, ended with no date)."""
    w = pack.spell.words
    return (f"{w.start}_event_date", f"{w.end}_event_date", f"{w.end}_in_window", "in_force_now",
            w.unknown)


def stock_population(df: pd.DataFrame, roles: dict[str, str], pack: Pack) -> pd.Series:
    """THE rows the stock tile (headcount, MRR) counts, as a mask: its `where` (contractors out
    on HR; trials, paused out on SaaS), its row kinds and the main currency (Domain 6 smoke: test
    accounts and EUR subscriptions were marked in force, the tile leaves them out). A rate's
    events, the in-force marker and a level by group read the same people."""
    stock = stock_kpi(pack, roles)
    if stock is None:
        return pd.Series(True, index=df.index)
    where = next((t.where for t in used_terms(pack, stock, roles) if t.spell), [])
    counted = _meets(df, roles, where) if where else pd.Series(True, index=df.index)
    return counted & row_kinds(df, roles, pack).isin(stock.rows) & main_currency(df, roles)[0]


def in_force_counted(df: pd.DataFrame, roles: dict[str, str], pack: Pack, as_of) -> pd.Index:
    """THE rows in force at `as_of` that the stock tile counts: the model's in_force_now marker
    and a level by group (MRR or pay by segment, utils._in_force_rows) read this, never their
    own copy (Domain 6 insight gate: MRR by segment added trials, test accounts and EUR)."""
    now = _in_force(df, roles, pack, as_of).index
    return now[stock_population(df, roles, pack).loc[now].to_numpy()]


def with_spell_events(df: pd.DataFrame, pack: Pack, roles: dict[str, str]) -> pd.DataFrame:
    """On spell data, the product's own events as columns for code the model writes: each
    spell's first row carries its hire (start) date, its last row its exit date (the end date,
    or on snapshots the first snapshot the person is missing from), up to the as-of; blank on
    every other row. Counting a window's exits is then counting rows with an end event date in
    it, by the row's own department (where the person left from): the model never re-derives a
    vanished leaver (Domain 5 smoke: it counted only dated exits, 20 of 49)."""
    if not has_spell(pack, roles):
        return df
    start_col, end_col, window_col, now_col, unknown_col = names = spell_event_columns(pack)
    if any(c in df.columns for c in names):
        return df
    w = spell_window(pack, df, roles)
    if w is None:
        return df
    as_of = w[1]
    counted = stock_population(df, roles, pack)
    out = df.copy()
    lo = w[0]
    for col, which in ((start_col, "starts"), (end_col, "ends")):
        rows, when = _spell_events(df, roles, pack, which, as_of)
        rows, when = rows[counted.loc[rows.index]], when[counted.loc[rows.index]]
        out[col] = pd.Series(pd.NaT, index=df.index, dtype="datetime64[ns]")
        out.loc[rows.index, col] = when.astype("datetime64[ns]")
        if which == "ends":   # the window the facts name (12 months to the as-of): marked, so a
            # model never forgets to filter it (Domain 5 gate: hr-1 counted every exit ever)
            out[window_col] = (out[col] > lo).map({True: "yes", False: ""})
        out[col] = out[col].dt.strftime("%Y-%m-%d")
    out[unknown_col] = _ended_undated(df, roles, pack).map({True: "yes", False: ""})
    out[now_col] = ""
    out.loc[in_force_counted(df, roles, pack, as_of), now_col] = "yes"
    return out


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
    if role and pack.roles[role].over_time == "last" and (gone := ended_records(df, contract)).any():
        # a spell's snapshot row past its end (its exit record) holds no one: its level is
        # blank, so latest_rows never averages a leaver in (Domain 5 gate: hr-8)
        return df.assign(**{column: df[column].where(~gone)}), \
            f"{int(gone.sum()):,} {pack.spell.words.end} records ({pack.spell.words.after_end}) " \
            f"hold no {column}."
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


def ended_records(df: pd.DataFrame, contract) -> pd.Series:
    """True on a spell's snapshot rows that record its end: dated on or after its end date, or
    with an `ended` status and no end date (the state rule _in_force applies); all False
    elsewhere (rosters, packs without a spell)."""
    pack = defaults_pack(contract)
    none = pd.Series(False, index=df.index)
    roles = {r: c for r, c in contract.roles.items() if c in df.columns} if pack else {}
    snap = _snapshot_column(pack, roles) if pack and pack.spell else None
    if not snap or not has_spell(pack, roles):
        return none
    return _ended(df, roles, pack, _day(df[snap]))


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
