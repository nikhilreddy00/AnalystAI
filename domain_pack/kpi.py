"""A KPI's value: the terms, sides and filters of a pack formula, `compute_kpi`, and the small-
cell rules that hide a protected group."""
from __future__ import annotations

import pandas as pd

from .pack import (KPI, KPIValue, Measured, MeasuredBy, Pack, Term, Unavailable, Within,
                   _column, _day, _meets, _snapshot_column, and_list, normalize_header, pack_for,
                   snapshot_role, spell_terms)
from .detect import main_currency, row_kinds
from .spell import (SPELL_PERIODS, _ended, _in_force, _spell_events, _started, has_spell,
                    spell_view, term_rows)
from .spell import window as spell_window_of
from .ledger import _ledger_term


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


def stock_rows(df: pd.DataFrame, roles: dict[str, str], pack: Pack,
               t: pd.Timestamp | None = None) -> pd.DataFrame:
    """THE rows the stock tile (headcount, MRR) counts: term_rows of its spell term, its row kinds
    in the main currency (Domain 6 smoke: test accounts and EUR subscriptions were marked in
    force, the tile leaves them out); with `t`, those in force then. The in-force marker, a level
    by group and the story's churned entities read the same rows."""
    stock = stock_kpi(pack, roles)
    if stock is None:
        return df if t is None else _in_force(df, roles, pack, t)
    term = next(x for x in used_terms(pack, stock, roles) if x.spell)   # stock_kpi: it has one
    counted = row_kinds(df, roles, pack).isin(set(term.rows or stock.rows)) & main_currency(df, roles)[0]
    return term_rows(df, roles, pack, counted, term.where, t)


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
        return Unavailable("role_missing", (s.entity, s.start))
    view = spell_view(pack, whole, roles, kinds.attrs.get("window"))
    if view is None:   # an asked period wholly before the data, or no dated row up to today
        return Unavailable("outside_the_data" if kinds.attrs.get("window") else "no_rows")
    win = view.window
    lo, as_of, first = win.lo, win.as_of, win.first
    allk = kinds.attrs.get("kinds", kinds)   # whole's kinds, or df's when df is the whole
    counted = allk.reindex(whole.index).isin(set(term.rows or kpi.rows))   # the KPI's kinds
    periodic = by is not None and isinstance(df[by].dtype, pd.PeriodDtype)
    ent = roles[s.entity]

    def kept(rows: pd.DataFrame) -> pd.DataFrame:   # the term's rows, in the part `df`
        return rows[rows.index.isin(df.index)]

    def count(rows: pd.DataFrame, key):
        """People in force: distinct entities (or a role summed over their one row each);
        events: rows (a returning entity's two exits are two)."""
        if term.sum is not None:   # nobody in force: a measured 0 (C5); all blank: unknown
            v = _column(rows, roles, term.sum)
            if key is None:
                return v.sum(min_count=1) if len(v) else 0.0
            return v.groupby(key).sum(min_count=1)
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
        pop = pop[counted.reindex(pop.index, fill_value=False).to_numpy()] if by else pop
        groups = pd.unique(pop[by].dropna()) if by else None

    if term.spell in ("opening", "retained", "moves"):   # a cohort rate's opening is its cohort's
        cohort = any(t.spell == "retained" for t in spell_terms(pack, kinds.attrs["asked_kpi"]))
        return _ledger_term(view, term, df.index, counted, by, axis, groups, cohort)

    if term.spell in ("starts", "ends"):
        rows, when = _spell_events(whole, roles, pack, term.spell, as_of)
        rows = kept(rows[rows.index.isin(term_rows(whole, roles, pack, counted, term.where).index)])
        when = when[rows.index]
        if periodic:
            n = count(rows, when.dt.to_period(freq))
            return n.reindex(axis, fill_value=0)
        rows = rows[when[rows.index] > lo]
        if by:
            return count(rows, rows[by]).reindex(groups, fill_value=0)
        return count(rows, None)

    def active_at(t: pd.Timestamp):
        rows = kept(term_rows(whole, roles, pack, counted, term.where, t))
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
            pts = win.period(p).month_ends
            out[p] = sum(active_at(t) for t in pts) / len(pts)
        return pd.Series(out, dtype=float).reindex(axis)
    pts = win.month_ends
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


def _blank(term: Term, roles: dict[str, str], pack: Pack, whole: pd.DataFrame) -> Unavailable | None:
    """A role the term reads whose column is blank on every row (a sum, a condition): it can't
    be measured, never a 0 from a filter that kept nobody (C5). A marker field's blanks mean none
    (no end date: nobody ended) and an optional term's mean 0: those are read."""
    if term.optional:
        return None
    blank = [r for r in term.roles() if r in roles and r not in pack.marker_fields
             and whole[roles[r]].isna().all()]
    return Unavailable("blank_column", tuple(blank)) if blank else None


def _term(df, kinds, roles, pack, kpi, term: Term, by):
    rows = kinds.isin(term.rows or kpi.rows)
    if term.kpi:
        return _kpi_value(df, kinds, roles, pack, pack.kpi(term.kpi), by)
    if blank := _blank(term, roles, pack, kinds.attrs.get("frame", df)):
        return blank
    if term.spell:
        return _spell(df, kinds, roles, pack, kpi, term, by)
    met = _meets(df, roles, term.where) if term.where else None
    if met is not None:
        rows &= met
        whole = kinds.attrs.get("frame", df)
        if not met.any() and not _meets(whole, roles, term.where).any():
            # no interest rows anywhere in the data: unknown, never a $0.00
            return Unavailable("no_kind_rows", tuple(c.role for alt in term.where for c in alt))
    part = df[rows]
    # no rows of these kinds here (no returns this month, in this store) while the data has them
    # elsewhere: 0, and a group keeps its place (a 0% return rate ranks lowest); none anywhere in
    # the data (returns may not be exported at all): unknown
    if part.empty and not set(term.rows or kpi.rows) & kinds.attrs.get("present", set(kinds)):
        return Unavailable("no_kind_rows")
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
    total = values.sum(min_count=1)
    # blank on every row here while the data has values (a debit-only part of a Dr/Cr
    # statement: no credits): a measured 0 (C5), never unknown
    if pd.isna(total) and _column(kinds.attrs.get("frame", df), roles, expr).notna().any():
        return 0.0
    return total


def _end_missing(pack: Pack, roles: dict[str, str], t: Term) -> bool:
    """A spell term that needs each spell's end (exits, churn, a return after one, who is
    retained) on a list of spells without an end-date column: a cancellation request or a status
    is no end date (C5, PR1-05). Snapshots date an end by absence; new and expansion need none."""
    needs = t.spell in ("ends", "retained") or (t.spell == "moves" and t.move in ("churn",
                                                                               "reactivation"))
    return needs and pack.spell.end not in roles and not _snapshot_column(pack, roles)


def _side(df, kinds, roles, pack, kpi, alternatives, by):
    """The first alternative the data can compute; else why none can: the reason met in one the
    data fits (a blank column), before a role another alternative needs and the data lacks."""
    why, lacks = None, None
    for alt in alternatives:
        missing = [r for t in alt if not t.optional for r in t.roles() if r not in roles]
        if missing:
            lacks = lacks or Unavailable("role_missing", tuple(dict.fromkeys(missing)))
            continue
        if any(_end_missing(pack, roles, t) for t in alt):
            why = why or Unavailable("end_date_missing", (pack.spell.end,))
            continue
        total = None
        for t in (t for t in alt if all(r in roles for r in t.roles())):
            v = _term(df, kinds, roles, pack, kpi, t, by)
            if isinstance(v, Unavailable):
                why = why or v
                break
            v = v * t.sign * t.times
            total = v if total is None else (total.add(v, fill_value=0) if by else total + v)
        else:
            if total is not None:
                return total
    return why or lacks or Unavailable("role_missing")


def _kpi_value(df, kinds, roles, pack, kpi: KPI, by):
    if kpi.at == "latest":   # a state: each entity's latest row of any kind, then the filters
        from agents.utils import snapshot_rows
        key, date = roles.get(pack.snapshot_key), roles.get(pack.date_role)
        if key is None:
            return Unavailable("role_missing", (pack.snapshot_key,))
        if date is None and df[key].duplicated().any():
            return Unavailable("undated_snapshots")   # which of an account's rows is the latest?
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
    if isinstance(num, Unavailable) or kpi.den is None:
        return num
    den = _side(df, kinds, roles, pack, kpi, kpi.den, by)
    if isinstance(den, Unavailable):
        return den
    if by:
        den = den.where(den != 0)
        return (num / den).dropna()
    return Unavailable("zero_denominator") if not den or pd.isna(den) else num / den


def unavailable_words(u: Unavailable, pack: Pack) -> str:
    """THE clause saying why a figure is unavailable ('the data has no end date, so customers
    who left can't be counted'), in the pack's words; every answer and tile says it this way."""
    from guardrails import SMALL_CELL
    named = and_list([r.replace("_", " ") for r in u.roles])
    s = pack.spell
    return {
        "role_missing": f"the data has no {named or 'column it needs'}",
        "end_date_missing": (f"the data has no {s.words.end} date, so {s.words.plural} who left "
                             "can't be counted") if s else "the data has no end date",
        "blank_column": f"{named or 'a column it reads'} is blank on every row",
        "no_rows": "there are no rows to count",
        "no_kind_rows": "the data has no rows of the kind it counts",
        "mixed_units": "its parts are in different units, which never add up",
        "undated_snapshots": "the rows have no date, so the latest of each can't be told",
        "zero_denominator": "its base is 0",
        "too_short": "the data covers too short a span",
        "outside_the_data": "the period falls outside the data",
        "small_cell": f"fewer than {SMALL_CELL} people, so a figure can identify them",
    }[u.reason]


def compute_kpi(df: pd.DataFrame, roles: dict[str, str], pack: Pack, name: str,
                by: str | None = None, full: pd.DataFrame | None = None,
                window: tuple | None = None, cut=()):
    """The tests' view of `measure`: the number (or the Series per group, its small cells in
    attrs['hidden']), or None when unavailable (or no such KPI: measure raises). Product code
    reads `measure` (its reason)."""
    if pack.kpi(name) is None:
        return None
    v = measure(df, roles, pack, name, by, full, window, cut)
    if isinstance(v, MeasuredBy):
        out = v.values
        out.attrs["hidden"] = list(v.hidden)
        return out
    return v.value if isinstance(v, Measured) else None


def measure(df: pd.DataFrame, roles: dict[str, str], pack: Pack, name: str,
            by: str | None = None, full: pd.DataFrame | None = None,
            window: tuple | None = None, cut=()) -> KPIValue:
    """THE value of a KPI on the main currency's rows: Measured (a number), MeasuredBy (per `by`
    group or period) or Unavailable with its reason — never a 0 or an infinity standing in for
    "unknown". `full`: the whole data when `df` is a period of it (a month with no returns has a
    0% return rate when the data has returns at all). `cut`: the columns `df` was filtered by (a
    dashboard filter, a named value): a protected one makes it a protected group. `window`
    (lo, hi): an asked period for a spell KPI, which clips its window and as-of instead of
    cutting rows (rows dated in Q3 are the people hired in Q3, not the people employed then).
    Rows are named by index label (a filter's part maps to the whole by it): a repeated label on
    the whole data is renumbered; on a part of it the mapping is lost, so it is refused."""
    if not df.index.is_unique or (full is not None and not full.index.is_unique):
        if full is not None:
            raise ValueError("compute_kpi: a part and its whole need unique row labels")
        df = df.reset_index(drop=True)
    kpi = pack.kpi(name)
    if kpi is None:
        raise KeyError(f"{pack.domain} has no KPI {name!r}")
    if df.empty:
        return Unavailable("no_rows")
    if mixes(df, roles, kpi, by):   # car-years and house-years never add up
        return Unavailable("mixed_units")
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
    kinds.attrs["asked_kpi"] = name   # a rate's opening (NRR's Opening MRR) reads its cohort
    value = _kpi_value(df, kinds, roles, pack, kpi, by)
    if isinstance(value, Unavailable):
        return value
    if by:   # a group with no values is no group (a NaN would break the answer's JSON)
        hidden = small_groups(df, roles, pack, name, by, kinds.attrs["frame"], value.index, window, cut)
        value = value.drop(hidden, errors="ignore").dropna()
        if len(value):
            return MeasuredBy(value.astype(float), tuple(hidden))   # hidden: 'Not shown: …'
        return Unavailable("small_cell" if hidden else
                           "zero_denominator" if kpi.den is not None else "no_rows")
    if full is not None and protected_value(df, full, roles, pack, name, window, cut):
        return Unavailable("small_cell")   # a protected group of < 11 people (pay, 6 people)
    return Unavailable("no_rows") if pd.isna(value) else Measured(float(value))


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
    if has_spell(pack, roles) and (w := spell_window_of(pack, whole, roles, window)):
        lo, as_of = w.lo, w.as_of
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
        return all(r in roles for r in t.roles()) and (not t.kpi or computable(pack, t.kpi, roles)) \
            and not (t.spell and _end_missing(pack, roles, t))

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


def kpi_roles(pack: Pack, name: str) -> set[str]:
    """Every role a KPI can read, through the KPIs it refers to; a spell term reads its whole
    spell (start, end, the date it pays from, the as-of, the status that ends it undated)."""
    kpi = pack.kpi(name)
    s = pack.spell
    out: set[str] = set()
    for alt in [*kpi.num, *(kpi.den or [])]:
        for t in alt:
            out |= set(t.roles()) | (kpi_roles(pack, t.kpi) if t.kpi else set())
            if t.spell:
                out |= {r for r in (s.start, s.end, s.paid_from, s.as_of, snapshot_role(pack),
                                    "status") if r}
    return out


def counted(pack: Pack, role: str) -> bool:
    """An additive amount or count (net sales, units): its totals use the KPI rows (sale and
    return rows, the main currency), so a trend and its tile are one number."""
    return pack.roles[role].kind in ("money", "count") and pack.roles[role].additive
