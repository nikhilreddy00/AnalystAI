from __future__ import annotations

import pytest

from memory.session_store import InMemorySessionStore
from orchestrator.state import AnalysisSession, Message, MessageRole, DataContract, ColumnInfo, ConnectorType


pytestmark = pytest.mark.asyncio


def _make_session(
    sid: str,
    user_id: str = "u1",
    dataset_name: str | None = None,
    file_hash: str | None = None,
    first_message: str | None = None,
    row_count: int | None = None,
    column_count: int | None = None,
    status: str = "in_progress",
) -> AnalysisSession:
    s = AnalysisSession(session_id=sid, user_id=user_id)
    s.dataset_name = dataset_name
    s.file_hash = file_hash
    s.status = status
    if first_message:
        s.conversation_history.append(Message(role=MessageRole.USER, content=first_message))
    if row_count is not None and column_count is not None:
        s.data_contract = DataContract(
            source_type=ConnectorType.CSV,
            row_count=row_count,
            column_count=column_count,
            columns=[ColumnInfo(name=f"c{i}", dtype="int64", nullable=False, sample_values=[]) for i in range(column_count)],
            sample_rows=[],
            date_columns=[],
            id_columns=[],
            metric_columns=[],
            schema_summary="test",
        )
    return s


async def test_list_sessions_returns_summaries_for_user_only():
    store = InMemorySessionStore()
    await store.save(_make_session("a", user_id="u1", dataset_name="titanic", first_message="why?", row_count=10, column_count=3, status="completed"))
    await store.save(_make_session("b", user_id="u2", dataset_name="sales", first_message="show me revenue"))

    summaries = await store.list_sessions("u1")
    ids = [s.session_id for s in summaries]
    assert ids == ["a"]
    assert summaries[0].dataset_name == "titanic"
    assert summaries[0].original_query == "why?"
    assert summaries[0].row_count == 10
    assert summaries[0].column_count == 3
    assert summaries[0].status == "completed"


async def test_list_sessions_sorted_by_updated_at_desc():
    store = InMemorySessionStore()
    await store.save(_make_session("old"))
    await store.save(_make_session("new"))
    summaries = await store.list_sessions("u1")
    assert [s.session_id for s in summaries] == ["new", "old"]


async def test_find_by_hash_matches_user_and_hash():
    store = InMemorySessionStore()
    await store.save(_make_session("a", user_id="u1", file_hash="HASH1"))
    await store.save(_make_session("b", user_id="u2", file_hash="HASH1"))   # different user
    await store.save(_make_session("c", user_id="u1", file_hash="HASH2"))

    hit = await store.find_by_hash("u1", "HASH1")
    assert hit is not None
    assert hit.session_id == "a"
    assert await store.find_by_hash("u1", "HASH_MISSING") is None


async def test_find_by_hash_ignores_null_hash():
    store = InMemorySessionStore()
    await store.save(_make_session("a", user_id="u1", file_hash=None))
    # Passing None must not match other null-hash sessions.
    assert await store.find_by_hash("u1", None) is None  # type: ignore[arg-type]


async def test_get_summary_enforces_user_id():
    store = InMemorySessionStore()
    await store.save(_make_session("a", user_id="u1", dataset_name="t"))
    assert (await store.get_summary("a", "u1")).dataset_name == "t"
    assert await store.get_summary("a", "u2") is None
    assert await store.get_summary("missing", "u1") is None


async def test_delete_removes_session_files(tmp_path, monkeypatch):
    from memory import session_data
    monkeypatch.setattr(session_data, "_DATA_DIR", tmp_path)
    store = InMemorySessionStore()
    await store.save(_make_session("a", user_id="u1"))
    # Drop a parquet file as if cleaning had written one.
    session_data.write_cleaned_parquet("a", b"bytes")
    assert (tmp_path / "a" / "cleaned_data.parquet").exists()

    await store.delete("a")
    assert not (tmp_path / "a").exists()
