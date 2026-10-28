"""Value-based date detection in build_data_contract (carried from Phase 5 Intake: a query's
`month` column was marked categorical because detection was name-only)."""
import datetime

import pandas as pd
import pytest

from connectors.base import _looks_like_dates
from connectors.csv_connector import CSVConnector
from evals import datagen
from orchestrator.state import ConnectorConfig, ConnectorType


@pytest.mark.parametrize("values, expected", [
    (["2025-01-01", "2025-02-01"], True),
    (["2025-01", "2025-02"], True),
    (["2025-01-31 10:00:00", "2025-02-01T08:30"], True),
    (["1/31/2025", "12/01/24"], True),
    (["Jan 31, 2025", "February 1, 2025"], True),
    ([datetime.date(2025, 1, 1), datetime.date(2025, 2, 1)], True),     # SQL DATE via psycopg
    (["North", "South"], False),
    (["A", "B"], False),
    (["2025", "2026"], False),                                          # a bare year is ambiguous
    (["2025-01-01", "n/a"], False),                                     # every value must be a date
    ([20250101, 20250201], False),
    ([], False),
])
def test_looks_like_dates(values, expected):
    assert _looks_like_dates(pd.Series(values, dtype=object)) is expected


def test_sql_month_column_is_a_date_column():
    df = pd.DataFrame({"month": [datetime.date(2025, m, 1) for m in (1, 2, 3)], "orders": [1, 2, 3]})
    assert CSVConnector(ConnectorConfig(type=ConnectorType.CSV)).build_data_contract(df).date_columns == ["month"]


@pytest.mark.parametrize("name, dates", [("sales", ["order_date"]), ("simpson", []),
                                         ("mixed_dates", ["order_date"])])
def test_eval_datasets_keep_their_date_columns(name, dates):
    """The E2E/analysis datasets' roles are unchanged, so those suites need no re-run."""
    df, _ = datagen.make(name, 1)
    df = pd.read_csv(__import__("io").StringIO(df.to_csv(index=False)))   # as the pipeline reads it
    assert CSVConnector(ConnectorConfig(type=ConnectorType.CSV)).build_data_contract(df).date_columns == dates
