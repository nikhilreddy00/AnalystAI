"""TEMPORARY (Domain 2): card_ledger differs on CI's Linux; print per-column hashes to compare."""
import hashlib
import io


def _h(x) -> str:
    return hashlib.sha256(str(x).encode()).hexdigest()[:10]


def test_print_card_ledger_fingerprint():
    import pandas as pd
    import numpy as np
    from connectors.csv_connector import CSVConnector, _read_bytes
    from evals import datagen
    from orchestrator.state import ConnectorConfig, ConnectorType
    raw, truth = datagen.make("card_ledger", 1)
    out = [f"versions numpy {np.__version__} pandas {pd.__version__}", f"shape {raw.shape}"]
    out += [f"raw {c}: {_h(raw[c].tolist())}" for c in raw.columns]
    df = _read_bytes(raw.to_csv(index=False).encode(), ".csv")[0]
    out += [f"read {c}: {df[c].dtype} {_h(df[c].tolist())}" for c in df.columns]
    c = CSVConnector(ConnectorConfig(type=ConnectorType.CSV)).build_data_contract(df)
    d = c.model_dump(mode="json")
    out += [f"contract {k}: {_h(v)}" for k, v in d.items() if k != "columns"]
    out += [f"col {x['name']}: {_h(x)}" for x in d["columns"]]
    out.append(f"summary: {c.schema_summary}")
    out.append(f"truth fee {truth['fee_income_total']} spend {truth['spend_q_total']}")
    print("\n".join(out))
    assert False, "\n".join(out)
