"""
Shared utilities for agent modules.
"""
from __future__ import annotations

import re


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
