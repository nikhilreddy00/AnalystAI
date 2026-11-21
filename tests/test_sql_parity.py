"""Right answers on every engine (5.8b hardening): the SQL eval's known-right questions, asked of
MySQL, MariaDB and SQL Server through the production path (`SQLConnector.query` → the guard, the
read-only session, `QueryResult.frame`), give Postgres's answer.

Before this, the 48 golden questions ran on Postgres only, and each other engine was proven by
one paid E2E question. The same seed data goes into every engine with the same column types
(the Postgres DDL transpiled), and each golden query is rewritten for the engine by sqlglot."""
import os
from pathlib import Path

import pytest
import sqlglot
import yaml
from sqlglot import exp

from evals import sqlgen
from evals.scoring import result_matches
from tests.test_connectors_sources import _admin, _each_dialect, _sql

ROOT_DIR = Path(__file__).parent.parent
ENGINES = ["mysql", "mariadb", "mssql"]
WRITE = {"postgresql": "postgres", "mysql": "mysql", "mariadb": "mysql", "mssql": "tsql"}
CASES = [c for c in (yaml.safe_load(p.read_text())
                     for p in sorted((ROOT_DIR / "evals/cases/sql").glob("*.yaml")))
         if c["category"] != "attack"]


# Where sqlglot's transpile of a golden is wrong for the engine (not the product's reading): the
# model writes the engine's own SQL there, never a Postgres DATE_TRUNC to translate.
SQLGLOT_CANT = {("retail-metric-05", "mysql"): "DATE_TRUNC(month) → DATE_ADD('0000-01-01'…): invalid",
                ("retail-metric-05", "mariadb"): "DATE_TRUNC(month) → DATE_ADD('0000-01-01'…): invalid",
                ("retail-metric-05", "mssql"): "DATETRUNC in GROUP BY by its alias: invalid"}


def _for(sql: str, name: str) -> str:
    """A Postgres query (or DDL) as the engine writes it. MySQL and MariaDB read one database, so
    schema `shop` table `orders` is the table `shop_orders` there; SQL Server keeps schemas.
    `agg(x) FILTER (WHERE c)` (Postgres only) is `agg(CASE WHEN c THEN x END)`, the same answer."""
    def rename(node):
        if isinstance(node, exp.Table) and node.db and name in ("mysql", "mariadb"):
            return exp.table_(f"{node.db}_{node.name}", alias=node.alias or None)
        if isinstance(node, exp.Reference):   # a foreign key names the other table: not needed
            return None
        if isinstance(node, exp.Filter) and isinstance(node.this, exp.AggFunc):
            agg, cond = node.this.copy(), node.expression.this
            inner = agg.this.expressions[0] if isinstance(agg.this, exp.Distinct) else agg.this
            if isinstance(inner, exp.Star):   # COUNT(*) FILTER: count the rows that match
                inner = exp.Literal.number(1)
            case = exp.Case(ifs=[exp.If(this=cond.copy(), true=inner.copy())])
            agg.set("this", exp.Distinct(expressions=[case]) if isinstance(agg.this, exp.Distinct)
                    else case)
            return agg
        return node
    return ";\n".join(s.transform(rename).sql(dialect=WRITE[name])
                      for s in sqlglot.parse(sql, read="postgres") if s is not None)


def _seed(tmp_path, name: str) -> str:
    """The SQL eval's shop and bank tables in this engine; its reader URL."""
    from sqlalchemy import text
    url = _each_dialect(tmp_path, name)
    if name == "postgresql":
        sqlgen.load(os.environ["TEST_PG_URL"])
        return url
    tables = {"shop": sqlgen.make_shop(), "bank": sqlgen.make_bank()}
    creates = [s for s in sqlglot.parse(sqlgen.DDL, read="postgres")
               if isinstance(s, exp.Create) and s.kind == "TABLE"]
    admin = _admin(tmp_path, name)
    if name == "mssql":   # rows in bulk: 140k rows one by one take minutes over ODBC
        from sqlalchemy import create_engine
        admin = create_engine(admin.url, fast_executemany=True)
    with admin.begin() as c:
        for create in reversed(creates):
            c.execute(text(_for(f"DROP TABLE IF EXISTS {create.this.this.sql('postgres')}", name)))
        if name == "mssql":
            for schema in tables:
                c.execute(text(f"IF SCHEMA_ID('{schema}') IS NULL EXEC('CREATE SCHEMA {schema}')"))
        for create in creates:
            c.execute(text(_for(create.sql("postgres"), name)))
        for schema, frames in tables.items():
            for table, df in frames.items():
                where = {"name": f"{schema}_{table}"} if name != "mssql" else \
                    {"name": table, "schema": schema}
                df.to_sql(**where, con=c, if_exists="append", index=False, chunksize=2000)
    return url


@pytest.fixture(scope="module")
def answers(tmp_path_factory):
    """Postgres's answer to each golden question, read by the driver alone: the reference must
    not share the product's reading (a plant in `QueryResult.frame` passed while both sides went
    through it)."""
    from sqlalchemy import create_engine, text
    _seed(tmp_path_factory.mktemp("pg"), "postgresql")
    engine = create_engine(os.environ["TEST_PG_URL"])
    with engine.connect() as c:
        return {case["id"]: [tuple(r) for r in c.execute(text(case["golden_sql"]))]
                for case in CASES}


def _rows(result) -> list[tuple]:
    df = result.frame()
    return [tuple(None if v != v else v for v in r) for r in df.itertuples(index=False)]   # NaN


@pytest.mark.parametrize("name", ENGINES)
async def test_every_golden_question_gets_postgres_answer_on_each_engine(tmp_path, name, answers):
    url = _seed(tmp_path, name)
    conn = _sql(url, None)
    wrong = {}
    for case in CASES:
        if (case["id"], name) in SQLGLOT_CANT:
            continue
        sql = _for(case["golden_sql"], name)
        try:
            got = _rows(await conn.query(sql))
        except Exception as exc:   # said with the case: which query, which engine, why
            wrong[case["id"]] = f"{type(exc).__name__}: {str(exc)[:160]} | {sql[:200]}"
            continue
        ok, why = result_matches(got, answers[case["id"]], case.get("ordered", False))
        if not ok:
            wrong[case["id"]] = f"{why} | {sql[:200]}"
    assert wrong == {}, "\n".join(f"{k}: {v}" for k, v in wrong.items())
    # the exceptions are exactly sqlglot's, named: one still passing is no longer one
    assert len([c for c in CASES if (c["id"], name) not in SQLGLOT_CANT]) >= 47
