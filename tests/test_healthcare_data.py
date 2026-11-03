"""Phase 5.7 Domain 3 WP1: the healthcare generators are deterministic, small, keep codes as text
and plant traps that move the answer (a trap the naive reading survives measures nothing)."""
from __future__ import annotations

import io
import json

import pandas as pd
import pytest

from evals import datagen

NAMES = ("inpatient", "health_claims", "member_months")
MONEY = {"inpatient": ["Total Charges"], "member_months": [],
         "health_claims": ["Billed Amount", "Allowed Amount", "Paid Amount", "Claim Total Billed"]}


@pytest.fixture(scope="module")
def made():
    return {n: datagen.make(n, 1) for n in NAMES}


@pytest.mark.parametrize("name", NAMES)
def test_deterministic_small_and_pii_known(name, made):
    df, truth = made[name]
    df2, truth2 = datagen.make(name, 1)
    assert df.equals(df2)
    assert json.dumps(truth, sort_keys=True) == json.dumps(truth2, sort_keys=True)
    assert len(df) == truth["row_count"] <= 7000
    assert set(truth["pii_columns"]) <= set(df.columns)
    for c in MONEY[name]:
        assert df[c].round(2).equals(df[c]), c


def test_inpatient_traps(made):
    df, t = made["inpatient"]
    assert "LOS" not in df.columns and "Length of Stay" not in df.columns
    assert abs(t["naive_alos_all_rows"] - t["alos_inpatient"]) > 0.3
    assert abs(t["naive_readmit_rate"] - t["readmit_rate_crude"]) > 0.02
    assert t["alos_inpatient"] > t["median_los_inpatient"]
    s = t["simpson"]
    assert t["readmit_by_dept"][s["higher_crude"]] > t["readmit_by_dept"][s["lower_crude"]]
    assert all(v[s["higher_crude"]] < v[s["lower_crude"]] for v in s["by_drg"].values())
    still = df["Discharge Disposition"].eq("30")
    assert still.sum() == t["still_patient_rows"] > 0 and df.loc[still, "Discharge Date"].isna().all()
    assert t["patients_90_plus"] > 0 and t["encounters_by_dept"][t["small_department"]] < 11
    assert t["same_day_inpatient_stays"] > 0 and 0 < t["mortality_rate_crude"] < 0.1
    assert df["MS-DRG"].dropna().isin(t["drg_values"]).all()
    assert df.loc[df["Encounter Type"].eq("Observation"), "MS-DRG"].isna().all()


def test_claims_traps(made):
    df, t = made["health_claims"]
    assert abs(t["naive_paid_total"] / t["paid_total_final"] - 1) > 0.03
    assert t["naive_header_billed_sum"] > 1.5 * t["billed_total_final"] > 3 * t["allowed_total_final"]
    assert abs(t["naive_denial_rate"] - t["denial_rate_claims"]) > 0.02
    assert t["negative_paid_rows"] > 0 and t["voided_claims"] > 0 and t["replaced_claims"] > 0
    months = list(t["paid_by_service_month"])
    prior6 = sum(t["paid_by_service_month"][m] for m in months[-9:-3]) / 6
    inc6 = sum(t["claims_incurred_by_service_month"][m] for m in months[-9:-3]) / 6
    assert t["incomplete_months"] == months[-3:]
    for m in t["incomplete_months"]:
        assert t["paid_by_service_month"][m] < 0.7 * prior6
        assert t["claims_incurred_by_service_month"][m] > 0.8 * inc6
    # a voided chain's final row is the void; it counts zero
    final = df.loc[df.groupby(["Claim ID", "Line No"])["Adjustment Seq"].idxmax()]
    assert final["Claim Frequency"].eq("8").any()
    assert not {"Patient Name", "DOB", "Principal Dx"} & set(df.columns)
    assert df["Member ID"].str.fullmatch(r"M\d{9}").all()


def test_member_months_from_enrollment_not_claims(made):
    mm, t = made["member_months"]
    _, ct = made["health_claims"]
    assert t["naive_member_months_from_claims"] < 0.6 * t["member_months_total"]
    assert t["pmpm_paid"] == round(ct["paid_total_final"] / t["member_months_total"], 6)
    assert t["members_without_claims"] > 0
    assert set(made["health_claims"][0]["Member ID"]) <= set(mm["Member ID"])


def test_codes_stay_text_after_a_csv_round_trip(made):
    ip = pd.read_csv(io.StringIO(made["inpatient"][0].to_csv(index=False)), dtype=str)
    assert {"01", "07", "20", "30"} <= set(ip["Discharge Disposition"])
    cl = pd.read_csv(io.StringIO(made["health_claims"][0].to_csv(index=False)), dtype=str)
    assert {"1", "7", "8"} <= set(cl["Claim Frequency"]) and "0450" in set(cl["Rev Code"])
    assert {"99213", "J1100"} <= set(cl["CPT/HCPCS"])
