"""The one guarded HTTP client (5.8b, ADR-0010): every outbound fetch the app makes for a user (the
REST API source, a share link's download) resolves the host once, refuses unless every address is
public, and connects to the address it checked, on every hop. Offline: the network is a fake
under the production client (tests/fake_network.py)."""
from __future__ import annotations

import subprocess
from pathlib import Path

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
    inner = base._network()
    connect_ipv4 = inner.connect_tcp

    async def connect(host, port, *a, **k):
        if ":" in host:
            wire.connected.append(host)
            raise httpcore.ConnectError("no IPv6 route")
        return await connect_ipv4(host, port)
    monkeypatch.setattr(inner, "connect_tcp", connect)
    monkeypatch.setattr(base, "_network", lambda: inner)
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
