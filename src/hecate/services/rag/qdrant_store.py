"""Qdrant vector store adapter implementing the VectorStore ABC.

Wraps ``qdrant-client`` for collection management, vector upsert,
and dense/sparse/hybrid search.  Falls back to deterministic mocks
when the ``qdrant_client`` package is not installed.
"""

from __future__ import annotations

import logging
from typing import Any

from hecate.services.rag.types import SearchResult
from hecate.services.rag.vector_store import VectorStore

logger = logging.getLogger(__name__)


class QdrantVectorStore(VectorStore):
    """VectorStore implementation backed by Qdrant.

    Supports native hybrid search via Qdrant's ``Prefetch + Fusion.RRF``
    query pattern — sets ``supports_hybrid = True``.
    """

    def __init__(self, url: str = "http://localhost:6333", api_key: str = "") -> None:
        self.url = url
        self.api_key = api_key or None
        self._client: Any = None

    @property
    def supports_hybrid(self) -> bool:
        return True

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                from qdrant_client import QdrantClient

                kwargs: dict[str, Any] = {"url": self.url}
                if self.api_key:
                    kwargs["api_key"] = self.api_key
                self._client = QdrantClient(**kwargs)
                logger.info(f"Connected to Qdrant at {self.url}")
            except ImportError:
                logger.warning("qdrant-client not installed. Using mock client.")
                self._client = "mock"
        return self._client

    def _build_workspace_filter(self, workspace_id: str | None) -> Any:
        """Build a Qdrant filter for workspace isolation.

        Returns None if workspace_id is not provided (backward-compatible fallback).
        """
        if workspace_id is None:
            logger.warning("Vector search without workspace_id filter — tenant isolation not enforced")
            return None

        try:
            from qdrant_client.models import FieldCondition, Filter, MatchValue

            return Filter(must=[FieldCondition(key="workspace_id", match=MatchValue(value=workspace_id))])
        except ImportError:
            return None

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
            from qdrant_client.models import Distance, SparseVectorParams, VectorParams

            collections = client.get_collections().collections
            if any(c.name == collection_name for c in collections):
                logger.info(f"Collection {collection_name} already exists")
                return True

            kwargs: dict[str, Any] = {
                "collection_name": collection_name,
                "vectors_config": VectorParams(size=vector_size, distance=Distance.COSINE),
            }
            if with_sparse:
                kwargs["sparse_vectors_config"] = {"sparse": SparseVectorParams()}

            client.create_collection(**kwargs)
            logger.info(f"Created collection {collection_name} (sparse={with_sparse})")
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
            client.delete_collection(collection_name=collection_name)
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
            info = client.get_collection(collection_name)
            return info is not None
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
        client = self._get_client()

        if client == "mock":
            logger.info(f"Mock: Upserted {len(ids)} vectors to {collection_name}")
            return True

        try:
            from qdrant_client.models import PointStruct, SparseVector

            points = []
            for i, (id_, vector, payload) in enumerate(zip(ids, vectors, payloads, strict=False)):
                point_vectors: dict[str, Any] = {"dense": vector}
                if sparse_vectors and i < len(sparse_vectors):
                    sparse = sparse_vectors[i]
                    if sparse:
                        point_vectors["sparse"] = SparseVector(
                            indices=list(sparse.keys()),
                            values=list(sparse.values()),
                        )
                points.append(PointStruct(id=id_, vector=point_vectors, payload=payload))

            client.upsert(collection_name=collection_name, points=points)
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
            client.delete(
                collection_name=collection_name,
                points_selector=ids,
            )
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
            query_filter = self._build_workspace_filter(workspace_id)
            results = client.query_points(
                collection_name=collection_name,
                query=query_vector,
                using="dense",
                limit=limit,
                with_payload=True,
                query_filter=query_filter,
            )
            return [SearchResult(id=str(r.id), score=r.score, payload=r.payload or {}) for r in results.points]
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
        client = self._get_client()

        if client == "mock":
            return [
                SearchResult(
                    id=f"mock_sparse_{i}",
                    score=0.85 - i * 0.1,
                    payload={"text": f"Mock sparse result {i}", "metadata": {}},
                )
                for i in range(min(limit, 3))
            ]

        try:
            from qdrant_client.models import SparseVector

            query_filter = self._build_workspace_filter(workspace_id)
            sparse_vec = SparseVector(
                indices=list(query_sparse.keys()),
                values=list(query_sparse.values()),
            )
            results = client.query_points(
                collection_name=collection_name,
                query=sparse_vec,
                using="sparse",
                limit=limit,
                with_payload=True,
                query_filter=query_filter,
            )
            return [SearchResult(id=str(r.id), score=r.score, payload=r.payload or {}) for r in results.points]
        except Exception as e:
            logger.error(f"Sparse search failed: {e}")
            return []

    async def search_hybrid(
        self,
        collection_name: str,
        query_dense: list[float],
        query_sparse: dict[int, float],
        limit: int = 10,
        workspace_id: str | None = None,
    ) -> list[SearchResult]:
        client = self._get_client()

        if client == "mock":
            return [
                SearchResult(
                    id=f"mock_hybrid_{i}",
                    score=0.95 - i * 0.05,
                    payload={"text": f"Mock hybrid result {i}", "metadata": {}},
                )
                for i in range(min(limit, 3))
            ]

        try:
            from qdrant_client.models import Fusion, FusionQuery, Prefetch, SparseVector

            query_filter = self._build_workspace_filter(workspace_id)
            sparse_vec = SparseVector(
                indices=list(query_sparse.keys()),
                values=list(query_sparse.values()),
            )
            results = client.query_points(
                collection_name=collection_name,
                prefetch=[
                    Prefetch(query=query_dense, using="dense", limit=limit * 2, filter=query_filter),
                    Prefetch(query=sparse_vec, using="sparse", limit=limit * 2, filter=query_filter),
                ],
                query=FusionQuery(fusion=Fusion.RRF),
                limit=limit,
                with_payload=True,
            )
            return [SearchResult(id=str(r.id), score=r.score, payload=r.payload or {}) for r in results.points]
        except Exception as e:
            logger.error(f"Hybrid search failed: {e}")
            return []

    async def count(self, collection_name: str) -> int:
        client = self._get_client()

        if client == "mock":
            return 42

        try:
            result = client.count(collection_name=collection_name)
            return result.count
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
            points, next_offset = client.scroll(
                collection_name=collection_name,
                limit=limit,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            results = [SearchResult(id=str(p.id), score=1.0, payload=p.payload or {}) for p in points]
            return results, next_offset
        except Exception as e:
            logger.error(f"Scroll failed: {e}")
            return [], None
