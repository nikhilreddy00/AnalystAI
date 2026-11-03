"""
Shared utilities for agent modules.
"""
from __future__ import annotations

import re

from guardrails import delimit_untrusted, is_pii_column


def strip_markdown_fences(raw: str) -> str:
    """
    Strip markdown code fences from LLM responses before JSON/code parsing.

    Handles:
      - ```json ... ```
      - ```python ... ```
      - ``` ... ``` (no language specifier)
      - Triple backticks embedded inside content (uses first/last fence pair)
      - Prose before/after a fenced block (returns the longest block)
    """
    raw = raw.strip()
    match = re.match(
        r"^```(?:json|python|sql)?\s*\n?(.*?)```\s*$",
        raw,
        re.DOTALL,
    )
    if match:
        return match.group(1).strip()
    # Prose around the block ("Now I have what I need: ```python ...```"): take the longest block.
    blocks = re.findall(r"```(?:json|python|sql)?[ \t]*\n(.*?)```", raw, re.DOTALL)
    return max(blocks, key=len).strip() if blocks else raw


def safe_column_name(col: str) -> str:
    """
    Escape a column name for safe use in generated Python code strings.
    BUG-23: Handles single quotes, double quotes, brackets, backslashes, and newlines.
    """
    return (
        col
        .replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
    )


# pandas 3 rejects the old period-end aliases ("M" → "ME", "Q" → "QE", "Y"/"A" → "YE").
_PANDAS_FREQ = {"M": "ME", "Q": "QE", "Y": "YE", "A": "YE"}


def pandas_freq(freq: str) -> str:
    """User-facing frequency letter → the resample alias the installed pandas accepts."""
    return _PANDAS_FREQ.get(freq.upper(), freq.upper())


def conversation_context(session, n_turns: int = 6) -> str:
    """The conversation memory Intake and the SQL agent share: the last `n_turns` messages
    (user turns cut to 1,000 chars, assistant turns to 400: a pasted wall of text must not ride
    along on every later call) and the last query run against a database, delimited because
    assistant turns quote data. "" when there is nothing yet."""
    lines = [f"{m.role.value.upper()}: {m.content[:1000 if m.role.value == 'user' else 400]}"
             for m in session.conversation_history[-n_turns:]]
    contract = session.data_contract
    if contract and contract.source_query:
        lines.append(f"LAST QUERY RUN:\n{contract.source_query.code}")
    if not lines:
        return ""
    # Earlier answers quote the data ("Jane Doe leads with $9,120"): the run's PII values become
    # the same tokens the LLM client's guard would use.
    from llm.client import pseudonymize
    return delimit_untrusted(pseudonymize("\n".join(lines))[0])


# A month is judged partial against months at least weekly-dense (median distinct dates), or when
# the dates keep a regular cadence (biweekly or semi-monthly pay runs): an 18-row sample's 1–3
# irregular dates a month are noise, a biweekly payroll cut mid-month is not.
PARTIAL_MIN_DAYS = 4
REGULAR_CV = 0.25   # the gaps between distinct dates vary less than this (std / mean): a cadence


def partial_last_month(dates) -> bool:
    """True when the data stops before the end of its last calendar month and that month has far
    fewer distinct days than the others (a mid-month export). A month-over-month change must not
    compare such a month with a full one. Monthly grain or sparse data (an 18-row sample: 1–3
    dates a month) is never partial: there are too few dates to tell.
    The analysis built-ins use it too (analysis_agent.standard_stats)."""
    import pandas as pd

    d = pd.to_datetime(dates, format="mixed", errors="coerce").dropna()
    if d.empty:
        return False
    days = d.dt.normalize().groupby(d.dt.to_period("M")).nunique().sort_index()
    if len(days) < 3:
        return False
    last = d.max().normalize()
    full = days.iloc[:-1].median()
    gaps = d.dt.normalize().drop_duplicates().sort_values().diff().dt.days.dropna()
    regular = len(gaps) >= 3 and gaps.std() <= REGULAR_CV * gaps.mean()
    return bool(last < last + pd.offsets.MonthEnd(0) and (full >= PARTIAL_MIN_DAYS or regular)
                and days.iloc[-1] < 0.9 * full)


def weekly_cadence(dates) -> bool:
    """One date a week (POS week endings): distinct dates 7 days apart. Weekly flows summed into
    calendar months show a ~25% sawtooth (4 vs 5 week-endings). Mirrored in the analysis
    built-ins (analysis_agent.standard_stats)."""
    import pandas as pd

    d = pd.to_datetime(dates, errors="coerce").dropna().drop_duplicates().sort_values()
    return len(d) >= 4 and d.diff().dt.days.median() == 7


def looks_like_sequential_id(s, n: int) -> bool:
    """True if the column looks like a row index / sequential id:
    whole-number values that fill an interval close to len(df). Shared by visualization (row
    counts) and ingestion (column roles)."""
    import pandas as pd

    arr = s.dropna()
    if arr.empty or not pd.api.types.is_numeric_dtype(arr):
        return False
    if not pd.api.types.is_integer_dtype(arr):
        # Float column containing only whole numbers also counts.
        try:
            if not bool((arr % 1 == 0).all()):
                return False
        except TypeError:
            return False
    nu = int(arr.nunique())
    if nu / max(n, 1) < 0.95:
        return False
    spread = float(arr.max()) - float(arr.min())
    return spread <= n * 1.5


def pii_columns(contract) -> set[str]:
    """The columns whose values are personal data: what ingestion detected (by name or by
    values, e.g. `sales_rep` holding names) plus the name rule. Every mask reads this set."""
    if contract is None:
        return set()
    # The name rule alone never takes a measure (births = 120 is a count, not a birth date).
    return {c.name for c in contract.columns if c.pii or (is_pii_column(c.name) and not c.is_metric)}


# ── Aggregation from the semantic layer (Phase 5.5) ───────────────────────────────────────────

def column_hint(contract, column: str):
    """The contract's ColumnInfo for `column` when it carries a semantic-layer hint, else None."""
    if contract is None:
        return None
    return next((c for c in contract.columns if c.name == column and c.aggregation), None)


def effective_agg(contract, column: str, requested: str | None = None) -> str | None:
    """How `column` aggregates. A level ('last' / 'first': a balance, MRR) overrides a sum,
    asked for or defaulted: summing snapshots counts the same money again. A layer's average
    ('mean': ALOS, a temperature) only replaces a default: an explicit sum ("total payroll",
    "how many failed") is kept. Any other explicit aggregation is kept."""
    hint = column_hint(contract, column)
    if hint is None:
        return requested
    if hint.aggregation in _LEVEL and requested in (None, "sum"):
        return hint.aggregation
    return requested if requested is not None else hint.aggregation


class LevelUnavailable(ValueError):
    """A level (a balance) whose entity or time column is gone (dropped at the high-null gate):
    its total can't be computed, and a plain sum would count each snapshot again."""


_LEVEL = ("last", "first")


def _snapshots(df, hint):
    """The frame ready for a level's window: undated snapshots out (one can't be the latest),
    ordered by time. Missing entity or time column: LevelUnavailable."""
    import pandas as pd
    missing = [c for c in [*hint.aggregation_by, hint.aggregation_time] if not c or c not in df.columns]
    if missing:   # no entity at all (aggregation_by []): one series, a single account's statement
        raise LevelUnavailable(f"{hint.name} is a level (a balance-like value), and the column "
                               f"{', '.join(missing or ['entity'])} it needs is no longer in the "
                               "data, so it can't be totalled")
    t = pd.to_datetime(df[hint.aggregation_time], format="mixed", errors="coerce")
    return _in_time_order(df.loc[t.notna()].assign(_t=t[t.notna()]))


def time_axis(contract, metric: str | None = None, df=None) -> str | None:
    """The date a metric is plotted and trended over: a level's own snapshot time when the layer
    names one, else the data's first time axis (never a birth date). One rule for charts,
    answers and the built-in trend."""
    hint = column_hint(contract, metric) if metric else None
    cands = [hint.aggregation_time] if hint and hint.aggregation_time else []
    cands += list(contract.date_columns) if contract is not None else []
    return next((c for c in cands if c and (df is None or c in df.columns)), None)


def aggregate(df, keys, column: str, agg: str, contract=None, over_time: bool = False):
    """`df.groupby(keys)[column].agg(agg)`, except a level ('last' / 'first'): over time (a key
    is a period) each entity's window value within each period, summed; across groups (no
    period) the current total, each entity's latest snapshot in the latest period (latest_rows),
    so the answer and its chart agree. Missing values are never carried forward."""
    import pandas as pd
    hint = column_hint(contract, column)
    names = [k for k in (keys if isinstance(keys, list) else [keys]) if isinstance(k, str)]
    if agg not in _LEVEL and not over_time and (hint is None or hint.aggregation_time not in names):
        return level_rows(df, column, contract).groupby(keys)[column].agg(agg)
    if agg not in _LEVEL or hint is None:
        return df.groupby(keys)[column].agg("sum" if agg in _LEVEL else agg)
    if not over_time and hint.aggregation_time not in names:
        return latest_rows(df, column, contract).groupby(keys)[column].sum()
    keys = keys if isinstance(keys, list) else [keys]
    if agg == "last":   # each entity's month-end in each period, by the one rule (snapshot_rows)
        _snapshots(df, hint)   # the entity and time columns are there, or LevelUnavailable
        grp = df.groupby([k.loc[df.index] if isinstance(k, pd.Series) else k for k in keys])
        labelled = df.assign(_period=grp.ngroup())
        rows = snapshot_rows(labelled[labelled["_period"] >= 0], hint.aggregation_by,
                             hint.aggregation_time, per="_period")
        totals = rows.groupby("_period")[column].sum(min_count=1).dropna()
        totals.index = grp.size().index[totals.index]
        return totals
    d = _snapshots(df.dropna(subset=[column]), hint)
    per = d.groupby([*(k.loc[d.index] if isinstance(k, pd.Series) else k for k in keys),
                     *(d[e] for e in hint.aggregation_by)])[column].agg(agg)
    return per.groupby(level=list(range(len(keys)))).sum()


def with_display_names(text: str, display: dict[str, str] | None) -> str:
    """Raw column names (also with spaces for underscores) → their display names, in one pass,
    longest first: 'order_date' wins over 'order', and a display name already written ('Order
    Total' for Total, 'Loan Product' for Product) is kept, never renamed again. A plain-word
    name ('Total', 'Product') is renamed only as written: 'total' is the English word. The one
    rename for every text a user reads (insight, expert review)."""
    import re
    if not text or not display:
        return text
    shown: dict[str, str] = {}
    for raw, d in display.items():
        if raw and raw.lower() != d.lower():
            for form in dict.fromkeys((raw, raw.replace("_", " "))):
                shown.setdefault(form.lower(), d)
    if not shown:
        return text
    keep = {d.lower() for d in shown.values()}
    plain = {r for r in display if r and re.fullmatch(r"[A-Za-z]+(?: [A-Za-z]+)*", r)}
    low_plain = {p.lower() for p in plain}
    pat = "|".join(re.escape(k) for k in sorted(shown.keys() | keep, key=len, reverse=True))

    def swap(m: re.Match[str]) -> str:
        w = m.group(0)
        if w.lower() in keep or (w not in plain and w.lower() in low_plain):
            return w
        return shown.get(w.lower(), w)
    return re.sub(rf"(?<!\w)(?:{pat})(?!\w)", swap, text, flags=re.IGNORECASE)


def pct_change(now, then) -> float | None:
    """(now / then − 1) × 100, or None when `then` is zero, negative or missing: a percent change
    off a zero or negative base means nothing (net flow −$4,464 → +$9,170 is not "−305%"). The one
    rule for every % change an answer states."""
    import math
    if then is None or now is None or (isinstance(then, float) and math.isnan(then)) or then <= 0:
        return None
    return (now / then - 1) * 100


def level_rows(df, column: str, contract):
    """The rows a statistic of `column` by group (mean, median, count …) reads: a level's latest
    snapshot per entity (an average balance by branch, never every week's snapshot of it: an
    account weighted by its history, a closed one still averaged in); anything else: all rows.
    The one selector for answers and their charts."""
    hint = column_hint(contract, column)
    return latest_rows(df, column, contract) if hint is not None and hint.aggregation in _LEVEL \
        else df


def latest_rows(df, column: str, contract):
    """For a level by group (bars, answers): each entity's latest snapshot (or first, for a
    'first' window), the rows a current total adds up (snapshot_rows). Anything else: the frame
    as it is."""
    hint = column_hint(contract, column)
    if hint is None or hint.aggregation not in _LEVEL:
        return df
    _snapshots(df, hint)   # the entity and time columns are there, or LevelUnavailable
    # the latest snapshot first, then its value: a blank latest balance (a row left out, an
    # account closed) is never replaced by an older one (never carried forward)
    rows = snapshot_rows(df, hint.aggregation_by, hint.aggregation_time, hint.aggregation)
    return rows.dropna(subset=[column])


def _in_time_order(d):
    """Rows by `_t`, ties in file order; a newest-first export (a bank statement) is reversed
    first, so a day's last row is its last transaction."""
    if len(d) > 1 and d["_t"].is_monotonic_decreasing and not d["_t"].is_monotonic_increasing:
        d = d.iloc[::-1]
    return d.sort_values("_t", kind="stable")


def snapshot_rows(df, by: list[str], time: str, window: str = "last", current: bool = True,
                  per: str | None = None):
    """Each entity's latest snapshot (or first, for a 'first' window): the one rule for a level's
    current rows (a balance's tile, bars, answers). An entity whose latest snapshot is a full
    usual gaps older than the newest one has stopped (a closed account) and is left out (monthly
    data: not in the newest complete month); staggered snapshots (each account on its own day)
    all count. Undated rows are out (one can't be the latest). `per` (a period column): each
    entity's latest row in each period, the stopped cut judged against THAT period's newest date
    (an account closed on 10 Aug is out of August's month-end). `current=False`: no stopped cut
    (an entity's last row, wherever it is: a correlation keeps accounts that stopped). No `by`:
    the file is one series (a single account's statement)."""
    import pandas as pd
    t = pd.to_datetime(df[time], format="mixed", errors="coerce")
    d = _in_time_order(df.loc[t.notna()].assign(_t=t[t.notna()]))
    helpers = ["_t"]
    if not by:   # one series: a constant key, dropped again below
        d, by = d.assign(__series=0), ["__series"]
        helpers.append("__series")
    g = d.groupby(by, sort=False)
    if window == "first":
        return g.head(1).drop(columns=helpers)
    gaps = g["_t"].diff().dt.total_seconds().dropna()
    each = gaps.groupby([d.loc[gaps.index, e] for e in by])   # each entity's gaps
    cadence = each.median().median() if len(gaps) else 0.0
    # snapshots come at a cadence (weekly, each account's statement day); a ledger's running
    # balance does not: a quiet account there is no closed one, so nothing is cut
    regular = len(gaps) and (each.std().fillna(0) / each.mean()).median() <= REGULAR_CV
    last = g.tail(1)
    newest = d["_t"].max()
    if per is not None:   # each period's own month-end
        last = d.groupby([*by, per], sort=False).tail(1)
        newest = d.groupby(per)["_t"].transform("max").loc[last.index]
        if current and cadence and cadence == cadence and regular and cadence < 27 * 86400:
            last = last[last["_t"] > newest - pd.Timedelta(seconds=2 * cadence)]
        return last.drop(columns=helpers)   # monthly or coarser: the month's own row is its end
    if current and cadence and cadence == cadence and regular:
        if cadence >= 27 * 86400:   # monthly or coarser: current = a snapshot in the newest month,
            # or the one before while the newest is still partial (a 4 Nov export of accounts
            # billed on their own day: most have only October so far)
            months = 2 if partial_last_month(d["_t"]) else 1
            last = last[last["_t"].dt.to_period("M") > newest.to_period("M") - months]
        else:   # daily / weekly, staggered or not: stopped = missed two usual gaps (a day's
            # refresh lag, or weekday-only rows on a Saturday export, is still current)
            last = last[last["_t"] > newest - pd.Timedelta(seconds=2 * cadence)]
    return last.drop(columns=helpers)


def _local(t):
    """Datetimes compared as wall-clock time: an aware column (one UTC offset in the file) drops
    its zone, as the loader's mixed-offset rule does (connectors.base)."""
    return t.dt.tz_localize(None) if getattr(t.dt, "tz", None) is not None else t


def _bound(text: str, end: bool):
    """'2025', '2025-03' or '2025-03-31' as the first (or, for an end, the last) instant."""
    import pandas as pd

    text = str(text).strip()
    ts = pd.Timestamp(text)
    if end and len(text) == 4:
        return ts + pd.offsets.YearEnd(0) + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)
    if end and len(text) == 7:
        return ts + pd.offsets.MonthEnd(0) + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)
    if end and ts == ts.normalize():
        return ts + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)
    return ts


def period_window(df, date: str | None, start: str | None, end: str | None,
                  span: str | None = None):
    """(lo, hi, data start, data end, error): the period asked, as bounds, and the data's span
    (`span`: the column the data's coverage is read from, when `date` is an event's own date)."""
    import pandas as pd

    if not date or date not in df.columns:
        return None, None, None, None, "the data has no date column to limit to that period"
    t = _local(pd.to_datetime(df[date], errors="coerce"))
    try:
        if start and end and _bound(start, False) > _bound(end, True):
            start, end = end, start   # "2025 to 2024": swapped whole, widths kept
        lo = _bound(start, False) if start else t.min()
        hi = _bound(end, True) if end else t.max()
    except (ValueError, TypeError):
        return None, None, None, None, f"I couldn't read the period ({start} to {end}) as dates"
    s = _local(pd.to_datetime(df[span], errors="coerce")) if span and span in df.columns else t
    return lo, hi, s.min(), s.max(), None


def filter_period(df, date: str | None, start: str | None, end: str | None,
                  span: str | None = None):
    """(rows in [start, end], a note, or an error) for a period the user named. Inclusive ISO
    dates (a year or a month means all of it); reversed bounds are swapped; a period the data
    doesn't reach, or doesn't fully cover, is said, never an empty or silent answer. No period:
    the frame unchanged."""
    import pandas as pd

    if not (start or end):
        return df, "", None
    lo, hi, first, last, why = period_window(df, date, start, end, span)
    if why:
        return df.iloc[:0], "", why
    t = _local(pd.to_datetime(df[date], errors="coerce"))
    rows = df[(t >= lo) & (t <= hi)]
    if rows.empty:
        return rows, "", (f"the data covers {first:%d %b %Y} to {last:%d %b %Y}, not "
                          f"{lo:%d %b %Y} to {hi:%d %b %Y}")
    note = f"{lo:%d %b %Y} to {hi:%d %b %Y}"
    gaps = ([f"the data starts {first:%d %b %Y}"] if lo < first.normalize() else []) + \
           ([f"the data ends {last:%d %b %Y}"] if hi > _bound(str(last.date()), True) else [])
    if gaps:
        note += f" ({' and '.join(gaps)}, so the period is incomplete)"
    return rows, note, None


def _year_back(weekly: bool):
    import pandas as pd
    # weekly data: 52 weeks back, so both windows hold the same number of week-endings
    return pd.DateOffset(weeks=52) if weekly else pd.DateOffset(years=1)


def year_earlier(df, date: str, start: str | None, end: str | None, weekly: bool = False):
    """(rows, note, error) for the same window one year earlier (YoY). Both windows are first cut
    to the days the data covers; a year-earlier window the data doesn't fully cover is an error
    to say (a 15-day March against a 31-day March is no year-over-year change). A data start up
    to a week inside the earlier window (2 Jan, the first trading day) still counts."""
    import pandas as pd

    lo, hi, first, last, why = period_window(df, date, start, end)
    if why:
        return df.iloc[:0], "", why
    lo, hi = max(lo, first.normalize()), min(hi, last)
    p_lo, p_hi = lo - _year_back(weekly), hi - _year_back(weekly)
    if not weekly and lo.day == 1 and hi.normalize() == (hi + pd.offsets.MonthEnd(0)).normalize():
        # whole months: the same months a year earlier (Feb 2025 against all of Feb 2024, the 29th too)
        p_lo = (lo.to_period("M") - 12).start_time
        p_hi = (hi.to_period("M") - 12).end_time
    calendar = (p_hi.normalize() - p_lo).days != (hi.normalize() - lo).days   # a leap February
    if first.normalize() - pd.Timedelta(days=7) <= p_lo < first.normalize():
        p_lo = first.normalize()   # (the current window is cut to match in current_window)
    if p_lo < first.normalize():
        return df.iloc[:0], "", (f"the data starts {first:%d %b %Y}, so it has no full "
                                 f"{p_lo:%d %b %Y} to {p_hi:%d %b %Y}")
    t = _local(pd.to_datetime(df[date], errors="coerce"))
    days = (f", {(p_hi.normalize() - p_lo).days + 1} days against {(hi.normalize() - lo).days + 1}"
            if calendar else "")   # callers then say "same months", not "same days"
    return df[(t >= p_lo) & (t <= p_hi)], f"{p_lo:%d %b %Y} to {p_hi:%d %b %Y}{days}", None


def current_window(df, date: str, start: str | None, end: str | None, weekly: bool = False):
    """(rows, label, cut): the asked window cut to the data's days, and to the year-earlier
    twin's (YoY compares the same days); `cut` when the twin's start made it shorter than the
    period shown, so the answer states the same-days value it compared."""
    import pandas as pd

    lo, hi, first, last, why = period_window(df, date, start, end)
    if why:
        return df.iloc[:0], "", False
    lo, hi = max(lo, first.normalize()), min(hi, last)
    twin = max(lo, first.normalize() + _year_back(weekly))   # the twin starts no earlier than the data
    t = _local(pd.to_datetime(df[date], errors="coerce"))
    return df[(t >= twin) & (t <= hi)], f"{twin:%d %b %Y} to {hi:%d %b %Y}", twin > lo


def cut_partial(series, t, freq: str, rule: str | None = None):
    """Leave out a first or last bucket the dates don't cover from its start to its end, by the
    data's own spacing: daily data by the weekdays it trades (a Mon-Fri week ends on Friday; a
    month may open on the 2nd or 3rd), weekly or monthly rows by whether the next expected date
    falls outside the bucket (weekly data covers a month through its last week-ending; month-start
    data covers its quarter). A middle bucket
    is never partial (a closed week is a fact to show). A month's end: the shared rule
    (utils.partial_last_month, also in the sandbox and the charts). May leave nothing: the note
    then says no complete period is covered, and the caller answers without a comparison."""
    import pandas as pd

    if freq not in ("W", "M", "Q", "Y") or series.empty:
        return series, ""
    d = _local(pd.to_datetime(t, errors="coerce")).dropna().dt.normalize().drop_duplicates().sort_values()
    if d.empty:
        return series, ""
    period = rule or freq
    labels = series.index if isinstance(series.index, pd.PeriodIndex) else series.index.to_period(period)
    step = d.diff().median() if len(d) > 1 else pd.Timedelta(days=1)
    first, last = labels[0], labels[-1]
    start, end = first.start_time.normalize(), last.end_time.normalize()
    if step <= pd.Timedelta(days=1):
        # daily data: the trading days it would have had, on the weekdays it trades (a Mon-Fri
        # seller's week ends on Friday); a long bucket may miss a couple (a holiday opening)
        trades = set(d.dt.weekday) if len(d) >= 14 else set(range(7))

        def missing(lo, hi) -> int:
            return sum(x.weekday() in trades for x in pd.date_range(lo, hi))
        slack = {"W": 0}.get(freq, 2)
        cut_first = missing(start, d.min() - pd.Timedelta(days=1)) > slack
        cut_last = missing(d.max() + pd.Timedelta(days=1), end) > slack
    else:   # weekly or monthly rows: the next expected date falls outside the bucket
        if pd.Timedelta(days=28) <= step <= pd.Timedelta(days=31):
            step = pd.Timedelta(days=31)   # monthly rows: a month on, whatever the month's length
        cut_first = d.min() - step >= start
        cut_last = d.max() + step <= end
    if freq == "M" and len(series) >= 3:   # a month's end: the shared rule
        cut_last = bool(partial_last_month(t)) and d.max() < end
    unit, note = {"M": "month", **_UNIT}[freq], ""
    if cut_last:
        note += f" The last {unit} ({last}) is left out: it is covered only to {d.max():%d %b %Y}."
    if cut_first and (len(series) > 1 or not cut_last):
        note += f" The first {unit} ({first}) is left out: it is covered only from {d.min():%d %b %Y}."
    series = series.iloc[int(cut_first): len(series) - int(cut_last)]
    if series.empty:
        note = f" No complete {unit} is covered ({d.min():%d %b %Y} to {d.max():%d %b %Y})."
    return series, note


_UNIT = {"W": "week", "Q": "quarter", "Y": "year"}


def ranked(series, ascending: bool = False):
    """A ranking that is the same on every machine: by label first, then a stable sort by
    value, so tied groups keep their name order (numpy's default sort is not stable, and Linux
    and macOS order ties differently)."""
    return series.sort_index(kind="stable").sort_values(ascending=ascending, kind="stable")
