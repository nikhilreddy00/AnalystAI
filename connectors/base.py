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
        if len(offsets) == 2 and abs(max(offsets) - min(offsets)) == 60:
            # a daylight-saving change: each value's local time, the clock the shop reports by
            # (Shopify's `Created at`: -0500 in winter, -0400 in summer)
            parsed = pd.to_datetime(text.str.replace(_OFFSET_RE, "", regex=True), format="mixed",
                                    dayfirst=dayfirst, errors="coerce")
        else:   # different places (pickup -05:00, delivery +01:00): one clock, UTC, keeps order
            parsed = pd.to_datetime(text, format="mixed", dayfirst=dayfirst, errors="coerce",
                                    utc=True).dt.tz_localize(None)
    return parsed if parsed.notna().sum() >= share * s.notna().sum() else None


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
