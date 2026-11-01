"""
Qdrant-backed vector store for institutional memory.
Embeds InsightReports using nomic-embed-text via Ollama.
Retrieves semantically similar past analyses to inject into the Insight Agent.
"""

from __future__ import annotations

import hashlib
import uuid as _uuid

import httpx
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance,
    FilterSelector,
    PointStruct,
    VectorParams,
    Filter,
    FieldCondition,
    MatchValue,
)

from orchestrator.state import AnalysisSession, ConnectorConfig, InsightReport

VECTOR_DIM = 768          # nomic-embed-text output dimension
COLLECTION = "analyst_memory"


def memory_key(session: AnalysisSession) -> str:
    """Which dataset a memory belongs to (ROADMAP: past insights are scoped to user + dataset).
    Phase 5.6b: the dataset id, so memory follows the dataset across re-uploads and versions.
    A thread with no dataset (a legacy CSV path) keeps the old per-source key."""
    return session.dataset_id or dataset_key(session.connector_config)


def dataset_key(cfg: ConnectorConfig | None) -> str:
    """A digest of where the data comes from, never the credentials (pre-5.6b memory key)."""
    if cfg is None:
        return ""
    where = [cfg.type.value, cfg.file_path, cfg.table_name, cfg.spreadsheet_id, cfg.sheet_name,
             cfg.url]
    return hashlib.sha256("|".join(str(w or "") for w in where).encode()).hexdigest()[:24]


class VectorStore:
    def __init__(self, qdrant_host: str, qdrant_port: int, ollama_host: str) -> None:
        self._client = AsyncQdrantClient(host=qdrant_host, port=qdrant_port)
        self._ollama_host = ollama_host

    async def init(self) -> None:
        collections = await self._client.get_collections()
        names = [c.name for c in collections.collections]
        if COLLECTION not in names:
            await self._client.create_collection(
                collection_name=COLLECTION,
                vectors_config=VectorParams(size=VECTOR_DIM, distance=Distance.COSINE),
            )

    async def _embed(self, text: str) -> list[float]:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{self._ollama_host}/api/embeddings",
                json={"model": "nomic-embed-text", "prompt": text},
            )
            response.raise_for_status()
            return response.json()["embedding"]

    async def store_insight(
        self,
        session_id: str,
        user_id: str,
        report: InsightReport,
        task_summary: str,
        dataset: str = "",
    ) -> None:
        text_to_embed = f"{task_summary}\n\n{report.executive_summary}\n\n" + \
                        "\n".join(report.technical_findings)
        vector = await self._embed(text_to_embed)
        # BUG-31: Use uuid5 for deterministic, collision-resistant point IDs
        point_id = int(_uuid.uuid5(_uuid.NAMESPACE_URL, session_id).int % (2**63))

        await self._client.upsert(
            collection_name=COLLECTION,
            points=[PointStruct(
                id=point_id,
                vector=vector,
                payload={
                    "session_id": session_id,
                    "user_id": user_id,
                    "dataset_key": dataset,
                    "executive_summary": report.executive_summary,
                    "technical_findings": report.technical_findings,
                    "task_summary": task_summary,
                    "generated_at": report.generated_at.isoformat(),
                },
            )],
        )

    async def retrieve_similar(
        self,
        query: str,
        user_id: str,
        dataset: str,
        top_k: int = 3,
        exclude_session: str | None = None,
    ) -> list[dict]:
        """Earlier insights by this user on this dataset, from other threads (a thread's own
        earlier report isn't "an earlier analysis"). Points stored before the dataset key
        existed don't match: another dataset's numbers must never read as this one's."""
        if not dataset:
            return []
        vector = await self._embed(query)
        results = await self._client.search(
            collection_name=COLLECTION,
            query_vector=vector,
            query_filter=Filter(
                must=[FieldCondition(key="user_id", match=MatchValue(value=user_id)),
                      FieldCondition(key="dataset_key", match=MatchValue(value=dataset))],
                must_not=[FieldCondition(key="session_id", match=MatchValue(value=exclude_session))]
                if exclude_session else None,
            ),
            limit=top_k,
            with_payload=True,
        )
        return [
            {
                "score": r.score,
                "session_id": r.payload["session_id"],
                "executive_summary": r.payload["executive_summary"],
                "task_summary": r.payload["task_summary"],
                "generated_at": r.payload["generated_at"],
            }
            for r in results
            if r.score > 0.6      # only return meaningfully similar results
        ]

    # ── Deletion (Phase 5.6b) ──────────────────────────────────────────────────

    async def _delete(self, key: str, value: str) -> None:
        await self._client.delete(collection_name=COLLECTION, points_selector=FilterSelector(
            filter=Filter(must=[FieldCondition(key=key, match=MatchValue(value=value))])))

    async def delete_session(self, session_id: str) -> None:
        await self._delete("session_id", session_id)

    async def delete_dataset(self, dataset: str) -> None:
        await self._delete("dataset_key", dataset)

    async def session_ids(self) -> set[str]:
        """Every thread that has a memory point (the orphan sweep).
        ponytail: scrolls the whole collection; page by user if it ever gets large."""
        out, offset = set(), None
        while True:
            points, offset = await self._client.scroll(
                collection_name=COLLECTION, limit=1000, offset=offset, with_payload=["session_id"],
                with_vectors=False)
            out |= {p.payload["session_id"] for p in points}
            if offset is None:
                return out
