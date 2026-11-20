"""Share links (5.8b, ADR-0010): a public Google Sheet, Drive, OneDrive/SharePoint or Dropbox link,
or any https CSV/xlsx URL, is downloaded once by the server through the guarded client (each
redirect hop checked where it connects, a byte cap) and becomes a dataset version exactly as an
upload of the same bytes would. No message, log line or stored field holds the link's token.
The route is the production path; the network is a fake under the production client (the
redirect chains research measured, docs/research/2026-11-20-5.8b-sources.md)."""
from __future__ import annotations

import io
import logging

import httpx
import pandas as pd
import pytest

from tests.fake_network import serve
from tests.test_workspace_api import _client

CSV = b"order_id,region,amount\n1,North,10.5\n2,South,3.25\n3,North,7.0\n"
SECRETS = ("SIGSECRET", "TOKSECRET", "RLKEYSECRET", "EKEYSECRET", "1AbCdEfGhIjKlMnOpQrStUvWxYz")


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
    from orchestrator.runner import in_memory_deps
    async with _client(in_memory_deps()) as c:
        yield c


async def _link(client, url: str, **extra):
    return await client.post("/connectors/link", json={"url": url, **extra})


@pytest.mark.parametrize(("shared", "direct"), [
    ("https://docs.google.com/spreadsheets/d/1AbC_d-9/edit#gid=123",
     "https://docs.google.com/spreadsheets/d/1AbC_d-9/export?format=csv&gid=123"),
    ("https://docs.google.com/spreadsheets/d/1AbC/edit?usp=sharing",
     "https://docs.google.com/spreadsheets/d/1AbC/export?format=csv&gid=0"),
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
        ("https://docs.google.com/spreadsheets/d/1AbC/export?format=csv&gid=0",
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


@pytest.mark.parametrize("content,why", [
    (_zip({"xl/worksheets/sheet1.xml": b"0" * (160 * 2**20)}), "unpacks"),   # 160 MB from ~160 KB
    (_zip({f"x{i}.xml": b"" for i in range(10_001)}), "parts"),
    (_zip({"../../etc/cron.d/x": b"a"}), "parts"),
    (b"a," * 10_001 + b"\n1\n", "columns"),
    (b"report,2026\n\n" + b"a," * 10_001 + b"\n1\n", "columns"),   # header past a preamble
    (b"x\r" + b"a;" * 10_001 + b"\r1\r", "columns"),                 # '\r' line endings
])
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
