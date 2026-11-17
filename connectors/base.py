"""
Abstract base class for all data connectors.
Every connector exposes a single .load() method that returns a DataFrame.
Adding a new source (Notion, Airtable, etc.) = add one subclass, zero agent changes.
"""

from __future__ import annotations

import datetime
import re
from abc import ABC, abstractmethod

import pandas as pd

from orchestrator.state import ConnectorConfig, ConnectorType, DataContract


# Date-shaped text: ISO (2025-01, 2025-01-31, 2025-01-31 10:00, … -0500), US (1/31/2025),
# European (31.01.2025), either with a time (31/01/2025 14:30), "Jan 31, 2025".
_DATE_TEXT_RE = re.compile(
    r"^(\d{4}-\d{1,2}(-\d{1,2})?([ T][\d:.]+( ?(Z|[+-]\d{2}:?\d{2}))?)?|(\d{1,2}/\d{1,2}/\d{2,4}|\d{1,2}\.\d{1,2}\.\d{4})( \d{1,2}:\d{2}(:\d{2})?)?"
    r"|[A-Za-z]{3,9} \d{1,2}, \d{4})$")
_FIRST_PART_RE = re.compile(r"^(\d{1,2})[/.]\d{1,2}[/.]\d{2,4}")
_OFFSET_RE = re.compile(r" ?(Z|[+-]\d{2}:?\d{2})$")


def _minutes(offset: str) -> int:
    """'-0500' / '+01:00' / 'Z' as minutes east of UTC."""
    if offset in ("Z", "z"):
        return 0
    digits = offset[1:].replace(":", "")
    return (1 if offset[0] == "+" else -1) * (int(digits[:2]) * 60 + int(digits[2:]))


def _looks_like_dates(s: pd.Series, share: float = 1.0) -> bool:
    """A text/object column whose values are dates, whatever its name: a SQL `DATE` column
    arrives as Python date objects, a query's `month` as '2025-01-01'. Checks that `share` of the
    first 50 non-null values are date-shaped (the gate); parse_text_dates checks the column."""
    if pd.api.types.is_numeric_dtype(s) or pd.api.types.is_bool_dtype(s):
        return False
    vals = s.dropna().head(50).tolist()
    return bool(vals) and sum(
        isinstance(v, (datetime.date, pd.Timestamp))
        or (isinstance(v, str) and bool(_DATE_TEXT_RE.match(v.strip()))) for v in vals
    ) >= share * len(vals)


def parse_text_dates(s: pd.Series, share: float = 1.0) -> pd.Series | None:
    """The column as datetimes if it is text whose values are dates (`share` of them parse),
    else None. Decided by the values, never the name: `lifetime_value` stays a number. The load
    converts only at share 1.0 (lossless: a junk value would vanish unlogged); a column with some
    junk is still a date column (share 0.8) and Cleaning parses it with an audit entry.
    Day-first when the dates use dots (31.01.2025) or a first part above 12 appears."""
    if pd.api.types.is_datetime64_any_dtype(s) or not _looks_like_dates(s, share):
        return None
    text = s.dropna().astype(str).str.strip()
    first = text.str.extract(_FIRST_PART_RE)[0].dropna().astype(int)
    dayfirst = bool(text.str.contains(r"^\d{1,2}\.\d", regex=True).any() or (first > 12).any())
    try:
        parsed = pd.to_datetime(s.astype("string"), format="mixed",
                                dayfirst=dayfirst, errors="coerce")
    except ValueError:   # mixed UTC offsets
        text = s.astype("string").str.strip()
        offsets = {_minutes(m) for m in text.str.extract(_OFFSET_RE)[0].dropna().unique()}
        if len(offsets) == 2 and max(offsets) - min(offsets) == 60 and (max(offsets) <= 0 or min(offsets) >= 0):
            # one place across a daylight-saving change (Shopify's -0500 / -0400): local time
            parsed = pd.to_datetime(text.str.replace(_OFFSET_RE, "", regex=True), format="mixed",
                                    dayfirst=dayfirst, errors="coerce")
        else:   # several places (pickup -05:00, delivery -08:00): UTC keeps instants and durations
            parsed = pd.to_datetime(text, format="mixed", dayfirst=dayfirst, errors="coerce",
                                    utc=True)
    return parsed if parsed.notna().sum() >= share * s.notna().sum() else None


# Unix-epoch seconds or milliseconds, 1998-07 .. 2039-09 (a historian's timeInSeconds, a JS
# timestamp): THE epoch rule of ingestion's typing, cleaning and every reader
_EPOCHS = (("s", 9e8, 2.2e9), ("ms", 9e11, 2.2e12))
# a date needs a year: 'M01', 'Jan' or '12:30' parse with an invented one
HAS_YEAR_RE = re.compile(r"\d{4}|\b\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2}\b")


def epoch_times(s: pd.Series, share: float = 0.8) -> pd.Series | None:
    """Numbers as datetimes when `share` of them are epoch seconds or milliseconds (one unit for
    the column; a value outside its range is NaT), else None."""
    n = pd.to_numeric(s, errors="coerce")
    if not n.notna().any():
        return None
    for unit, lo, hi in _EPOCHS:
        inside = n.between(lo, hi, inclusive="left")
        if inside[n.notna()].mean() >= share:
            return pd.to_datetime(n.where(inside), unit=unit)
    return None


def as_times(s: pd.Series) -> pd.Series:
    """A column as datetimes by the load's one rule (offsets included); values that aren't
    dates are NaT. Already-parsed columns come back as they are."""
    if pd.api.types.is_datetime64_any_dtype(s):
        return s
    if (epoch := epoch_times(s)) is not None:
        return epoch
    parsed = parse_text_dates(s, 0.0)   # all blank: None (no value looks like a date)
    if parsed is None:
        return pd.Series(pd.NaT, index=s.index, dtype="datetime64[ns]")
    text = s.astype("string").str.strip()
    dated = s.map(lambda v: isinstance(v, (datetime.date, pd.Timestamp))).astype(bool)
    return parsed.where(dated | text.str.contains(HAS_YEAR_RE, regex=True).fillna(False))


# A capped load names its cap: the source evidence carries this note and the report's alerts find
# it by this start (agents/insight_agent._alerts). One wording for every connector.
TRUNCATED = "Only the first "


def truncation_note(kept: int, total: int | None, limit: str) -> str:
    """'Only the first 50,000 of 61,234 rows were loaded (…)'; `total` None: the source has
    more, how many is unknown."""
    of = f" of {total:,}" if total else ""
    more = "" if total else "; the source has more"
    return f"{TRUNCATED}{kept:,}{of} rows were loaded ({limit}{more})."


class DataConnector(ABC):
    read_info: dict = {}   # how the last load read the source (file connector); see source evidence

    def __init__(self, config: ConnectorConfig) -> None:
        self.config = config

    @abstractmethod
    async def load(self) -> pd.DataFrame:
        """Load data from the source and return a clean DataFrame."""

    @abstractmethod
    async def test_connection(self) -> tuple[bool, str]:
        """
        Test that the source is reachable and credentials are valid.
        Returns (success, message).
        """

    def build_data_contract(self, df: pd.DataFrame) -> DataContract:
        """Profile a DataFrame into a DataContract (roles, PII, masked samples, summary,
        fingerprint, source evidence). The ingestion agent owns the rules; every connector and
        every eval harness gets the same contract from the same frame."""
        from agents.ingestion_agent import build_contract
        return build_contract(df, self.config.type, self.read_info)


def get_connector(config: ConnectorConfig) -> DataConnector:
    """Factory function — returns the right connector for the given config type."""
    from connectors.csv_connector import CSVConnector
    from connectors.sql_connector import SQLConnector
    from connectors.sheets_connector import SheetsConnector
    from connectors.api_connector import APIConnector

    mapping = {
        ConnectorType.CSV: CSVConnector,
        ConnectorType.SQL: SQLConnector,
        ConnectorType.SHEETS: SheetsConnector,
        ConnectorType.API: APIConnector,
    }
    cls = mapping.get(config.type)
    if not cls:
        raise ValueError(f"Unsupported connector type: {config.type}")
    return cls(config)
