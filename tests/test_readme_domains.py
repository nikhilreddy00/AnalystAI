"""The README lists exactly the domains the committed Phase 5.5 reports passed (ROADMAP 5.5 "Done
when"): every run → the first list; a majority of runs → the second."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "evals" / "reports"
DOMAIN_K3 = "analysis-20261101T153341Z-rescored.json"   # the 22 domain cases, k=3
ALL_K1 = "analysis-20261101T152434Z-rescored.json"      # all 68 cases (retail's 46), k=1
E2E = "e2e-20261101T152850Z-rescored.json"
NAMES = {"retail": "retail", "banking": "banking", "healthcare": "healthcare", "insurance": "insurance",
         "hr": "HR", "logistics": "logistics", "iot": "IoT", "saas": "SaaS"}


def _runs(name):
    return json.loads((REPORTS / name).read_text())["runs"]


def _domains():
    import yaml
    cases = {}
    for p in (ROOT / "evals" / "cases" / "analysis").glob("*.yaml"):
        c = yaml.safe_load(p.read_text())
        cases[c["id"]] = c.get("domain", "retail")
    return cases


def test_the_readme_domain_lists_match_the_reports():
    domain = _domains()
    k3, k1 = _runs(DOMAIN_K3), _runs(ALL_K1)
    runs = {cid: rs for cid, rs in k1.items() if cid not in k3} | k3
    every, most = set(), set()
    for d in set(domain.values()):
        mine = [rs for cid, rs in runs.items() if domain.get(cid) == d]
        if mine and all(r["passed"] for rs in mine for r in rs):
            every.add(d)
        elif mine and all(sum(r["passed"] for r in rs) * 2 > len(rs) for rs in mine):
            most.add(d)
    e2e = _runs(E2E)
    assert all(r["passed"] for rs in e2e.values() for r in rs)
    block = (ROOT / "README.md").read_text().split("<!-- domains:start -->")[1].split("<!-- domains:end -->")[0]
    first, second = block.split("Passed by majority")
    assert (_listed(first), _listed(second)) == (every, most)


def _listed(text: str) -> set[str]:
    bold = " ".join(re.findall(r"\*\*([^*]+)\*\*", text))
    return {d for d, n in NAMES.items() if re.search(rf"\b{n}\b", bold)}
