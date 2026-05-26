"""Qdrant vector store indexer for managing document embeddings.

Provides collection management and vector upsert operations
for the Qdrant vector database.
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
    - Vector upsert with metadata
    - Similarity search
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
    ) -> bool:
        """Create a Qdrant collection.

        Args:
            collection_name: Name of the collection.
            vector_size: Dimension of the dense vectors.

        Returns:
            bool: True if collection was created or already exists.
        """
        client = self._get_client()

        if client == "mock":
            logger.info(f"Mock: Created collection {collection_name}")
            return True

        try:
            from qdrant_client.models import Distance, VectorParams

            collections = client.get_collections().collections
            if any(c.name == collection_name for c in collections):
                logger.info(f"Collection {collection_name} already exists")
                return True

            client.create_collection(
                collection_name=collection_name,
                vectors_config=VectorParams(
                    size=vector_size,
                    distance=Distance.COSINE,
                ),
            )
            logger.info(f"Created collection {collection_name}")
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
    ) -> bool:
        """Upsert vectors into a collection.

        Args:
            collection_name: Name of the collection.
            ids: List of point IDs.
            vectors: List of dense vectors.
            payloads: List of metadata payloads.

        Returns:
            bool: True if upsert was successful.
        """
        client = self._get_client()

        if client == "mock":
            logger.info(f"Mock: Upserted {len(ids)} vectors to {collection_name}")
            return True

        try:
            from qdrant_client.models import PointStruct

            points = [
                PointStruct(id=id_, vector=vector, payload=payload)
                for id_, vector, payload in zip(ids, vectors, payloads, strict=False)
            ]

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
        """Search for similar vectors.

        Args:
            collection_name: Name of the collection.
            query_vector: The query vector.
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
            results = client.search(
                collection_name=collection_name,
                query_vector=query_vector,
                limit=limit,
            )
            return [
                SearchResult(
                    id=str(r.id),
                    score=r.score,
                    payload=r.payload or {},
                )
                for r in results
            ]
        except Exception as e:
            logger.error(f"Search failed: {e}")
            return []


qdrant_indexer = QdrantIndexer()
