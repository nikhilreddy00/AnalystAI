"""Phase 5.7 Domain 3 (healthcare): the shared fixes the healthcare pack needs, each fail-first,
each with negative cases from the finished domains (retail, banking)."""
import shutil

import pandas as pd
import pytest

import domain_pack
from agents.ingestion_agent import build_contract
from orchestrator.state import ConnectorType

TOYCLINIC = """
domain: toyclinic
phi: true
signals:
  - {member: [enrollee_ref], money: [settled]}
roles:
  enrollee_ref: {kind: id, headers: [enrollee_ref]}
  settled:      {kind: money, headers: [settled]}
  paid_on:      {kind: date, headers: [paid_on]}
caveats:
  read_as_domain_weak: "Read as clinic data: {roles}."
"""


@pytest.fixture
def toy_clinic(tmp_path, monkeypatch):
    for p in domain_pack.PACKS.glob("*.yaml"):
        shutil.copy(p, tmp_path / p.name)
    (tmp_path / "toyclinic.yaml").write_text(TOYCLINIC)
    monkeypatch.setattr(domain_pack, "PACKS", tmp_path)
    domain_pack.load_packs.cache_clear()
    yield domain_pack.pack_for("toyclinic")
    domain_pack.load_packs.cache_clear()


def _claim_lines(n=40) -> pd.DataFrame:
    """A payer claims extract: member ids and procedure codes, no MRN, no ICD, no name."""
    return pd.DataFrame({
        "Claim ID": [f"C{1000 + i // 2}" for i in range(n)],
        "Member ID": [f"M{100000000 + i % 13}" for i in range(n)],
        "CPT/HCPCS": ["99213", "80053", "J1100", "71046"] * (n // 4),
        "Rendering NPI": [1234567890 + i % 5 for i in range(n)],
        "Paid Amount": [round(50.0 + i, 2) for i in range(n)]})


def test_a_claims_file_without_mrn_or_icd_is_patient_data():
    """B1: member ids next to procedure/provider codes are PHI; the old signal needed an MRN, an
    ICD column or a personal column, so a claims extract loaded with member ids in clear."""
    assert build_contract(_claim_lines(), ConnectorType.CSV).phi


def test_a_pack_that_declares_phi_turns_patient_mode_on(toy_clinic):
    """Detection runs before the PHI decision: any match of a `phi: true` pack fails closed,
    whatever the headers say."""
    df = pd.DataFrame({"enrollee_ref": [f"E{i % 9}" for i in range(30)],
                       "settled": [10.0 + i for i in range(30)]})
    assert toy_clinic.phi and build_contract(df, ConnectorType.CSV).phi


@pytest.mark.parametrize("cols", [
    # a gym's members and their fees; a retailer's loyalty members; a bank's member accounts
    {"Member ID": ["M1", "M2"] * 10, "Monthly Fee": [30.0] * 20},
    {"member_id": ["L1", "L2"] * 10, "order_total": [12.5] * 20, "sku": ["A", "B"] * 10},
    {"Member Number": ["1001", "1002"] * 10, "Balance": [500.0] * 20,
     "Account": ["A1", "A2"] * 10},
])
def test_member_ids_alone_are_not_patient_data(cols):
    assert not build_contract(pd.DataFrame(cols), ConnectorType.CSV).phi
