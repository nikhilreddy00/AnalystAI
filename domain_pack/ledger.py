"""THE ledger and its moves: each entity's value at each month-end, and its change between two
(new, expansion, contraction, churn, reactivation), opening and retained cohorts."""
from __future__ import annotations

import pandas as pd

from .pack import Term, Unavailable, _column, _meets, _snapshot_column
from .spell import SpellView, _in_force, _month_ends


MOVES = ("new", "expansion", "contraction", "churn", "reactivation")


def _ledger(view: SpellView, term: Term, counted, points, part, by):
    """THE ledger: (values, in the part, group), each an entity × date frame. An entity's value
    at t is its rows in force at t (on ALL rows: _in_force) of the KPI's kinds meeting `where`,
    the term's role summed (concurrent spells add up) or 1 for a count (the entity is there,
    however many of its spells are); 0 when none. `in part`:
    one of those rows is in `part` (a filter); `group`: the `by` value of its first row.
    `counted`: by the whole data's row label, the rows of the KPI's kinds."""
    whole, roles, pack = view.whole, view.roles, view.pack
    ent = roles[pack.spell.entity]
    vals, inside, grp = {}, {}, {}
    for t in points:
        rows = _in_force(whole, roles, pack, t)
        m = counted.reindex(rows.index, fill_value=False).to_numpy()
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


def _ledger_term(view: SpellView, term: Term, part, counted, by, axis, groups, cohort: bool):
    """An opening / retained / moves term from the ledger. Moves: each pair of consecutive
    month-ends classes each entity's change (0→x new, or reactivation when it was in force at an
    earlier month-end; up expansion; down contraction; x→0 churn), counted where its later row
    is (a churn: its earlier row), so a segment's bridge adds up and a mover's growth counts in
    its new segment. Opening / retained: the entities in force at the start, placed by their row
    then; retained values them at the end (an entity back after a churn is retained). The start:
    a cohort rate's (`cohort`: NRR, logo churn) is the Window's cohort_start, 12 months before
    the end whatever period is asked (expert M3); a bridge's opening level is where its moves
    start (moves_from); by period, each period's own cohort (NaN before the data). A start or
    end before the data counts 0. `part`: the filtered rows' labels; `counted`: the rows of the
    KPI's kinds (as in _ledger)."""
    roles, pack, w = view.roles, view.pack, view.window
    as_of, lo = w.as_of, w.moves_from   # moves never start before the data knows a state (an
    # asked window of 20 months keeps its 20: pass 3)

    # from the month-end before the first period, so the first period's moves have a start
    before = (w.first.to_period("M") - 1).end_time.normalize()
    history = [before, *(t for t in _month_ends(before, as_of) if t <= as_of)]
    if term.spell == "moves":
        points = sorted({*history, lo, as_of})
    else:
        periods = [w.period(p) for p in axis] if axis is not None else []
        ends = [pw.as_of for pw in periods] if axis is not None else [as_of]
        starts = [pw.cohort_start for pw in periods] if axis is not None \
            else [w.cohort_start if cohort else lo]
        points = sorted({*ends, *starts})
    points = [t for t in points if t <= as_of]
    by = by if axis is None else None   # a period axis is the ledger's own dates, never a group
    v, inside, g = _ledger(view, term, counted, points, part, by)

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
                series[series.index <= pd.Period(w.first, freq=axis.freq)] = float("nan")
            return series
        inside_window = [b for b in out if b > lo]
        if not inside_window:   # no month of moves (one month of data): unknown, never $0 (pass 7)
            return Unavailable("too_short")
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

    if axis is not None:   # a period whose whole cohort precedes the data: NaN, never a short one
        return pd.Series({p: cohort(s_, e) if pw.full_cohort else float("nan")
                          for p, pw, s_, e in zip(axis, periods, starts, ends)},
                         dtype=float).reindex(axis)
    value = cohort(starts[0], as_of)
    return Unavailable("outside_the_data") if isinstance(value, float) and pd.isna(value) else value
