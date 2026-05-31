"""Qdrant vector store indexer for managing document embeddings.

Provides collection management and vector upsert operations
for the Qdrant vector database, supporting both dense and sparse vectors
for hybrid search.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class SearchResult:
    """A single search result from Qdrant."""

    id: str
    score: float
    payload: dict[str, Any]


class QdrantIndexer:
    """Manage Qdrant collections and document indexing.

    Supports:
    - Collection creation with dense/sparse vector configs
    - Vector upsert with metadata (dense and sparse)
    - Similarity search (dense, sparse, and hybrid)
    """

    def __init__(self, url: str = "http://localhost:6333"):
        self.url = url
        self._client = None

    def _get_client(self):
        """Lazy load the Qdrant client."""
        if self._client is None:
            try:
                from qdrant_client import QdrantClient

                self._client = QdrantClient(url=self.url)
                logger.info(f"Connected to Qdrant at {self.url}")
            except ImportError:
                logger.warning("qdrant-client not installed. Using mock client.")
                self._client = "mock"
        return self._client

    async def create_collection(
        self,
        collection_name: str,
        vector_size: int = 1024,
        with_sparse: bool = True,
    ) -> bool:
        """Create a Qdrant collection.

        Args:
            collection_name: Name of the collection.
            vector_size: Dimension of the dense vectors.
            with_sparse: Whether to add sparse vector configuration.

        Returns:
            bool: True if collection was created or already exists.
        """
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
                "vectors_config": VectorParams(
                    size=vector_size,
                    distance=Distance.COSINE,
                ),
            }
            if with_sparse:
                kwargs["sparse_vectors_config"] = {
                    "sparse": SparseVectorParams(),
                }

            client.create_collection(**kwargs)
            logger.info(f"Created collection {collection_name} (sparse={with_sparse})")
            return True
        except Exception as e:
            logger.error(f"Failed to create collection: {e}")
            return False

    async def upsert_vectors(
        self,
        collection_name: str,
        ids: list[str],
        vectors: list[list[float]],
        payloads: list[dict[str, Any]],
        sparse_vectors: list[dict[int, float]] | None = None,
    ) -> bool:
        """Upsert vectors into a collection.

        Args:
            collection_name: Name of the collection.
            ids: List of point IDs.
            vectors: List of dense vectors.
            payloads: List of metadata payloads.
            sparse_vectors: Optional list of sparse vectors (token_id -> weight).

        Returns:
            bool: True if upsert was successful.
        """
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

            client.upsert(
                collection_name=collection_name,
                points=points,
            )
            logger.info(f"Upserted {len(ids)} vectors to {collection_name}")
            return True
        except Exception as e:
            logger.error(f"Failed to upsert vectors: {e}")
            return False

    async def search(
        self,
        collection_name: str,
        query_vector: list[float],
        limit: int = 10,
    ) -> list[SearchResult]:
        """Search for similar vectors (dense only).

        Args:
            collection_name: Name of the collection.
            query_vector: The dense query vector.
            limit: Maximum number of results.

        Returns:
            List of SearchResult objects.
        """
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
            results = client.query_points(
                collection_name=collection_name,
                query=query_vector,
                using="dense",
                limit=limit,
                with_payload=True,
            )
            return [
                SearchResult(
                    id=str(r.id),
                    score=r.score,
                    payload=r.payload or {},
                )
                for r in results.points
            ]
        except Exception as e:
            logger.error(f"Search failed: {e}")
            return []

    async def search_sparse(
        self,
        collection_name: str,
        query_sparse: dict[int, float],
        limit: int = 10,
    ) -> list[SearchResult]:
        """Search using sparse vectors only.

        Args:
            collection_name: Name of the collection.
            query_sparse: The sparse query vector (token_id -> weight).
            limit: Maximum number of results.

        Returns:
            List of SearchResult objects.
        """
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
            )
            return [
                SearchResult(
                    id=str(r.id),
                    score=r.score,
                    payload=r.payload or {},
                )
                for r in results.points
            ]
        except Exception as e:
            logger.error(f"Sparse search failed: {e}")
            return []

    async def search_hybrid(
        self,
        collection_name: str,
        query_dense: list[float],
        query_sparse: dict[int, float],
        limit: int = 10,
    ) -> list[SearchResult]:
        """Search using hybrid (dense + sparse) with RRF fusion.

        Args:
            collection_name: Name of the collection.
            query_dense: The dense query vector.
            query_sparse: The sparse query vector (token_id -> weight).
            limit: Maximum number of results.

        Returns:
            List of SearchResult objects ordered by fused score.
        """
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

            sparse_vec = SparseVector(
                indices=list(query_sparse.keys()),
                values=list(query_sparse.values()),
            )
            results = client.query_points(
                collection_name=collection_name,
                prefetch=[
                    Prefetch(query=query_dense, using="dense", limit=limit * 2),
                    Prefetch(query=sparse_vec, using="sparse", limit=limit * 2),
                ],
                query=FusionQuery(fusion=Fusion.RRF),
                limit=limit,
                with_payload=True,
            )
            return [
                SearchResult(
                    id=str(r.id),
                    score=r.score,
                    payload=r.payload or {},
                )
                for r in results.points
            ]
        except Exception as e:
            logger.error(f"Hybrid search failed: {e}")
            return []

    async def has_sparse_vectors(self, collection_name: str) -> bool:
        """Check if a collection has sparse vector configuration.

        Args:
            collection_name: Name of the collection.

        Returns:
            bool: True if collection has sparse vectors configured.
        """
        client = self._get_client()

        if client == "mock":
            return True

        try:
            info = client.get_collection(collection_name)
            return info.config.params.sparse_vectors is not None
        except Exception:
            return False

    async def scroll(
        self,
        collection_name: str,
        offset: str | None = None,
        limit: int = 20,
    ) -> tuple[list[SearchResult], str | None]:
        """Scroll through collection points with cursor-based pagination.

        Args:
            collection_name: Name of the collection.
            offset: Cursor offset from previous scroll (None for first page).
            limit: Number of points to return.

        Returns:
            Tuple of (list of SearchResult, next_offset or None if no more).
        """
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
            results = [
                SearchResult(
                    id=str(p.id),
                    score=1.0,
                    payload=p.payload or {},
                )
                for p in points
            ]
            return results, next_offset
        except Exception as e:
            logger.error(f"Scroll failed: {e}")
            return [], None

    async def count(self, collection_name: str) -> int:
        """Get total point count in a collection.

        Args:
            collection_name: Name of the collection.

        Returns:
            int: Total number of points.
        """
        client = self._get_client()

        if client == "mock":
            return 42

        try:
            result = client.count(collection_name=collection_name)
            return result.count
        except Exception as e:
            logger.error(f"Count failed: {e}")
            return 0


qdrant_indexer = QdrantIndexer()
