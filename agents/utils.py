"""
Shared utilities for agent modules.
"""
from __future__ import annotations

import re

from guardrails import delimit_untrusted


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
    return delimit_untrusted("\n".join(lines)) if lines else ""
