"""The network under the guarded HTTP client (`connectors.base.public_client`), faked for tests:
DNS answers from a table and each request is answered by a handler (an httpx.Request in, an
httpx.Response out). The production client, its checks and its pinning all run; only the socket
is fake. `serve(monkeypatch, handler)` installs it and returns the record of what happened."""
from __future__ import annotations

import socket
from dataclasses import dataclass, field

import httpcore
import httpx

import connectors.base as base

PUBLIC = "93.184.215.14"


@dataclass
class Wire:
    handler: object
    connected: list[str] = field(default_factory=list)   # the address each socket went to
    sni: list[str | None] = field(default_factory=list)   # the name TLS verified
    lookups: list[str] = field(default_factory=list)
    requests: list[httpx.Request] = field(default_factory=list)


class _Stream(httpcore.AsyncNetworkStream):
    def __init__(self, wire: Wire) -> None:
        self.wire, self.sent, self.out = wire, b"", b""

    async def write(self, buffer: bytes, timeout=None) -> None:
        self.sent += buffer
        if b"\r\n\r\n" not in self.sent:
            return
        head = self.sent.split(b"\r\n\r\n", 1)[0].decode()
        self.sent = b""
        line, *lines = head.split("\r\n")
        method, target, _ = line.split(" ")
        headers = dict(h.split(": ", 1) for h in lines)
        request = httpx.Request(method, f"https://{headers['Host']}{target}", headers=headers)
        self.wire.requests.append(request)
        r = self.wire.handler(request)
        body = b"".join(r.stream)   # the bytes as given: a Content-Encoding is the client's to read
        hdrs = {k: v for k, v in r.headers.items() if k.lower() != "content-length"}
        hdrs["Content-Length"] = str(len(body))
        self.out += (f"HTTP/1.1 {r.status_code} {r.reason_phrase}\r\n"
                     + "".join(f"{k}: {v}\r\n" for k, v in hdrs.items()) + "\r\n").encode() + body

    async def read(self, max_bytes: int, timeout=None) -> bytes:
        chunk, self.out = self.out[:max_bytes], self.out[max_bytes:]
        return chunk

    async def aclose(self) -> None:
        pass

    async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        self.wire.sni.append(server_hostname)
        return self

    def get_extra_info(self, info: str):
        return None


class _Backend(httpcore.AsyncNetworkBackend):
    def __init__(self, wire: Wire) -> None:
        self.wire = wire

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        self.wire.connected.append(host)
        return _Stream(self.wire)

    async def sleep(self, seconds: float) -> None:
        pass


def serve(monkeypatch, handler, dns=None) -> Wire:
    """`handler(request) -> httpx.Response`; `dns`: host → addresses, or `(host, n) -> addresses`
    for the n-th lookup (default: every host is one public address)."""
    wire = Wire(handler)

    def getaddrinfo(host, *a, **k):
        wire.lookups.append(host)
        if dns is None:
            answer = [PUBLIC]
        elif callable(dns):
            answer = dns(host, len(wire.lookups))
        else:
            answer = dns[host]
        return [(0, 0, 0, "", (ip, 0)) for ip in answer]
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(base, "_NETWORK", lambda: _Backend(wire))
    return wire
