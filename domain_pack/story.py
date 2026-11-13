"""The pack's prose: the file's story and facts, a KPI's description, the brief and caveats the
agents read, the first questions."""
from __future__ import annotations

import re

import pandas as pd

from .pack import (CONDITIONS, Cond, KPI, Maturity, Measured, MeasuredBy, Pack, ROW_KINDS,
                   ROW_KIND_COLUMN, Term, _column,
                   _day, _snapshot_column, and_list, defaults_pack, expand_header, normalize_header,
                   ordinal, roles_of, spell_terms)
from .detect import main_currency, row_kinds
from .spell import (WINDOW_MONTHS, Window, _ended_undated, has_spell, not_counted_yet,
                    today_only, window, window_words)
from .ledger import MOVES, TRANSFERS
from .kpi import (_person_col, computable, kpi_roles, ledger_kpi, measure, stock_kpi, stock_rows,
                  used_terms)


def _fact(*args, **kw):
    """A KPI's number (or Series) for a fact line, None when unavailable: a story states only
    what is measured (a 0 included); why a figure is unavailable is the tile's and the answer's
    to say (measure's reason)."""
    v = measure(*args, **kw)
    return v.value if isinstance(v, Measured) else v.values if isinstance(v, MeasuredBy) else None


def _amount(df: pd.DataFrame, roles: dict[str, str], kpi: KPI, v: float) -> str:
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
    w = window(pack, df, roles)
    if not s or stock is None or w is None:
        return []
    lo, as_of = w.lo, w.as_of
    frame = df.assign(_period=pd.Series(pd.NaT, index=df.index).astype("period[M]"))
    series = _fact(frame, roles, pack, stock.name, by="_period")
    shown = ", ".join(f"{t:%b %Y} {_amount(df, roles, stock, series.get(t.to_period('M')))}"
                      for t in w.month_ends
                      if series is not None and pd.notna(series.get(t.to_period("M"))))
    lines = [f"As of {as_of:%-d %b %Y}: the window is the {w.months} months "
             f"{w.first_day:%-d %b %Y} – {as_of:%-d %b %Y}."]
    if shown:
        lines.append(f"{stock.label} at its month-ends: {shown}.")
    if mean and (v := _fact(df, roles, pack, mean.name)) is not None:
        lines.append(f"{mean.label} = {v:,.1f} (their mean): divide the window's counts by it "
                     "for a rate; never recount it from rows.")
        frame_y = df.assign(_period=pd.Series(pd.NaT, index=df.index).astype("period[Y]"))
        years = _fact(frame_y, roles, pack, mean.name, by="_period")
        if years is not None and len(years):   # a year's rate divides by that year's own base
            lines.append(f"{mean.label} by year (each year's month-ends; the last year to "
                         f"{as_of:%-d %b %Y} only): " + ", ".join(
                             f"{p} {x:,.1f}" for p, x in years.items() if pd.notna(x)) + ".")
        for g in [g for g in pack.overview_groups if g in roles][:2]:   # a group's own base
            by = _fact(df, roles, pack, mean.name, by=roles[g])
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
        if (s := _fact(frame, roles, pack, k.name, by="_period")) is not None:
            out.append(f"{k.label} by month ({window_words(pack, df, roles, k.name)}): " + ", ".join(
                f"{p} {_amount(df, roles, k, s[p])}" for p in months(s)) + ".")
    heads = stock_kpi(pack, roles, "count")
    lost = ledger_kpi(pack, roles, "moves", "churn", "count")
    if heads and lost:
        n = _fact(frame, roles, pack, heads.name, by="_period")
        c = _fact(frame, roles, pack, lost.name, by="_period")
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
            if (by := _fact(df, roles, pack, k.name, by=roles[g])) is not None and len(by) <= 15:
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
    if not has_spell(pack, roles) or (w := window(pack, df, roles)) is None:
        return []
    lo, as_of = w.lo, w.as_of
    out: list[str] = []
    stock = stock_kpi(pack, roles)
    ends = next((k for k in pack.kpis if k.tile and k.unit == "count" and computable(pack, k.name, roles)
                 and [t.spell for t in used_terms(pack, k, roles)] == ["ends"]), None)
    prior = w.year_earlier()   # the default window a year earlier, when the data covers it all
    back = prior.asked if prior else None
    for k in pack.kpis:   # each rate tile now and a year earlier, the change in points
        if not (k.tile and k.unit == "percent" and spell_terms(pack, k.name) and computable(pack, k.name, roles)):
            continue
        now = _fact(df, roles, pack, k.name)
        cohort = any(t.spell in ("opening", "retained") for t in spell_terms(pack, k.name))
        # an 11-month cohort is no 12
        then = _fact(df, roles, pack, k.name, window=back) \
            if prior and (not cohort or prior.full_cohort) else None
        if now is not None:
            out.append(f"{k.label} ({window_words(pack, df, roles, k.name)}): {now:.1%}" + (
                f"; a year earlier ({window_words(pack, df, roles, k.name, back)}): {then:.1%}, "
                f"{(now - then) * 100:+.1f} points" if then is not None
                else "; no full year-earlier window in the data") + ".")
    frame = df.assign(_period=pd.Series(pd.NaT, index=df.index).astype("period[M]"))
    if stock is not None and (s_ := _fact(frame, roles, pack, stock.name, by="_period")) is not None:
        inside = s_[[p for p in s_.index if p.end_time.normalize() > lo]].dropna()
        if len(inside) > 1 and inside.idxmax() != inside.index[-1] \
                and inside.iloc[-1] <= 0.95 * inside.max():   # a material fall only (5%+)
            peak, now = inside.max(), inside.iloc[-1]
            out.append(f"{stock.label} peaked at {_amount(df, roles, stock, peak)} "
                       f"({inside.idxmax().strftime('%b %Y')}) and is {_amount(df, roles, stock, now)} "
                       f"at {as_of:%-d %b %Y} ({(now / peak - 1) * 100:+.1f}%).")
    if ends is not None and (e := _fact(frame, roles, pack, ends.name, by="_period")) is not None \
            and len(e) >= 6:
        top, usual = e.idxmax(), float(e.drop(e.idxmax()).median())
        if e[top] >= 10 and e[top] >= 2.5 * max(usual, 1):
            line = (f"The largest month of {ends.phrase}: {top.strftime('%b %Y')} with {e[top]:,.0f} (a usual month: "
                    f"{usual:,.0f})")
            group = next((roles[g] for g in pack.overview_groups if g in roles), None)
            if group and (g := _fact(df, roles, pack, ends.name, by=group,
                                           window=(top.start_time, top.end_time.normalize()))) is not None \
                    and len(g) and g.max() >= 0.5 * e[top]:
                line += f", {g.max():,.0f} of them in {g.idxmax()} ({group})"
            out.append(line + ".")
    out += _ledger_story(pack, df, roles, w)
    col = _person_col(pack, roles)
    heads = stock_kpi(pack, roles, "count")
    if col:
        people = df[col].nunique()
        now_n = _fact(df, roles, pack, heads.name) if heads else None
        out.append(f"{len(df):,} rows are {people:,} {pack.spell.words.plural} ({int((df[col].value_counts() > 1).sum()):,} with "
                   "more than one row)" + (f"; {now_n:,.0f} in force at {as_of:%-d %b %Y}"
                                           if now_n is not None else "")   # 0 in force is said (C5)
                   + ".")
    if later := not_counted_yet(pack, df, roles, as_of):
        out.append(later)
    if held := today_only(pack, df, roles):
        out.append(held)
    undated = int(_ended_undated(df, roles, pack).sum())
    if undated:
        out.append(f"{undated:,} rows have an ended status but no end date.")
    return out


def _ledger_story(pack: Pack, df: pd.DataFrame, roles: dict[str, str], w: Window) -> list[str]:
    """The ledger's facts (Domain 6 S9), from the pack's KPIs of one moves or opening term each:
    the bridge (opening + new + expansion + reactivation − contraction − churned = now), the
    churned-customer count beside the cohort rate (they differ: new and returning customers'
    churns are in the count only), and the largest month of churned value against a usual
    month, with its largest single entity's share and group. No line without its KPIs."""
    out: list[str] = []
    lo, as_of = w.lo, w.as_of
    stock = stock_kpi(pack, roles, "money")
    parts = {m: ledger_kpi(pack, roles, "moves", m) for m in ("new", "expansion", "reactivation",
                                                              "contraction", "churn")}
    opening = ledger_kpi(pack, roles, "opening")
    say = lambda k, v: _amount(df, roles, k, v)   # noqa: E731
    if stock and opening and parts["new"] and parts["churn"]:
        val = {m: _fact(df, roles, pack, k.name) for m, k in parts.items() if k}
        start, now = _fact(df, roles, pack, opening.name), _fact(df, roles, pack, stock.name)
        if start is not None and now is not None and None not in val.values():
            terms = [f"+ {k.phrase} {say(k, val[m])}" if m not in ("contraction", "churn")
                     else f"− {k.phrase} {say(k, val[m])}" for m, k in parts.items() if k]
            rest = now - start - sum(v if m not in ("contraction", "churn") else -v for m, v in val.items())
            if not parts["expansion"] and abs(rest) >= 0.005:   # it ties, always (pass 3: \$198 off)
                terms.append(f"{'+' if rest > 0 else '−'} net expansion less contraction (not split in "
                             f"this file) {say(stock, abs(rest))}")
            begin = w.moves_from   # the bridge opens where its moves start
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
    if lost and rate and (n := _fact(df, roles, pack, lost.name)) is not None \
            and _fact(df, roles, pack, rate.name) is not None:   # never without its rate
        who = pack.spell.words.plural
        out.append(f"{n:,.0f} {who} left at a month-end in the window ({lost.phrase}); {rate.phrase} "
                   f"counts only the {who} in force on {w.cohort_start:%-d %b %Y} "
                   "who are not in force now, so "
                   f"the two differ: {who} who joined in the window or came back are in the count only.")
    if (k := parts["churn"]) is not None:
        frame = df.assign(_period=pd.Series(pd.NaT, index=df.index).astype("period[M]"))
        e = _fact(frame, roles, pack, k.name, by="_period")
        e = e[[p for p in e.index if p.end_time.normalize() > lo]] if e is not None else None
        if e is not None and len(e) >= 6 and e.max() > 0:
            top, usual = e.idxmax(), float(e.drop(e.idxmax()).median())
            if e[top] >= 2.5 * max(usual, 1):
                line = (f"The largest month of {k.phrase}: {top.strftime('%b %Y')} with "
                        f"{say(k, e[top])} (a usual month: {say(k, usual)})")
                ent, prev = roles[pack.spell.entity], (top - 1).end_time.normalize()
                rows = stock_rows(df, roles, pack, prev)   # the tile's people (no test account)
                gone = rows[~rows[ent].isin(stock_rows(df, roles, pack, top.end_time.normalize())[ent])]
                if len(gone) and (m := _column(gone, roles, k.num[0][0].sum)).notna().any():
                    big = m.groupby(gone[ent]).sum()
                    line += f", {big.max() / e[top]:.0%} of it one {ent}"
                    group = next((roles[g] for g in pack.overview_groups if g in roles), None)
                    if group:
                        line += f" in {gone.loc[gone[ent] == big.idxmax(), group].iloc[0]} ({group})"
                out.append(line + ".")
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
                    "opening": f"distinct {ent} in force {WINDOW_MONTHS} months before the date, or at the "
                               f"data's first month-end when it covers less ({at}, {gone})",
                    "retained": f"same {ent} (in force at the cohort's start) at the date"
                                + (", each capped at its value then" if t.capped else ""),
                    "moves": (("value moved in from another group: each {e}'s rise in a group "
                               "matched by its fall in another" if t.move == "transfer_in" else
                               "value moved out to another group: each {e}'s fall in a group "
                               "matched by its rise in another").format(e=ent)
                              if t.move in TRANSFERS else
                              f"{t.move} moves: each {ent} whose value went "
                              + {"new": "from 0 (never in force before) to above 0",
                                 "reactivation": "from 0 back above 0 (in force before)",
                                 "expansion": "up", "contraction": "down (not to 0)",
                                 "churn": "to 0"}.get(t.move, ""))
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
