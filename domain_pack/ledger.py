"""THE ledger and its moves: each entity's value at each month-end, and its change between two
(new, expansion, contraction, churn, reactivation), opening and retained cohorts."""
from __future__ import annotations


import pandas as pd

from .pack import (Term, _column, _meets, _snapshot_column)
from .spell import (WINDOW_MONTHS, _in_force, _month_ends, cohort_start, first_known)


MOVES = ("new", "expansion", "contraction", "churn", "reactivation")


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
    known = first_known(pack, whole, roles)
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
        starts = [e - pd.DateOffset(months=WINDOW_MONTHS) for e in ends] if axis is not None \
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
