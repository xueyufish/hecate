"""Abstract vector store interface for pluggable backends.

Defines the ``VectorStore`` ABC that all vector database adapters
(Qdrant, Chroma, etc.) must implement.  Provides a default
``search_hybrid`` with application-layer RRF fusion so backends
that lack native hybrid search get a working fallback.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections import defaultdict
from typing import Any

from hecate.services.rag.types import SearchResult

logger = logging.getLogger(__name__)

DEFAULT_RRF_K = 60
"""k constant for Reciprocal Rank Fusion (Cormack et al. 2009)."""


def _rrf_fuse(
    dense_results: list[SearchResult],
    sparse_results: list[SearchResult],
    k: int = DEFAULT_RRF_K,
    top_k: int = 10,
) -> list[SearchResult]:
    """Fuse dense and sparse search results using Reciprocal Rank Fusion.

    Standard RRF formula: ``score(d) = Σ 1/(k + rank_i(d))``
    with 1-based ranking (rank 1 = top result) and k=60.

    A document that appears in both channels gets the sum of its
    RRF scores from each; documents in only one channel get a single
    contribution.

    Args:
        dense_results: Results from dense vector search.
        sparse_results: Results from sparse vector search.
        k: RRF smoothing constant (default 60 per Cormack et al.).
        top_k: Number of results to return.

    Returns:
        Top-k results ordered by fused RRF score (descending).
    """
    scores: dict[str, float] = defaultdict(float)
    payload_map: dict[str, dict[str, Any]] = {}

    for rank, result in enumerate(dense_results, start=1):
        scores[result.id] += 1.0 / (k + rank)
        payload_map[result.id] = result.payload

    for rank, result in enumerate(sparse_results, start=1):
        scores[result.id] += 1.0 / (k + rank)
        payload_map[result.id] = result.payload

    sorted_ids = sorted(scores, key=lambda doc_id: scores[doc_id], reverse=True)

    return [
        SearchResult(
            id=doc_id,
            score=scores[doc_id],
            payload=payload_map.get(doc_id, {}),
        )
        for doc_id in sorted_ids[:top_k]
    ]


class VectorStore(ABC):
    """Abstract base class for vector store backends.

    Every backend MUST implement all abstract methods.  The non-abstract
    ``search_hybrid`` provides a default application-layer RRF fusion
    using ``search_dense`` and ``search_sparse`` with 4× prefetch.
    Backends with native hybrid (e.g. Qdrant, Milvus) SHOULD override
    ``search_hybrid`` and set ``supports_hybrid = True``.
    """

    # ------------------------------------------------------------------
    # Collection CRUD
    # ------------------------------------------------------------------

    @abstractmethod
    async def create_collection(
        self,
        collection_name: str,
        vector_size: int = 1024,
        with_sparse: bool = True,
    ) -> bool:
        """Create a collection in the backend.

        Args:
            collection_name: Name of the collection.
            vector_size: Dimension of the dense vectors.
            with_sparse: Whether to configure sparse vectors.

        Returns:
            ``True`` if the collection was created or already exists.
        """

    @abstractmethod
    async def delete_collection(self, collection_name: str) -> bool:
        """Delete a collection from the backend.

        Args:
            collection_name: Name of the collection to delete.

        Returns:
            ``True`` if the collection was deleted.
        """

    @abstractmethod
    async def collection_exists(self, collection_name: str) -> bool:
        """Check whether a collection exists.

        Args:
            collection_name: Name of the collection.

        Returns:
            ``True`` if the collection exists.
        """

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    @abstractmethod
    async def upsert(
        self,
        collection_name: str,
        ids: list[str],
        vectors: list[list[float]],
        payloads: list[dict[str, Any]],
        sparse_vectors: list[dict[int, float]] | None = None,
    ) -> bool:
        """Upsert vectors with metadata into a collection.

        Args:
            collection_name: Name of the collection.
            ids: Unique point IDs.
            vectors: Dense vectors (same length as *ids*).
            payloads: Metadata dicts (same length as *ids*).
            sparse_vectors: Optional sparse vectors (token_id → weight).

        Returns:
            ``True`` if the upsert succeeded.
        """

    @abstractmethod
    async def delete_by_ids(
        self,
        collection_name: str,
        ids: list[str],
    ) -> bool:
        """Delete points by their IDs.

        Args:
            collection_name: Name of the collection.
            ids: IDs of the points to delete.

        Returns:
            ``True`` if the deletion succeeded.
        """

    # ------------------------------------------------------------------
    # Search operations
    # ------------------------------------------------------------------

    @abstractmethod
    async def search_dense(
        self,
        collection_name: str,
        query_vector: list[float],
        limit: int = 10,
        workspace_id: str | None = None,
    ) -> list[SearchResult]:
        """Dense vector similarity search.

        Args:
            collection_name: Name of the collection.
            query_vector: Dense query vector.
            limit: Maximum number of results.
            workspace_id: Optional workspace filter for tenant isolation.

        Returns:
            Results ordered by similarity (descending).
        """

    @abstractmethod
    async def search_sparse(
        self,
        collection_name: str,
        query_sparse: dict[int, float],
        limit: int = 10,
        workspace_id: str | None = None,
    ) -> list[SearchResult]:
        """Sparse vector search.

        Args:
            collection_name: Name of the collection.
            query_sparse: Sparse query vector (token_id → weight).
            limit: Maximum number of results.
            workspace_id: Optional workspace filter for tenant isolation.

        Returns:
            Results ordered by relevance (descending).
        """

    @property
    def supports_hybrid(self) -> bool:
        """Whether this backend provides native hybrid search.

        Backends that override ``search_hybrid`` with a native
        implementation (e.g. Qdrant, Milvus) return ``True``.
        The default returns ``False``.
        """
        return False

    async def search_hybrid(
        self,
        collection_name: str,
        query_dense: list[float],
        query_sparse: dict[int, float],
        limit: int = 10,
        workspace_id: str | None = None,
    ) -> list[SearchResult]:
        """Hybrid search combining dense and sparse retrieval.

        The default implementation performs application-layer RRF fusion:
        fetches 4× *limit* from each channel, then fuses via
        :func:`_rrf_fuse` with k=60.

        Backends with native hybrid search SHOULD override this method
        for zero quality loss (single-request server-side fusion).

        Args:
            collection_name: Name of the collection.
            query_dense: Dense query vector.
            query_sparse: Sparse query vector (token_id → weight).
            limit: Maximum number of results.
            workspace_id: Optional workspace filter for tenant isolation.

        Returns:
            Results ordered by fused score (descending).
        """
        prefetch = limit * 4
        dense_results, sparse_results = (
            await self.search_dense(collection_name, query_dense, limit=prefetch, workspace_id=workspace_id),
            await self.search_sparse(collection_name, query_sparse, limit=prefetch, workspace_id=workspace_id),
        )
        return _rrf_fuse(dense_results, sparse_results, k=DEFAULT_RRF_K, top_k=limit)

    # ------------------------------------------------------------------
    # Utility operations
    # ------------------------------------------------------------------

    @abstractmethod
    async def count(self, collection_name: str) -> int:
        """Return total number of points in the collection.

        Args:
            collection_name: Name of the collection.

        Returns:
            Total point count.
        """

    @abstractmethod
    async def scroll(
        self,
        collection_name: str,
        offset: str | None = None,
        limit: int = 20,
    ) -> tuple[list[SearchResult], str | None]:
        """Cursor-based pagination over collection points.

        Args:
            collection_name: Name of the collection.
            offset: Cursor from a previous ``scroll`` call, or ``None``
                for the first page.
            limit: Number of points to return.

        Returns:
            Tuple of (results, next_offset). ``next_offset`` is ``None``
            when there are no more pages.
        """
