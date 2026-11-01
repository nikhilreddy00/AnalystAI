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
    Mirrored inside the analysis agent's sandbox code (`_cut` in _build_standard_code)."""
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
    sandbox (`_weekly` in _build_standard_code)."""
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
    if not hint.aggregation_by or missing:
        raise LevelUnavailable(f"{hint.name} is a level (a balance-like value), and the column "
                               f"{', '.join(missing or ['entity'])} it needs is no longer in the "
                               "data, so it can't be totalled")
    t = pd.to_datetime(df[hint.aggregation_time], format="mixed", errors="coerce")
    return df.loc[t.notna()].assign(_t=t[t.notna()]).sort_values("_t", kind="stable")


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
    if agg not in _LEVEL or hint is None:
        return df.groupby(keys)[column].agg("sum" if agg in _LEVEL else agg)
    names = [k for k in (keys if isinstance(keys, list) else [keys]) if isinstance(k, str)]
    if not over_time and hint.aggregation_time not in names:
        return latest_rows(df, column, contract).groupby(keys)[column].sum()
    keys = keys if isinstance(keys, list) else [keys]
    d = _snapshots(df.dropna(subset=[column]), hint)
    per = d.groupby([*(k.loc[d.index] if isinstance(k, pd.Series) else k for k in keys),
                     *(d[e] for e in hint.aggregation_by)])[column].agg(agg)
    return per.groupby(level=list(range(len(keys)))).sum()


def latest_rows(df, column: str, contract):
    """For a level by group (bars, answers): each entity's latest snapshot (or first, for a
    'first' window), the rows a current total adds up. An entity whose latest snapshot is a full
    usual gaps older than the newest one has stopped (a closed account) and is left out (monthly
    data: not in the newest complete month); staggered snapshots (each account on its own day)
    all count. Anything else: the frame as it is."""
    import pandas as pd
    hint = column_hint(contract, column)
    if hint is None or hint.aggregation not in _LEVEL:
        return df
    d = _snapshots(df.dropna(subset=[column]), hint)
    g = d.groupby(hint.aggregation_by, sort=False)
    if hint.aggregation == "first":
        return g.head(1).drop(columns="_t")
    gaps = g["_t"].diff().dt.total_seconds().dropna()
    cadence = gaps.groupby([d.loc[gaps.index, e] for e in hint.aggregation_by]).median().median() \
        if len(gaps) else 0.0
    last = g.tail(1)
    newest = d["_t"].max()
    if cadence and cadence == cadence:
        if cadence >= 27 * 86400:   # monthly or coarser: current = a snapshot in the newest month,
            # or the one before while the newest is still partial (a 4 Nov export of accounts
            # billed on their own day: most have only October so far)
            months = 2 if partial_last_month(d["_t"]) else 1
            last = last[last["_t"].dt.to_period("M") > newest.to_period("M") - months]
        else:   # daily / weekly, staggered or not: stopped = missed two usual gaps (a day's
            # refresh lag, or weekday-only rows on a Saturday export, is still current)
            last = last[last["_t"] > newest - pd.Timedelta(seconds=2 * cadence)]
    return last.drop(columns="_t")


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


def period_window(df, date: str | None, start: str | None, end: str | None):
    """(lo, hi, data start, data end, error): the period asked, as bounds, and the data's span."""
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
    return lo, hi, t.min(), t.max(), None


def filter_period(df, date: str | None, start: str | None, end: str | None):
    """(rows in [start, end], a note, or an error) for a period the user named. Inclusive ISO
    dates (a year or a month means all of it); reversed bounds are swapped; a period the data
    doesn't reach, or doesn't fully cover, is said, never an empty or silent answer. No period:
    the frame unchanged."""
    import pandas as pd

    if not (start or end):
        return df, "", None
    lo, hi, first, last, why = period_window(df, date, start, end)
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
    if first.normalize() - pd.Timedelta(days=7) <= p_lo < first.normalize():
        p_lo = first.normalize()   # (the current window is cut to match in current_window)
    if p_lo < first.normalize():
        return df.iloc[:0], "", (f"the data starts {first:%d %b %Y}, so it has no full "
                                 f"{p_lo:%d %b %Y} to {p_hi:%d %b %Y}")
    t = _local(pd.to_datetime(df[date], errors="coerce"))
    return df[(t >= p_lo) & (t <= p_hi)], f"{p_lo:%d %b %Y} to {p_hi:%d %b %Y}", None


def current_window(df, date: str, start: str | None, end: str | None, weekly: bool = False):
    """The asked window cut to the data's days, and to the year-earlier twin's (YoY compares
    the same days)."""
    import pandas as pd

    lo, hi, first, last, why = period_window(df, date, start, end)
    if why:
        return df.iloc[:0]
    lo, hi = max(lo, first.normalize()), min(hi, last)
    lo = max(lo, first.normalize() + _year_back(weekly))   # the twin starts no earlier than the data
    t = _local(pd.to_datetime(df[date], errors="coerce"))
    return df[(t >= lo) & (t <= hi)]
