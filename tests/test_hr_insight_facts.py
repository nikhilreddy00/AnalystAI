"""Domain 5 smoke (e2e-71): 'covers 1,770 employee records' on a roster of 885 employees in
force (rows are employment spells). The insight facts name the people, never rows as people."""
from agents.insight_agent import _entity_count
from agents.visualization_agent import pack_kpis
from orchestrator.state import AnalysisFindings, DataNarrative
from tests.test_oracle import _built


def test_a_roster_counts_employees_by_the_headcount_tile_never_rows():
    s, clean, contract, truth = _built("hr_roster")
    s.analysis_findings = AnalysisFindings.model_validate({
        "eda_summary": {}, "correlations": {}, "trends": [], "segments": [], "anomalies": [],
        "statistical_tests": [], "code_executed": "", "kpis": pack_kpis(clean, contract)})
    s.narrative = DataNarrative.model_construct(entity_singular="employee", entity_plural="employees")
    fact = _entity_count(s)
    assert fact and f"{truth['headcount_asof']:,} employees in {len(clean):,} rows" in fact, fact
