"""THE ledger and its moves: each entity's value at each month-end, and its change between two
(new, expansion, contraction, churn, reactivation), opening and retained cohorts."""
from __future__ import annotations

import pandas as pd

from .pack import Term, Unavailable, _column, _snapshot_column
from .spell import SpellView, _month_ends, term_rows


MOVES = ("new", "expansion", "contraction", "churn", "reactivation")
TRANSFERS = ("transfer_in", "transfer_out")   # a move between parts: nothing in the whole
IN, GROUP = "__in", "__group"   # a ledger line's part: in the filter's part, its `by` group


def _ledger(view: SpellView, term: Term, counted, points, part, by):
    """THE ledger (GLOSSARY: Ledger): (lines, in the part, group). `lines`: one row per ledger
    line (entity, in `part`, `by` group) × date: the entity's term rows at t (term_rows, on ALL
    rows) in that line, the term's role summed (concurrent spells add up) or 1 for a count
    (there, however many of its spells are); 0 when none. `in part` / `group`, entity × date: one
    of its rows is in `part` (a filter) / its `by` value, the first in sort order: where a
    cohort places an entity (GLOSSARY: Opening cohort). `counted`: by the whole data's row label, the rows of
    the KPI's kinds."""
    whole, roles, pack = view.whole, view.roles, view.pack
    ent = roles[pack.spell.entity]
    vals, inside, grp = {}, {}, {}
    for t in points:
        rows = term_rows(whole, roles, pack, counted, term.where, t)
        here = pd.Series(rows.index.isin(part), index=rows.index)
        key = [rows[ent].rename(ent), here.rename(IN),
               (rows[by] if by else pd.Series("", index=rows.index)).rename(GROUP)]
        v = _column(rows, roles, term.sum) if term.sum is not None else pd.Series(1.0, index=rows.index)
        if len(rows):   # a count: 1 per line, however many rows
            vals[t] = v.groupby(key, dropna=False).agg("sum" if term.sum is not None else "max")
        inside[t] = here.groupby(rows[ent]).any()
        if by:   # two groups at once: the first in sort order, never the file's (PR9-01)
            s = rows[by].iloc[rows[by].astype(str).argsort(kind="stable")]
            grp[t] = s.groupby(rows[ent].loc[s.index]).first()
    lines = pd.DataFrame(vals, columns=points).fillna(0.0).clip(lower=0)
    if not vals:
        lines.index = pd.MultiIndex.from_arrays([[], [], []], names=[ent, IN, GROUP])
    ents = lines.index.get_level_values(0).unique()
    return (lines, pd.DataFrame(inside, columns=points).reindex(ents).eq(True),
            pd.DataFrame(grp, columns=points).reindex(ents) if by else None)


def _moved(x: pd.Series, y: pd.Series, ever: pd.Series, kind: str, count: bool) -> pd.Series:
    """Each ledger line's amount of one move `kind` between two month-ends (GLOSSARY: Move), x
    and y its values then. Per entity its lines' matched rise and fall is a transfer, each line's
    share in proportion to its own; the rest is classed on the entity's whole value (`ever`:
    in force at an earlier month-end) and booked in the lines that moved. On a count a line is
    there or not: a part gained or lost while the entity stays is a transfer on its own."""
    ents = x.index.get_level_values(0)
    up, down = (y - x).clip(lower=0), (x - y).clip(lower=0)

    def whole(s):
        return s.groupby(ents).transform("sum")
    was, now, rose, fell = whole(x), whole(y), whole(up), whole(down)
    if count:
        stays = (was > 0) & (now > 0)
        t_in, t_out = up.where(stays, 0.0), down.where(stays, 0.0)
    else:
        matched = pd.concat([rose, fell], axis=1).min(axis=1)
        t_in = (up * matched / rose.where(rose > 0)).fillna(0.0)
        t_out = (down * matched / fell.where(fell > 0)).fillna(0.0)
    rise, fall = up - t_in, down - t_out
    before = pd.Series(ever.reindex(ents).fillna(False).to_numpy(dtype=bool), index=x.index)
    return {"transfer_in": t_in, "transfer_out": t_out,
            "new": rise.where((was == 0) & ~before, 0.0),
            "reactivation": rise.where((was == 0) & before, 0.0),
            "expansion": rise.where(was > 0, 0.0),
            "contraction": fall.where(now > 0, 0.0), "churn": fall.where(now == 0, 0.0)}[kind]


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
    lines, inside, g = _ledger(view, term, counted, points, part, by)
    # each entity's value: its lines summed; a count's, there or not (two lines are one customer)
    v = lines.groupby(level=0).agg("sum" if term.sum is not None else "max")

    def total(x: pd.Series) -> float | pd.Series:
        """x summed over the ledger lines in the part (by group or one number)."""
        x = x[x.index.get_level_values(IN).to_numpy(dtype=bool) & (x != 0).to_numpy()]
        if by:
            return x.groupby(level=GROUP).sum().reindex(groups, fill_value=0).rename_axis(None)
        return float(x.sum())

    def placed(x: pd.Series, at) -> float | pd.Series:
        """x summed over a cohort's entities, each placed by its row at `at` (Opening cohort)."""
        x = x[inside[at] & (x != 0)]
        if by:
            return x.groupby(g.loc[x.index, at]).sum().reindex(groups, fill_value=0)
        return float(x.sum())

    if term.spell == "moves":
        prev_ever = (v > 0).cummax(axis=1).shift(1, axis=1, fill_value=False)
        out = {b: total(_moved(lines[a], lines[b], prev_ever[a], term.move, term.sum is None))
               for a, b in zip(points, points[1:])}
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

    def level(start, end):
        if start not in v.columns or end not in v.columns or start >= end:
            return float("nan")   # a window after the data (a date filter past the as-of)
        if term.spell == "opening" and not cohort:   # a bridge's opening: its lines (Ledger)
            return total(lines[start])
        base = v[start]
        if term.spell == "opening":
            return placed(base, start)
        later = v[end].where(base > 0, 0.0)
        return placed(later.clip(upper=base) if term.capped else later, start)

    if axis is not None:   # a period whose whole cohort precedes the data: NaN, never a short one
        return pd.Series({p: level(s_, e) if pw.full_cohort else float("nan")
                          for p, pw, s_, e in zip(axis, periods, starts, ends)},
                         dtype=float).reindex(axis)
    if starts[0] >= as_of:   # a cohort of no span (one month of data): no rate, never 0% churn
        return Unavailable("too_short")
    return level(starts[0], as_of)   # both are ledger points, start < end: never NaN
