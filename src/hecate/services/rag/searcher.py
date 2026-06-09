"""Hybrid search service combining dense and sparse retrieval.

Provides semantic search using dense embeddings with optional
BM25-style sparse retrieval for improved relevance.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Literal

from hecate.services.rag.embedding import embedding_service
from hecate.services.rag.vector_store import VectorStore

logger = logging.getLogger(__name__)

SearchMode = Literal["hybrid", "dense", "sparse"]


@dataclass
class HybridSearchResult:
    """Result from hybrid search with combined score."""

    id: str
    score: float
    content: str
    metadata: dict[str, Any]
    dense_score: float = 0.0
    sparse_score: float = 0.0


class HybridSearcher:
    """Combine dense and sparse search for better retrieval.

    Uses:
    - Dense vectors for semantic similarity
    - Sparse vectors for keyword matching (BM25-style)
    - Score fusion (RRF) for final ranking in hybrid mode
    """

    def __init__(
        self,
        store: VectorStore,
        dense_weight: float = 0.7,
        sparse_weight: float = 0.3,
    ) -> None:
        self._store = store
        self.dense_weight = dense_weight
        self.sparse_weight = sparse_weight

    async def search(
        self,
        collection_name: str,
        query: str,
        limit: int = 10,
        mode: SearchMode = "hybrid",
        workspace_id: str | None = None,
    ) -> list[HybridSearchResult]:
        """Search for relevant documents.

        Args:
            collection_name: Vector store collection name.
            query: The search query.
            limit: Maximum number of results.
            mode: Search mode - "hybrid" (default), "dense", or "sparse".
            workspace_id: Optional workspace ID for tenant isolation filtering.

        Returns:
            List of HybridSearchResult ordered by relevance.
        """
        query_embedding = await embedding_service.encode_query(query)

        if mode == "sparse" and not query_embedding.sparse:
            logger.warning("Sparse mode requested but no sparse vector generated. Falling back to dense.")
            mode = "dense"

        if mode == "hybrid":
            return await self._search_hybrid(collection_name, query_embedding, limit, workspace_id)
        elif mode == "sparse":
            return await self._search_sparse(collection_name, query_embedding, limit, workspace_id)
        else:
            return await self._search_dense(collection_name, query_embedding, limit, workspace_id)

    async def _search_hybrid(
        self,
        collection_name: str,
        query_embedding: Any,
        limit: int,
        workspace_id: str | None = None,
    ) -> list[HybridSearchResult]:
        """Perform hybrid search with fallback to dense-only."""
        if not query_embedding.sparse:
            logger.warning("No sparse vector available. Falling back to dense-only search.")
            return await self._search_dense(collection_name, query_embedding, limit, workspace_id)

        hybrid_results, dense_results, sparse_results = await asyncio.gather(
            self._store.search_hybrid(
                collection_name=collection_name,
                query_dense=query_embedding.dense,
                query_sparse=query_embedding.sparse,
                limit=limit,
                workspace_id=workspace_id,
            ),
            self._store.search_dense(
                collection_name=collection_name,
                query_vector=query_embedding.dense,
                limit=limit,
                workspace_id=workspace_id,
            ),
            self._store.search_sparse(
                collection_name=collection_name,
                query_sparse=query_embedding.sparse,
                limit=limit,
                workspace_id=workspace_id,
            ),
        )

        dense_scores = {r.id: r.score for r in dense_results}
        sparse_scores = {r.id: r.score for r in sparse_results}

        return [
            HybridSearchResult(
                id=r.id,
                score=r.score,
                content=r.payload.get("text", ""),
                metadata=r.payload.get("metadata", {}),
                dense_score=dense_scores.get(r.id, 0.0),
                sparse_score=sparse_scores.get(r.id, 0.0),
            )
            for r in hybrid_results
        ]

    async def _search_dense(
        self,
        collection_name: str,
        query_embedding: Any,
        limit: int,
        workspace_id: str | None = None,
    ) -> list[HybridSearchResult]:
        results = await self._store.search_dense(
            collection_name=collection_name,
            query_vector=query_embedding.dense,
            limit=limit,
            workspace_id=workspace_id,
        )
        return [
            HybridSearchResult(
                id=r.id,
                score=r.score,
                content=r.payload.get("text", ""),
                metadata=r.payload.get("metadata", {}),
                dense_score=r.score,
                sparse_score=0.0,
            )
            for r in results
        ]

    async def _search_sparse(
        self,
        collection_name: str,
        query_embedding: Any,
        limit: int,
        workspace_id: str | None = None,
    ) -> list[HybridSearchResult]:
        results = await self._store.search_sparse(
            collection_name=collection_name,
            query_sparse=query_embedding.sparse,
            limit=limit,
            workspace_id=workspace_id,
        )
        return [
            HybridSearchResult(
                id=r.id,
                score=r.score,
                content=r.payload.get("text", ""),
                metadata=r.payload.get("metadata", {}),
                sparse_score=r.score,
            )
            for r in results
        ]
