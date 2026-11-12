"""THE spell rules: the as-of, the window and its month-ends, the cohort start, and who is in
force at a date. Every other module reads these, never re-derives them."""
from __future__ import annotations

import threading
from dataclasses import dataclass

import pandas as pd

from .pack import Pack, _day, _meets, _snapshot_column, spell_terms, words


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


@dataclass(frozen=True)
class Window:
    """THE window of a spell KPI (GLOSSARY: Window), built only by `window()` (and its own
    `year_earlier` / `period`): flows count in (lo, as_of], from first_day; an average reads the
    month-ends; moves (and a bridge's opening) start at moves_from, the first date the data knows
    a state at the earliest; a cohort rate (NRR, logo churn) follows the entities in force at
    cohort_start, 12 months before the as-of whatever period is asked (expert M3). On the
    default window moves_from = cohort_start. Never unpacked: readers name what they read."""
    lo: pd.Timestamp
    as_of: pd.Timestamp
    month_ends: tuple[pd.Timestamp, ...]
    first: pd.Timestamp   # spell_from: the first date a spell figure is shown from
    known: pd.Timestamp   # first_known: the first date the data knows each entity's state
    asked: tuple | None = None   # (first day, last day), inclusive, either None

    def __post_init__(self):
        e = self.month_ends   # never empty: the as-of ends its own month, so an average has one
        if not (e and e[-1] == self.as_of   # with lo < each month-end: lo < as_of
                and all(a < b for a, b in zip(e, e[1:]))
                and all(self.lo < x <= self.as_of and x >= self.first for x in e)):
            raise ValueError(f"a window its readers can't share: {self}")

    @property
    def first_day(self) -> pd.Timestamp:   # the inclusive start every label and asked period reads
        return self.lo + pd.Timedelta(days=1)

    @property
    def moves_from(self) -> pd.Timestamp:
        return min(max(self.lo, self.known), self.as_of)

    @property
    def cohort_start(self) -> pd.Timestamp:   # the opening is a real value, never a 0 before the data
        return min(max(self.as_of - pd.DateOffset(months=WINDOW_MONTHS), self.known), self.as_of)

    @property
    def full_cohort(self) -> bool:   # the data knows a state a whole cohort span before the end
        return (self.as_of - pd.DateOffset(months=WINDOW_MONTHS)).to_period("M") >= \
            self.known.to_period("M")

    @property
    def months(self) -> int:
        return len(self.month_ends)

    @property
    def move_months(self) -> int:   # the month-ends moves are counted at (after moves_from)
        return sum(e > self.moves_from for e in self.month_ends)

    def year_earlier(self) -> Window | None:
        """The same window a year earlier, or None when the data covers fewer of its month-ends
        (a 4-month prior is no year-earlier 12)."""
        # its own span a year back, asked or default (never 'the 12 months to' the earlier date:
        # after a leap year that opens on 29 Feb, a day off the asked span's 1 Mar)
        start = self.asked[0] if self.asked and self.asked[0] is not None else \
            self.as_of - pd.DateOffset(months=WINDOW_MONTHS) + pd.Timedelta(days=1)
        prior = _cut(self.as_of, self.first, self.known, (start - pd.DateOffset(years=1),
                                                          self.as_of - pd.DateOffset(years=1)))
        return prior if prior is not None and prior.months >= self.months else None

    def period(self, p: pd.Period) -> Window | None:
        """A period's own window (a month, a quarter, a year; the last clipped to the as-of)."""
        return _cut(self.as_of, self.first, self.known,
                    (p.start_time.normalize(), min(p.end_time.normalize(), self.as_of)))


def _cut(as_of: pd.Timestamp, first: pd.Timestamp, known: pd.Timestamp,
         asked: tuple | None) -> Window | None:
    """window()'s rule on its inputs (the data's as-of and first dates). Month-ends: a span of
    exactly N months (the 12 months to 24 Oct) has N, the end's own month ending at the end; any
    other has one per month it touches ('since 15 Mar' keeps 31 Mar)."""
    asked = tuple(None if x is None else pd.Timestamp(x).normalize() for x in asked) \
        if asked and any(x is not None for x in asked) else None
    if asked and asked[1] is not None:
        as_of = min(as_of, asked[1])
    start = (asked[0] if asked and asked[0] is not None
             else as_of - pd.DateOffset(months=WINDOW_MONTHS) + pd.Timedelta(days=1))
    lo = max(start, first) - pd.Timedelta(days=1)
    if lo >= as_of:   # asked wholly before the data: nothing to read, never a 0 (C5)
        return None
    months = pd.period_range(start.to_period("M"), as_of.to_period("M"))
    # exactly N whole months starting mid-month (25 Oct – 24 Oct): its first month is the end's
    whole = start.day != 1 and start == as_of - pd.DateOffset(months=len(months) - 1) + pd.Timedelta(days=1)
    ends = [min(m.end_time.normalize(), as_of) for m in months[1 if whole else 0:]]
    return Window(lo, as_of, tuple(dict.fromkeys(e for e in ends if e >= first)), first, known,
                  asked)


def window(pack: Pack, df: pd.DataFrame, roles: dict[str, str],
           asked: tuple | None = None) -> Window | None:
    """THE window of a spell KPI asked without a period grouping: the 12 months to the as-of, or
    an asked period (first day, last day, inclusive; either may be None), cut to the span the
    data covers (month-ends on or after its first date: a stock there is complete). None when the
    data has no as-of or first date, or the asked period is wholly before the data."""
    as_of, first = spell_as_of(pack, df, roles), spell_from(pack, df, roles)
    if as_of is None or first is None:
        return None
    return _cut(as_of, first, first_known(pack, df, roles), asked)


@dataclass(frozen=True, eq=False)   # eq=False: a DataFrame is never compared or hashed
class SpellView:
    """A spell KPI's frame of reference, built once by spell_view: the whole data and its roles,
    the window every reader takes, and the first date a spell figure is shown from. A term's own
    rows (its kinds, its filter) travel beside it, never in it."""
    pack: Pack
    roles: dict[str, str]
    whole: pd.DataFrame   # ALL rows: a filter's part maps to them by row label
    window: Window


def spell_view(pack: Pack, whole: pd.DataFrame, roles: dict[str, str],
               asked: tuple | None = None) -> SpellView | None:
    """The view of `whole` for a spell KPI (an asked period clips it); None when the data has no
    as-of or first date, or the asked period is wholly before the data."""
    if not whole.index.is_unique:
        raise ValueError("spell_view: the whole data needs unique row labels")
    w = window(pack, whole, roles, asked)
    return None if w is None else SpellView(pack, roles, whole, w)


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
                 asked: tuple | None = None) -> str:
    """'as of 31 Oct 2026' (a state) or '12 months to 31 Oct 2026' (a flow, a rate): how a spell
    KPI's answer names its period, read from its Window; '' for other KPIs."""
    kpi = pack.kpi(name)
    if not pack.spell or not kpi or not spell_terms(pack, name):
        return ""
    w = window(pack, df, roles, asked)
    if w is None:
        return ""
    if kpi.state:
        return f"as of {w.as_of:%-d %b %Y}"
    spells = {t.spell for t in spell_terms(pack, name)}
    months = w.months
    if "moves" in spells:   # moves start after the first known state (pass 6: a month too many)
        months = w.move_months
        if months < 1:
            return ""
    if "retained" in spells:   # a cohort rate: the cohort ending there, whatever period is asked
        n = round((w.as_of - w.cohort_start).days / 30.44)
        return f"{n}-month cohort to {w.as_of:%-d %b %Y}" if n >= 1 else ""
    if spells == {"opening"}:   # a bridge's opening level (Opening MRR): where its moves start
        return f"on {w.moves_from:%-d %b %Y}"
    return f"{months} month{'s' * (months != 1)} to {w.as_of:%-d %b %Y}"


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


def term_rows(whole: pd.DataFrame, roles: dict[str, str], pack: Pack, counted: pd.Series,
              where: list | None, t: pd.Timestamp | None = None) -> pd.DataFrame:
    """THE rows a spell term reads (GLOSSARY: Term rows): of the KPI's row kinds (`counted`, by
    the whole data's row label) meeting the term's `where`; with `t`, only those in force then
    (_in_force). The KPI, the ledger and the model frame's markers read it, never their own copy."""
    rows = whole if t is None else _in_force(whole, roles, pack, t)
    m = counted.reindex(rows.index, fill_value=False).to_numpy()
    if where:
        m = m & _meets(rows, roles, where).to_numpy()
    return rows[m]


def _in_force_at(whole: pd.DataFrame, roles: dict[str, str], pack: Pack, t) -> pd.DataFrame:
    """_in_force's rule, uncached."""
    s, snap = pack.spell, _snapshot_column(pack, roles)
    ent = roles[s.entity]
    if snap:
        from agents.utils import snapshot_rows
        upto = whole[_day(whole[snap]) <= t]
        rows = snapshot_rows(upto, [ent], snap, every=s.snapshot_every)
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
        # a declared monthly cadence reads months: snapshots on each entity's own day are one
        # month, and a month with no file at all is no gap (C7)
        step = d["__t"].dt.to_period("M") if s.snapshot_every else d["__t"]
        steps = pd.Series(sorted(step.unique()))
        keys = [ent, *([roles[s.start]] if s.start in roles else [])]
        if s.start not in roles:   # no start date: back after a step it was missing from is a
            # new spell, a hire (PR7-01: a return after a gap was no hire)
            at = pd.Series(steps.searchsorted(step), index=d.index)
            d = d.assign(__run=(at.groupby(d[ent], sort=False).diff().fillna(2) > 1)
                         .groupby(d[ent], sort=False).cumsum())
            keys.append("__run")
        g = d.groupby(keys, sort=False, dropna=False)
        if which == "starts":
            rows = g.head(1)
            when = (_day(rows[roles[s.start]]) if s.start in roles
                    else rows["__t"].where(step[rows.index] > steps.min()))   # the first holds none
        else:
            rows = g.tail(1)
            when = _day(rows[roles[s.end]]) if s.end in roles else pd.Series(pd.NaT, index=rows.index)
            first = d.groupby(step, sort=True)["__t"].min()   # each step's first snapshot
            pos = steps.searchsorted(step[rows.index], side="right")
            missing = pd.Series([first[steps[p]] if p < len(steps) else pd.NaT for p in pos],
                                index=rows.index)
            when = when.where(when.notna(), missing)
        rows = rows.drop(columns=[c for c in ("__t", "__run") if c in rows])
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
