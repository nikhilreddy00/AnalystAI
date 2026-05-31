from __future__ import annotations

from pathlib import Path

import pytest

from memory import session_data


@pytest.fixture
def tmp_session_dir(tmp_path, monkeypatch):
    # Point SESSION_DATA_DIR at a tmp path for this test only.
    monkeypatch.setattr(session_data, "_DATA_DIR", tmp_path)
    return tmp_path


def test_session_dir_creates_parent(tmp_session_dir):
    d = session_data.session_dir("abc-123")
    assert d == tmp_session_dir / "abc-123"
    assert d.is_dir()


def test_write_and_read_round_trip(tmp_session_dir):
    payload = b"some-parquet-bytes"
    path = session_data.write_cleaned_parquet("sess-1", payload)
    assert Path(path).is_file()
    assert Path(path).name == "cleaned_data.parquet"
    assert session_data.read_cleaned_parquet("sess-1") == payload


def test_read_missing_returns_none(tmp_session_dir):
    assert session_data.read_cleaned_parquet("never-existed") is None


def test_write_is_atomic_replace(tmp_session_dir):
    # Two writes to the same session should leave only the final bytes.
    session_data.write_cleaned_parquet("sess-2", b"v1")
    session_data.write_cleaned_parquet("sess-2", b"v2")
    assert session_data.read_cleaned_parquet("sess-2") == b"v2"
    # No stray .tmp left behind
    assert not (tmp_session_dir / "sess-2" / "cleaned_data.parquet.tmp").exists()


def test_delete_session_files_is_idempotent(tmp_session_dir):
    session_data.write_cleaned_parquet("sess-3", b"x")
    session_data.delete_session_files("sess-3")
    assert not (tmp_session_dir / "sess-3").exists()
    # Calling again on missing dir must not raise.
    session_data.delete_session_files("sess-3")
