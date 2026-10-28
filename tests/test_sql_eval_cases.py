"""Gates on the SQL eval set itself (Phase 4): a benchmark is only as good as its cases.
The PG half needs TEST_PG_URL (CI always sets it); it rebuilds the seeded schemas."""
import os
import re
from collections import Counter
from pathlib import Path

import pytest
import sqlglot
import yaml

import semantic_layer
from evals.scoring import result_matches
from guardrails import check_sql_readonly

ROOT = Path(__file__).parent.parent
CASES = [yaml.safe_load(p.read_text()) for p in sorted((ROOT / "evals/cases/sql").glob("*.yaml"))]
ANSWERABLE = [c for c in CASES if c["category"] != "attack"]
LAYERS = {d: semantic_layer.load(ROOT / f"evals/sql/{d}.yaml") for d in ("retail", "banking")}
PG_URL = os.environ.get("TEST_PG_URL")
if os.environ.get("CI") and not PG_URL:   # never let CI pass by skipping the database half
    raise RuntimeError("CI must set TEST_PG_URL")


def _ids(cases):
    return [c["id"] for c in cases]


def test_case_mix_matches_the_plan():
    assert len(CASES) == 50
    assert Counter(c["category"] for c in CASES) == {
        "simple": 10, "join": 10, "fanout": 8, "metric": 10, "metric_not": 2, "attack": 10}
    assert Counter(c["domain"] for c in CASES) == {"retail": 36, "banking": 14}


@pytest.mark.parametrize("case", CASES, ids=_ids(CASES))
def test_case_is_well_formed(case):
    allowed = {"id", "domain", "category", "question", "golden_sql", "trap_sql", "ordered",
               "top_n", "metric", "note"}
    assert set(case) <= allowed
    if case["category"] == "attack":
        assert "golden_sql" not in case
        return
    assert check_sql_readonly(case["golden_sql"], dialect="postgres") is None
    if "trap_sql" in case:
        assert check_sql_readonly(case["trap_sql"], dialect="postgres") is None
    assert "now()" not in case["golden_sql"].lower(), "goldens must not depend on today's date"
    if case["category"] == "metric":
        assert LAYERS[case["domain"]].metric(case["metric"])
    if case["category"] in ("fanout", "metric", "metric_not"):
        assert "trap_sql" in case, "a trap case must prove its trap"


def _norm(sql: str) -> str:
    return sqlglot.transpile(sql, read="postgres", pretty=False)[0].lower()


def test_golden_sql_never_leaks_to_the_agent():
    """ADR-0002: golden cases are never prompt examples or verified queries."""
    shown = [_norm(q.sql) for layer in LAYERS.values() for q in layer.verified_queries]
    prompts = " ".join(p.read_text().lower() for p in (ROOT / "prompts").rglob("*.md"))
    for c in ANSWERABLE:
        golden = _norm(c["golden_sql"])
        assert golden not in shown, c["id"]
        assert re.sub(r"\s+", " ", c["golden_sql"].strip().lower()) not in prompts, c["id"]


# ── needs Postgres ─────────────────────────────────────────────────────────────

pg = pytest.mark.skipif(not PG_URL, reason="set TEST_PG_URL to a scratch Postgres (CI does)")


@pytest.fixture(scope="module")
def engine():
    from sqlalchemy import create_engine

    from evals import sqlgen
    sqlgen.load(PG_URL)
    return create_engine(PG_URL)


def _rows(engine, sql):
    from sqlalchemy import text
    with engine.connect() as conn:
        return [tuple(r) for r in conn.execute(text(sql))]


@pg
@pytest.mark.parametrize("case", ANSWERABLE, ids=_ids(ANSWERABLE))
def test_golden_runs_is_informative_and_trap_traps(engine, case):
    gold = _rows(engine, case["golden_sql"])
    assert gold and any(v is not None for r in gold for v in r), "empty golden matches anything"
    if "trap_sql" in case:
        ok, _ = result_matches(_rows(engine, case["trap_sql"]), gold, case.get("ordered", False))
        assert not ok, "the naive SQL gives the golden answer: this trap tests nothing"
    if n := case.get("top_n"):
        wider = _rows(engine, case["golden_sql"].replace(f"LIMIT {n}", f"LIMIT {n + 1}"))
        assert wider[n - 1][-1] != wider[n][-1], "tie at the top-N boundary"


@pg
@pytest.mark.parametrize("domain", sorted(LAYERS))
def test_layers_match_the_seeded_database(engine, domain):
    assert semantic_layer.check_against_db(LAYERS[domain], engine) == []


@pg
def test_fingerprint_is_stable(engine):
    from evals.sqlgen import fingerprint
    assert fingerprint(engine) == fingerprint(engine)
