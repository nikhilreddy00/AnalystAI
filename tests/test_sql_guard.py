"""The four SQL safety layers (connectors/sql_connector.py), each tested on its own, plus the
fan-out and metric checks run over the eval cases. PG tests need TEST_PG_URL (CI sets it)."""
import os
from pathlib import Path

import pytest
import yaml
from sqlalchemy import create_engine, text

import guardrails as g
import semantic_layer
from connectors.sql_connector import run_readonly

ROOT = Path(__file__).parent.parent
PG_URL = os.environ.get("TEST_PG_URL")
pg = pytest.mark.skipif(not PG_URL, reason="set TEST_PG_URL to a scratch Postgres (CI does)")
LAYERS = {d: semantic_layer.load(ROOT / f"evals/sql/{d}.yaml") for d in ("retail", "banking")}
CASES = [yaml.safe_load(p.read_text()) for p in sorted((ROOT / "evals/cases/sql").glob("*.yaml"))]

ATTACKS = [
    "DELETE FROM shop.orders",
    "UPDATE shop.products SET unit_cost = unit_cost * 1.1",
    "INSERT INTO shop.orders SELECT * FROM shop.orders",
    "DROP TABLE shop.returns",
    "TRUNCATE shop.returns",
    "ALTER TABLE shop.orders ADD COLUMN x int",
    "CREATE TABLE top AS SELECT * FROM shop.customers",
    "SELECT * INTO top_customers FROM shop.customers",
    "GRANT ALL ON ALL TABLES IN SCHEMA shop TO PUBLIC",
    "REVOKE SELECT ON shop.orders FROM PUBLIC",
    "COPY shop.customers TO '/tmp/c.csv'",
    "EXPLAIN ANALYZE DELETE FROM shop.returns",
    "SELECT 1; DROP TABLE shop.orders",
    "SELECT count(*) FROM shop.orders; DROP TABLE shop.orders; --",
    "WITH d AS (DELETE FROM shop.orders RETURNING *) SELECT * FROM d",
    "WITH u AS (UPDATE bank.transactions SET amount = 0 RETURNING 1) SELECT count(*) FROM u",
    "SELECT setval('shop.order_seq', 1)",
    "SELECT nextval('shop.order_seq')",
    "SELECT pg_read_file('/etc/passwd')",
    "SELECT lo_export(1234, '/tmp/x')",
    "SELECT dblink_exec('host=evil', 'DROP TABLE x')",
    "SELECT set_config('default_transaction_read_only', 'off', false)",
    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity",
    "SELECT pg_sleep(600)",
    "SELECT query_to_xml('DELETE FROM shop.orders', true, true, '')",
    "SELECT * FROM shop.orders FOR UPDATE",
    "SELECT pg_advisory_lock(1)",
    "CALL some_procedure()",
    "DO $$ BEGIN DELETE FROM shop.orders; END $$",
    "VACUUM shop.orders",
]


@pytest.mark.parametrize("sql", ATTACKS)
def test_attack_corpus_is_rejected_by_the_parse_guard_alone(sql):
    assert g.check_sql_readonly(sql, dialect="postgres"), sql


@pytest.mark.parametrize("sql", [
    "SELECT SUM(x) FILTER (WHERE y = 'a') FROM t",
    "SELECT date_trunc('month', d)::date, count(*) FROM t GROUP BY 1",
    "SELECT a, rank() OVER (ORDER BY b DESC) FROM t",
    "WITH x AS (SELECT 1 AS a) SELECT a FROM x WHERE EXISTS (SELECT 1 FROM t)",
    "SELECT coalesce(region, 'unknown'), pg_typeof(1) FROM shop.customers",
])
def test_ordinary_analytics_sql_is_not_over_blocked(sql):
    assert g.check_sql_readonly(sql, dialect="postgres") is None


def test_fanout_check_flags_every_trap_and_no_golden():
    for c in CASES:
        if "golden_sql" not in c:
            continue
        layer = LAYERS[c["domain"]]
        assert g.sql_fanout(c["golden_sql"], layer) is None, c["id"]
        if c["category"] == "fanout":
            assert g.sql_fanout(c["trap_sql"], layer), c["id"]


def test_fanout_allows_line_grain_products_and_flags_summed_header_measures():
    layer = LAYERS["retail"]
    ok = ("SELECT SUM(oi.quantity * (oi.unit_price - p.unit_cost)) FROM shop.order_items oi "
          "JOIN shop.products p ON p.product_id = oi.product_id")
    assert g.sql_fanout(ok, layer) is None
    count_distinct = ("SELECT COUNT(DISTINCT o.order_id) FROM shop.orders o "
                      "JOIN shop.order_items oi ON oi.order_id = o.order_id")
    assert g.sql_fanout(count_distinct, layer) is None
    assert g.sql_fanout(count_distinct.replace("DISTINCT ", ""), layer)


def test_metric_check_is_alias_insensitive():
    layer = LAYERS["retail"]
    metric = layer.metric("net_revenue")
    for c in CASES:
        if c.get("metric"):
            dom = LAYERS[c["domain"]]
            assert g.sql_uses_metric(c["golden_sql"], dom.metric(c["metric"]), dom), c["id"]
    naive = ("SELECT SUM(oi.quantity * oi.unit_price) FROM shop.order_items oi "
             "JOIN shop.orders o ON o.order_id = oi.order_id")
    assert not g.sql_uses_metric(naive, metric, layer)


def test_sqlite_query_only_blocks_a_write_the_guard_never_saw():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE t (a int)")
        conn.exec_driver_sql("INSERT INTO t VALUES (1)")
    with pytest.raises(Exception, match="readonly|read-only|query_only"):
        run_readonly(engine, "DELETE FROM t")
    assert run_readonly(engine, "SELECT count(*) FROM t").rows == [(1,)]


def test_row_cap_truncates_and_says_so():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE t (a int)")
        conn.exec_driver_sql("INSERT INTO t VALUES (1), (2), (3)")
    res = run_readonly(engine, "SELECT a FROM t", max_rows=2)
    assert res.rows == [(1,), (2,)] and res.truncated and res.columns == ["a"]


# ── Postgres ───────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def engine():
    from evals import sqlgen
    sqlgen.load(PG_URL)
    return create_engine(PG_URL)


@pg
@pytest.mark.parametrize("sql", [
    "DELETE FROM shop.returns",
    "WITH d AS (DELETE FROM shop.returns RETURNING 1) SELECT count(*) FROM d",
    "SELECT nextval('shop.order_seq')",
    "SELECT * INTO shop.copy FROM shop.returns",
])
def test_read_only_transaction_blocks_writes_with_the_guard_bypassed(engine, sql):
    from evals.sqlgen import fingerprint
    before = fingerprint(engine)
    with pytest.raises(Exception, match="read-only transaction"):
        run_readonly(engine, sql)
    assert fingerprint(engine) == before


@pg
def test_statement_timeout_stops_a_slow_query(engine):
    with pytest.raises(Exception, match="statement timeout"):
        run_readonly(engine, "SELECT pg_sleep(5)", timeout_s=1)


@pg
def test_connection_is_usable_after_a_read_only_query(engine):
    run_readonly(engine, "SELECT 1")
    with engine.connect() as conn:   # the pooled connection must not stay read-only
        assert conn.execute(text("SHOW transaction_read_only")).scalar() == "off"
