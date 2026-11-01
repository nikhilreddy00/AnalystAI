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


def partial_last_month(dates) -> bool:
    """True when the data stops before the end of its last calendar month and that month has far
    fewer distinct days than the others (a mid-month export). A month-over-month change must not
    compare such a month with a full one. Monthly-grain data (one date per month) is never partial.
    Mirrored inside the analysis agent's sandbox code (`_partial` in _build_standard_code)."""
    import pandas as pd

    d = pd.to_datetime(dates, format="mixed", errors="coerce").dropna()
    if d.empty:
        return False
    days = d.dt.normalize().groupby(d.dt.to_period("M")).nunique().sort_index()
    if len(days) < 3:
        return False
    last = d.max().normalize()
    return bool(last < last + pd.offsets.MonthEnd(0) and days.iloc[-1] < 0.9 * days.iloc[:-1].median())


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
    usual gap older than the newest one has stopped (a closed account) and is left out (monthly
    data: not in the newest month); staggered snapshots (each account on its own day) all count. Anything else: the
    frame as it is."""
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
        if cadence >= 27 * 86400:   # monthly or coarser: current = a snapshot in the newest month
            last = last[last["_t"].dt.to_period("M") == newest.to_period("M")]
        else:   # daily / weekly, staggered or not: within one usual gap of the newest snapshot
            last = last[last["_t"] > newest - pd.Timedelta(seconds=cadence)]
    return last.drop(columns="_t")
