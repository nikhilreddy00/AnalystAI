"""Phase 5.7 Domain 3 (healthcare): the shared fixes the healthcare pack needs, each fail-first,
each with negative cases from the finished domains (retail, banking)."""
import shutil

import pandas as pd
import pytest

import domain_pack
from agents.ingestion_agent import build_contract, prepare_frame
from orchestrator.state import ConnectorType

TOYCLINIC = r"""
domain: toyclinic
phi: true
signals:
  - {member: [enrollee_ref], money: [settled]}
roles:
  enrollee_ref: {kind: id, headers: [enrollee_ref]}
  settled:      {kind: money, headers: [settled]}
  paid_on:      {kind: date, headers: [paid_on]}
  claim:        {kind: id, headers: [claim]}
  line:         {kind: category, headers: [line]}
  seq:          {kind: count, headers: [seq]}
  freq:         {kind: category, headers: [freq]}
  group_code:   {kind: category, pattern: '\d{3}', headers: [group_code, group_no]}
  disposition:  {kind: category, headers: [disposition]}
  procedure:    {kind: category, pattern: '[0-9A-Z]\d{3}[0-9A-Z]', headers: [procedure]}
  unit:         {kind: category, headers: [unit]}
  outcome:      {kind: category, pattern: '(?i)\d{1,2}|[a-z][a-z /,-]{1,38}', headers: [outcome]}
kpis:
  - {name: deaths, label: Deaths, num: [[{nunique: enrollee_ref,
                                          where: [[{role: disposition, in: ["20"]}]]}]]}
version_key: [claim, line]
version_order: seq
version_void: {freq: ["8"]}
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


def test_a_version_chain_counts_only_its_final_row(toy_clinic):
    """B2: one claim line adjudicated three times (original, replacement) counts once, at its
    last version; a chain whose last version is a void counts as cancelled; a claim's other line
    is its own chain. Rows of other chains are untouched."""
    df = pd.DataFrame({
        "claim": ["C1", "C1", "C1", "C1", "C2", "C2", "C3"],
        "line":  ["1", "1", "1", "2", "1", "1", "1"],
        "seq":   [0, 2, 1, 0, 0, 1, 0],
        "freq":  ["1", "7", "7", "1", "1", "8", "1"],
        "settled": [100.0, 90.0, 95.0, 20.0, 50.0, -50.0, 30.0],
        "enrollee_ref": ["E1"] * 7, "paid_on": ["2026-01-05"] * 7})
    roles = {r: r for r in ("claim", "line", "seq", "freq", "settled", "enrollee_ref")}
    kinds = domain_pack.row_kinds(df, roles, toy_clinic).tolist()
    assert kinds == ["non_product", "sale", "non_product", "sale", "cancel", "cancel", "sale"]


def test_no_version_fields_means_no_version_rule(toy_clinic):
    """A pack without version_key (retail, banking) never drops a repeated key's earlier rows."""
    retail = domain_pack.pack_for("retail")
    assert not retail.version_key and not domain_pack.pack_for("banking").version_key


def test_a_code_written_as_a_number_is_a_category_not_a_metric(toy_clinic):
    """M4: a pack category role with a pattern (a 3-digit group code: 470, 291) fits a numeric
    column, and ingestion reads it as a label, never a quantity to sum or average."""
    df = pd.DataFrame({"enrollee_ref": [f"E{i % 9}" for i in range(30)],
                       "settled": [10.0 + i for i in range(30)],
                       "paid_on": ["2026-01-05"] * 30,
                       "group_code": [470, 291, 871] * 10})
    c = build_contract(df, ConnectorType.CSV)
    col = next(x for x in c.columns if x.name == "group_code")
    assert c.roles.get("group_code") == "group_code" and not col.is_metric and not col.is_id


def _stays(n=300) -> pd.DataFrame:
    return pd.DataFrame({
        "enrollee_ref": [f"E{i % 120}" for i in range(n)],
        "settled": [100.0 + i for i in range(n)], "paid_on": ["2026-01-05"] * n,
        "disposition": ["20" if i < 5 else "07" if i < 8 else "01" for i in range(n)],  # 5 deaths
        "unit": ["ICU" if i < 4 else "Ward A" if i % 2 else "Ward B" for i in range(n)],
        "procedure": [f"{99000 + i % 80}" for i in range(n)],                # 80 distinct codes
        "comment": [f"called back re visit {i}" for i in range(n)]})        # free text


def test_values_a_kpi_filters_on_are_never_pooled(toy_clinic):
    """M2/D4: pooling 'expired' (5 rows) into 'Other' would make mortality 0; a KPI's filter
    values stay as written, and only those: another rare value of the same column (3 '07') and
    small groups of other columns (a 4-row unit) are still pooled."""
    assert toy_clinic.value_words()["disposition"] == {"20"}
    out, c = prepare_frame(_stays(), build_contract(_stays(), ConnectorType.CSV), ConnectorType.CSV)
    assert (out["disposition"] == "20").sum() == 5 and "07" not in set(out["disposition"])
    assert "ICU" not in set(out["unit"])
    assert any(d.column == "disposition" and "kept as written" in d.reason
               for d in c.deidentification)


def test_a_pattern_matched_code_column_is_kept_free_text_is_not(toy_clinic):
    """M5: 80 procedure codes are a category the analysis groups by (kept); a free-text comment
    with as many values is still withheld."""
    out, _ = prepare_frame(_stays(), build_contract(_stays(), ConnectorType.CSV), ConnectorType.CSV)
    assert out["procedure"].nunique() == 80 and "comment" not in out.columns


@pytest.mark.parametrize("text, hit", [
    ("Oncology had 4 deaths in May.", "4 deaths"),
    ("Only three patients were readmitted from Ward B.", "three patients"),
    ("7 of 412 encounters were observation stays.", "7 of 412 encounters"),
    ("Readmissions rose 5% to 214 patients.", None),            # a percentage, a large count
    ("Cardiology's ALOS was 4.2 days across 9 months.", None),   # days and months aren't people
    ("10 departments reported.", None),
])
def test_a_reported_count_of_1_to_10_people_is_a_small_cell(text, hit):
    """D4(c): small cells are controlled in what is reported, not by pooling the data: a count
    of 1–10 of the pack's people nouns on patient data is flagged."""
    from guardrails import claim_small_cell
    nouns = ["patients", "encounters", "deaths", "stays", "admissions", "members"]
    r = claim_small_cell(text, nouns)
    assert (r is None) if hit is None else (hit in r)


def test_no_nouns_no_small_cell_check():
    from guardrails import claim_small_cell
    assert claim_small_cell("Oncology had 4 deaths in May.", []) is None


def test_one_value_off_the_code_pattern_withholds_the_column(toy_clinic):
    """Security review: a column vote (90% of 1,000) let free text ride in a code column; every
    value must match."""
    df = _stays()
    df.loc[299, "procedure"] = "pt Jane Roe called"
    out, _ = prepare_frame(df, build_contract(df, ConnectorType.CSV), ConnectorType.CSV)
    assert "procedure" not in out.columns


def _asks(pack, roles):
    from agents.visualization_agent import _overview_asks
    return [(i.value, p.kpi, p.group_by) for i, p in _overview_asks(pack, roles, list(roles.values()))]


def test_overview_kpis_choose_the_headline_and_the_rates_by_group():
    """m1: a hospital's overview opens on stays and readmission by department, never on billed
    charges (the first money tile); KPIs the data can't compute are skipped."""
    pack = domain_pack.Pack.model_validate({
        "domain": "toyward", "signals": [{"a": ["stay"], "b": ["charges"]}],
        "roles": {"stay": {"kind": "id", "headers": ["stay"]},
                  "charges": {"kind": "money", "headers": ["charges"]},
                  "dept": {"kind": "category", "headers": ["dept"]},
                  "died": {"kind": "category", "headers": ["died"]},
                  "day": {"kind": "date", "headers": ["day"]}},
        "kpis": [{"name": "billed", "label": "Billed", "num": [[{"sum": "charges"}]], "tile": True},
                 {"name": "stays", "label": "Stays", "num": [[{"nunique": "stay"}]], "tile": True},
                 {"name": "mortality", "label": "Mortality", "unit": "percent", "tile": True,
                  "num": [[{"nunique": "stay", "where": [[{"role": "died", "in": ["y"]}]]}]],
                  "den": [[{"nunique": "stay"}]]}],
        "overview_groups": ["dept"], "overview_kpis": ["stays", "mortality"],
        "caveats": {"read_as_domain_weak": "x"}})
    roles = {r: r for r in ("stay", "charges", "dept", "died", "day")}
    assert _asks(pack, roles) == [("time_series", "stays", None), ("top_n", "stays", "dept"),
                                  ("top_n", "mortality", "dept")]
    del roles["died"]
    assert _asks(pack, roles) == [("time_series", "stays", None), ("top_n", "stays", "dept")]


def test_without_overview_kpis_retail_and_banking_keep_their_charts():
    """Unset: the first money tile, and a state ratio by group (byte-identical to before)."""
    bank = domain_pack.pack_for("banking")
    roles = {r: r for r in bank.roles}
    asks = _asks(bank, roles)
    assert asks[0] == ("time_series", "total_balance", None)
    assert [a[1] for a in asks[1:]] == ["total_balance", "npl_ratio"]
    assert not bank.overview_kpis and not domain_pack.pack_for("retail").overview_kpis


WARD = {
    "domain": "toyward2", "signals": [{"a": ["who"], "b": ["day_in"]}],
    "roles": {"who": {"kind": "id", "headers": ["who"]},
              "day_out": {"kind": "date", "headers": ["day_out"]},
              "day_in": {"kind": "date", "headers": ["day_in"]},
              "kind_of": {"kind": "category", "headers": ["kind_of"]},
              "how_left": {"kind": "category", "headers": ["how_left"]}},
    "kpis": [
        {"name": "back", "label": "Back within 30 days", "unit": "percent",
         "num": [[{"within": {"entity": "who", "start": "day_in", "end": "day_out", "days": 30,
                              "follow": [[{"role": "kind_of", "in": ["in"]}]]},
                   "where": [[{"role": "how_left", "not_in": ["died"]}]]}]],
         "den": [[{"within": {"entity": "who", "start": "day_in", "end": "day_out", "days": 30,
                              "base": True},
                   "where": [[{"role": "how_left", "not_in": ["died"]}]]}]]},
        {"name": "avg_stay", "label": "Average stay", "unit": "number",
         "num": [[{"sum": ["day_out", "days", "day_in"]}]], "den": [[{"nunique": "who"}]]}],
    "caveats": {"read_as_domain_weak": "x"}}

STAYS = pd.DataFrame([
    # who, in, out, kind, left
    ("A", "2026-01-01", "2026-01-05", "in", "home"),   # index → A back on 01-20 (15 d): hit
    ("A", "2026-01-20", "2026-01-22", "in", "home"),   # index → no follow-up: no
    ("B", "2026-01-03", "2026-01-03", "in", "home"),   # same-day stay; B back 02-03 (31 d): no
    ("B", "2026-02-03", "2026-02-04", "in", "home"),   # index → none
    ("C", "2026-01-10", "2026-01-12", "in", "home"),   # follow-up is an observation stay: no
    ("C", "2026-01-15", "2026-01-15", "obs", "home"),  # index (obs stays are filtered by where
    ("D", "2026-01-02", "2026-01-06", "in", "died"),   #   in a real pack) ; died: not an index
    ("E", "2026-03-01", "2026-03-04", "in", "home"),   # window passes the last start: censored
    ("E", "2026-03-10", "2026-03-12", "in", "home"),   # last start 03-10; censored too
], columns=["who", "day_in", "day_out", "kind_of", "how_left"])


def _ward_kpi(name, by=None):
    pack = domain_pack.Pack.model_validate(WARD)
    roles = {r: r for r in pack.roles}
    return domain_pack.compute_kpi(STAYS, roles, pack, name, by=by)


def test_within_counts_a_follow_up_in_the_window_and_censors_unfinished_windows():
    """D2: index = not died, discharge ≤ last admit − 30 d (A×2, B×2, C×2 = 6); back = a later
    inpatient admit of the same patient in (0, 30] days after discharge (A's first only)."""
    assert _ward_kpi("back") == pytest.approx(1 / 6)


def test_days_between_two_dates_is_whole_days_and_same_day_is_zero():
    """m3: LOS = discharge − admit in days (HCUP: same day = 0); 5 people, 9 stays."""
    los = [4, 2, 0, 1, 2, 0, 4, 3, 2]
    assert _ward_kpi("avg_stay") == pytest.approx(sum(los) / 5)


def _audit(date_col, first, last, months):
    from orchestrator.state import CleaningAudit
    return CleaningAudit(decisions=[], rows_before=1, rows_after=1, columns_coerced=[],
                         duplicates_removed=0, outliers_flagged=[], high_null_columns=[],
                         summary="", date_ranges={date_col: [first, last, months]})


def test_runout_names_the_incomplete_recent_months_when_the_lag_date_is_there():
    """m2 / trap 4: claims keep arriving for months after service; the last `months` service
    months are incomplete (a drop there is lag, not a trend). Only on data with the lag date."""
    pack = domain_pack.Pack.model_validate({
        **WARD, "domain": "toyclaims", "runout": {"role": "how_left", "months": 3},
        "caveats": {"read_as_domain_weak": "x",
                    "recent_incomplete": "Service months from {from_month} on are incomplete."}})
    roles = {r: r for r in pack.roles}
    audit = _audit("day_out", "2025-11-01", "2026-10-31", 12)
    assert domain_pack.pack_caveats(pack, audit, roles) == [
        "Service months from Aug 2026 on are incomplete."]
    del roles["how_left"]
    assert domain_pack.pack_caveats(pack, audit, roles) == []


def test_a_whole_number_read_as_float_compares_as_its_digits():
    """A code column with a blank reads as float (DRG 313.0): the pattern check and `words()`
    (THE value comparison) must see '313', never '313.0'."""
    s = pd.Series([313.0, None, 7.0])
    assert domain_pack.words(s).tolist()[::2] == ["313", "7"]
    assert domain_pack.as_text(pd.Series([2.5, 3.0])).tolist() == ["2.5", "3.0"]   # not whole


def test_a_code_column_named_like_an_id_is_not_pseudonymised(toy_clinic):
    """Claims rehearsal: 'Line No' (a pack line role, values 1–4) was pseudonymised by its 'no'
    suffix, so every claim's lines lost their version chains. A pattern-matched code is a code."""
    df = _stays().assign(**{"Group No": [470, 291, 871] * 100})
    out, c = prepare_frame(df, build_contract(df, ConnectorType.CSV), ConnectorType.CSV)
    assert set(out["Group No"]) == {470, 291, 871}


def test_describe_kpi_says_within_and_days_in_words():
    """The analysis brief and answers describe every KPI: a within term and a days difference
    crashed describe_kpi (golden run)."""
    pack = domain_pack.Pack.model_validate(WARD)
    roles = {r: r for r in pack.roles}
    back = domain_pack.describe_kpi(pack, "back", roles)
    assert "followed by another row of the same who starting 1–30 days after day_out" in back
    assert "kind_of is in" in back and "how_left is not died" in back
    assert "days from day_in to day_out" in domain_pack.describe_kpi(pack, "avg_stay", roles)


def test_pooling_a_code_column_read_as_numbers_keeps_a_storable_frame(toy_clinic):
    """Smoke run: small MS-DRG groups (a float column: blanks on observation stays) pooled into
    'Other (fewer than 11 each)' made a float/str column parquet can't store (ArrowInvalid)."""
    import io
    df = _stays().assign(group_code=[470.0] * 150 + [871.0] * 140 + [291.0] * 5 + [None] * 5)
    out, _ = prepare_frame(df, build_contract(df, ConnectorType.CSV), ConnectorType.CSV)
    out.to_parquet(io.BytesIO(), index=False)
    assert set(out["group_code"].dropna()) == {"470", "871", "Other (fewer than 11 each)"}


def test_words_that_fit_a_loose_code_pattern_are_not_a_code(toy_clinic):
    """Security review: a role whose pattern also admits words (a disposition: '07' or
    'Expired') must not exempt a free-text column 1:1 with patients from withholding."""
    words = [f"{a}{b}q" for a in "abcdefghijklmnopqrstuvwxyz" for b in "abcdefghijklm"]   # 1:1 tokens
    df = _stays().assign(enrollee_ref=[f"E{i}" for i in range(300)], outcome=words[:300])
    out, _ = prepare_frame(df, build_contract(df, ConnectorType.CSV), ConnectorType.CSV)
    assert "outcome" not in out.columns
