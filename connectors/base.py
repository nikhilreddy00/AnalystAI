"""
Abstract base class for all data connectors.
Every connector exposes a single .load() method that returns a DataFrame.
Adding a new source (Notion, Airtable, etc.) = add one subclass, zero agent changes.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod

import pandas as pd

from guardrails import MASK, is_pii_column, mask_pii, mask_row
from orchestrator.state import ConnectorConfig, DataContract, ColumnInfo, ConnectorType


class DataConnector(ABC):
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
        """
        Introspect a DataFrame and produce a DataContract.
        Called by every connector after .load() returns.
        """
        columns: list[ColumnInfo] = []
        date_cols, id_cols, metric_cols = [], [], []

        for col in df.columns:
            dtype_str = str(df[col].dtype)
            null_pct = round(df[col].isna().mean() * 100, 2)
            # PII never leaves here unmasked: every LLM prompt reads samples from the contract.
            sample = df[col].dropna().head(5).tolist()
            sample = [MASK] * len(sample) if is_pii_column(col) else [mask_pii(v) for v in sample]

            # BUG-34: Use endswith('_at') for 'at' to avoid false positives like 'category'
            is_date = "datetime" in dtype_str or any(
                kw in col.lower() for kw in ("date", "time", "created", "updated")
            ) or col.lower().endswith("_at") or col.lower() == "at"
            # BUG-33: Use word-boundary pattern to avoid matching 'video', 'Florida', etc.
            is_id = bool(re.search(r'(?:^|_)(?:id|uuid|key|code)(?:$|_)', col.lower()))
            is_metric = dtype_str in ("int64", "float64") and not is_id

            if is_date:
                date_cols.append(col)
            if is_id:
                id_cols.append(col)
            if is_metric:
                metric_cols.append(col)

            columns.append(ColumnInfo(
                name=col,
                dtype=dtype_str,
                nullable=null_pct > 0,
                sample_values=sample,
                is_date=is_date,
                is_id=is_id,
                is_metric=is_metric,
                null_pct=null_pct,
            ))

        schema_summary = (
            f"{len(df)} rows × {len(df.columns)} columns. "
            f"Metrics: {metric_cols or 'none detected'}. "
            f"Dates: {date_cols or 'none detected'}. "
            f"IDs: {id_cols or 'none detected'}."
        )

        return DataContract(
            source_type=self.config.type,
            row_count=len(df),
            column_count=len(df.columns),
            columns=columns,
            sample_rows=[mask_row(r) for r in df.head(5).fillna("").to_dict(orient="records")],
            date_columns=date_cols,
            id_columns=id_cols,
            metric_columns=metric_cols,
            schema_summary=schema_summary,
        )


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
