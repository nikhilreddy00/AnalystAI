"""Domain 4 (insurance): the shared rules the insurance pack needs, on toy packs (no domain word
in shared code), plus the real pack once it exists."""
import shutil

import pandas as pd
import pytest

import domain_pack

TOYRUN = r"""
domain: toyrun
signals:
  - {case: [case_ref], money: [owed]}
roles:
  valued_on: {kind: date, headers: [valued_on]}
  case_ref:  {kind: id, headers: [case_ref]}
  party:     {kind: category, headers: [party]}
  owed:      {kind: money, headers: [owed]}
version_key: [case_ref, party]
version_order: valued_on
caveats:
  read_as_domain_weak: "Read as toy run data: {roles}."
"""


@pytest.fixture
def toy_run(tmp_path, monkeypatch):
    for p in domain_pack.PACKS.glob("*.yaml"):
        shutil.copy(p, tmp_path / p.name)
    (tmp_path / "toyrun.yaml").write_text(TOYRUN)
    monkeypatch.setattr(domain_pack, "PACKS", tmp_path)
    domain_pack.load_packs.cache_clear()
    yield domain_pack.pack_for("toyrun")
    domain_pack.load_packs.cache_clear()


def test_a_version_chain_ordered_by_a_date_counts_only_the_latest_date(toy_run):
    """S1: snapshots of one case and party at several valuation dates: only the latest date
    counts, whatever the file order and the date's spelling. Each party is its own chain."""
    df = pd.DataFrame({
        "case_ref":  ["K1", "K1", "K1", "K1", "K2"],
        "party":     ["1", "1", "1", "2", "1"],
        "valued_on": ["2026-03-31", "12/31/2025", "2026-06-30", "2025-12-31", "2026-06-30"],
        "owed":      [90.0, 50.0, 120.0, 10.0, 30.0]})
    roles = {r: r for r in ("case_ref", "party", "valued_on", "owed")}
    kinds = domain_pack.row_kinds(df, roles, toy_run).tolist()
    assert kinds == ["non_product", "non_product", "sale", "sale", "sale"]


def test_a_blank_valuation_date_is_the_earliest_version(toy_run):
    """A snapshot without a date never beats a dated one (first/last/none of the chain)."""
    df = pd.DataFrame({"case_ref": ["K1", "K1", "K2"], "party": ["1", "1", "1"],
                       "valued_on": [None, "2026-03-31", None], "owed": [1.0, 2.0, 3.0]})
    roles = {r: r for r in ("case_ref", "party", "valued_on", "owed")}
    assert domain_pack.row_kinds(df, roles, toy_run).tolist() == ["non_product", "sale", "sale"]


def _audit(ranges: dict):
    from orchestrator.state import CleaningAudit
    return CleaningAudit(decisions=[], rows_before=1, rows_after=1, columns_coerced=[],
                         duplicates_removed=0, outliers_flagged=[], high_null_columns=[],
                         summary="", date_ranges=ranges)


def _matured(**extra):
    return domain_pack.Pack.model_validate({
        "domain": "toymature", "signals": [{"case": ["case_ref"], "money": ["owed"]}],
        "roles": {"happened_on": {"kind": "date", "headers": ["happened_on"]},
                  "valued_on": {"kind": "date", "headers": ["valued_on"]},
                  "case_ref": {"kind": "id", "headers": ["case_ref"]},
                  "owed": {"kind": "money", "headers": ["owed"]}},
        "maturity": {"role": "valued_on", "months": 24},
        "caveats": {"read_as_domain_weak": "x",
                    "developing_periods": "As of {as_of}, still developing: {periods}.",
                    "developing_periods_undated": "No valuation date; by {as_of}: {periods}."},
        **extra})


def test_years_younger_than_the_maturity_are_named_with_their_age():
    """S2 (B2): each period year younger than `months` at the latest valuation date, with its
    age in months from the year's start; nothing is cut. Older years are not named."""
    pack = _matured()
    roles = {r: r for r in pack.roles}
    audit = _audit({"happened_on": ["2022-01-03", "2026-09-12", 57],
                    "valued_on": ["2022-12-31", "2026-09-30", 46]})
    assert domain_pack.pack_caveats(pack, audit, roles) == [
        "As of 30 Sep 2026, still developing: 2026 (9 months) and 2025 (21 months)."]


def test_without_the_valuation_date_ages_run_to_the_last_period_date():
    pack = _matured()
    roles = {r: r for r in pack.roles if r != "valued_on"}
    audit = _audit({"happened_on": ["2024-02-01", "2026-01-31", 24]})
    assert domain_pack.pack_caveats(pack, audit, roles) == [
        "No valuation date; by 31 Jan 2026: 2026 (1 month) and 2025 (13 months)."]


def test_every_year_mature_names_none():
    pack = _matured(maturity={"role": "valued_on", "months": 6})
    roles = {r: r for r in pack.roles}
    audit = _audit({"happened_on": ["2024-01-01", "2025-12-20", 24],
                    "valued_on": ["2024-03-31", "2026-09-30", 11]})
    assert domain_pack.pack_caveats(pack, audit, roles) == []


@pytest.mark.parametrize("name", ["VIN", "Vehicle VIN", "vin_number", "VehicleIdentificationNumber",
                                  "Driver License", "drivers_licence_no", "DriverLicenseNumber",
                                  "DL No.", "License Plate", "plate_number", "licence_no"])
def test_vehicle_and_licence_columns_are_personal_data(name):
    """S3 (NAIC Model 668: a driver's licence number identifies a consumer); VIN and plate
    identify the insured's vehicle."""
    from guardrails import is_pii_column, pii_kind
    assert is_pii_column(name) and pii_kind(name, ["x"]) is not None


@pytest.mark.parametrize("name", ["license_count", "licenses", "license_type", "Licensed Seats",
                                  "plate_count", "plate", "vintage", "vin_count", "Driver",
                                  "drivers", "dl_rate", "license_tier"])
def test_counts_tiers_and_other_words_are_not_vehicle_or_licence_data(name):
    """Negative cases from SaaS (license_count, seats), logistics (plate_count, driver), wine
    (vintage): a widened rule ships with them."""
    from guardrails import is_pii_column
    assert not is_pii_column(name)


def test_vins_in_a_neutrally_named_column_are_personal_data():
    """A VIN is recognised by its check digit (North American position 9): a 17-character order
    id or hash is not one."""
    from guardrails import mask_pii, pii_kind
    vins = ["1HGCM82633A004352", "1M8GDM9AXKP042788", "1FTFW1ET9DFC10312"]
    assert pii_kind("Vehicle Ref", vins) is not None
    assert pii_kind("Order Ref", ["A1B2C3D4E5F6G7H8J", "ORD20260301XYZ12", "Z9Y8X7W6V5U4T3S2R"]) is None
    assert pii_kind("Event Id", ["01ARZ3NDEKTSV4RRFFQ69G5FAV", "01BX5ZZKBKACTAV9WEVGEMMVRZ",
                                 "01HQ3J8Z6V7Y4N2K9W5T0R1PXM"]) is None   # 26-character ULIDs
    assert mask_pii("vehicle 1HGCM82633A004352 towed") == "vehicle 1HGCM82633A004352 towed"


def test_a_role_caveat_is_said_whenever_its_role_is_in_the_data():
    """S4 (D11): a pack's caution for one role (a protected attribute) is said on a strong or a
    weak match, only when the data has that role, after the condition caveats."""
    pack = _matured(roles={"case_ref": {"kind": "id", "headers": ["case_ref"]},
                           "owed": {"kind": "money", "headers": ["owed"]},
                           "sex": {"kind": "category", "headers": ["sex"]},
                           "valued_on": {"kind": "date", "headers": ["valued_on"]}},
                    role_caveats={"sex": "Sex is described, never a rating recommendation."})
    roles = {"case_ref": "case_ref", "owed": "owed", "sex": "sex"}
    said = domain_pack.pack_caveats(pack, _audit({}), roles, confidence="weak")
    assert said == ["x", "Sex is described, never a rating recommendation."]
    del roles["sex"]
    assert domain_pack.pack_caveats(pack, _audit({}), roles, confidence="weak") == ["x"]


def test_a_role_caveat_must_name_a_pack_role():
    with pytest.raises(ValueError, match="nobody"):
        _matured(role_caveats={"nobody": "text"})


def test_a_marker_fields_blank_is_the_layout_and_never_fires_the_high_null_gate():
    """S6: a column most rows leave blank because blank means none (an event code on the few
    rows it hit) is the file's layout, not missing values: no approval gate, no 'Unknown'."""
    pack = _matured(marker_fields=["owed"])
    df = pd.DataFrame({"case_ref": ["K1", "K2", "K3", "K4"], "owed": [None, None, None, 5.0]})
    assert domain_pack.layout_columns(df, pack, {"case_ref": "case_ref", "owed": "owed"}) \
        == {"owed": "marker"}


# ── Detection: insurance vs every other domain (the Domain 3 collision kept) ────────────────

def _contract(df: pd.DataFrame):
    from connectors.csv_connector import CSVConnector, _read_bytes
    from orchestrator.state import ConnectorConfig, ConnectorType
    df = _read_bytes(df.to_csv(index=False).encode(), ".csv")[0]
    return CSVConnector(ConnectorConfig(type=ConnectorType.CSV)).build_data_contract(df)


INSURANCE = {"claims", "policies", "loss_run"}


@pytest.mark.parametrize("name", sorted(__import__("evals.datagen").datagen.DATASETS))
def test_only_insurance_datasets_read_as_insurance(name):
    """No retail, banking, healthcare, HR, SaaS, IoT or logistics dataset reads as insurance;
    every insurance dataset reads as insurance (strong), never healthcare."""
    from evals import datagen
    c = _contract(datagen.make(name, 1)[0])
    if name in INSURANCE:
        assert (c.domain, c.domain_confidence, c.phi) == ("insurance", "strong", False)
    else:
        assert c.domain != "insurance"


def test_a_workers_comp_loss_run_is_insurance_with_patient_data_protected():
    """D7: a workers' comp or injury loss run carries diagnoses: insurance (reserve + loss date),
    and PHI mode on (fail closed), never read as a health payer's claims."""
    n = 30
    c = _contract(pd.DataFrame({
        "Claim Number": [f"WC{1000 + i}" for i in range(n)],
        "Loss Date": pd.date_range("2025-01-01", periods=n, freq="7D").strftime("%Y-%m-%d"),
        "Diagnosis Code": ["S93.401A", "M54.5", "S62.101A"] * 10,
        "Paid to Date": [round(1000.0 + 37 * i, 2) for i in range(n)],
        "Case Reserve": [round(500.0 + 11 * i, 2) for i in range(n)]}))
    assert (c.domain, c.phi) == ("insurance", True)


def test_the_insight_states_a_version_chains_grain_and_its_counted_rows():
    """Domain 4 insight read (lesson 17): 'covers 38,255 claims' was the row count; the facts
    said 'One row is one claim'. A chained file's grain is its key at one version, and only
    the counted rows count (generic: the pack's version roles, no domain word)."""
    import asyncio
    from agents.insight_agent import build_context
    from evals import run
    case = {"id": "x", "dataset": {"name": "loss_run", "seed": 1}, "question": "Give me an overview.",
            "plan": {"target_metrics": ["Incurred"], "segments": ["Line of Business"],
                     "analysis_types": ["eda"]},
            "findings": [], "entity": ["claim", "claims"]}
    session, _, _ = asyncio.run(run.insight_bundle(case))
    facts = build_context(session).facts
    assert "One row is one claim." not in facts
    assert ("One row is one Claim Number and Claimant Number at one Valuation Date: only the "
            "latest counts (3,975 of 38,255 rows)") in facts, facts


def test_the_insight_harness_summary_stats_equal_the_analysis_built_in():
    """CODING_STANDARDS (adapters take the production path): the insight harness summarised a
    loss run over all 16 valuations (mean $7,974) where the analysis built-in reads each
    claimant's latest ($8,471.69); the model then wrote the harness's number."""
    import sys
    sys.path.insert(0, "tests")
    from test_oracle import _built, builtin
    from evals.run import viz_findings
    _, clean, contract, _ = _built("loss_run")
    case = {"plan": {"target_metrics": ["Incurred"], "segments": ["Line of Business"]}, "findings": []}
    harness, _ = viz_findings(case, clean, ["Incurred"], contract)
    want = builtin("loss_run", ["Incurred"], [], ["eda"])["eda_summary"]["Incurred"]
    assert harness.eda_summary["Incurred"] == pytest.approx(want), (harness.eda_summary, want)


def test_the_insight_harness_month_over_month_equals_the_analysis_built_in():
    """CODING_STANDARDS: the harness compared a partial last month with a full one (-70.4%) where
    production's built-in leaves it out; and it made a MoM of a developing measure that
    production never makes. Both follow analysis_agent's rules."""
    import sys
    sys.path.insert(0, "tests")
    from test_oracle import _built, builtin
    from evals.run import viz_findings
    _, clean, contract, _ = _built("policies")
    case = {"plan": {"target_metrics": ["Written Premium"], "segments": []},
            "findings": [{"kind": "trend", "column": "Written Premium"}]}
    harness, _ = viz_findings(case, clean, [], contract)
    built = [t for t in builtin("policies", ["Written Premium"], [], ["trend"])["trends"]
             if t["period"] == "MoM" and not t.get("segment")]
    assert harness.trends[0].pct_change == pytest.approx(built[0]["pct_change"], abs=0.01)
    _, clean, contract, _ = _built("loss_run")
    case = {"plan": {"target_metrics": ["Incurred"], "segments": []},
            "findings": [{"kind": "trend", "column": "Incurred"}]}
    assert viz_findings(case, clean, [], contract)[0].trends == []


def test_the_insight_never_says_one_row_per_entity_when_a_count_tile_disagrees():
    """Domain 4 insight read: 'This portfolio of 2,674 policies' (rows: terms and cancellation
    transactions) beside a Policies tile of 1,300. Generic: a count tile named like the
    narrative's noun, with another value than the rows, sets the grain sentence."""
    import asyncio
    from agents.insight_agent import build_context
    from evals import run
    case = {"id": "x", "dataset": {"name": "policies", "seed": 1},
            "question": "Give me an overview of this dataset.",
            "plan": {"target_metrics": ["Earned Premium"], "segments": ["Line of Business"],
                     "analysis_types": ["eda"]},
            "findings": [], "entity": ["policy", "policies"]}
    session, _, _ = asyncio.run(run.insight_bundle(case))
    facts = build_context(session).facts
    assert "One row is one policy." not in facts
    assert "1,300 policies in 2,674 rows: a row is not one policy" in facts, facts


def test_a_column_blank_by_the_files_layout_is_never_called_missing_in_evidence():
    """Live loss run: 'Case Reserve: 71% missing, left out of this result' (closed claims carry no
    reserve, by design) beside cleaning's own 'blank by design'. analysis_agent kept its own copy
    of the layout rule (order-level only); every layout kind is the file's layout."""
    import sys
    sys.path.insert(0, "tests")
    from agents.analysis_agent import build_evidence
    from orchestrator.state import AnalysisFindings, SegmentResult
    from test_oracle import _built
    s, _, _, _ = _built("loss_run")
    f = AnalysisFindings(eda_summary={}, correlations={}, trends=[], anomalies=[], statistical_tests=[],
                         forecast_points=[], distributions={}, code_executed="", segments=[
        SegmentResult(segment_column="Line of Business", metric_column=c, aggregation="sum",
                      results=[{"name": "Auto", "value": 1.0}]) for c in ("Case Reserve", "Cat Event")])
    notes = " ".join(a for e in build_evidence(f, {}, "", "", s.cleaning_audit) for a in e.assumptions)
    assert "missing" not in notes, notes


def test_a_kpi_given_per_a_role_is_never_one_total_across_it():
    """Audit pass 1 (spec + expert, major): 'What's our claim frequency?' answered 0.11, claims
    over car-years plus house-years. A KPI with `per: <role>` is computed within one value of
    that role only: no total across values, no other grouping that mixes them; asked without a
    group, the answer is given by that role."""
    import sys
    sys.path.insert(0, "tests")
    from test_oracle import _built, answer
    _, clean, contract, truth = _built("policies")
    pack = domain_pack.pack_for("insurance")
    for kpi in ("frequency", "pure_premium"):
        assert domain_pack.compute_kpi(clean, contract.roles, pack, kpi) is None
        assert domain_pack.compute_kpi(clean, contract.roles, pack, kpi, by="State") is None
    auto = clean[clean["Line of Business"] == "Auto"]
    assert domain_pack.compute_kpi(auto, contract.roles, pack, "frequency") == pytest.approx(
        truth["frequency_by_line"]["Auto"])
    text, chart = answer("policies", "segment_compare", "What's our claim frequency?", kpi="frequency")
    assert chart.keys() == {"Auto", "Home"} and "by Line of Business" in text, text



def test_workers_comp_files_read_as_insurance():
    """Audit pass 1 (expert): payroll is a workers' comp policy's exposure base and an employee
    id its claimant; vetoing [payroll] / [employee, id] read both files as no domain."""
    n = 30
    policy = _contract(pd.DataFrame({
        "Policy Number": [f"WC-{i:04d}" for i in range(n)],
        "Effective Date": pd.date_range("2025-01-01", periods=n, freq="7D").strftime("%Y-%m-%d"),
        "Payroll": [round(250000.0 + 1000 * i, 2) for i in range(n)],
        "Written Premium": [round(4000.0 + 37 * i, 2) for i in range(n)],
        "Earned Premium": [round(3000.0 + 29 * i, 2) for i in range(n)]}))
    claims = _contract(pd.DataFrame({
        "Claim Number": [f"WC{1000 + i}" for i in range(n)],
        "Employee ID": [f"E{i % 9:05d}" for i in range(n)],
        "Date of Injury": pd.date_range("2025-01-01", periods=n, freq="7D").strftime("%Y-%m-%d"),
        "Paid to Date": [round(1000.0 + 37 * i, 2) for i in range(n)],
        "Case Reserve": [round(500.0 + 11 * i, 2) for i in range(n)]}))
    assert policy.domain == claims.domain == "insurance"
    assert claims.roles["date"] == "Date of Injury"


def test_the_gender_caution_is_said_on_a_policy_table():
    """Trap 13: described, never a rating recommendation; the caution is in the report."""
    import sys
    sys.path.insert(0, "tests")
    from test_oracle import _built
    s, _, contract, _ = _built("policies")
    said = domain_pack.pack_caveats(domain_pack.pack_for("insurance"), s.cleaning_audit,
                                    contract.roles, contract.domain_confidence)
    assert any(c.startswith("Gender is shown as a description only") for c in said)
