"""Share links (5.8b, ADR-0010): a public Google Sheet, Drive, OneDrive/SharePoint or Dropbox link,
or any https CSV/xlsx URL, is downloaded once by the server through the guarded client (each
redirect hop checked where it connects, a byte cap) and becomes a dataset version exactly as an
upload of the same bytes would. No message, log line or stored field holds the link's token.
The route is the production path; the network is a fake under the production client (the
redirect chains research measured, docs/research/2026-11-20-5.8b-sources.md)."""
from __future__ import annotations

import io
import logging
from functools import cache

import httpx
import pandas as pd
import pytest

from agents.errors import IngestionError
from tests.fake_network import serve
from tests.test_workspace_api import _client

CSV = b"order_id,region,amount\n1,North,10.5\n2,South,3.25\n3,North,7.0\n"
SECRETS = ("SIGSECRET", "TOKSECRET", "RLKEYSECRET", "EKEYSECRET", "1AbCdEfGhIjKlMnOpQrStUvWxYz")


@cache   # a workbook carries its creation time: one copy, so an upload and a link match
def _xlsx() -> bytes:
    buf = io.BytesIO()
    pd.read_csv(io.BytesIO(CSV)).to_excel(buf, index=False)
    return buf.getvalue()


def _chain(*hops):
    """A handler answering URLs in turn: each hop (prefix of the URL it expects, response)."""
    def handler(request: httpx.Request) -> httpx.Response:
        for prefix, response in hops:
            if str(request.url).startswith(prefix):
                return response
        return httpx.Response(404, text="not here")
    return handler


@pytest.fixture
async def client():
    from api.rate_limit import limiter
    from orchestrator.runner import in_memory_deps
    limiter.reset()
    async with _client(in_memory_deps()) as c:
        yield c


async def test_a_user_imports_at_most_ten_links_a_minute(client, monkeypatch):
    """Audit pass 2 (code review): each link holds up to 50 MB for up to 120 s; the route had
    no limit, the session and dataset routes do."""
    serve(monkeypatch, _chain(("https://files.example/", _ok())))
    codes = [(await _link(client, "https://files.example/q.csv")).status_code for _ in range(11)]
    assert codes[:10] == [200] * 10 and codes[10] == 429


async def _link(client, url: str, **extra):
    return await client.post("/connectors/link", json={"url": url, **extra})


@pytest.mark.parametrize(("shared", "direct"), [
    ("https://docs.google.com/spreadsheets/d/1AbC_d-9/edit#gid=123",
     "https://docs.google.com/spreadsheets/d/1AbC_d-9/export?format=csv&gid=123"),
    # no tab named: the first tab, whatever its gid (live check: gid=0 was a 400 there)
    ("https://docs.google.com/spreadsheets/d/1AbC/edit?usp=sharing",
     "https://docs.google.com/spreadsheets/d/1AbC/export?format=csv"),
    ("https://docs.google.com/spreadsheets/d/1AbC/edit?gid=7#gid=7",
     "https://docs.google.com/spreadsheets/d/1AbC/export?format=csv&gid=7"),
    ("https://drive.google.com/file/d/1XyZ/view?usp=sharing",
     "https://drive.google.com/uc?export=download&id=1XyZ&confirm=t"),
    ("https://drive.google.com/file/d/1XyZ/view?resourcekey=0-k",
     "https://drive.google.com/uc?export=download&id=1XyZ&confirm=t&resourcekey=0-k"),
    ("https://drive.google.com/open?id=1XyZ",
     "https://drive.google.com/uc?export=download&id=1XyZ&confirm=t"),
    ("https://www.dropbox.com/scl/fi/abc/data.csv?rlkey=r1&dl=0",
     "https://www.dropbox.com/scl/fi/abc/data.csv?rlkey=r1&dl=1"),
    ("https://acme-my.sharepoint.com/:x:/g/personal/a_acme_com/EaBc?e=xY12",
     "https://acme-my.sharepoint.com/:x:/g/personal/a_acme_com/EaBc?e=xY12&download=1"),
    ("https://1drv.ms/x/s!AbCd", "https://1drv.ms/x/s!AbCd?download=1"),
    ("https://example.com/exports/orders.csv?sig=x", "https://example.com/exports/orders.csv?sig=x"),
])
def test_a_share_link_is_rewritten_to_its_file(shared, direct):
    from connectors.link import direct_url
    assert direct_url(shared) == direct


async def test_a_public_sheet_link_becomes_a_dataset_as_its_upload_would(client, monkeypatch):
    """Google's export answers 307 to googleusercontent.com, then the CSV (research §6)."""
    wire = serve(monkeypatch, _chain(
        ("https://docs.google.com/spreadsheets/d/1AbC/export?format=csv",
         httpx.Response(307, headers={"location": "https://doc-0s-sheets.googleusercontent.com/export/x"})),
        ("https://doc-0s-sheets.googleusercontent.com/export/x",
         httpx.Response(200, content=CSV, headers={
             "content-type": "text/csv; charset=utf-8",
             "content-disposition": 'attachment; filename="Q3 orders - Sheet1.csv"'}))))
    r = await _link(client, "https://docs.google.com/spreadsheets/d/1AbC/edit")
    assert r.status_code == 200, r.text
    linked = r.json()
    up = await client.post("/connectors/upload", files={"file": ("x.csv", CSV, "text/csv")})
    assert linked["file_hash"] == up.json()["file_hash"]   # the same bytes as an upload of them
    assert linked["dataset_name"] == "Q3 orders - Sheet1" and linked["status"] == "new_dataset"
    assert up.json()["status"] == "existing"               # the same version, not a copy
    assert wire.sni == ["docs.google.com", "doc-0s-sheets.googleusercontent.com"]


async def test_a_drive_xlsx_link_reads_as_the_uploaded_xlsx(client, monkeypatch):
    """Drive answers 303 to drive.usercontent.google.com; the workbook's bytes read exactly as
    the same file uploaded (P58-08: an xlsx from a link is an xlsx)."""
    from connectors.csv_connector import _read_bytes
    serve(monkeypatch, _chain(
        ("https://drive.google.com/uc?", httpx.Response(303, headers={
            "location": "https://drive.usercontent.google.com/download?id=1XyZ&export=download"})),
        ("https://drive.usercontent.google.com/", httpx.Response(200, content=_xlsx(), headers={
            "content-type": "application/octet-stream",
            "content-disposition": 'attachment; filename="orders.xlsx"'}))))
    r = await _link(client, "https://drive.google.com/file/d/1XyZ/view")
    assert r.status_code == 200, r.text
    up = await client.post("/connectors/upload", files={"file": ("orders.xlsx", _xlsx(), "x")})
    assert r.json()["file_hash"] == up.json()["file_hash"]
    pd.testing.assert_frame_equal(_read_bytes(_xlsx(), ".xlsx")[0], _read_bytes(CSV, ".csv")[0],
                                  check_dtype=False)


async def test_a_dropbox_link_without_a_file_name_is_named_by_its_path(client, monkeypatch):
    serve(monkeypatch, _chain(
        ("https://www.dropbox.com/", httpx.Response(302, headers={
            "location": "https://uc1.dl.dropboxusercontent.com/cd/0/inline/AAA"})),
        ("https://uc1.dl.dropboxusercontent.com/", httpx.Response(200, content=CSV, headers={
            "content-type": "application/binary",
            "content-disposition": "attachment; filename=unspecified"}))))
    r = await _link(client, "https://www.dropbox.com/scl/fi/abc/q3_orders.csv?rlkey=RLKEYSECRET&dl=0")
    assert r.status_code == 200 and r.json()["dataset_name"] == "q3_orders"


@pytest.mark.parametrize("answer", [
    httpx.Response(200, text="<!DOCTYPE html><html>Sign in</html>",
                   headers={"content-type": "text/html; charset=utf-8"}),
    httpx.Response(200, text="<html><body>Google Drive can't scan this file</body></html>",
                   headers={"content-type": "application/octet-stream"}),
    httpx.Response(302, headers={"location": "https://accounts.google.com/ServiceLogin?x=1"}),
    httpx.Response(401), httpx.Response(403), httpx.Response(404),
])
async def test_a_link_that_isnt_public_says_so(client, monkeypatch, answer):
    """A private or broken link often answers 200 with an HTML page (Dropbox, Google's sign-in):
    the status says nothing, the bytes do."""
    serve(monkeypatch, _chain(("https://accounts.google.com/", httpx.Response(200, text="<html>",
                                                                            headers={"content-type": "text/html"})),
                              ("https://", answer)))
    r = await _link(client, "https://docs.google.com/spreadsheets/d/1AbC/edit")
    assert r.status_code == 400
    assert "public" in r.json()["detail"] and "docs.google.com" in r.json()["detail"]


@pytest.mark.parametrize("location", [
    "http://files.example/data.csv",                     # https only, on every hop
    "https://internal.example/data.csv",                 # resolves inside the network
    "https://metadata.example/latest/meta-data",         # NAT64 to the metadata address
    "ftp://files.example/data.csv", "file:///etc/passwd",
])
async def test_a_redirect_off_the_public_internet_or_off_https_is_refused(client, monkeypatch,
                                                                           location):
    wire = serve(monkeypatch, _chain(
        ("https://files.example/", httpx.Response(302, headers={"location": location})),
        ("https://internal.example/", httpx.Response(200, content=CSV)),
        ("https://metadata.example/", httpx.Response(200, content=CSV))),
        dns=lambda host, n: {"internal.example": ["10.0.0.7"],
                             "metadata.example": ["64:ff9b::a9fe:a9fe"]}.get(host, ["93.184.215.14"]))
    r = await _link(client, "https://files.example/start.csv")
    assert r.status_code == 400 and wire.connected == ["93.184.215.14"]   # only the first hop


async def test_a_redirect_loop_stops(client, monkeypatch):
    serve(monkeypatch, lambda r: httpx.Response(302, headers={"location": str(r.url) + "x"}))
    r = await _link(client, "https://files.example/a.csv")
    assert r.status_code == 400 and "redirect" in r.json()["detail"]


async def test_a_file_past_the_upload_cap_is_refused_while_it_streams(client, monkeypatch):
    import connectors.link as link
    monkeypatch.setattr(link, "MAX_BYTES", 40)
    serve(monkeypatch, lambda r: httpx.Response(200, content=CSV * 10,
                                                headers={"content-type": "text/csv"}))
    r = await _link(client, "https://files.example/big.csv")
    assert r.status_code == 413 and "50 MB" in r.json()["detail"]


def _zip(entries: dict[str, bytes]) -> bytes:
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in entries.items():
            z.writestr(name, data)
    return buf.getvalue()


def _declared(zipped: bytes, size: int | None = None, version: int | None = None) -> bytes:
    """A zip whose central directory declares another unpacked size or extract version (what
    zipfile reads before unpacking): a bomb's shape without building one."""
    import struct
    at = zipped.index(b"PK\x01\x02")
    out = bytearray(zipped)
    if size is not None:
        out[at + 24:at + 28] = struct.pack("<I", size)
    if version is not None:
        out[at + 6:at + 8] = struct.pack("<H", version)
    return bytes(out)


def test_a_real_sized_workbook_fits_under_the_unpacked_cap():
    """5.8b review: 150 MB unpacked refused 20-30 MB workbooks (sheet XML packs 5-10 times)."""
    from connectors.csv_connector import _MAX_UNPACKED, MAX_UPLOAD_MB
    assert _MAX_UNPACKED >= 10 * MAX_UPLOAD_MB * 2**20


def test_a_workbook_zip_can_t_open_is_not_a_table_never_a_crash():
    """5.8b audit: a part's version zipfile can't read raised NotImplementedError: a 500."""
    from agents.errors import IngestionError
    from connectors.csv_connector import _NOT_A_TABLE, _read_bytes
    with pytest.raises(IngestionError) as err:
        _read_bytes(_declared(_xlsx(), version=100), ".xlsx")
    assert err.value.user_message == _NOT_A_TABLE


@pytest.mark.parametrize("content,why", [
    (_declared(_zip({"xl/worksheets/sheet1.xml": b"0" * 1000}), size=2**31), "unpacks"),
    # under the total but packed a thousand times: a bomb's ratio, not XML's
    (_zip({"xl/worksheets/sheet1.xml": b"0" * (64 * 2**20)}), "unpacks"),
    (_zip({f"x{i}.xml": b"" for i in range(10_001)}), "parts"),
    (_zip({"../../etc/cron.d/x": b"a"}), "parts"),
    (b"a," * 10_001 + b"\n1\n", "columns"),
    # the reader's own header: '\r' line endings, quoted names
    (b"a;" * 10_001 + b"\r" + b"1;" * 10_001 + b"\r", "columns"),
    (b'"a,b",' * 10_001 + b"\n" + b"1," * 10_001 + b"\n", "columns"),
], ids=["zip-bomb", "zip-ratio", "zip-parts", "zip-path", "csv-header", "csv-cr", "csv-quoted"])
def test_a_file_that_would_blow_up_when_read_is_refused_before_it_is(content, why):
    """Uploads and links share the one reader: a workbook is a zip whose parts openpyxl unpacks
    whole (a 50 MB upload can declare gigabytes), and a CSV header can name millions of
    columns. Checked before parsing; zipfile never unpacks past a part's declared size."""
    from agents.errors import IngestionError
    from connectors.csv_connector import _read_bytes
    suffix = ".csv" if why == "columns" else ".xlsx"
    with pytest.raises(IngestionError) as err:
        _read_bytes(content, suffix)
    assert why in err.value.user_message


def test_a_wide_table_behind_a_preamble_never_reads_wide():
    """The layout may take a preamble's shape (here one column): what is read is never wider
    than the header the reader chose."""
    from connectors.csv_connector import _read_bytes
    content = (b"Sales report\nGenerated 2026-11-20\n" + b"a," * 10_001 + b"\n"
               + b"1," * 10_001 + b"\n")
    with pytest.raises(IngestionError, match="too many columns"):   # pinned (test analyst)
        _read_bytes(content, ".csv")


def test_a_header_split_by_quoted_newlines_is_still_counted():
    """Audit pass 2 (test analyst): the cheap per-line gate can't see a header whose quoted
    fields hold newlines; only the exact header read does, and nothing tested it."""
    from connectors.csv_connector import _read_bytes, _readable_size
    content = b'"a\nb",' * 10_001 + b"\n" + b"1," * 10_001 + b"\n"
    _readable_size(content, ".csv")   # the cheap gate passes it
    with pytest.raises(IngestionError, match="too many columns"):
        _read_bytes(content, ".csv")


def test_an_ordinary_workbook_still_reads():
    from connectors.csv_connector import _read_bytes
    assert _read_bytes(_xlsx(), ".xlsx")[0].shape == (3, 3)


@pytest.mark.parametrize("kind", ["html", "status", "private", "loop", "cap", "unreachable", "ok"])
async def test_a_link_token_is_never_in_a_message_or_a_log(client, monkeypatch, caplog, kind):
    """P58-06: a share link carries its capability in the URL (SharePoint `e=`, Dropbox `rlkey`,
    a signed URL's `sig=`/`token=`, the file id in the path). Whatever happens, no message the
    user reads and no log line holds it."""
    import connectors.link as link
    caplog.set_level(logging.DEBUG)
    for name in ("httpx", "httpcore"):   # as the app's install() sets them (test_public_http)
        monkeypatch.setattr(logging.getLogger(name), "level", logging.WARNING)
    monkeypatch.setattr(link, "MAX_BYTES", 40)
    answers = {"html": httpx.Response(200, text="<html>", headers={"content-type": "text/html"}),
               "status": httpx.Response(403), "loop": httpx.Response(302, headers={
                   "location": "https://files.example/1AbCdEfGhIjKlMnOpQrStUvWxYz?sig=SIGSECRET"}),
               "cap": httpx.Response(200, content=CSV * 10, headers={"content-type": "text/csv"}),
               "ok": httpx.Response(200, content=CSV, headers={"content-type": "text/csv"})}

    def handler(request):
        if kind == "unreachable":
            raise httpx.ConnectError("refused")
        return answers.get(kind, answers["ok"])
    serve(monkeypatch, handler, dns=lambda host, n: ["10.0.0.1"] if kind == "private"
          else ["93.184.215.14"])
    r = await _link(client, "https://files.example/1AbCdEfGhIjKlMnOpQrStUvWxYz/q.csv"
                            "?sig=SIGSECRET&token=TOKSECRET&rlkey=RLKEYSECRET&e=EKEYSECRET")
    said = r.text + "\n".join(rec.getMessage() for rec in caplog.records)
    assert not [s for s in SECRETS if s in said], said


# The refusals in the product's own words (each names the host, never the link) and the edges
# of what is fetched: the cap, the hop count, the sniff, the name.

def _ok(content=CSV, **headers):
    return httpx.Response(200, content=content, headers=headers)


async def _refused(monkeypatch, handler, link="https://files.example/q.csv?sig=SIGSECRET",
                   dns=None):
    from connectors.link import download
    serve(monkeypatch, handler, dns=dns)
    with pytest.raises(IngestionError) as err:
        await download(link)
    return err.value


NOT_PUBLIC = ("The link to files.example didn't give a file ({}): it may not be public. Share it "
              "as 'Anyone with the link', and link to a CSV or Excel file.")


@pytest.mark.parametrize(("answer", "why"), [
    (httpx.Response(403), "it answered HTTP 403"),
    # the type says a page, the body is CSV-shaped: still a page
    (_ok(CSV, **{"content-type": "text/html; charset=utf-8"}), "a web page came back, not a file"),
    # the body says a page whatever the type: after leading whitespace, in any case
    (_ok(b" \r\n\t<!DOCTYPE HTML>\n<body>x</body>", **{"content-type": "application/octet-stream"}),
     "a web page came back, not a file"),
    (_ok(b"\n<HTML><body>x</body></HTML>"), "a web page came back, not a file"),
    # audit pass 2 (code review): the type's case, a page starting <head> or <!--, a BOM
    (_ok(CSV, **{"content-type": "Text/HTML"}), "a web page came back, not a file"),
    (_ok(b"<!-- sign in -->\n<p>x</p>"), "a web page came back, not a file"),
    (_ok(b"\xef\xbb\xbf<head><title>x</title></head>"), "a web page came back, not a file"),
    # audit pass 2 (silent-failure hunter): an empty Location looped to 'redirected 5 times'
    (httpx.Response(302, headers={"location": ""}), "it redirected nowhere"),
], ids=["status", "html-type", "doctype", "html-tag", "type-case", "comment", "bom-head",
        "empty-location"])
async def test_a_link_that_gives_no_file_is_refused_in_these_words(monkeypatch, answer, why):
    err = await _refused(monkeypatch, lambda r: answer)
    assert str(err) == "ingestion: link not public"
    assert err.user_message == NOT_PUBLIC.format(why)


async def test_a_redirect_off_https_is_refused_in_these_words(monkeypatch):
    err = await _refused(monkeypatch, _chain(("https://files.example/", httpx.Response(
        302, headers={"location": "http://files.example/q.csv?sig=SIGSECRET"}))))
    assert str(err) == "ingestion: link not https"
    assert err.user_message == ("The link to files.example has to be an https link "
                                "(every redirect too).")


async def test_a_link_with_no_host_is_called_the_link(monkeypatch):
    err = await _refused(monkeypatch, lambda r: _ok(), link="http:///q.csv?sig=SIGSECRET")
    assert err.user_message == "The link to the link has to be an https link (every redirect too)."


async def test_a_host_off_the_public_internet_is_refused_in_these_words(monkeypatch):
    err = await _refused(monkeypatch, lambda r: _ok(), dns={"files.example": ["10.0.0.7"]})
    assert str(err) == "ingestion: link host refused"
    assert err.user_message == ("The link to files.example leads to an address this server "
                                "doesn't fetch from (only the public internet).")


async def test_an_unreachable_host_is_refused_in_these_words(monkeypatch):
    def handler(request):
        raise httpx.ConnectError("refused https://files.example/q.csv?sig=SIGSECRET")
    err = await _refused(monkeypatch, handler)
    assert str(err) == "ingestion: link unreachable"
    assert err.user_message == "files.example couldn't be reached (ConnectError)."
    assert err.__cause__ is None and err.__suppress_context__   # the class, never its text


def _hops(n: int):
    """n redirects (/0 → /1 → … → /n), then the CSV."""
    def handler(request):
        i = int(request.url.path.strip("/"))
        return (httpx.Response(302, headers={"location": f"https://files.example/{i + 1}"})
                if i < n else _ok())
    return handler


async def test_max_hops_redirects_are_followed_and_one_more_is_refused(monkeypatch):
    from connectors.link import MAX_HOPS, download
    serve(monkeypatch, _hops(MAX_HOPS))
    assert (await download("https://files.example/0"))[0] == CSV
    err = await _refused(monkeypatch, _hops(MAX_HOPS + 1), link="https://files.example/0")
    assert str(err) == "ingestion: link redirects"
    assert err.user_message == "The link to files.example redirected more than 5 times."


async def test_a_relative_redirect_is_followed_from_where_it_was_said(monkeypatch):
    from connectors.link import download
    wire = serve(monkeypatch, _chain(
        ("https://files.example/a/start", httpx.Response(302, headers={"location": "../b/q.csv"})),
        ("https://files.example/b/q.csv", _ok())))
    assert await download("https://files.example/a/start") == (CSV, ".csv", "files.example.link")
    assert [(r.method, str(r.url)) for r in wire.requests] == [
        ("GET", "https://files.example/a/start"), ("GET", "https://files.example/b/q.csv")]


async def test_the_cap_is_exactly_max_bytes_counted_across_chunks(monkeypatch):
    """A body larger than one read: the count is the whole body's, and MAX_BYTES itself passes."""
    import connectors.link as link
    body = b"n\n" + b"1\n" * 60_000          # 120 KB: several 64 KB reads
    serve(monkeypatch, lambda r: _ok(body))
    monkeypatch.setattr(link, "MAX_BYTES", len(body))
    assert (await link.download("https://files.example/q.csv"))[0] == body
    monkeypatch.setattr(link, "MAX_BYTES", len(body) - 1)
    with pytest.raises(link.TooLarge) as err:
        await link.download("https://files.example/q.csv?sig=SIGSECRET")
    assert str(err.value) == "ingestion: link too large"
    assert err.value.user_message == ("The file at files.example is larger than 50 MB, "
                                      "the upload limit.")


async def test_the_fetch_has_a_60_second_timeout(monkeypatch):
    import connectors.link as link
    seen = []
    real = link.public_client
    monkeypatch.setattr(link, "public_client", lambda **k: seen.append(k) or real(**k))
    serve(monkeypatch, lambda r: _ok())
    await link.download("https://files.example/q.csv")
    assert seen == [{"timeout": 60}]


@pytest.mark.parametrize(("link", "headers", "name"), [
    ("https://files.example/a/b/q3%20orders.csv", {}, "q3 orders.csv"),
    ("https://files.example/a/q3.csv/", {}, "q3.csv"),             # a trailing slash
    ("https://files.example/exports/q3.XLSX", {}, "q3.XLSX"),
    ("https://files.example", {}, "files.example.link"),             # no path: the host
    ("https://files.example/", {}, "files.example.link"),
    ("https://files.example/a/export", {}, "files.example.link"),    # no dot: the host
    ("https://files.example/x",
     {"content-disposition": "attachment; filename*=UTF-8''Q3%20%C3%A9.csv"}, "Q3 é.csv"),
    ("https://files.example/x", {"content-disposition": "attachment; filename=orders.csv"},
     "orders.csv"),
    ("https://files.example/a/q.csv", {"content-disposition": "attachment; filename=unspecified"},
     "q.csv"),
    ("https://files.example/a/q.csv", {"content-disposition": "inline"}, "q.csv"),
])
async def test_the_file_is_named_by_the_server_else_the_path_else_the_host(monkeypatch, link,
                                                                           headers, name):
    from connectors.link import download
    serve(monkeypatch, lambda r: _ok(CSV, **headers))
    assert await download(link) == (CSV, ".csv", name)


async def test_only_the_first_512_bytes_are_sniffed_for_a_page(monkeypatch):
    """The sniff reads a bounded head: a page tag past it is the reader's to refuse."""
    from connectors.link import download
    serve(monkeypatch, lambda r: _ok(b" " * 508 + b"<html>"))
    assert (await download("https://files.example/q.csv"))[1] == ".csv"


@pytest.mark.parametrize(("shared", "direct"), [
    ("https://docs.google.com/spreadsheets/d/1AbC/edit?gid=7",
     "https://docs.google.com/spreadsheets/d/1AbC/export?format=csv&gid=7"),
    ("https://onedrive.live.com/view.aspx?resid=AB!123",
     "https://onedrive.live.com/view.aspx?resid=AB!123&download=1"),
    # a blank parameter is kept, the fragment dropped
    ("https://www.dropbox.com/scl/fi/abc/q.csv?rlkey=r1&st=&dl=0#top",
     "https://www.dropbox.com/scl/fi/abc/q.csv?rlkey=r1&st=&dl=1"),
    # 5.8b audit (code review): "Publish to web" links name a published id, not a sheet's
    ("https://docs.google.com/spreadsheets/d/e/2PACX-1vQabc/pubhtml",
     "https://docs.google.com/spreadsheets/d/e/2PACX-1vQabc/pub?output=csv"),
    ("https://docs.google.com/spreadsheets/d/e/2PACX-1vQabc/pubhtml?gid=5&single=true",
     "https://docs.google.com/spreadsheets/d/e/2PACX-1vQabc/pub?output=csv&gid=5"),
])
def test_more_share_links_are_rewritten_to_their_file(shared, direct):
    from connectors.link import direct_url
    assert direct_url(shared) == direct


# ── 5.8b audit pass 1 (silent-failure hunter, code review, test analyst) ────────────────────

@pytest.mark.parametrize("encoding", ["gzip", "zstd", "br", "deflate"])
async def test_a_compressed_answer_is_refused_not_unpacked(client, monkeypatch, encoding):
    """Asked for identity, a server may still compress; httpx would unpack a chunk to any size
    before the cap counted it (a zstd bomb). Refused, counted raw."""
    import gzip
    import zlib

    import zstandard
    packed = {"gzip": gzip.compress, "deflate": zlib.compress, "br": lambda b: b,
              "zstd": zstandard.ZstdCompressor().compress}[encoding](CSV)
    serve(monkeypatch, lambda r: httpx.Response(200, content=packed, headers={
        "content-type": "text/csv", "content-encoding": encoding}))
    r = await _link(client, "https://files.example/q.csv")
    assert r.status_code == 400
    assert r.json()["detail"] == ("The file at files.example couldn't be read: it compressed its "
                                  f"answer ({encoding}) though asked not to.")


def _parquet() -> bytes:
    buf = io.BytesIO()
    pd.read_csv(io.BytesIO(CSV)).to_parquet(buf, index=False)
    return buf.getvalue()


@pytest.mark.parametrize(("body", "ctype"), [
    (b'[{"order_id": 1, "region": "North", "amount": 10.5}, {"order_id": 2, "region": "South",'
     b' "amount": 3.25}, {"order_id": 3, "region": "North", "amount": 7.0}]', "application/json"),
    (b'\xef\xbb\xbf{"order_id": 1, "region": "North", "amount": 10.5}\n{"order_id": 2, "region":'
     b' "South", "amount": 3.25}\n{"order_id": 3, "region": "North", "amount": 7.0}\n',
     "application/octet-stream"),
    ("parquet", "application/octet-stream"),
])
async def test_a_json_or_parquet_link_reads_as_its_own_kind(client, monkeypatch, body, ctype):
    """A JSON link was read as a CSV (its first record the header) and Parquet as 'not a
    table': each file goes to its own reader, as an upload of it would."""
    from connectors.link import download
    content = _parquet() if body == "parquet" else body
    serve(monkeypatch, lambda r: httpx.Response(200, content=content, headers={"content-type": ctype}))
    got, suffix, _ = await download("https://files.example/data")
    from connectors.csv_connector import _read_bytes
    df = _read_bytes(got, suffix)[0]
    assert list(df.columns) == ["order_id", "region", "amount"] and len(df) == 3


async def test_an_old_xls_link_is_refused_as_its_upload_is(client, monkeypatch):
    from connectors.csv_connector import XLS_REFUSAL
    serve(monkeypatch, lambda r: httpx.Response(200, content=b"\xd0\xcf\x11\xe0" + b"\0" * 600))
    r = await _link(client, "https://files.example/old.xls")
    assert (r.status_code, r.json()["detail"]) == (400, XLS_REFUSAL)


@pytest.mark.parametrize("status", [429, 500, 502, 503])
async def test_a_busy_or_broken_host_is_not_called_a_sharing_problem(client, monkeypatch, status):
    serve(monkeypatch, lambda r: httpx.Response(status))
    r = await _link(client, "https://files.example/q.csv")
    assert r.json()["detail"] == (f"files.example is having trouble (HTTP {status}). Try again "
                                  "in a few minutes.")


async def test_a_host_that_doesnt_exist_is_said_so(client, monkeypatch):
    import socket

    def missing(host, *a, **k):
        raise socket.gaierror("nodename nor servname provided")
    serve(monkeypatch, lambda r: _ok(CSV))
    monkeypatch.setattr(socket, "getaddrinfo", missing)
    r = await _link(client, "https://flies.example/q.csv")
    assert r.json()["detail"] == "The link's host 'flies.example' wasn't found. Check the link."


@pytest.mark.parametrize("link", ["https://[::1/x.csv", "https://files.example/\x00.csv",
                                  "not a link at all"])
async def test_a_malformed_link_is_a_400_not_a_server_error(client, monkeypatch, link):
    serve(monkeypatch, lambda r: _ok(CSV))
    r = await _link(client, link)
    assert r.status_code == 400 and "\x00" not in r.text


async def test_a_redirect_to_a_malformed_location_is_a_400(client, monkeypatch):
    serve(monkeypatch, lambda r: httpx.Response(302, headers={"location": "https://[bad/x"}))
    r = await _link(client, "https://files.example/q.csv")
    assert r.status_code == 400


async def test_a_download_that_trickles_stops_at_the_deadline(client, monkeypatch):
    """httpx's timeout is per read: a byte every 59 s held a worker for days. The whole download
    has one deadline."""
    import asyncio

    import connectors.link as link
    import tests.fake_network as fake
    monkeypatch.setattr(link, "DEADLINE_S", 0.3)
    serve(monkeypatch, lambda r: _ok(CSV))
    real = fake._Stream.read

    async def trickle(self, max_bytes, timeout=None):
        await asyncio.sleep(0.2)
        return await real(self, 1, timeout)
    monkeypatch.setattr(fake._Stream, "read", trickle)
    r = await _link(client, "https://files.example/q.csv")
    assert r.json()["detail"] == "files.example took longer than 0.3 s to send the file."
