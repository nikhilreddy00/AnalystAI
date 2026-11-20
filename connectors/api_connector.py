"""
REST API connector — httpx with Bearer / API key / Basic auth support.
Handles paginated responses automatically using a configurable pagination key.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlparse

import httpx
import pandas as pd

from connectors.base import BodyTooLarge, DataConnector, capped_body, public_client, truncation_note

MAX_PAGES = 20          # safety cap to prevent infinite pagination loops
PAGE_SIZE = 1000
MAX_RECORDS = 100_000   # hard ceiling across all pages to prevent OOM
MAX_PAGE_BYTES = 50 * 2**20   # one page's body, as the upload cap


def _http_url(url: str) -> None:
    """An http(s) URL with a host; which hosts may be fetched is the guarded client's rule
    (`public_client`, checked at each connection: 5.8b moved it there, the check here was
    followed by a second lookup)."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Only http(s) URLs are allowed (got scheme '{parsed.scheme}').")
    if not parsed.hostname:
        raise ValueError("URL is missing a hostname.")


def _status(response: httpx.Response) -> None:
    """Any answer but a success as a message without the URL (httpx's own quotes it, and a URL
    can carry a key). A redirect isn't followed (5.8b audit: it read as 'reachable', then its
    empty body as JSON)."""
    if response.is_redirect:
        raise ValueError(f"The API redirected (HTTP {response.status_code}): use the URL it "
                         "redirects to.")
    if not response.is_success:
        raise ValueError(f"The API answered HTTP {response.status_code} "
                         f"({response.reason_phrase}).")


async def _page(client: httpx.AsyncClient, url: str, headers: dict) -> Any:
    """One page's JSON, its body read up to MAX_PAGE_BYTES as it arrives (5.8b audit: the whole
    answer was read before any cap, compressed or not)."""
    async with client.stream("GET", url, headers=headers) as response:
        _status(response)
        try:
            body = await capped_body(response, MAX_PAGE_BYTES)
        except BodyTooLarge:
            raise ValueError(f"An API page is larger than {MAX_PAGE_BYTES // 2**20} MB.") from None
    return json.loads(body)


_RECORD_KEYS = ("data", "results", "items", "records")


def _records(data: Any, nested: bool = True) -> list | None:
    """The records in one response: a list; the list under a usual key ({"data": [...]}); the one
    list of objects under any key ({"orders": [...]}); or any of those one level down
    ({"data": {"items": [...]}})."""
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return None
    for key in _RECORD_KEYS:
        if isinstance(data.get(key), list):
            return data[key]
    lists = [v for v in data.values()
             if isinstance(v, list) and v and all(isinstance(r, dict) for r in v)]
    if len(lists) == 1:
        return lists[0]
    if nested:
        for v in data.values():
            if (found := _records(v, nested=False)) is not None:
                return found
    return None


class APIConnector(DataConnector):
    def _build_headers(self) -> dict[str, str]:
        auth_type = (self.config.auth_type or "").lower()
        auth_value = self.config.auth_value or ""
        headers: dict[str, str] = {"Accept": "application/json"}

        if auth_type == "bearer":
            headers["Authorization"] = f"Bearer {auth_value}"
        elif auth_type == "api_key":
            headers[self.config.api_key_header or "X-API-Key"] = auth_value
        elif auth_type == "basic":
            import base64
            encoded = base64.b64encode(auth_value.encode()).decode()
            headers["Authorization"] = f"Basic {encoded}"

        return headers

    async def _read(self) -> pd.DataFrame:
        url = self.config.url
        if not url:
            raise ValueError("url is required for API connector")
        _http_url(url)

        headers = self._build_headers()
        records: list[dict[str, Any]] = []

        # Redirects aren't followed; a next-page URL from the body is a new connection, checked
        # by the client like the first
        async with public_client(timeout=30) as client:
            page = 1
            next_url: str | None = url

            while next_url and page <= MAX_PAGES and len(records) < MAX_RECORDS:
                _http_url(next_url)
                data = await _page(client, next_url, headers)

                records.extend(_records(data) or [])
                if not isinstance(data, dict):   # a bare list (or a scalar) has no next page
                    next_url = None
                    break
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

                page += 1

        if not records:
            return pd.DataFrame()

        if next_url or len(records) > MAX_RECORDS:   # stopped at a cap, not at the last page
            records = records[:MAX_RECORDS]
            self.read_info = {"notes": [truncation_note(
                len(records), None,
                f"an API load stops at {MAX_PAGES} pages or {MAX_RECORDS:,} records")]}

        return pd.json_normalize(records)

    async def test_connection(self) -> tuple[bool, str]:
        url = self.config.url
        if not url:
            return False, "url not provided"
        try:
            _http_url(url)
            async with public_client(timeout=10) as client, \
                    client.stream("GET", url, headers=self._build_headers()) as response:
                _status(response)   # the status is the answer: no body is read
            return True, f"API reachable (HTTP {response.status_code})"
        except ValueError as e:   # ours: the URL, the host refused, an error status
            return False, str(e)
        except (httpx.HTTPError, httpx.InvalidURL) as e:   # its class names it, never the URL
            return False, f"The API couldn't be reached ({type(e).__name__})."
