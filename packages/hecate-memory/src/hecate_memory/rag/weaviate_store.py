"""Weaviate vector store adapter implementing the VectorStore ABC.

Wraps ``weaviate-client`` v4 for Weaviate deployments. Collections are
created with ``Vectorizer.none()`` (Hecate supplies its own embeddings)
and store the chunk text, a JSON metadata blob, and a ``workspace_id``
property for tenant-isolated filtering. Sparse vectors are not supported —
``search_sparse`` returns an empty list so hybrid queries fall back to the
ABC's application-layer RRF fusion.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import urlsplit

from hecate_memory.rag.types import SearchResult
from hecate_memory.rag.vector_store import VectorStore

logger = logging.getLogger(__name__)


class WeaviateVectorStore(VectorStore):
    """VectorStore implementation backed by Weaviate (weaviate-client v4)."""

    def __init__(
        self,
        url: str = "http://localhost:8080",
        grpc_host: str = "localhost:50051",
        api_key: str = "",
    ) -> None:
        split = urlsplit(url if "://" in url else f"http://{url}")
        self.http_host = split.hostname or "localhost"
        self.http_port = split.port or (443 if split.scheme == "https" else 8080)
        self.http_secure = split.scheme == "https"
        grpc_split = urlsplit(grpc_host if "://" in grpc_host else f"grpc://{grpc_host}")
        self.grpc_host = grpc_split.hostname or "localhost"
        grpc_port = grpc_split.port or (443 if self.http_secure else 50051)
        self.grpc_port = str(grpc_port)
        self.grpc_secure = self.http_secure
        self.api_key = api_key
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                import weaviate

                auth = weaviate.auth.AuthApiKey(self.api_key) if self.api_key else None
                self._client = weaviate.connect_to_custom(
                    http_host=self.http_host,
                    http_port=self.http_port,
                    http_secure=self.http_secure,
                    grpc_host=self.grpc_host,
                    grpc_port=self.grpc_port,
                    grpc_secure=self.grpc_secure,
                    auth_credentials=auth,
                )
                logger.info(f"Connected to Weaviate at {self.http_host}:{self.http_port}")
            except ImportError:
                logger.warning("weaviate-client not installed. Using mock client.")
                self._client = "mock"
        return self._client

    def _get_collection(self, collection_name: str) -> Any:
        return self._client.collections.get(collection_name)

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
            if client.collections.exists(collection_name):
                return True
            from weaviate.classes.config import Configure, DataType, Property

            client.collections.create(
                name=collection_name,
                vectorizer_config=Configure.Vectorizer.none(),
                properties=[
                    Property(name="text", data_type=DataType.TEXT),
                    Property(name="metadata_json", data_type=DataType.TEXT),
                    Property(name="workspace_id", data_type=DataType.TEXT),
                ],
            )
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
            client.collections.delete(collection_name)
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
            return bool(client.collections.exists(collection_name))
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
            logger.warning("Weaviate adapter does not support sparse vectors — ignoring sparse payloads")

        client = self._get_client()

        if client == "mock":
            logger.info(f"Mock: Upserted {len(ids)} vectors to {collection_name}")
            return True

        try:
            if not client.collections.exists(collection_name) and not await self.create_collection(collection_name):
                return False
            collection = self._get_collection(collection_name)
            for doc_id, vector, payload in zip(ids, vectors, payloads, strict=True):
                text = str(payload.get("text", ""))
                metadata = {k: v for k, v in payload.items() if k != "text"}
                properties = {
                    "text": text,
                    "metadata_json": json.dumps(metadata, default=str),
                    "workspace_id": str(payload.get("workspace_id", "")),
                }
                if collection.data.exists(str(doc_id)):
                    collection.data.update(uuid=str(doc_id), properties=properties, vector=list(vector))
                else:
                    collection.data.insert(uuid=str(doc_id), properties=properties, vector=list(vector))
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
            collection = self._get_collection(collection_name)
            for doc_id in ids:
                collection.data.delete_by_id(str(doc_id))
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
            from weaviate.classes.query import MetadataQuery

            if workspace_id is None:
                logger.warning("Vector search without workspace_id filter — tenant isolation not enforced")

            collection = self._get_collection(collection_name)
            filters = None
            if workspace_id is not None:
                from weaviate.classes.query import Filter

                filters = Filter.by_property("workspace_id").equal(workspace_id)

            response = collection.query.near_vector(
                near_vector=query_vector,
                limit=limit,
                filters=filters,
                return_metadata=MetadataQuery(distance=True),
            )

            search_results: list[SearchResult] = []
            for obj in response.objects:
                try:
                    metadata = json.loads(obj.properties.get("metadata_json", "{}"))
                except (TypeError, ValueError):
                    metadata = {}
                metadata["text"] = obj.properties.get("text", "")
                distance = obj.metadata.distance if obj.metadata is not None else 0.0
                search_results.append(
                    SearchResult(id=str(obj.uuid), score=1.0 - float(distance or 0.0), payload=metadata)
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
        logger.warning("Weaviate adapter does not support sparse vector search. Returning empty results.")
        return []

    async def count(self, collection_name: str) -> int:
        client = self._get_client()

        if client == "mock":
            return 42

        try:
            collection = self._get_collection(collection_name)
            response = collection.aggregate.over_all(total_count=True)
            return int(response.total_count or 0)
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
            collection = self._get_collection(collection_name)
            offset_int = int(offset) if offset else 0
            response = collection.query.fetch_objects(offset=offset_int, limit=limit)

            search_results: list[SearchResult] = []
            for obj in response.objects:
                try:
                    metadata = json.loads(obj.properties.get("metadata_json", "{}"))
                except (TypeError, ValueError):
                    metadata = {}
                metadata["text"] = obj.properties.get("text", "")
                search_results.append(SearchResult(id=str(obj.uuid), score=1.0, payload=metadata))

            next_offset = str(offset_int + len(search_results)) if len(search_results) == limit else None
            return search_results, next_offset
        except Exception as e:
            logger.error(f"Scroll failed: {e}")
            return [], None
