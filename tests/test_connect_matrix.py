"""Connect or fail correctly, on every engine (5.8b hardening): what a user sees when they press
"Test and connect", through the route the form calls, on the real databases. Each failure says
its own cause, never the password, and answers within a bound.

Found by the senior audit: the local Postgres trusted every password, so "wrong password" had
never been tried here (scripts/test_dbs.sh now checks it, as CI does); SQL Server said "Login
timeout expired" for a host that doesn't answer."""
import os
import time

import pytest
from sqlalchemy.engine import make_url

from tests.test_workspace_api import _client

ENV = {"postgresql": "TEST_PG_URL", "mysql": "TEST_MYSQL_URL", "mariadb": "TEST_MARIADB_URL",
       "mssql": "TEST_MSSQL_READER_URL"}
SECRET = "Wr0ng-pw-matrix-5_8b"


def _url(name: str, **change) -> str:
    if not (raw := os.environ.get(ENV[name])):
        pytest.skip(f"no {name} database here (CI sets one)")
    url = make_url(raw.replace("+psycopg", "").replace("+pymysql", "").replace("+pyodbc", ""))
    return url.set(**change).render_as_string(hide_password=False)


async def _test(cs: str) -> tuple[bool, str, float]:
    from orchestrator.runner import in_memory_deps
    started = time.monotonic()
    async with _client(in_memory_deps()) as c:
        r = (await c.post("/connectors/test", json={"type": "sql", "connection_string": cs})).json()
    return r["success"], r["message"], time.monotonic() - started


# per engine: what each failure must say (the database's own words for its cause)
SAID = {
    "postgresql": {"password": "password authentication failed", "database": "does not exist",
                   "listening": "refused", "host": "resolve"},
    "mysql": {"password": "Access denied", "database": "Unknown database",
              "listening": "Can't connect", "host": "Can't connect"},
    "mariadb": {"password": "Access denied", "database": "Unknown database",
                "listening": "Can't connect", "host": "Can't connect"},
    "mssql": {"password": "Login failed", "database": "Cannot open database",
              "listening": "can't reach", "host": "can't reach"},
}
CASES = {"password": {"password": SECRET}, "database": {"database": "no_such_db_5_8b"},
         "listening": {"port": 1}, "host": {"host": "db.invalid"}}


@pytest.mark.parametrize("name", list(ENV))
async def test_a_good_string_connects(name):
    ok, said, took = await _test(_url(name))
    assert ok and said == "Connection successful" and took < 30


@pytest.mark.parametrize("case", list(CASES))
@pytest.mark.parametrize("name", list(ENV))
async def test_each_failure_names_its_own_cause_never_the_password(name, case):
    ok, said, took = await _test(_url(name, **CASES[case]))
    assert not ok and SAID[name][case].lower() in said.lower(), said
    assert SECRET not in said and "ingestion:" not in said
    assert took < 30, f"{name} {case} took {took:.0f} s"


async def test_a_sql_server_login_that_can_write_is_refused_in_the_form():
    if not os.environ.get("TEST_MSSQL_WRITER_URL"):
        pytest.skip("no SQL Server here (CI sets one)")
    ok, said, _ = await _test(os.environ["TEST_MSSQL_WRITER_URL"])
    assert not ok and "read-only login" in said


async def test_a_mysql_string_pointed_at_mariadb_connects():
    ok, said, _ = await _test(_url("mariadb", drivername="mysql"))
    assert ok, said


@pytest.mark.parametrize("name", ["mysql", "mariadb"])
def test_tls_required_gives_an_encrypted_session(name):
    """ssl_mode=REQUIRED connects only encrypted: the session itself reports its cipher (MariaDB
    11.4 and MySQL 8 serve TLS by default; the matrix's first draft assumed MariaDB didn't)."""
    from connectors.sql_connector import SQLConnector, run_readonly
    from orchestrator.state import ConnectorConfig, ConnectorType
    url = _url(name, query={"ssl_mode": "REQUIRED"})
    engine = SQLConnector(ConnectorConfig(type=ConnectorType.SQL, connection_string=url))._engine()
    (_, cipher), = run_readonly(engine, "SHOW SESSION STATUS LIKE 'Ssl_cipher'").rows
    assert cipher, "REQUIRED connected without TLS"
