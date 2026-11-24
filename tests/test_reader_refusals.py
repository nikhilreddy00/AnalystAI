"""The file reader's bounds (5.8c), each at its edge and each refusal by what it says: the mutation
gate found them tested only as "it refuses" (a refusal said in the wrong words, or one bound off by
one, passed). Through `_read_bytes`, the one reader for uploads, links and imports."""
from __future__ import annotations

import io
import json
import threading

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from agents.errors import IngestionError
from connectors import csv_connector as cc
from connectors.csv_connector import _first_lines, _read_bytes


def _refused(content: bytes, suffix: str) -> IngestionError:
    with pytest.raises(IngestionError) as e:
        _read_bytes(content, suffix)
    return e.value


def _json(obj) -> bytes:
    return json.dumps(obj).encode()


# ── JSON: an allow list of record shapes, counted while read ──────────────────────────────────

def test_a_nested_object_counts_a_column_per_path(monkeypatch):
    """{"a": {"x", "y"}} counts a_x and a_y (never a bare "a"), beside the inner names x and y
    (the count is a bound above the columns): 4. Two records shaped differently count every path
    of both, never the first one's again: x a_x y z b_y b_z, 6."""
    monkeypatch.setattr(cc, "_MAX_COLUMNS", 4)
    _read_bytes(_json([{"a": {"x": 1, "y": 2}}]), ".json")   # at the bound
    monkeypatch.setattr(cc, "_MAX_COLUMNS", 3)
    e = _refused(_json([{"a": {"x": 1, "y": 2}}]), ".json")
    assert str(e) == "ingestion: too many columns"
    assert e.user_message == ("The file has more than 3 columns, or a column name over 1,024 "
                              "characters, past what can be read.")
    monkeypatch.setattr(cc, "_MAX_COLUMNS", 5)
    _refused(_json([{"a": {"x": 1}}, {"b": {"y": 1, "z": 2}}]), ".json")
    monkeypatch.setattr(cc, "_MAX_COLUMNS", 1)
    _refused(_json([{"a": 1, "b": 2}]), ".json")   # two top-level names


def test_a_column_name_at_its_limit_reads_and_one_past_is_refused(monkeypatch):
    monkeypatch.setattr(cc, "_MAX_PATH", 4)
    assert list(_read_bytes(_json([{"abcd": 1}]), ".json")[0].columns) == ["abcd"]
    assert str(_refused(_json([{"abcde": 1}]), ".json")) == "ingestion: too many columns"


def test_the_cells_are_counted_before_and_after_the_parse(monkeypatch):
    """Before: each value follows a key's colon or a comma (+1); after: records × paths."""
    monkeypatch.setattr(cc, "_MAX_JSON_CELLS", 2)
    _read_bytes(_json([{"a": 1}]), ".json")             # 1 colon + 1 = 2: at the bound
    monkeypatch.setattr(cc, "_MAX_JSON_CELLS", 1)
    e = _refused(_json([{"a": 1}]), ".json")            # colons count
    assert (str(e), e.user_message) == ("ingestion: too many cells", cc._TOO_MANY_JSON_CELLS)
    e = _refused(b"[1, 2]", ".json")                    # commas count too
    assert str(e) == "ingestion: too many cells"
    monkeypatch.setattr(cc, "_MAX_JSON_CELLS", 8)       # 4 records × 2 paths = 8: at the bound
    _read_bytes(_json([{"a": 1}, {"b": 1}, {"a": 2}, {"b": 2}]), ".json")
    monkeypatch.setattr(cc, "_MAX_JSON_CELLS", 9)       # 7 values pass the first count; the
    e = _refused(_json([{"a": n} for n in range(5)] + [{"b": 1}]), ".json")   # parse: 6 × 2 > 9
    assert (str(e), e.user_message) == ("ingestion: too many cells", cc._TOO_MANY_JSON_CELLS)


@pytest.mark.parametrize("content, detail", [
    (b'"text"', "json shape"),          # JSON, not records
    (b"[1, 2]", "json shape"),          # an array of values
    (b"[]", "json shape"),              # no records
    (b"{not json", "json"),             # not JSON at all
])
def test_anything_but_records_is_said_as_not_records(content, detail):
    e = _refused(content, ".json")
    assert (str(e), e.user_message) == (f"ingestion: {detail}", cc._NOT_RECORDS)


# ── parquet: its decoded size measured from the footer and the dictionaries ────────────────────

def _parquet(table: pa.Table) -> bytes:
    out = io.BytesIO()
    pq.write_table(table, out)
    return out.getvalue()


def _unpacked(monkeypatch, limit: int, table: pa.Table):
    monkeypatch.setattr(cc, "_MAX_PARQUET_UNPACKED", limit)
    return _read_bytes(_parquet(table), ".parquet")


def test_text_is_measured_by_its_values_and_every_other_column_by_its_bytes(monkeypatch):
    """1,000 values of 10 characters are 10,000 bytes of text (no offsets: read as a dictionary);
    an int64 column of 1,000 is 8,000. Each text type alike: string, binary and their large forms."""
    for t in (pa.string(), pa.binary(), pa.large_string(), pa.large_binary()):
        text = pa.table({"t": pa.array([b"x" * 10 if "binary" in str(t) else "x" * 10] * 1000, t)})
        _unpacked(monkeypatch, 10_000, text)               # at the bound
        e = pytest.raises(IngestionError, _unpacked, monkeypatch, 9_999, text).value
        assert (str(e), e.user_message) == ("ingestion: parquet too large", cc._PARQUET_TOO_LARGE)
    # repeated ints: a small footer (dictionary pages), 8 bytes a row once read
    both = pa.table({"t": ["x" * 10] * 1000, "n": pa.array([7] * 1000, pa.int64())})
    n = pq.read_table(io.BytesIO(_parquet(both))).column("n").nbytes
    _unpacked(monkeypatch, 10_000 + n, both)
    with pytest.raises(IngestionError, match="parquet too large"):
        _unpacked(monkeypatch, 10_000 + n - 1, both)


def test_a_text_column_of_nulls_unpacks_to_nothing(monkeypatch):
    table = pa.table({"n": pa.array([7] * 1000, pa.int64()), "t": pa.array([None] * 1000, pa.string())})
    _unpacked(monkeypatch, pq.read_table(io.BytesIO(_parquet(table))).column("n").nbytes, table)


def test_a_list_of_dates_reads_as_json_text():
    import datetime as dt
    df = _read_bytes(_parquet(pa.table({"d": [[dt.date(2024, 1, 1)], None]})), ".parquet")[0]
    assert df["d"].iloc[0] == '["2024-01-01"]' and df["d"].isna().iloc[1]


def test_a_corrupt_parquet_file_is_said_as_no_table():
    e = _refused(b"PAR1 not a parquet file PAR1", ".parquet")
    assert e.user_message == cc._NOT_A_TABLE and str(e).startswith("ingestion: parquet: ")
    assert str(e) != "ingestion: parquet: NoneType"


# ── the first lines: bounded work, and an end ─────────────────────────────────────────────────

def test_the_first_lines_end_on_every_size_of_text():
    """Short text, text past the first head, text with fewer lines than asked: each returns (a
    loop that never grows, or never stops at the text's end, would hang the upload)."""
    texts = [("", 5), ("a\nb", 5), ("a\n" * 10, 3), ("x" * (1 << 17) + "\nb", 5), ("a\n" * 70_000, 2)]
    got = {}

    def run():
        for text, n in texts:
            got[len(got)] = _first_lines(text, n)
    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(5)
    assert not worker.is_alive(), "_first_lines never returned"
    assert [got[i] for i in range(len(texts))] == [t.splitlines()[:n] for t, n in texts]
