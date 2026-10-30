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
    return {c.name for c in contract.columns if c.pii or is_pii_column(c.name)}
