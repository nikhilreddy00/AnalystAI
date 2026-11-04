"""S3 (Domain 5): national ids, NI numbers and bank details are personal data; another domain's
account ids, a company's tax id and look-alike words are not."""
import pytest

from guardrails import is_pii_column, pii_kind


@pytest.mark.parametrize("name", ["national_id", "National ID Number", "ni_number", "NINO",
                                  "sin_number", "social_insurance_number", "bank_account",
                                  "Bank Account Number", "bank_account_no", "routing_number",
                                  "sort_code", "tax_file_number", "national_insurance_number"])
def test_personal_identifiers_by_name(name):
    assert is_pii_column(name) and pii_kind(name, ["x"])[0] == "national_id"


@pytest.mark.parametrize("name", ["account_number", "account_id", "account_no", "loan_id",
                                  "tax_id", "vat_number", "tax_amount", "tax_rate", "sine",
                                  "single", "sin_count", "account_balance", "sort_order",
                                  "routing", "national_sales"])
def test_other_domains_ids_and_lookalikes_are_not(name):
    assert not is_pii_column(name) and pii_kind(name, ["A1", "A2"]) is None


def test_ni_numbers_by_value_in_a_column():
    vals = ["QQ123456C", "AB 12 34 56 D", "JM 65 43 21 A", "CE123456B"]
    assert pii_kind("ref", vals)[0] == "national_id"
    assert pii_kind("ref", ["BG123456A", "GB123456A", "QO123456A", "XX"]) is None   # invalid prefixes
