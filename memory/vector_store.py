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
    PointStruct,
    VectorParams,
    Filter,
    FieldCondition,
    MatchValue,
)

from orchestrator.state import ConnectorConfig, InsightReport

VECTOR_DIM = 768          # nomic-embed-text output dimension
COLLECTION = "analyst_memory"


def dataset_key(cfg: ConnectorConfig | None) -> str:
    """Which dataset a memory belongs to (ROADMAP: past insights are scoped to user + dataset).
    A digest of where the data comes from, never the credentials."""
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
    ) -> list[dict]:
        """Earlier insights by this user on this dataset. Points stored before the dataset key
        existed don't match: another dataset's numbers must never read as this one's."""
        if not dataset:
            return []
        vector = await self._embed(query)
        results = await self._client.search(
            collection_name=COLLECTION,
            query_vector=vector,
            query_filter=Filter(
                must=[FieldCondition(key="user_id", match=MatchValue(value=user_id)),
                      FieldCondition(key="dataset_key", match=MatchValue(value=dataset))]
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
