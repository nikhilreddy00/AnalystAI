"""The one guarded HTTP client (5.8b, ADR-0010): every outbound fetch the app makes for a user (the
REST API source, a share link's download) resolves the host once, refuses unless every address is
public, and connects to the address it checked, on every hop. Offline: the network is a fake
under the production client (tests/fake_network.py)."""
from __future__ import annotations

import socket
import ssl
import subprocess
from pathlib import Path

import certifi
import httpcore
import httpx
import pytest

import connectors.base as base
from connectors.base import get_connector
from orchestrator.state import ConnectorConfig, ConnectorType
from tests.fake_network import PUBLIC, serve

ROOT = Path(__file__).parent.parent


def _json(body):
    return lambda request: httpx.Response(200, json=body)


def test_every_outbound_http_goes_through_the_guarded_client():
    """P58-07 (5.8b recurrence: the API source checked a host, then httpx resolved it again):
    product code builds no HTTP client of its own. Qdrant/Ollama (configured by the operator)
    and the dev scripts are the only others."""
    out = subprocess.run(
        ["git", "grep", "-nE", r"httpx\.(Async)?Client\(|requests\.|urlopen\(|aiohttp", "--",
         "*.py", ":!tests", ":!scripts", ":!evals", ":!connectors/base.py",
         ":!memory/vector_store.py"], cwd=ROOT, capture_output=True, text=True, check=False)
    assert out.stdout == "", out.stdout


def test_the_guarded_pool_is_still_where_httpx_keeps_it():
    """httpx has no network_backend option, so public_client replaces the transport's pool: an
    httpx upgrade that renames `_pool` would silently fall back to an unchecked network."""
    client = base.public_client()
    network = client._transport._pool._network_backend  # noqa: SLF001
    assert isinstance(network, base._CheckedNetwork)
    assert isinstance(network._inner, httpcore.AnyIOBackend)   # the real sockets, under the check


def test_the_guarded_client_settings():
    """The contract: one timeout, redirects returned not followed, environment ignored,
    uncompressed bodies, and TLS verified against certifi's roots (not the OS store)."""
    client = base.public_client(timeout=7)
    assert client.timeout == httpx.Timeout(7) and base.public_client().timeout == httpx.Timeout(30)
    assert client.follow_redirects is False and client.trust_env is False
    assert client.headers["accept-encoding"] == "identity"
    pool = client._transport._pool  # noqa: SLF001
    ctx = pool._ssl_context  # noqa: SLF001
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname
    assert ctx.get_ca_certs() == ssl.create_default_context(cafile=certifi.where()).get_ca_certs()


async def test_a_redirect_is_returned_not_followed(monkeypatch):
    wire = serve(monkeypatch, lambda r: httpx.Response(302, headers={"Location": "https://b.example/"}))
    async with base.public_client() as client:
        r = await client.get("https://files.example/a")
    assert r.status_code == 302 and [q.url.host for q in wire.requests] == ["files.example"]


@pytest.mark.parametrize(("dns", "message"), [
    ({}, "The host 'files.example' wasn't found."),
    ({"files.example": ["10.0.0.5"]}, "The host 'files.example' isn't on the public internet, "
                                      "so it can't be fetched from this server."),
])
def test_a_refused_host_says_why_in_the_users_words(monkeypatch, dns, message):
    def getaddrinfo(host, *a, **k):
        if host not in dns:
            raise socket.gaierror("nope")
        return [(0, 0, 0, "", (ip, 0)) for ip in dns[host]]
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    with pytest.raises(base.RefusedHost) as refused:
        base.public_addresses("files.example")
    assert str(refused.value) == message


async def test_each_address_gets_the_callers_arguments_and_the_last_error_is_raised(monkeypatch):
    serve(monkeypatch, _json([]), {"files.example": ["2606:4700::1111", PUBLIC]})
    calls = []

    class Inner:
        async def connect_tcp(self, *args):
            calls.append(args)
            raise (httpcore.ConnectError if len(calls) == 1 else httpcore.ConnectTimeout)(args[0])
    monkeypatch.setattr(base, "_NETWORK", Inner)
    args = ("files.example", 8443, 4.5, "0.0.0.0", [(1, 2, 3)])   # noqa: S104
    with pytest.raises(httpcore.ConnectTimeout, match=PUBLIC):
        await base._CheckedNetwork().connect_tcp(*args)
    assert calls == [(ip, *args[1:]) for ip in ("2606:4700::1111", PUBLIC)]


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.5", "192.168.1.2", "169.254.169.254",
                                     "100.64.0.1", "64:ff9b::a9fe:a9fe", "::ffff:10.0.0.1",
                                     "fd00::1", "0.0.0.0"])   # noqa: S104
async def test_a_host_with_any_non_public_address_is_never_connected(monkeypatch, address):
    wire = serve(monkeypatch, _json([]), {"files.example": [PUBLIC, address]})
    async with base.public_client() as client:
        with pytest.raises(base.RefusedHost) as refused:
            await client.get("https://files.example/data.csv")
    assert wire.connected == []
    assert "files.example" in str(refused.value) and address not in str(refused.value)


async def test_the_connection_goes_to_the_address_that_was_checked(monkeypatch):
    """DNS rebinding: the first lookup says public, a second would say loopback. The client looks
    up once, connects to the checked address, and TLS still verifies the host's name."""
    wire = serve(monkeypatch, _json([1]), lambda host, n: [PUBLIC] if n == 1 else ["127.0.0.1"])
    async with base.public_client() as client:
        r = await client.get("https://files.example/a")
    assert r.status_code == 200
    assert wire.connected == [PUBLIC] and wire.lookups == ["files.example"]
    assert wire.sni == ["files.example"]


async def test_every_address_is_tried_in_turn(monkeypatch):
    """No happy-eyeballs in httpcore: an IPv6-first answer on an IPv4-only host still connects."""
    wire = serve(monkeypatch, _json([1]), {"files.example": ["2606:4700::1111", PUBLIC]})
    inner = base._NETWORK()
    connect_ipv4 = inner.connect_tcp

    async def connect(host, port, *a, **k):
        if ":" in host:
            wire.connected.append(host)
            raise httpcore.ConnectError("no IPv6 route")
        return await connect_ipv4(host, port)
    monkeypatch.setattr(inner, "connect_tcp", connect)
    monkeypatch.setattr(base, "_NETWORK", lambda: inner)
    async with base.public_client() as client:
        assert (await client.get("https://files.example/a")).status_code == 200
    assert wire.connected == ["2606:4700::1111", PUBLIC]


async def test_proxies_from_the_environment_are_ignored(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://10.0.0.9:3128")
    wire = serve(monkeypatch, _json([1]))
    async with base.public_client() as client:
        await client.get("https://files.example/a")
    assert wire.connected == [PUBLIC]


async def test_responses_are_asked_for_uncompressed(monkeypatch):
    wire = serve(monkeypatch, _json([1]))
    async with base.public_client() as client:
        await client.get("https://files.example/a")
    assert wire.requests[0].headers["accept-encoding"] == "identity"


async def test_the_api_source_fetches_through_the_guarded_client(monkeypatch):
    wire = serve(monkeypatch, _json([{"a": 1}, {"a": 2}]),
                 lambda host, n: [PUBLIC] if n == 1 else ["127.0.0.1"])
    df = await get_connector(ConnectorConfig(type=ConnectorType.API,
                                             url="https://api.example/v1/rows")).load()
    assert list(df["a"]) == [1, 2] and wire.connected == [PUBLIC]


async def test_the_api_source_refuses_a_private_host(monkeypatch):
    wire = serve(monkeypatch, _json([]), {"api.example": ["10.0.0.5"]})
    conn = get_connector(ConnectorConfig(type=ConnectorType.API, url="https://api.example/x"))
    with pytest.raises(ValueError, match="public internet"):
        await conn.load()
    assert wire.connected == []


async def test_an_api_error_never_quotes_the_url(monkeypatch):
    """P58-06 (5.8b recurrence): httpx's raise_for_status message carries the URL, and a URL can
    carry a key (`?api_key=`, `?sig=`)."""
    serve(monkeypatch, lambda r: httpx.Response(403))
    conn = get_connector(ConnectorConfig(type=ConnectorType.API,
                                         url="https://api.example/v1/rows?sig=SECRET123"))
    with pytest.raises(ValueError, match="403") as err:
        await conn.load()
    assert "SECRET123" not in str(err.value)
    ok, message = await conn.test_connection()
    assert not ok and "403" in message and "SECRET123" not in message


@pytest.mark.parametrize("status", [301, 302, 307, 308])
async def test_an_api_that_redirects_is_said_so_never_reachable(monkeypatch, status):
    """5.8b audit (silent-failure hunter, code review): only >= 400 was an error, so a redirect
    tested 'API reachable' and then its empty body failed as JSON."""
    serve(monkeypatch, lambda r: httpx.Response(status, headers={"location": "https://api.example/v2"}))
    conn = get_connector(ConnectorConfig(type=ConnectorType.API, url="https://api.example/v1"))
    said = f"The API redirected (HTTP {status}): use the URL it redirects to."
    assert await conn.test_connection() == (False, said)
    with pytest.raises(ValueError, match="redirected"):
        await conn.load()


async def test_a_next_page_inside_the_network_is_never_fetched(monkeypatch):
    """The next-page URL comes from the response body: its host is checked where it connects."""
    wire = serve(monkeypatch, lambda r: httpx.Response(200, json={
        "data": [{"a": 1}], "next": "https://internal.example/2"}),
        {"api.example": [PUBLIC], "internal.example": ["10.0.0.5"]})
    conn = get_connector(ConnectorConfig(type=ConnectorType.API, url="https://api.example/1"))
    with pytest.raises(base.RefusedHost):
        await conn.load()
    assert wire.connected == [PUBLIC]
    serve(monkeypatch, lambda r: httpx.Response(200, json=[]), {"api.example": ["10.0.0.5"]})
    ok, message = await conn.test_connection()
    assert not ok and "public internet" in message


async def test_an_api_page_past_the_cap_or_compressed_is_refused(monkeypatch):
    import gzip

    import connectors.api_connector as ac
    monkeypatch.setattr(ac, "MAX_PAGE_BYTES", 40)
    serve(monkeypatch, lambda r: httpx.Response(200, json=[{"a": "x" * 100}]))
    conn = get_connector(ConnectorConfig(type=ConnectorType.API, url="https://api.example/1"))
    with pytest.raises(ValueError, match="larger than"):
        await conn.load()
    serve(monkeypatch, lambda r: httpx.Response(200, content=gzip.compress(b"[]"), headers={
        "content-encoding": "gzip"}))
    with pytest.raises(ValueError, match="compressed its answer"):
        await conn.load()


def test_no_request_url_reaches_the_server_log(monkeypatch):
    """P58-06 (5.8b recurrence): httpx logs `HTTP Request: GET <url>` at INFO and the app's root
    logger is at INFO, so every share link's token and API key in a query would be written. The
    API and the worker both start with `install()` (no handlers here: the scrub filter would
    stay on pytest's own and change other tests' records)."""
    import logging

    from api.logging_scrub import install
    monkeypatch.setattr(logging.getLogger(), "handlers", [])
    for name in ("httpx", "httpcore"):
        monkeypatch.setattr(logging.getLogger(name), "level", logging.NOTSET)
    install()
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
    assert logging.getLogger("httpcore").getEffectiveLevel() >= logging.WARNING
