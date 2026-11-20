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


# MySQL (5.8a). Payload shapes from PayloadsAllTheThings' MySQL injection page (MIT,
# github.com/swisskyrepo/PayloadsAllTheThings) and sqlmap's `versioned*` tamper idea (rewritten,
# not copied: sqlmap is GPL). sqlglot reads /*! … */, /*M! … */ and /*+ … */ as comments; the
# server runs what is inside them (verified with sqlglot 30.25).
MYSQL_ATTACKS = [
    "DELETE FROM orders", "UPDATE products SET cost = 0", "DROP TABLE returns", "TRUNCATE returns",
    "SELECT * FROM customers INTO OUTFILE '/tmp/c.csv'",
    "SELECT * FROM customers INTO DUMPFILE '/tmp/c'",
    "SELECT * FROM customers /*!50000 INTO OUTFILE '/tmp/c.csv' */",
    "SELECT 1 /*!, SLEEP(30) */", "SELECT * FROM t WHERE 1 /*!50000 AND SLEEP(100) */",
    "SELECT * FROM t /*M! INTO OUTFILE '/tmp/x' */",
    "SELECT /*+ MAX_EXECUTION_TIME(999999999) */ * FROM t",
    "SELECT SLEEP(30)", "SELECT BENCHMARK(100000000, MD5('a'))", "SELECT GET_LOCK('a', 10)",
    "SELECT RELEASE_LOCK('a')", "SELECT RELEASE_ALL_LOCKS()", "SELECT LOAD_FILE('/etc/passwd')",
    "SELECT MASTER_POS_WAIT('log', 4)", "SELECT SOURCE_POS_WAIT('log', 4)",
    "SELECT user, authentication_string FROM mysql.user",
    "SELECT * FROM performance_schema.threads", "SELECT * FROM sys.session",
    "SELECT * FROM t FOR UPDATE", "SELECT * FROM t LOCK IN SHARE MODE",
    "SELECT 1; DROP TABLE orders", "SELECT 1 # comment\n; DROP TABLE orders",
    # a quote inside plain comments must not hide the executable one between them (security review)
    "SELECT 1 /* ' */ /*!50000 , SLEEP(5) */ /* ' */",
    "SELECT 1 -- '\n/*! , SLEEP(5) */ -- '",
]


@pytest.mark.parametrize("sql", MYSQL_ATTACKS)
def test_the_mysql_attack_corpus_is_rejected_by_the_parse_guard_alone(sql):
    assert g.check_sql_readonly(sql, dialect="mysql"), sql


@pytest.mark.parametrize("sql", [
    "SELECT `Order Date`, SUM(`Net Sales`) FROM `orders` GROUP BY 1 ORDER BY 2 DESC LIMIT 10",
    "SELECT DATE_FORMAT(created_at, '%Y-%m') AS m, COUNT(*) FROM t GROUP BY m",
    "SELECT IFNULL(region, 'unknown'), GROUP_CONCAT(DISTINCT sku) FROM t GROUP BY 1",
    "SELECT a, RANK() OVER (ORDER BY b DESC) FROM t",
    "WITH x AS (SELECT 1 AS a) SELECT a FROM x",
    "SELECT a /* a plain comment */ FROM t",
    "SELECT table_name FROM information_schema.tables",   # metadata is no catalog secret
])
def test_ordinary_mysql_analytics_is_not_over_blocked(sql):
    assert g.check_sql_readonly(sql, dialect="mysql") is None


@pytest.mark.parametrize("dialect", ["postgres", "mysql", "sqlite"])
def test_every_denied_function_is_refused_in_every_dialect(dialect):
    """The deny check reads Anonymous nodes only: this proves every denied name (and a sample of
    each denied prefix) still parses as one, so a sqlglot upgrade that types one fails here, not
    in production (security review, 5.8a)."""
    names = sorted(g._SQL_DENY_FUNCS) + [p + "x" for p in g._SQL_DENY_PREFIXES]
    let_through = [n for n in names if g.check_sql_readonly(f"SELECT {n}(1)", dialect) is None]
    assert let_through == []


@pytest.mark.parametrize("sql", [
    "SELECT (ts_stat('SELECT to_tsvector(rolpassword) FROM pg_authid')).word",
    "SELECT ts_rewrite('a'::tsquery, 'SELECT t, s FROM aliases')",
    "SELECT some_future_fn('  with x as (select 1) select * from x')",
    "SELECT f(1, 'TABLE pg_authid')",
    # security review of that fix: a leading comment, a line comment, dollar quoting
    "SELECT f('/* x */ SELECT rolpassword FROM pg_authid')",
    "SELECT f('-- x\nSELECT 1')",
    "SELECT f($q$ /**/ (select 1) $q$)",
    "SELECT table_to_xmlschema('pg_authid', true, false, '')"])
def test_a_function_given_sql_as_text_is_refused(sql):
    """Security review (5.8a audit 2): a function that runs a query passed as text (ts_stat,
    ts_rewrite, query_to_xml) reads past every table rule. Named ones are denied, and the class:
    an unknown function whose text argument is a SQL statement."""
    assert g.check_sql_readonly(sql, "postgres") is not None


def test_sql_that_doesnt_parse_says_the_parsers_first_line():
    import sqlglot
    sql = "SELECT FROM WHERE ("
    try:
        sqlglot.parse(sql, read="postgres")
        first = None
    except sqlglot.errors.ParseError as exc:
        first = str(exc).splitlines()[0]
    assert first and g.check_sql_readonly(sql, "postgres") == f"SQL does not parse: {first}"


def test_the_sql_text_check_is_linear_on_hostile_text():
    """Security review: the first version's comment regex backtracked on '/*/*/*…'."""
    import time
    start = time.monotonic()
    g.check_sql_readonly("SELECT f('" + "/*" * 20_000 + "')", "postgres")
    assert time.monotonic() - start < 2


def test_a_function_given_ordinary_text_is_allowed():
    assert g.check_sql_readonly("SELECT initcap('selection'), to_char(now(), 'YYYY')", "postgres") is None
    # unknown functions given text that isn't a query: a word, nothing, a bracket, a lone quote
    for text_ in ["selection", "", "(", "it''s"]:
        assert g.check_sql_readonly(f"SELECT my_fn('{text_}')", "postgres") is None, text_


def test_sql_as_text_is_read_in_the_connections_dialect():
    """MySQL's `#` starts a comment: the query after it is found only when read as MySQL."""
    assert g.check_sql_readonly("SELECT my_fn('# c\nSELECT 1')", "mysql") is not None


def test_the_catalog_rules_follow_the_dialect_rows():
    assert g.check_sql_readonly("SELECT * FROM pg_catalog.pg_authid", "postgres") == \
        "the database's own tables are not readable: pg_catalog"
    assert g.check_sql_readonly("SELECT table_name FROM information_schema.tables", "postgres") is None
    assert g.check_sql_readonly("SELECT 1 /*!50000 , 2 */", "mysql") == \
        "executable comments and optimizer hints (/*! */, /*+ */) are not allowed"


def test_any_read_carrying_an_executable_comment_or_hint_is_refused():
    """The property behind the corpus rows: whatever read query, wherever /*!, /*M! or /*+
    appears outside a string literal, the guard refuses it (on every dialect: Postgres has no
    such comments, so nothing ordinary is lost)."""
    from hypothesis import given
    from hypothesis import strategies as st
    reads = st.sampled_from(["SELECT 1", "SELECT a FROM t", "SELECT a, b FROM t WHERE c > 1",
                             "SELECT COUNT(*) FROM t GROUP BY a"])
    opener = st.sampled_from(["/*!", "/*!50000", "/*M!", "/*M!100100", "/*+", "/*!  "])
    body = st.sampled_from([" SLEEP(5) ", " INTO OUTFILE '/tmp/x' ", " 1 ", "", " MAX_EXECUTION_TIME(1) "])

    @given(reads, opener, body, st.integers(min_value=0, max_value=40), st.sampled_from(["mysql", "postgres"]))
    def check(read, open_, inner, at, dialect):
        cut = min(at, len(read))
        sql = f"{read[:cut]} {open_}{inner}*/ {read[cut:]}"
        assert g.check_sql_readonly(sql, dialect=dialect), sql
    check()


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


# Regressions from the Phase 4 k=3 run (sql-20261028T024536Z): the model's own SQL.

def test_fanout_through_a_same_grain_subquery_is_flagged():
    sql = """
    SELECT SUM(oi.quantity * oi.unit_price * (1 - oi.discount_pct)) AS net_line_value,
           SUM(o_fees.shipping_fee) AS total_shipping_fees
    FROM shop.order_items oi
    JOIN shop.orders o ON o.order_id = oi.order_id
    JOIN (SELECT order_id, shipping_fee FROM shop.orders
          WHERE status = 'completed') o_fees ON o_fees.order_id = oi.order_id
    WHERE o.status = 'completed'"""
    assert "orders" in g.sql_fanout(sql, LAYERS["retail"])
    cte = ("WITH c AS (SELECT order_id, shipping_fee FROM shop.orders WHERE status = 'completed') "
           "SELECT SUM(c.shipping_fee) FROM c JOIN shop.order_items oi ON oi.order_id = c.order_id")
    assert g.sql_fanout(cte, LAYERS["retail"])
    grouped = ("SELECT SUM(f.fee) FROM shop.order_items oi JOIN (SELECT order_id, SUM(shipping_fee) "
               "AS fee FROM shop.orders GROUP BY order_id) f ON f.order_id = oi.order_id")
    assert g.sql_fanout(grouped, LAYERS["retail"]) is None   # a grouped subquery has its own grain


def test_metric_with_unqualified_columns_in_a_one_table_scope_counts():
    layer = LAYERS["banking"]
    sql = """
    SELECT a.branch, COALESCE(SUM(f.fees), 0) AS fees
    FROM bank.accounts a
    LEFT JOIN (SELECT account_id, -SUM(amount) FILTER (WHERE category = 'fees') AS fees
               FROM bank.transactions GROUP BY account_id) f ON f.account_id = a.account_id
    GROUP BY a.branch"""
    assert g.sql_uses_metric(sql, layer.metric("fee_income"), layer)
    assert g.sql_fanout(sql, layer) is None


def test_metric_filter_may_add_conditions_but_not_drop_them():
    layer = LAYERS["banking"]
    fees = layer.metric("fee_income")
    base = ("SELECT a.branch, {agg} FROM bank.accounts a LEFT JOIN bank.transactions t "
            "ON t.account_id = a.account_id GROUP BY a.branch")
    extra = "-SUM(t.amount) FILTER (WHERE t.category = 'fees' AND t.posted_date >= DATE '2025-01-01')"
    assert g.sql_uses_metric(base.format(agg=extra), fees, layer)   # sql-20261028T025054Z
    dropped = "-SUM(t.amount) FILTER (WHERE t.posted_date >= DATE '2025-01-01')"
    assert not g.sql_uses_metric(base.format(agg=dropped), fees, layer)
    retail = LAYERS["retail"]
    aov = retail.metric("average_order_value")   # Div of two FILTERs: both checked
    sql = ("SELECT SUM(oi.quantity * oi.unit_price * (1 - oi.discount_pct)) FILTER (WHERE "
           "o.status = 'completed' AND o.channel = 'app') / COUNT(DISTINCT o.order_id) FILTER "
           "(WHERE o.status = 'completed' AND o.channel = 'app') FROM shop.order_items oi "
           "JOIN shop.orders o ON o.order_id = oi.order_id")
    assert g.sql_uses_metric(sql, aov, retail)
    assert not g.sql_uses_metric(sql.replace("o.status = 'completed' AND ", "", 1), aov, retail)


@pg
async def test_queries_leave_no_idle_connections_on_the_users_database(engine):
    from connectors.sql_connector import SQLConnector
    from orchestrator.state import ConnectorConfig, ConnectorType
    conn = SQLConnector(ConnectorConfig(type=ConnectorType.SQL, connection_string=PG_URL))

    def idle():
        with engine.connect() as c:
            return c.execute(text("SELECT count(*) FROM pg_stat_activity WHERE "
                                  "datname = current_database() AND state = 'idle' "
                                  "AND pid <> pg_backend_pid()")).scalar()
    before = idle()
    for _ in range(5):
        await conn.query("SELECT 1")
    assert idle() <= before
