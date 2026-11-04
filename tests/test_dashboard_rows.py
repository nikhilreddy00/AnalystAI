"""The dashboard's filter options must be valid JSON: missing and infinite values never reach a
range or a date span (the browser gets options, never rows)."""
import json

import numpy as np
import pandas as pd
from fastapi.encoders import jsonable_encoder

from agents.visualization_agent import filter_options


def test_missing_and_infinite_values_never_reach_the_filter_options():
    df = pd.DataFrame({"price": [1.5, np.nan, np.inf] + [2.0 + i / 7 for i in range(27)],
                       "sku": ["a", None, "c", "a", "c", "a"] * 5,
                       "when": pd.to_datetime(["2026-01-01", None, "2026-01-03", "2026-01-04",
                                               "2026-01-05", "2026-01-06"] * 5)})
    out = filter_options(df, None)
    json.dumps(jsonable_encoder(out["options"]), allow_nan=False)   # what the route's response does
    price = next((o for o in out["options"] if o["column"] == "price"), None)
    assert price["control"] == "range" and price["min"] == 1.5 and np.isfinite(price["max"])
