"""Regression review of the audit-pass-3 fixes (7b6fcb3..868db1e): rules widened or narrowed there,
checked against data from other domains."""
import numpy as np
import pandas as pd
import pytest

from agents.ingestion_agent import build_contract, phi_plan
from orchestrator.state import ConnectorType

rng = np.random.default_rng(0)
N = 200


def _contract(df):
    return build_contract(df, ConnectorType.CSV, {})


@pytest.mark.parametrize("cols", [
    {"is_trial": rng.choice([True, False], N), "trial_days": rng.integers(0, 30, N)},   # SaaS
    {"ab_arm": rng.choice(["A", "B"], N)},                                              # A/B test
    {"discharge_port": rng.choice(["Rotterdam", "Hamburg"], N)},                        # shipping
    {"election_ward": rng.choice(["W1", "W2", "W3"], N)},                               # civic
])
def test_a_shared_word_next_to_an_email_is_not_patient_data(cols):
    df = pd.DataFrame({"contact_email": [f"u{i}@x.com" for i in range(N)],
                       "company": rng.choice(["Acme Inc", "Globex", "Initech"], N),
                       "mrr": rng.integers(10, 900, N), **cols})
    assert not _contract(df).phi


@pytest.mark.parametrize("cols", [
    {"subject_id": [f"S-{i % 60:03}" for i in range(N)], "visit": rng.choice(["BL", "W4"], N),
     "sbp": rng.integers(100, 160, N), "dbp": rng.integers(60, 100, N), "age": rng.integers(20, 95, N)},
    {"pt_id": range(N), "los_days": rng.integers(1, 9, N), "drg": rng.choice(["470", "871"], N)},
])
def test_clinical_abbreviations_mark_patient_data(cols):
    assert _contract(pd.DataFrame(cols)).phi


def test_counts_with_id_like_headers_stay_numbers():
    df = pd.DataFrame({"mrn": [f"MRN{i}" for i in range(N)], "enrolment_count": rng.integers(1, 40, N),
                       "enrolled": rng.choice([True, False], N), "cycle_no": rng.integers(1, 7, N),
                       "dose_mg": rng.integers(5, 50, N)})
    assert phi_plan(df, _contract(df))[1] == ["mrn"]


@pytest.mark.parametrize("name, values", [("birth_order", [1, 2, 3, 1]), ("birth_length_cm", [50.2, 48.1]),
                                          ("birth_height_cm", [51.0, 49.5]), ("birth_parity", [0, 1, 2]),
                                          ("birth_gestation_weeks", [38, 40, 39])])
def test_measures_taken_at_birth_are_not_dates_of_birth(name, values):
    from guardrails import pii_kind
    assert pii_kind(name, values * 5) is None


@pytest.mark.parametrize("values", [["2021-03-04", "2020-11-30"], [pd.Timestamp("2020-01-01")]])
def test_a_date_of_death_is_personal(values):
    from guardrails import pii_kind
    assert pii_kind("deceased", values)[0] == "dob" and pii_kind("is_deceased", ["Y", "N"]) is None


def test_a_threshold_or_category_equal_to_an_id_is_not_rewritten():
    from agents.analysis_agent import pseudonymize_id_literals
    from guardrails import pseudonym
    key = b"k" * 32
    present = {"patient_id": {pseudonym(str(i), key, "patient_id") for i in range(100000, 100500)},
               "visit_code": {pseudonym(v, key, "visit_code") for v in ["Q1", "V2"]}}
    code = ("big = df[df.total_charges > 100000]\nq = df[df['quarter'] == 'Q1']\n"
            "ids = [100003]\nx = df[df.patient_id == 100004]")
    out = pseudonymize_id_literals(code, present, key).split("\n")
    assert out[:2] == code.split("\n")[:2]
    assert "100003" not in out[2] and "100004" not in out[3]


def test_lowercase_and_place_categories_are_kept_in_patient_mode():
    df = pd.DataFrame({"mrn": [f"MRN{i}" for i in range(N)],
                       "region": rng.choice(["north east", "south west", "mid west"], N),
                       "shift": rng.choice(["night shift", "day shift"], N),
                       "site": rng.choice(["Seattle, Washington", "Portland, Oregon"], N),
                       "los_days": rng.integers(1, 9, N)})
    assert phi_plan(df, _contract(df))[0] == []


def test_zero_padded_codes_keep_their_zeros():
    from connectors.csv_connector import _read_bytes
    df, _ = _read_bytes(b"unit_code,sales_rep_code,qty\n007,0099,050\n012,0100,010\n", "x.csv")
    assert df["unit_code"].tolist() == ["007", "012"] and df["qty"].tolist() == [50, 10]


@pytest.mark.parametrize("col, kind", [("sales_qty", "count"), ("usd_count", "count"),
                                       ("TotalAmount", "currency"), ("unitPrice", "currency"),
                                       ("unit_price", "currency"), ("sales", "currency")])
def test_kpi_money_rule(col, kind):
    from agents.visualization_agent import _kpi_format_kind
    assert _kpi_format_kind(col, pd.Series([3, 5, 8]), set()) == kind


def test_a_last_value_without_a_level_hint_is_scored_as_pandas_last():
    from evals.scoring import _grouped
    df = pd.DataFrame({"g": ["a", "a", "b"], "v": [1.0, 2.0, 5.0]})
    assert _grouped(df, "g", "v", "last").to_dict() == {"a": 2.0, "b": 5.0}
