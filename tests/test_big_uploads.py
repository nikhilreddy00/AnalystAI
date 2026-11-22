"""Bigger uploads (5.8c, ADR-0010 amendment): the cap rises on today's one reader to what a measured
read holds (100 MB and 25 million cells: a peak under 1.5 GB and 10 s, measured as the process).
The must-read corpus comes first (5.8b hardening, D1-38: every gate built from attacks alone):
the normal files the reader must still read the same, then the bounds."""
from __future__ import annotations

import io
import re
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest
from hypothesis import given, settings, strategies as st

from agents.errors import IngestionError
from connectors.csv_connector import MAX_UPLOAD_MB, _first_lines, _read_bytes

ROOT = Path(__file__).resolve().parents[1]


# ── the must-read corpus: normal files, each read as before ────────────────────────────────────

def _old_way(content: bytes, encoding: str, **kw) -> pd.DataFrame:
    """The parse before 5.8c: the decoded text through io.StringIO (a stand-in kept only to prove
    the bytes path reads the same)."""
    return pd.read_csv(io.StringIO(content.decode(encoding)), index_col=False, low_memory=False, **kw)


@pytest.mark.parametrize(("name", "content", "encoding"), [
    ("utf-8 with a BOM", "﻿name,city,amount\nAnn,Köln,1.5\nBo,Zürich,2\n".encode(), "utf-8-sig"),
    ("UTF-16 with a BOM", "name,city,amount\nAnn,Köln,1.5\nBo,Zürich,2\n".encode("utf-16"), "utf-16"),
    ("cp1252 (Windows export)", "name,city,amount\nAnn,Café,1.5\nBo,Señor,2\n".encode("cp1252"),
     "cp1252"),
    ("a long quoted cell", b'id,note\n1,"' + b"x, y\n" * 400_000 + b'"\n2,short\n', "utf-8"),
    ("\\r line ends (old Mac)", b"a,b\r1,2\r3,4\r", "utf-8"),
])
def test_a_normal_file_reads_as_the_text_path_read_it(name, content, encoding):
    df, info = _read_bytes(content, ".csv")
    old = _old_way(content, encoding)
    assert list(df.columns) == list(old.columns), name
    assert len(df) == len(old), name
    pd.testing.assert_frame_equal(df.reset_index(drop=True), _read_bytes_frame_of(old), check_dtype=False)


def _read_bytes_frame_of(old: pd.DataFrame) -> pd.DataFrame:
    from connectors.base import normalize_frame
    return normalize_frame(old).reset_index(drop=True)


def test_a_semicolon_file_with_decimal_commas_reads_its_numbers():
    df, info = _read_bytes("name;amount;qty\nAnn;1.249,00;3\nBo;12,50;4\nCy;7,25;1\n".encode(), ".csv")
    assert info["delimiter"] == ";" and info["decimal"] == ","
    assert df["amount"].tolist() == [1249.0, 12.5, 7.25] and df["qty"].tolist() == [3, 4, 1]


@pytest.mark.parametrize(("content", "suffix"), [
    (b'[{"id": 1, "region": "North", "amount": 2.76}, {"id": 2, "region": "South", "amount": 3.1}]',
     ".json"),
    (b'\xef\xbb\xbf[{"id": 1, "region": "North", "amount": 2.76}, '
     b'{"id": 2, "region": "South", "amount": 3.1}]', ".json"),
    (b'{"id": 1, "region": "North", "amount": 2.76}\n{"id": 2, "region": "South", "amount": 3.1}\n',
     ".jsonl"),
    (b'{"id": 1, "region": "North", "amount": 2.76}\r\n\r\n'
     b'{"id": 2, "region": "South", "amount": 3.1}', ".json"),
])
def test_json_records_read(content, suffix):
    """Normal files first (D1-38): an array of objects (a BOM too), JSON Lines (\\r\\n, a blank)."""
    df, _ = _read_bytes(content, suffix)
    assert df.to_dict("list") == {"id": [1, 2], "region": ["North", "South"], "amount": [2.76, 3.1]}


def test_one_json_object_is_one_record():
    df, _ = _read_bytes(b'{"id": 1, "holder": {"name": "Ann"}}', ".jsonl")
    assert df.to_dict("list") == {"id": [1], "holder_name": ["Ann"]}


@pytest.mark.parametrize("content", [
    b"[[0, 1, 2], [3, 4, 5]]",              # arrays of arrays
    b'[{"a": 1}, 2, "x"]',                  # a record and two scalars
    b"[]",
    '[{"a": "é"}]'.encode("latin-1"),       # not UTF-8: JSON's own encoding
])
def test_json_that_isnt_records_is_refused_in_words(content):
    """An allow list (5.8c review 3): only the shape counted is read."""
    with pytest.raises(IngestionError) as exc:
        _read_bytes(content, ".json")
    assert "list of records" in exc.value.user_message


def test_parquet_with_every_logical_type_reads():
    import datetime as dt
    import decimal
    import pyarrow as pa
    import pyarrow.parquet as pq
    table = pa.table({
        "i8": pa.array([1, -2], pa.int8()), "u32": pa.array([1, 4_000_000_000], pa.uint32()),
        "i64": pa.array([2**60, None], pa.int64()), "f32": pa.array([0.5, 1.25], pa.float32()),
        "b": pa.array([True, None]), "s": pa.array(["a", None]),
        "cat": pa.array(["x", "y"]).dictionary_encode(),
        "d": pa.array([dt.date(2024, 3, 1), dt.date(2024, 3, 2)], pa.date32()),
        "ts": pa.array([dt.datetime(2024, 3, 1, 9), None], pa.timestamp("ms")),
        "tsz": pa.array([dt.datetime(2024, 3, 1, 9, tzinfo=dt.timezone.utc)] * 2,
                        pa.timestamp("us", tz="UTC")),
        "dec": pa.array([decimal.Decimal("12.34"), decimal.Decimal("-0.01")], pa.decimal128(10, 2)),
    })
    buf = io.BytesIO()
    pq.write_table(table, buf)
    df, _ = _read_bytes(buf.getvalue(), ".parquet")
    assert len(df) == 2 and list(df.columns) == table.column_names
    assert df["i64"].iloc[0] == 2**60 and df["u32"].iloc[1] == 4_000_000_000
    assert [float(v) for v in df["dec"]] == [12.34, -0.01]
    assert df["d"].notna().all() and df["tsz"].notna().all()


def test_parquet_list_and_struct_columns_read_as_json_text():
    """As the JSON reader writes a list (5.8c: a parquet list column crashed normalize_frame)."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    buf = io.BytesIO()
    pq.write_table(pa.table({"id": [1, 2], "tags": [["a", "b"], []],
                             "addr": [{"city": "Oslo", "zip": 150}, None]}), buf)
    df, _ = _read_bytes(buf.getvalue(), ".parquet")
    assert df["tags"].tolist() == ['["a", "b"]', "[]"]
    assert df["addr"].iloc[0] == '{"city": "Oslo", "zip": 150}' and pd.isna(df["addr"].iloc[1])


@settings(max_examples=300, deadline=None)
@given(st.lists(st.text(st.sampled_from("ab,\"é"), max_size=6), max_size=40),
       st.lists(st.sampled_from(["\n", "\r\n", "\r", "\x1c", " ", "\x0b"]), min_size=40,
                max_size=40), st.integers(0, 50))
def test_the_first_lines_are_splitlines_first_lines(cells, ends, n):
    text = "".join(c + e for c, e in zip(cells, ends))
    assert _first_lines(text, n) == text.splitlines()[:n]


@pytest.mark.parametrize("cut", [2**16 - 1, 2**16, 2**16 + 1, 2**18 - 1, 2**18])
def test_the_first_lines_hold_across_the_head_cut(cut):
    """The head grows in steps of 4: a \\r\\n split by the cut, or a line ending on it."""
    text = "x" * (cut - 1) + "\r\n" + "y\n" * 300
    assert _first_lines(text, 200) == text.splitlines()[:200]
    assert _first_lines("z" * cut + "\n", 5) == ["z" * cut]


# ── the bounds ─────────────────────────────────────────────────────────────────────────────────

def test_the_cap_is_the_measured_one_everywhere():
    """One constant: the backend, the frontend's label and link copy, the README source list."""
    assert MAX_UPLOAD_MB == 100
    ui = (ROOT / "frontend/src/components/ConnectorSetup.tsx").read_text()
    labels = re.findall(r"max (\d+) MB", ui)
    assert labels and set(labels) == {str(MAX_UPLOAD_MB)}


def _in_a_child(body: str) -> list[str]:
    from tests.conftest import PEAK_MB
    out = subprocess.run([sys.executable, "-c", PEAK_MB + body], capture_output=True, text=True,
                         timeout=300, cwd=str(ROOT))
    assert out.returncode == 0, out.stderr[-2000:]
    print(out.stdout)
    return out.stdout.split()


def test_a_long_narrow_file_at_the_cap_reads_in_bounds_measured_as_the_process():
    """P58-36: the peak is the child's own. 5.8c measured (macOS): 1.28 GB, 5.3 s at 100 MB."""
    took, peak, rows = _in_a_child("""
import datetime, random, time
from connectors.csv_connector import MAX_UPLOAD_MB, _read_bytes
random.seed(1)
d0, stores, lines = datetime.date(2023, 1, 1), [f"Store {i}" for i in range(40)], []
n, size = 0, 0
while size < MAX_UPLOAD_MB * 2**20 - 200:
    n += 1
    line = (f"{n},{d0 + datetime.timedelta(days=n % 900)},{random.choice(stores)},SKU-{n % 3000:05d},"
            f"{n % 9 + 1},{n % 99999 / 100},C{n % 200000:07d},{'completed' if n % 17 else 'cancelled'}\\n")
    lines.append(line)
    size += len(line)
content = ("order_id,order_date,store,sku,quantity,unit_price,customer_id,status\\n"
           + "".join(lines[:-1])).encode()
del lines
started = time.monotonic()
df, _ = _read_bytes(content, ".csv")
print(round(time.monotonic() - started, 1), round(peak_mb()), len(df))
""")
    # memory is the gate (1.28 GB measured); time 5.2 s alone, 13 s under `-n 8`: the bound catches
    # a pathological parse (an O(n²) scan), not a loaded machine
    assert float(took) < 30 and float(peak) < 1500 and int(rows) > 1_500_000


_DENSE = {   # each under the byte cap, each past what a read may build
    # a 100 MB CSV of single digits is 52M cells and peaked at 2.49 GB (5.8c measured)
    "csv": ('.csv', 'b"a,b,c,d,e,f,g,h,i,j\\n" + b"0,1,2,3,4,5,6,7,8,9\\n" * 2_600_001'),
    # rows wider than their header: pandas builds every field, then drops the extras (security
    # review of WP1: lines × header width missed it)
    "csv wider rows": ('.csv', 'b"a,b\\n" + b"1,2\\n" * 300 + (b"0," * 9_000 + b"0\\n") * 3_000'),
    # JSON builds a dict a record: a 28 MB file of 4M cells peaked at 678 MB (5.8c measured)
    "json": ('.json', 'b"[" + b\'{"a":0,"b":1},\' * 4_100_000 + b\'{"a":0}]\''),
    "parquet": ('.parquet', '_pq({"a": pa.array([0] * 26_000_001, pa.int8())})'),
    # a list column: few rows and one column, millions of values (security review of WP1)
    "parquet nested": ('.parquet', '_pq({"a": pa.array([list(range(1_000_000))] * 26)})'),
    # long strings: few cells, gigabytes once unpacked (repeated text compresses to nothing)
    "parquet long strings": ('.parquet', '_pq({"a": pa.array(["x" * 10_000_000] * 60)})'),
    # the same stored plainly and compressed: decoded before any measure of it (security
    # review 2 of WP1): the footer's declared unpacked size is read first
    "parquet plain long strings": ('.parquet', '_pq({"a": pa.array(["x" * 10_000_000] * 60)}, '
                                   'use_dictionary=False, compression="zstd")'),
    # values under a list: each a Python object once read (security review 2)
    "parquet nested values": ('.parquet', '_pq({"a": pa.array([list(range(500_000))] * 6)})'),
    # sparse JSON: each record its own key, a frame of records × keys (security review 2)
    "json sparse keys": ('.json', '("[" + ",".join(f\'{{"k{i}":0}}\' for i in range(20_000)) '
                         '+ "]").encode()'),
}


@pytest.mark.parametrize("shape", list(_DENSE))
def test_a_file_past_the_cell_bound_is_refused_before_it_is_parsed(shape, tmp_path):
    """The bytes cap alone doesn't bound a read; the declared or counted cells (and a parquet
    file's unpacked size) do, before the parse. Built in one child, read in a fresh one: the
    reader's peak is its own, never the build's."""
    suffix, build = _DENSE[shape]
    path = tmp_path / f"dense{suffix}"
    _in_a_child(f"""
import io
import pyarrow as pa, pyarrow.parquet as pq
def _pq(cols, **kw):
    buf = io.BytesIO()
    pq.write_table(pa.table(cols), buf, **kw)
    return buf.getvalue()
open({str(path)!r}, "wb").write({build})
""")
    took, peak, said = _in_a_child(f"""
import time
from agents.errors import IngestionError
from connectors.csv_connector import _read_bytes
content = open({str(path)!r}, "rb").read()
started = time.monotonic()
try:
    _read_bytes(content, {suffix!r})
    said = "read"
except IngestionError as e:
    said = "refused"   # in its own words (the cell bound, or rows that don't line up)
print(round(time.monotonic() - started, 1), round(peak_mb()), said)
""")
    # a parse of any of these: 1 GB+; the file and the imports alone ~250 MB.
    # time: 0.9 s alone, 2.0 s under `-n 8`; the bound catches a parse, not load
    assert said == "refused" and float(took) < 5 and float(peak) < 500


def test_a_file_just_under_the_cell_bound_reads():
    df, _ = _read_bytes(b"a,b\n" + b"1,2\n" * 1000, ".csv")
    assert len(df) == 1000


# ── the route: a body past the cap is refused before it is stored ─────────────────────────────

@pytest.fixture
async def client():
    from api.rate_limit import limiter
    from orchestrator.runner import in_memory_deps
    from tests.test_workspace_api import _client
    limiter.reset()
    async with _client(in_memory_deps()) as c:
        yield c


async def test_a_body_declared_past_the_cap_is_refused_unread(client):
    r = await client.post("/connectors/upload", content=b"x" * 10,
                          headers={"content-length": str((MAX_UPLOAD_MB + 2) * 2**20),
                                   "content-type": "multipart/form-data; boundary=b"})
    assert r.status_code == 413 and f"max {MAX_UPLOAD_MB} MB" in r.json()["detail"]


async def test_a_streamed_body_past_the_cap_is_refused(client, monkeypatch):
    """No Content-Length (chunked): counted as it arrives, never spooled whole."""
    monkeypatch.setattr("api.main.MAX_BODY", 4096)
    async def chunks():   # a well-formed part whose file runs past the limit
        yield (b'--b\r\nContent-Disposition: form-data; name="file"; filename="a.csv"\r\n'
               b"Content-Type: text/csv\r\n\r\n")
        for _ in range(10):
            yield b"x" * 1000
    r = await client.post("/connectors/upload", content=chunks(),
                          headers={"content-type": "multipart/form-data; boundary=b"})
    assert r.status_code == 413


async def test_a_file_at_the_cap_is_accepted_and_one_past_it_refused(client, monkeypatch):
    monkeypatch.setattr("api.routes.connectors.MAX_UPLOAD_MB", 1)
    head = b"order_id,amount\n"
    row = b"1,2.5\n"
    at_cap = head + row * ((2**20 - len(head)) // len(row))
    ok = await client.post("/connectors/upload", files={"file": ("a.csv", at_cap, "text/csv")})
    over = await client.post("/connectors/upload",
                             files={"file": ("b.csv", at_cap + row * 2, "text/csv")})
    assert ok.status_code == 200, ok.text
    assert over.status_code == 413 and "max 1 MB" in over.json()["detail"]


def test_refusals_say_the_cap_in_words():
    with pytest.raises(IngestionError) as exc:
        _read_bytes(b"a,b,c,d,e,f,g,h,i,j\n" + b"0,1,2,3,4,5,6,7,8,9\n" * 2_600_001, ".csv")
    assert "25 million cells" in exc.value.user_message
