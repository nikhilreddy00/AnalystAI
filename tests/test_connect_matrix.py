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


# ── SaaS imports (5.8c): every vendor, connect or fail correctly, through the route ─────────────
# The recorded real pages answer; a fault is put in front of them. Each failure is said in its own
# words, within a bound, and a refresh that fails keeps every last good copy.

IMPORTS = {"stripe": {"vendor": "stripe", "key": "rk_test_51CANARYmatrixKEY0000000000"},
           "shopify": {"vendor": "shopify", "shop": "analystai-test.myshopify.com",
                       "client_id": "cid-matrix", "key": "shpss_CANARYmatrixSECRET000000000"}}


@pytest.fixture(autouse=True)
def _fresh_limits():
    """Each case its own rate limits (connecting is 10 a minute per user)."""
    from api.rate_limit import limiter
    limiter.reset()


def _faulty(handler, fault):
    """The recorded vendor, except: `fault` (request → a response, or None to answer as recorded)."""
    return lambda request: fault(request) or handler(request)


def _status(code, **headers):
    import httpx
    return lambda request: httpx.Response(code, json={"errors": "x", "error": {"message": "x"}},
                                          headers=headers)


def _api(request) -> bool:   # a data request, never the token mint
    return not request.url.path.endswith("/oauth/access_token")


CONNECT = {   # case → (fault, the words it must say)
    "ok": (lambda r: None, None),
    "wrong key": (lambda r: _status(401)(r) if _api(r) or "shopify" in str(r.url) else None,
                  "rejected the key"),
    "missing permission": (lambda r: _status(403)(r) if _api(r) else None, "can't read"),
    "vendor down": (lambda r: _status(503)(r) if _api(r) else None, "didn't answer properly"),
}


@pytest.mark.parametrize("case", list(CONNECT))
@pytest.mark.parametrize("vendor", list(IMPORTS))
async def test_an_import_connects_or_says_why(vendor, case, monkeypatch):
    from orchestrator.runner import in_memory_deps
    from tests.fake_network import serve
    from tests.test_imports import _settle, recorded
    fault, words = CONNECT[case]
    serve(monkeypatch, _faulty(recorded(vendor), fault))
    started = time.monotonic()
    async with _client(in_memory_deps()) as c:
        r = await c.post("/api/imports", json=IMPORTS[vendor])
        await _settle(c)
    assert time.monotonic() - started < 10
    if words is None:
        assert r.status_code == 201, r.text
    else:
        assert r.status_code == 400 and words in r.json()["detail"], r.text
        assert IMPORTS[vendor]["key"] not in r.text


async def test_a_dns_failure_is_said_as_the_network(monkeypatch):
    """Network down: the vendor's host doesn't resolve."""
    from orchestrator.runner import in_memory_deps
    from tests.fake_network import serve
    from tests.test_imports import recorded
    import socket

    def no_such_host(host, n):
        raise socket.gaierror(socket.EAI_NONAME, "nodename nor servname provided")
    serve(monkeypatch, recorded("stripe"), dns=no_such_host)
    async with _client(in_memory_deps()) as c:
        r = await c.post("/api/imports", json=IMPORTS["stripe"])
    assert r.status_code == 400 and "Couldn't reach Stripe" in r.json()["detail"]


async def test_a_429_is_waited_out_once(monkeypatch):
    import connectors.imports as imports
    from orchestrator.runner import in_memory_deps
    from tests.fake_network import serve
    from tests.test_imports import _settle, recorded
    monkeypatch.setattr(imports, "RETRY_WAIT_S", 0)
    seen = []
    def once(request):
        if _api(request) and not seen:
            seen.append(1)
            return _status(429, **{"Retry-After": "0"})(request)
    serve(monkeypatch, _faulty(recorded("stripe"), once))
    async with _client(in_memory_deps()) as c:
        r = await c.post("/api/imports", json=IMPORTS["stripe"])
        await _settle(c)
    assert r.status_code == 201 and seen


@pytest.mark.parametrize("vendor", list(IMPORTS))
async def test_a_refresh_that_fails_or_is_interrupted_keeps_every_last_good_copy(vendor, monkeypatch):
    """A good import, then a refresh that the vendor fails (503), then one interrupted (a
    shutdown cancels its task): each is said, the state failed, every table's copy unchanged."""
    import asyncio

    import memory.session_store as ss
    from orchestrator.runner import in_memory_deps
    from tests.fake_network import serve
    from tests.test_imports import _settle, recorded
    monkeypatch.setattr(ss, "REFRESH_GAP_S", 0)
    serve(monkeypatch, recorded(vendor))
    async with _client(in_memory_deps()) as c:
        made = (await c.post("/api/imports", json=IMPORTS[vendor])).json()
        await _settle(c)
        good = (await c.get(f"/api/imports/{made['id']}")).json()
        latest = {t["name"]: (await c.get(f"/api/datasets/{t['dataset_id']}")).json()["latest"]["id"]
                  for t in good["tables"]}
        serve(monkeypatch, _faulty(recorded(vendor), lambda r: _status(503)(r) if _api(r) else None))
        await c.post(f"/api/imports/{made['id']}/refresh")
        await _settle(c)
        failed = (await c.get(f"/api/imports/{made['id']}")).json()
        assert failed["state"] == "failed"
        assert all("didn't answer properly" in t["said"] for t in failed["tables"])
        serve(monkeypatch, recorded(vendor))
        await c.post(f"/api/imports/{made['id']}/refresh")
        await asyncio.sleep(0.01)   # under way (one cancelled before its first step reads as
        #                              interrupted once its deadline passes: test_imports_store)
        for task in list(c._transport.app.state.import_tasks):   # a shutdown: the task cancelled
            task.cancel()
        await asyncio.gather(*c._transport.app.state.import_tasks, return_exceptions=True)
        stopped = (await c.get(f"/api/imports/{made['id']}")).json()
        assert stopped["state"] == "failed"
        assert {t["error"]["code"] for t in stopped["tables"] if t.get("error")} <= {"interrupted"}
        kept = {t["name"]: (await c.get(f"/api/datasets/{t['dataset_id']}")).json()["latest"]["id"]
                for t in good["tables"]}
    assert kept == latest


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
