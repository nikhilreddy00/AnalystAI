import copy
from pathlib import Path

import pytest

import semantic_layer
from semantic_layer import SemanticLayer

LAYERS = Path(__file__).parent.parent / "evals" / "sql"

BASE = {
    "tables": [
        {"name": "orders", "source": "s.orders", "primary_key": "order_id", "columns": [
            {"name": "order_id", "type": "id"}, {"name": "amount", "type": "fact"},
            {"name": "status", "type": "dimension"}, {"name": "day", "type": "time"}]},
    ],
    "metrics": [{"name": "revenue", "table": "orders", "expr": "SUM(orders.amount)",
                 "synonyms": ["sales"]}],
    "verified_queries": [{"question": "q", "sql": "SELECT 1"}],
}


def _with(**patch) -> dict:
    d = copy.deepcopy(BASE)
    for path, value in patch.items():
        target = d
        *keys, last = path.split("__")
        for k in keys:
            target = target[int(k)] if k.isdigit() else target[k]
        target[int(last) if last.isdigit() else last] = value
    return d


@pytest.mark.parametrize("name", ["retail", "banking"])
def test_shipped_layers_load(name):
    layer = semantic_layer.load(LAYERS / f"{name}.yaml")
    assert layer.metrics and layer.relationships


def test_minimal_layer_loads_and_normalizes_primary_key():
    layer = SemanticLayer.model_validate(BASE)
    assert layer.table("orders").primary_key == ["order_id"]
    assert layer.metric("revenue").terms() == ["revenue", "sales"]


@pytest.mark.parametrize("patch, error", [
    ({"metrics__0__expr": "SUM(orders.nope)"}, "unknown column orders.nope"),
    ({"metrics__0__expr": "SUM(amount)"}, "must be qualified"),
    ({"metrics__0__expr": "SUM(orders.amount"}, "does not parse"),
    ({"metrics__0__joins": ["customers"]}, "unknown table customers"),
    ({"metrics__0__synonyms": ["revenue"]}, "duplicate metric name or synonym: revenue"),
    ({"tables__0__primary_key": "id"}, "primary key 'id' is not a column"),
    ({"verified_queries__0__sql": "DELETE FROM s.orders"}, "verified query"),
    ({"metrics__0__non_additive_dimension": {"name": "status", "window": "last"}},
     "not a time column"),
    ({"relationships": [{"from": "orders.x", "to": "orders.order_id",
                         "cardinality": "many_to_one"}]}, "relationship orders.x"),
    ({"tables__0__columns__0__type": "measure"}, "type"),
    ({"tables__0__colour": "red"}, "colour"),
])
def test_invalid_layer_is_rejected(patch, error):
    with pytest.raises(ValueError, match=error):
        SemanticLayer.model_validate(_with(**patch))
