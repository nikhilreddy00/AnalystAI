"""
REST API connector — httpx with Bearer / API key / Basic auth support.
Handles paginated responses automatically using a configurable pagination key.
"""

from __future__ import annotations

import ipaddress
import socket
from typing import Any
from urllib.parse import urlparse

import httpx
import pandas as pd

from connectors.base import DataConnector

MAX_PAGES = 20          # safety cap to prevent infinite pagination loops
PAGE_SIZE = 1000
MAX_RECORDS = 100_000   # hard ceiling across all pages to prevent OOM


def _assert_public_url(url: str) -> None:
    """Block SSRF: reject URLs whose host resolves to a private/loopback/link-local
    address. Also rejects non-http(s) schemes and unresolved hostnames."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Only http(s) URLs are allowed (got scheme '{parsed.scheme}').")
    host = parsed.hostname
    if not host:
        raise ValueError("URL is missing a hostname.")
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise ValueError(f"Could not resolve hostname '{host}': {exc}") from exc
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise ValueError(
                f"Refusing to fetch URL: '{host}' resolves to non-public address {addr}."
            )


class APIConnector(DataConnector):
    def _build_headers(self) -> dict[str, str]:
        auth_type = (self.config.auth_type or "").lower()
        auth_value = self.config.auth_value or ""
        headers: dict[str, str] = {"Accept": "application/json"}

        if auth_type == "bearer":
            headers["Authorization"] = f"Bearer {auth_value}"
        elif auth_type == "api_key":
            headers["X-API-Key"] = auth_value
        elif auth_type == "basic":
            import base64
            encoded = base64.b64encode(auth_value.encode()).decode()
            headers["Authorization"] = f"Basic {encoded}"

        return headers

    async def load(self) -> pd.DataFrame:
        url = self.config.url
        if not url:
            raise ValueError("url is required for API connector")
        _assert_public_url(url)

        headers = self._build_headers()
        records: list[dict[str, Any]] = []

        # follow_redirects=False — redirects could point at internal IPs and
        # bypass the SSRF check we just performed.
        async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
            page = 1
            next_url: str | None = url

            while next_url and page <= MAX_PAGES and len(records) < MAX_RECORDS:
                # Re-validate every URL we touch (including next-page URLs from the response body)
                _assert_public_url(next_url)
                response = await client.get(next_url, headers=headers)
                response.raise_for_status()
                data = response.json()

                # Handle common response shapes: list, {"data": [...]}, {"results": [...]}
                if isinstance(data, list):
                    records.extend(data)
                    break
                elif isinstance(data, dict):
                    items = (
                        data.get("data")
                        or data.get("results")
                        or data.get("items")
                        or data.get("records")
                        or []
                    )
                    records.extend(items)

                    # Pagination: follow next_url / next / links.next
                    pagination_key = self.config.pagination_key
                    if pagination_key:
                        next_url = data.get(pagination_key)
                    else:
                        next_url = (
                            data.get("next")
                            or data.get("next_url")
                            or (data.get("links") or {}).get("next")
                        )
                else:
                    break

                page += 1

        if not records:
            return pd.DataFrame()

        # Truncate if we blew past MAX_RECORDS on the final page
        if len(records) > MAX_RECORDS:
            records = records[:MAX_RECORDS]

        return pd.json_normalize(records)

    async def test_connection(self) -> tuple[bool, str]:
        url = self.config.url
        if not url:
            return False, "url not provided"
        try:
            _assert_public_url(url)
            async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
                response = await client.get(url, headers=self._build_headers())
                response.raise_for_status()
            return True, f"API reachable (HTTP {response.status_code})"
        except Exception as e:
            return False, str(e)
