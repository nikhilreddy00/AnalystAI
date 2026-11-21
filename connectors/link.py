"""Share links (5.8b, ADR-0010): a public Google Sheet, Drive, OneDrive/SharePoint or Dropbox link, or
any https CSV/xlsx URL, downloaded once by the server and then handled as an upload of the same
bytes (api/routes/connectors.py), so a run reads a fixed version and the link is never stored.

The fetch is the guarded client's (`public_client`): every hop's host is resolved once, refused
unless public, and connected to as checked. Here: https on every hop, at most MAX_HOPS redirects,
at most the upload cap read (counted as it streams; a body can't be trusted to say its size), and
only bytes that are a workbook or text: a private or broken link often answers 200 with an HTML
sign-in page. A link's capability is its URL (a path id, `e=`, `rlkey=`, `sig=`), so no message
says more than the host."""
from __future__ import annotations

import asyncio
import re
from urllib.parse import parse_qsl, unquote, urlencode, urljoin, urlparse

import httpx

from agents.errors import IngestionError
from connectors.base import (FETCH_DEADLINE_S, BodyTooLarge, HostNotFound, RefusedHost,
                             capped_body, public_client)
from connectors.csv_connector import MAX_UPLOAD_MB

MAX_HOPS = 5
MAX_BYTES = MAX_UPLOAD_MB * 1024 * 1024
DEADLINE_S = FETCH_DEADLINE_S   # the whole download, every hop
_XLSX = b"PK\x03\x04"   # a workbook is a zip
_SHEET = re.compile(r"^/spreadsheets/d/([\w-]+)")
_PUBLISHED = re.compile(r"^/spreadsheets/d/e/([\w-]+)")   # File > Share > Publish to web
_DRIVE_FILE = re.compile(r"^/file/d/([\w-]+)")
_FILENAME = re.compile(r"""filename\*=UTF-8''([^;]+)|filename="?([^";]+)"?""", re.I)


def _with_query(url, **params) -> str:
    query = dict(parse_qsl(url.query, keep_blank_values=True)) | params
    return url._replace(query=urlencode(query, safe="!"), fragment="").geturl()


def direct_url(link: str) -> str:
    """The URL of the file a share link names: a Sheet's CSV export (the tab in `gid`, as the
    Sheets source reads a sheet), Drive's download (past its virus-scan page), Dropbox's and
    SharePoint/OneDrive's download switches; any other URL as it is."""
    url = urlparse(link)
    host = (url.hostname or "").lower()
    # the tab the link names; none named: the first tab (live check 5.8b: a sheet made from an
    # uploaded file has a random first gid, and gid=0 answered 400)
    gid = (re.search(r"gid=(\d+)", url.fragment) or re.search(r"gid=(\d+)", url.query))
    tab = f"&gid={gid.group(1)}" if gid else ""
    if host == "docs.google.com" and (m := _PUBLISHED.match(url.path)):   # its id is not a sheet's
        return f"https://docs.google.com/spreadsheets/d/e/{m.group(1)}/pub?output=csv{tab}"
    if host == "docs.google.com" and (m := _SHEET.match(url.path)):
        return f"https://docs.google.com/spreadsheets/d/{m.group(1)}/export?format=csv{tab}"
    if host == "drive.google.com":
        query = dict(parse_qsl(url.query))
        file_id = (m.group(1) if (m := _DRIVE_FILE.match(url.path)) else query.get("id"))
        if file_id:
            keep = {"resourcekey": query["resourcekey"]} if "resourcekey" in query else {}
            return ("https://drive.google.com/uc?" +
                    urlencode({"export": "download", "id": file_id, "confirm": "t", **keep}))
    if host.endswith("dropbox.com"):
        return _with_query(url, dl="1")
    if host.endswith((".sharepoint.com", "onedrive.live.com", "1drv.ms")):
        return _with_query(url, download="1")
    return link


def _name(response: httpx.Response, link: str) -> str:
    """The file's name: the server's (Content-Disposition), else the link's last path part, else
    its host. Dropbox says 'unspecified'."""
    for quoted, plain in _FILENAME.findall(response.headers.get("content-disposition", "")):
        name = unquote(quoted or plain).strip()
        if name and name.lower() != "unspecified":
            return name
    last = urlparse(link).path.rstrip("/").rsplit("/", 1)[-1]
    # the dataset is named by the stem: a host keeps its dots behind a suffix of its own (a link
    # with no host never gets here: its fetch fails first)
    return unquote(last) if "." in last else f"{urlparse(link).hostname}.link"


async def download(link: str) -> tuple[bytes, str, str]:
    """(content, suffix, file name) of the file a public share link names. IngestionError, its
    message naming at most the host, for a link that isn't a valid https link, isn't public,
    isn't a file, redirects too often, is past the upload cap or takes longer than DEADLINE_S."""
    try:
        host = urlparse(link).hostname or "the link"
        url = direct_url(link)
    except ValueError:   # https://[::1 … : no message may quote the link
        raise IngestionError("link invalid", "That isn't a valid https link.") from None
    try:
        # the whole download, not each read (5.8b audit: a byte every 59 s held a worker for days)
        async with asyncio.timeout(DEADLINE_S), public_client(timeout=60) as client:
            for _ in range(MAX_HOPS + 1):
                if urlparse(url).scheme != "https":
                    raise IngestionError("link not https", f"The link to {host} has to be an "
                                         "https link (every redirect too).")
                async with client.stream("GET", url) as response:
                    if response.is_redirect:
                        if not (location := response.headers.get("location", "").strip()):
                            # it joined back to the same URL six times (silent-failure hunter)
                            raise _not_public(host, "it redirected nowhere")
                        url = urljoin(url, location)
                        continue
                    _ok(response.status_code, host)
                    content = await _capped(response, host)
                    return (content, _suffix(content, response, host),
                            _name(response, link))
    except HostNotFound:
        raise IngestionError("link host not found", f"The link's host '{host}' wasn't "
                             "found. Check the link.") from None
    except RefusedHost as exc:
        raise IngestionError("link host refused", f"The link to {host} leads to an address "
                             "this server doesn't fetch from (only the public internet).") from exc
    except (httpx.HTTPError, httpx.InvalidURL, ValueError) as exc:   # the class, never the text
        raise IngestionError("link unreachable", f"{host} couldn't be reached "   # (it may hold
                             f"({type(exc).__name__}).") from None                # the URL)
    except TimeoutError:
        raise IngestionError("link slow", f"{host} took longer than {DEADLINE_S} s to send "
                             "the file.") from None
    raise IngestionError("link redirects", f"The link to {host} redirected more than "
                         f"{MAX_HOPS} times.")


def _ok(status: int, host: str) -> None:
    """A status that isn't the file, said as what the user can do: a link that isn't shared
    (or doesn't exist) is theirs to fix, a host that's down or busy is not (5.8b audit)."""
    if status == 429 or status >= 500:
        raise IngestionError("link host trouble", f"{host} is having trouble (HTTP {status}). "
                             "Try again in a few minutes.")
    if status != 200:
        raise _not_public(host, f"it answered HTTP {status}")


def _not_public(host: str, why: str) -> IngestionError:
    return IngestionError("link not public", f"The link to {host} didn't give a file ({why}): "
                          "it may not be public. Share it as 'Anyone with the link', and link to "
                          "a CSV or Excel file.")


class TooLarge(IngestionError):
    """Past the upload cap (the route answers 413, as an upload's does)."""


async def _capped(response: httpx.Response, host: str) -> bytes:
    try:
        return await capped_body(response, MAX_BYTES)
    except BodyTooLarge:
        raise TooLarge("link too large", f"The file at {host} is larger than "
                       f"{MAX_UPLOAD_MB} MB, the upload limit.") from None
    except ValueError as exc:   # a compressed answer: its own words name the encoding only
        raise IngestionError("link encoding", f"The file at {host} couldn't be read: {exc}.") \
            from None


# the file kinds told by their first bytes (5.8b audit: a JSON or Parquet link read as a CSV)
_MAGIC = ((_XLSX, ".xlsx"), (b"PAR1", ".parquet"), (b"\xd0\xcf\x11\xe0", ".xls"))


def _suffix(content: bytes, response: httpx.Response, host: str) -> str:
    """The reader a file's bytes go to: a workbook, Parquet, an old .xls (refused, as an upload
    is), JSON or JSON lines, else CSV text; an HTML page (a sign-in, a virus-scan notice) is no
    file, whatever its status and type say."""
    for magic, suffix in _MAGIC:
        if content.startswith(magic):
            return suffix
    head = content[:512].lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    if "text/html" in response.headers.get("content-type", "").lower() or \
            head.startswith((b"<!doctype html", b"<html", b"<head", b"<!--")):
        raise _not_public(host, "a web page came back, not a file")
    return ".json" if content.lstrip(b"\xef\xbb\xbf \t\r\n")[:1] in (b"[", b"{") else ".csv"
