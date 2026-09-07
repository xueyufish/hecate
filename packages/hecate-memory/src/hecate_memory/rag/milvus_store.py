"""Milvus vector store adapter implementing the VectorStore ABC.

Wraps ``pymilvus`` (MilvusClient API, 2.4+) for standalone/cluster Milvus
deployments. Sparse vectors are not supported in this adapter —
``search_sparse`` returns an empty list so hybrid queries fall back to the
ABC's application-layer RRF fusion.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from hecate_memory.rag.types import SearchResult
from hecate_memory.rag.vector_store import VectorStore

logger = logging.getLogger(__name__)

_ID_FIELD_MAX_CHARS = 64


class MilvusVectorStore(VectorStore):
    """VectorStore implementation backed by Milvus (pymilvus MilvusClient).

    Collections use an explicit schema: VARCHAR primary key ``id`` (our
    chunk UUIDs), FLOAT_VECTOR ``vector`` with COSINE metric, TEXT ``text``,
    TEXT ``metadata_json`` and an optional ``workspace_id`` field used for
    tenant-isolated search filtering.
    """

    def __init__(self, uri: str = "http://localhost:19530", token: str = "") -> None:
        self.uri = uri
        self.token = token
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                from pymilvus import MilvusClient

                self._client = MilvusClient(uri=self.uri, token=self.token or "")
                logger.info(f"Connected to Milvus at {self.uri}")
            except ImportError:
                logger.warning("pymilvus not installed. Using mock client.")
                self._client = "mock"
        return self._client

    async def create_collection(
        self,
        collection_name: str,
        vector_size: int = 1024,
        with_sparse: bool = True,
    ) -> bool:
        client = self._get_client()

        if client == "mock":
            logger.info(f"Mock: Created collection {collection_name}")
            return True

        try:
            if client.has_collection(collection_name):
                return True
            from pymilvus import DataType, MilvusClient

            schema = MilvusClient.create_schema(auto_id=False, enable_dynamic_field=True)
            schema.add_field("id", DataType.VARCHAR, is_primary=True, max_length=_ID_FIELD_MAX_CHARS)
            schema.add_field("vector", DataType.FLOAT_VECTOR, dim=vector_size)
            schema.add_field("text", DataType.TEXT, nullable=True)
            schema.add_field("metadata_json", DataType.TEXT, nullable=True)
            schema.add_field("workspace_id", DataType.VARCHAR, max_length=_ID_FIELD_MAX_CHARS, nullable=True)
            index_params = client.prepare_index_params()
            index_params.add_index(field_name="vector", index_type="AUTOINDEX", metric_type="COSINE")
            client.create_collection(collection_name, schema=schema, index_params=index_params)
            logger.info(f"Created collection {collection_name}")
            return True
        except Exception as e:
            logger.error(f"Failed to create collection: {e}")
            return False

    async def delete_collection(self, collection_name: str) -> bool:
        client = self._get_client()

        if client == "mock":
            logger.info(f"Mock: Deleted collection {collection_name}")
            return True

        try:
            client.drop_collection(collection_name)
            logger.info(f"Deleted collection {collection_name}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete collection: {e}")
            return False

    async def collection_exists(self, collection_name: str) -> bool:
        client = self._get_client()

        if client == "mock":
            return True

        try:
            return bool(client.has_collection(collection_name))
        except Exception:
            return False

    async def upsert(
        self,
        collection_name: str,
        ids: list[str],
        vectors: list[list[float]],
        payloads: list[dict[str, Any]],
        sparse_vectors: list[dict[int, float]] | None = None,
    ) -> bool:
        if sparse_vectors:
            logger.warning("Milvus adapter does not support sparse vectors — ignoring sparse payloads")

        client = self._get_client()

        if client == "mock":
            logger.info(f"Mock: Upserted {len(ids)} vectors to {collection_name}")
            return True

        try:
            if not client.has_collection(collection_name):
                vector_size = len(vectors[0]) if vectors else 1024
                if not await self.create_collection(collection_name, vector_size=vector_size):
                    return False

            rows: list[dict[str, Any]] = []
            for doc_id, vector, payload in zip(ids, vectors, payloads, strict=True):
                text = str(payload.get("text", ""))
                metadata = {k: v for k, v in payload.items() if k != "text"}
                rows.append(
                    {
                        "id": str(doc_id)[:_ID_FIELD_MAX_CHARS],
                        "vector": list(vector),
                        "text": text,
                        "metadata_json": json.dumps(metadata, default=str),
                        "workspace_id": str(payload.get("workspace_id", ""))[:_ID_FIELD_MAX_CHARS],
                    }
                )
            client.upsert(collection_name, data=rows)
            logger.info(f"Upserted {len(ids)} vectors to {collection_name}")
            return True
        except Exception as e:
            logger.error(f"Failed to upsert vectors: {e}")
            return False

    async def delete_by_ids(self, collection_name: str, ids: list[str]) -> bool:
        client = self._get_client()

        if client == "mock":
            logger.info(f"Mock: Deleted {len(ids)} points from {collection_name}")
            return True

        try:
            id_list = ", ".join(f'"{str(doc_id)[:_ID_FIELD_MAX_CHARS]}"' for doc_id in ids)
            client.delete(collection_name, filter=f"id in [{id_list}]")
            logger.info(f"Deleted {len(ids)} points from {collection_name}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete points: {e}")
            return False

    async def search_dense(
        self,
        collection_name: str,
        query_vector: list[float],
        limit: int = 10,
        workspace_id: str | None = None,
    ) -> list[SearchResult]:
        client = self._get_client()

        if client == "mock":
            return [
                SearchResult(
                    id=f"mock_{i}",
                    score=0.9 - i * 0.1,
                    payload={"text": f"Mock result {i}", "metadata": {}},
                )
                for i in range(min(limit, 3))
            ]

        try:
            search_filter = f'workspace_id == "{workspace_id}"' if workspace_id is not None else ""
            if workspace_id is None:
                logger.warning("Vector search without workspace_id filter — tenant isolation not enforced")

            results = client.search(
                collection_name,
                data=[query_vector],
                limit=limit,
                filter=search_filter or None,
                output_fields=["text", "metadata_json"],
                search_params={"metric_type": "COSINE"},
            )

            search_results: list[SearchResult] = []
            for hit in results[0] if results else []:
                entity = hit.get("entity", {}) if isinstance(hit, dict) else {}
                try:
                    metadata = json.loads(entity.get("metadata_json", "{}"))
                except (TypeError, ValueError):
                    metadata = {}
                metadata["text"] = entity.get("text", "")
                search_results.append(
                    SearchResult(id=str(hit.get("id", "")), score=float(hit.get("distance", 0.0)), payload=metadata)
                )
            return search_results
        except Exception as e:
            logger.error(f"Dense search failed: {e}")
            return []

    async def search_sparse(
        self,
        collection_name: str,
        query_sparse: dict[int, float],
        limit: int = 10,
        workspace_id: str | None = None,
    ) -> list[SearchResult]:
        logger.warning("Milvus adapter does not support sparse vector search. Returning empty results.")
        return []

    async def count(self, collection_name: str) -> int:
        client = self._get_client()

        if client == "mock":
            return 42

        try:
            stats = client.get_collection_stats(collection_name)
            return int(stats.get("row_count", 0))
        except Exception as e:
            logger.error(f"Count failed: {e}")
            return 0

    async def scroll(
        self,
        collection_name: str,
        offset: str | None = None,
        limit: int = 20,
    ) -> tuple[list[SearchResult], str | None]:
        client = self._get_client()

        if client == "mock":
            results = [
                SearchResult(
                    id=f"mock_chunk_{i}",
                    score=1.0,
                    payload={"text": f"Mock chunk content {i}", "metadata": {"source_file": f"doc_{i}.txt"}},
                )
                for i in range(min(limit, 3))
            ]
            return results, None

        try:
            offset_int = int(offset) if offset else 0
            rows = client.query(
                collection_name,
                filter="",
                offset=offset_int,
                limit=limit,
                output_fields=["id", "text", "metadata_json"],
            )

            search_results: list[SearchResult] = []
            for row in rows:
                try:
                    metadata = json.loads(row.get("metadata_json", "{}"))
                except (TypeError, ValueError):
                    metadata = {}
                metadata["text"] = row.get("text", "")
                search_results.append(SearchResult(id=str(row.get("id", "")), score=1.0, payload=metadata))

            next_offset = str(offset_int + len(search_results)) if len(search_results) == limit else None
            return search_results, next_offset
        except Exception as e:
            logger.error(f"Scroll failed: {e}")
            return [], None
