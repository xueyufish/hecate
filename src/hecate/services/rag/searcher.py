"""Hybrid search service combining dense and sparse retrieval.

Provides semantic search using dense embeddings with optional
BM25-style sparse retrieval for improved relevance.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from hecate.services.rag.embedding import embedding_service
from hecate.services.rag.indexer import qdrant_indexer

logger = logging.getLogger(__name__)


@dataclass
class HybridSearchResult:
    """Result from hybrid search with combined score."""

    id: str
    score: float
    content: str
    metadata: dict[str, Any]


class HybridSearcher:
    """Combine dense and sparse search for better retrieval.

    Uses:
    - Dense vectors for semantic similarity
    - Optional sparse vectors for keyword matching
    - Score fusion for final ranking
    """

    def __init__(
        self,
        dense_weight: float = 0.7,
        sparse_weight: float = 0.3,
    ):
        self.dense_weight = dense_weight
        self.sparse_weight = sparse_weight

    async def search(
        self,
        collection_name: str,
        query: str,
        limit: int = 10,
    ) -> list[HybridSearchResult]:
        """Search for relevant documents.

        Args:
            collection_name: Qdrant collection name.
            query: The search query.
            limit: Maximum number of results.

        Returns:
            List of HybridSearchResult ordered by relevance.
        """
        query_embedding = await embedding_service.encode_query(query)

        dense_results = await qdrant_indexer.search(
            collection_name=collection_name,
            query_vector=query_embedding.dense,
            limit=limit,
        )

        results = []
        for r in dense_results:
            content = r.payload.get("text", "")
            metadata = r.payload.get("metadata", {})

            results.append(
                HybridSearchResult(
                    id=r.id,
                    score=r.score,
                    content=content,
                    metadata=metadata,
                )
            )

        results.sort(key=lambda x: x.score, reverse=True)
        return results[:limit]


hybrid_searcher = HybridSearcher()
