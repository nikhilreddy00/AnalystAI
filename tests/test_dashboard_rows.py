"""The dashboard's raw rows must be valid JSON: missing and infinite floats become null."""
import json

import numpy as np
import pandas as pd
from fastapi.encoders import jsonable_encoder

from api.routes.dashboard import json_rows


def test_missing_and_infinite_floats_are_null_in_the_dashboard_rows():
    df = pd.DataFrame({"price": [1.5, np.nan, np.inf], "sku": ["a", None, "c"],
                       "when": pd.to_datetime(["2026-01-01", None, "2026-01-03"])})
    rows = json_rows(df)
    json.dumps(jsonable_encoder(rows), allow_nan=False)  # what the route's response does
    assert rows[1] == {"price": None, "sku": None, "when": None}
    assert rows[2]["price"] is None and rows[0]["price"] == 1.5
