"""Guards for rules that kept coming back in Phase 5.7 (each bug was a copy of a rule, or a rule
that depended on the machine). A failure here names the one shared definition to use instead."""
import re
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent.parent
SHARED = [*sorted((ROOT / "agents").glob("*.py")), ROOT / "guardrails.py", ROOT / "domain_pack.py"]


def _hits(pattern: str, allowed: set[str] = frozenset()) -> list[str]:
    rx = re.compile(pattern)
    return [f"{p.name}:{i}" for p in SHARED if p.name not in allowed
            for i, line in enumerate(p.read_text().splitlines(), 1)
            if rx.search(line) and not line.lstrip().startswith("#")]


def test_ties_rank_the_same_on_every_machine():
    """CI (Linux) and macOS ranked tied groups differently: an unstable sort. utils.ranked sorts
    by label, then stably by value."""
    from agents.utils import ranked
    s = pd.Series([11.0, 5.0, 11.0, 11.0], index=["c", "a", "b", "d"])
    assert list(ranked(s)) == [11.0, 11.0, 11.0, 5.0]
    assert list(ranked(s).index) == ["b", "c", "d", "a"]
    assert list(ranked(s, ascending=True).index) == ["a", "b", "c", "d"]


def test_rankings_go_through_ranked():
    assert _hits(r"\.sort_values\(ascending=(?![^)]*kind=)") == [], "use agents.utils.ranked(series, ascending)"


def test_one_sentence_splitter():
    assert _hits(r"\(\?<=\[\.!\?\]\)", {"guardrails.py"}) == [], "use guardrails._SENTENCE_RE"


def test_shared_code_never_names_a_pack_role():
    """A pack declares its layout in YAML (order_key, negative_is_return, cost_roles …): shared
    code reading roles["order_id"] gives every other domain retail's behaviour, or none. Only the
    schema's reserved names (domain_pack.RESERVED_ROLES: status, currency) may be named."""
    import domain_pack
    names = {r for p in domain_pack.load_packs() for r in p.roles} - set(domain_pack.RESERVED_ROLES)
    alt = "|".join(sorted(names))
    found = _hits(rf"""roles(?:\.get\(|\[)\s*["']({alt})["']|["']({alt})["']\s+in\s+(?:ctx\.)?roles\b""")
    assert found == [], f"read the pack's field, not a role name: {found}"


def test_a_comparison_says_ties_and_names_the_real_lowest_group():
    """CI found it: 'MRN-006520' leads at 11.00, 'MRN-004874' trails at 11.00 — a tie named as
    a lead, and 'trails' was the 20th group shown, not the lowest."""
    from agents.targeted_analysis_agent import handle_segment_compare
    from connectors.csv_connector import CSVConnector
    from orchestrator.state import ConnectorConfig, ConnectorType, TargetedParams
    df = pd.DataFrame({"site": [f"S{i:02d}" for i in range(25)],
                       "stay": [11.0] * 3 + [5.0] * 21 + [1.0]})
    c = CSVConnector(ConnectorConfig(type=ConnectorType.CSV)).build_data_contract(df)
    r = handle_segment_compare(df, TargetedParams(metric_column="stay", segment_column="site",
                                                  aggregation="mean"), c)
    assert "'S00', 'S01' and 'S02' tie at 11.00" in r.answer_text
    assert "'S24' is lowest at 1.00" in r.answer_text
    flat = pd.DataFrame({"site": ["a", "b"], "stay": [3.0, 3.0]})
    r = handle_segment_compare(flat, TargetedParams(metric_column="stay", segment_column="site",
                                                    aggregation="mean"), c)
    assert "all 2 groups are at 3.00" in r.answer_text
