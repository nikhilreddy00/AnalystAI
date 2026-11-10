"""THE spell rules: the as-of, the window and its month-ends, the cohort start, and who is in
force at a date. Every other module reads these, never re-derives them."""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import NamedTuple

import pandas as pd

from .pack import Pack, _day, _snapshot_column, spell_terms, words


# A spell series reaches back at most this many years and has at most this many periods (one
# in-force pass per period: a weekly axis over a decade is 520 passes).
# ponytail: one pass per period; an interval sweep over start/end events if rosters pass ~500k rows
SPELL_YEARS = 10
SPELL_PERIODS = 520


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
            if col == snap and _month_labelled(pack, df, roles):
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


class SpellWindow(NamedTuple):
    """spell_window's answer: flows count in (lo, as_of], an average reads the month-ends."""
    lo: pd.Timestamp
    as_of: pd.Timestamp
    month_ends: list[pd.Timestamp]


def spell_window(pack: Pack, df: pd.DataFrame, roles: dict[str, str],
                 window: tuple | None = None) -> SpellWindow | None:
    """THE window of a spell KPI asked without a period grouping: (lo, as_of, month-ends), flows
    counted in (lo, as_of] and the average over the month-ends. The 12 months to the as-of, or
    the shorter span the data covers (month-ends on or after its first date: a stock there is
    complete); an asked window (lo, hi) clips both ends."""
    as_of, first = spell_as_of(pack, df, roles), spell_from(pack, df, roles)
    if as_of is None or first is None:
        return None
    if window and window[1] is not None:
        as_of = min(as_of, pd.Timestamp(window[1]).normalize())
    lo = as_of - pd.DateOffset(months=WINDOW_MONTHS)
    ends = _month_ends(lo, as_of)
    if window and window[0] is not None:   # an asked start: its own month's end counts ('since
        lo = pd.Timestamp(window[0]).normalize() - pd.Timedelta(days=1)   # 15 Mar' keeps 31 Mar)
        ends = [e for e in _month_ends(lo - pd.offsets.MonthEnd(1), as_of) if e > lo]
    return SpellWindow(max(lo, first - pd.Timedelta(days=1)), as_of,
                       [e for e in ends if e >= first])


@dataclass(frozen=True, eq=False)   # eq=False: a DataFrame is never compared or hashed
class SpellView:
    """A spell KPI's frame of reference, built once by spell_view: the whole data and its roles,
    the window every reader takes, and the first date a spell figure is shown from. A term's own
    rows (its kinds, its filter) travel beside it, never in it."""
    pack: Pack
    roles: dict[str, str]
    whole: pd.DataFrame   # ALL rows: a filter's part maps to them by row label
    window: SpellWindow
    first: pd.Timestamp   # spell_from


def spell_view(pack: Pack, whole: pd.DataFrame, roles: dict[str, str],
               window: tuple | None = None) -> SpellView | None:
    """The view of `whole` for a spell KPI (an asked window clips it); None when the data has no
    as-of or first date. An asked window past the data is no error: its month-ends are none."""
    w = spell_window(pack, whole, roles, window)
    if w is None:
        return None
    view = SpellView(pack, roles, whole, w, spell_from(pack, whole, roles))
    ends = w.month_ends
    if not (whole.index.is_unique and all(a < b for a, b in zip(ends, ends[1:]))
            and all(w.lo < e <= w.as_of and e >= view.first for e in ends)):
        raise ValueError(f"spell_view: a window its readers can't share ({w})")
    return view


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
        known = first_known(pack, df, roles)
        months = sum(p > known for p in pts)
        if months < 1:
            return ""
    if spells & {"opening", "retained"}:   # whatever period is asked: the cohort ending there
        start = cohort_start(as_of, first_known(pack, df, roles))
        if spells == {"opening"}:   # a level at the cohort's start (Opening MRR)
            return f"on {start:%-d %b %Y}"
        n = round((as_of - start).days / 30.44)
        return f"{n}-month cohort to {as_of:%-d %b %Y}" if n >= 1 else ""
    return f"{months} month{'s' * (months != 1)} to {as_of:%-d %b %Y}"


# THE spell window and cohort span: flows and rates read the months to the as-of; every sentence
# naming the span reads this (or window_words), never a written '12 months'
WINDOW_MONTHS = 12


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


def today_only(pack: Pack, df: pd.DataFrame, roles: dict[str, str]) -> str:
    """THE sentence about a list's statuses held for today only (GLOSSARY: Today-only status):
    how many rows, and why they are out of every month-end. Snapshots carry each date's own
    status: never. The file's story and every spell answer say it."""
    s = pack.spell
    if not (s and s.today_only and "status" in roles) or _snapshot_column(pack, roles):
        return ""
    n = int(words(df[roles["status"]]).isin(set(words(pd.Series(s.today_only)))).sum())
    return f"{n:,} row{'s are' if n != 1 else ' is'} {s.words.today_only}" if n else ""


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


def _month_labelled(pack: Pack, df: pd.DataFrame, roles: dict[str, str]) -> bool:
    """THE month-label test: the pack reads labels and every snapshot is on a 1st (one mid-month
    date: the dates are real days)."""
    snap = _snapshot_column(pack, roles)
    return bool(snap and pack.spell.month_labels and (_day(df[snap]).dropna().dt.day == 1).all())


def first_known(pack: Pack, df: pd.DataFrame, roles: dict[str, str]) -> pd.Timestamp:
    """THE first date the data knows each entity's state: snapshots, the first snapshot (month
    labels: that month's end, as spell_as_of reads them); spells, the day before the first date
    (in force by start dates). The ledger's opening and moves start there at the earliest."""
    first = spell_from(pack, df, roles)
    if not _snapshot_column(pack, roles):
        return first - pd.Timedelta(days=1)
    return first + pd.offsets.MonthEnd(0) if _month_labelled(pack, df, roles) else first


def cohort_start(as_of, known):
    """THE start of a cohort KPI's window: 12 months before its end, or the data's first known
    state when it covers less: the opening is a real value, never a 0 before the data."""
    return max(as_of - pd.DateOffset(months=WINDOW_MONTHS), known)
