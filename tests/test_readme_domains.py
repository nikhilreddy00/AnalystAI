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
    first, rest = block.split("Passed by majority")
    second, depth = rest.split("Domain depth")
    assert (_listed(first), _listed(second)) == (every, most)
    # Phase 5.7: a domain listed with depth passed every run of its committed depth reports (a
    # later re-run of a case, after a diagnosed fix, replaces its earlier runs)
    assert _listed(depth) == set(DEPTH)
    for d, names in DEPTH.items():
        runs = {}
        for name in names:
            runs |= _runs(name)
        assert all(r["passed"] for rs in runs.values() for r in rs), d


DEPTH = {"retail": ["analysis-20261102T002211Z.json",           # 7 retail cases, k=3
                    "e2e-20261102T002956Z-rescored.json",       # 25 E2E cases, k=1
                    "e2e-20261102T004453Z-rescored.json",       # the 10 re-run after their fixes
                    "intake-20261102T004821Z.json",             # 13 intake cases, k=3
                    "intake-20261102T011951Z.json",             # intake-retail-07 k=3 after its note named the KPI
                    "insight-20261102T004752Z.json"],           # the 5 changed overviews
         "banking": ["analysis-20261102T203011Z.json",          # 7 banking cases, k=3
                     "analysis-20261102T203339Z.json",          # bank-4/5/6/7 k=3 after their fixes
                     "analysis-20261102T204011Z.json",          # bank-5 k=3 after its fix
                     "intake-20261102T205448Z.json",            # 12 banking + 8 changed intake cases, k=3
                     "intake-20261102T205953Z.json",            # intake-bank-08 k=3 after its fix
                     "insight-20261102T210024Z.json",           # 2 banking overviews
                     "quality-20261102T210206Z.json",           # 2 seeded banking reports
                     "e2e-20261102T212128Z-rescored.json",      # 10 banking E2E cases, k=1
                     "e2e-20261102T213308Z.json"],              # 4 retail E2E cases whose context changed
         "healthcare": ["analysis-20261103T174405Z-rescored.json",   # smoke: health-4/6/7 k=1
                        "analysis-20261103T174902Z.json",            # health-4 after its fix
                        "analysis-20261103T175226Z-rescored.json",   # 8 healthcare cases, k=3
                        "analysis-20261103T180918Z.json",            # health-8 k=3 on prompt v9
                        "intake-20261103T175225Z.json",              # 14 healthcare intake cases
                        "intake-20261103T194212Z.json",              # health-10/12 k=3 after the glossary fix
                        "insight-20261103T182036Z.json",             # 2 healthcare overviews
                        "quality-20261103T182408Z.json",             # 2 seeded healthcare reports
                        "e2e-20261103T184600Z-rescored.json",        # 9 healthcare E2E cases, k=1
                        "e2e-20261103T191033Z.json",                 # 4 re-gated after the audit
                        "e2e-20261103T192807Z.json"],                # e2e-52 after its fix
         "insurance": ["analysis-20261104T011708Z-rescored.json",    # smoke: ins-6/8 k=1
                       "analysis-20261104T012924Z.json",             # ins-1..3 re-measured k=1
                       "analysis-20261104T012925Z.json",             # ins-4..9 k=3
                       "analysis-20261104T013543Z.json",             # ins-6/9 k=3 after the brief fix
                       "analysis-20261104T013802Z.json",             # ins-4/5/7/8 k=3 on that brief
                       "analysis-20261104T031811Z.json",             # ins-1..9 k=1 after the audits
                       "intake-20261104T012924Z.json",               # 12 insurance intake cases
                       "intake-20261104T013542Z.json",               # ins-09/11 k=3 after the glossary fix
                       "insight-20261104T014955Z.json",              # ins-42/44 re-measured + 45/46
                       "insight-20261104T015458Z.json",              # 45/46 on the final harness
                       "insight-20261104T015642Z.json",              # 45 after the entity grain
                       "insight-20261104T031810Z.json",              # 45/46 after the audits
                       "quality-20261104T015827Z.json",              # 2 seeded insurance reports
                       "e2e-20261104T021044Z-rescored.json",         # 9 insurance E2E cases, k=1
                       "e2e-20261104T021614Z.json",                  # e2e-64/65 k=3 after their fixes
                       "e2e-20261104T021756Z.json",                  # e2e-65 k=3 after the totals rule
                       "e2e-20261104T033752Z-rescored.json"]}        # 9 + 3 cross-domain after the audits


def _listed(text: str) -> set[str]:
    bold = " ".join(re.findall(r"\*\*([^*]+)\*\*", text))
    return {d for d, n in NAMES.items() if re.search(rf"\b{n}\b", bold)}
