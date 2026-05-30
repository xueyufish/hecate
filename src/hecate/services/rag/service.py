"""Knowledge Base service orchestrating the RAG pipeline.

Coordinates document parsing, chunking, embedding, and indexing
for complete knowledge base management, with support for hybrid
search (dense + sparse vectors).
"""

from __future__ import annotations

import logging
from typing import Any

from hecate.services.rag.chunker import text_chunker
from hecate.services.rag.embedding import embedding_service
from hecate.services.rag.indexer import qdrant_indexer
from hecate.services.rag.parser import document_parser
from hecate.services.rag.searcher import HybridSearchResult, SearchMode, hybrid_searcher

logger = logging.getLogger(__name__)


class KnowledgeBaseService:
    """Manage knowledge base operations.

    Provides:
    - Document ingestion (parse → chunk → embed → index) with sparse vectors
    - Similarity search (hybrid, dense, sparse modes)
    - Collection management with sparse vector support
    - Re-indexing existing collections with sparse vectors
    """

    async def ingest_document(
        self,
        file_path: str,
        collection_name: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Ingest a document into the knowledge base.

        Generates both dense and sparse embeddings for each chunk and stores
        them in the Qdrant collection.

        Args:
            file_path: Path to the document file.
            collection_name: Qdrant collection name.
            metadata: Optional metadata to attach to chunks.

        Returns:
            dict with ingestion results (chunk_count, etc.).
        """
        text = await document_parser.parse(file_path)
        if not text:
            return {"chunk_count": 0, "error": "No text extracted"}

        chunks = text_chunker.chunk_text(text, metadata or {})
        if not chunks:
            return {"chunk_count": 0, "error": "No chunks generated"}

        chunk_texts = [c.content for c in chunks]
        embeddings = await embedding_service.encode(chunk_texts)

        ids = [f"{file_path}_{i}" for i in range(len(chunks))]
        vectors = [e.dense for e in embeddings]
        sparse_vectors = [e.sparse for e in embeddings]
        payloads = [
            {
                "text": chunk.content,
                "metadata": {
                    **chunk.metadata,
                    "chunk_index": chunk.index,
                    "source_file": file_path,
                },
            }
            for chunk in chunks
        ]

        await qdrant_indexer.upsert_vectors(
            collection_name=collection_name,
            ids=ids,
            vectors=vectors,
            payloads=payloads,
            sparse_vectors=sparse_vectors,
        )

        return {"chunk_count": len(chunks), "collection": collection_name}

    async def search(
        self,
        collection_name: str,
        query: str,
        limit: int = 10,
        mode: SearchMode = "hybrid",
    ) -> list[HybridSearchResult]:
        """Search the knowledge base.

        Args:
            collection_name: Qdrant collection name.
            query: The search query.
            limit: Maximum number of results.
            mode: Search mode - "hybrid" (default), "dense", or "sparse".

        Returns:
            List of HybridSearchResult ordered by relevance.
        """
        return await hybrid_searcher.search(
            collection_name=collection_name,
            query=query,
            limit=limit,
            mode=mode,
        )

    async def create_collection(
        self,
        collection_name: str,
        vector_size: int = 1024,
        with_sparse: bool = True,
    ) -> bool:
        """Create a Qdrant collection for a knowledge base.

        Args:
            collection_name: Name of the collection.
            vector_size: Dimension of the dense vectors.
            with_sparse: Whether to configure sparse vectors.

        Returns:
            bool: True if collection was created or already exists.
        """
        return await qdrant_indexer.create_collection(
            collection_name=collection_name,
            vector_size=vector_size,
            with_sparse=with_sparse,
        )

    async def reindex_with_sparse(
        self,
        collection_name: str,
    ) -> dict[str, Any]:
        """Re-index an existing collection to add sparse vectors.

        Scrolls through all existing points, generates sparse embeddings
        for their text content, and updates them with sparse vectors.

        Args:
            collection_name: Name of the collection to re-index.

        Returns:
            dict with re-indexing results (updated_count, etc.).
        """
        client = qdrant_indexer._get_client()
        if client == "mock":
            return {"updated_count": 0, "status": "mock"}

        try:
            from qdrant_client.models import SparseVector

            offset = None
            updated_count = 0

            while True:
                results, offset = client.scroll(
                    collection_name=collection_name,
                    limit=100,
                    offset=offset,
                    with_payload=True,
                    with_vectors=True,
                )

                if not results:
                    break

                texts = []
                points_to_update = []
                for point in results:
                    text = point.payload.get("text", "")
                    if text:
                        texts.append(text)
                        points_to_update.append(point)

                if not texts:
                    continue

                embeddings = await embedding_service.encode(texts)

                for point, emb in zip(points_to_update, embeddings, strict=True):
                    if not emb.sparse:
                        continue

                    client.update_vectors(
                        collection_name=collection_name,
                        points=[
                            {
                                "id": point.id,
                                "vector": {
                                    "sparse": SparseVector(
                                        indices=list(emb.sparse.keys()),
                                        values=list(emb.sparse.values()),
                                    )
                                },
                            }
                        ],
                    )
                    updated_count += 1

                if offset is None:
                    break

            return {"updated_count": updated_count, "status": "completed"}
        except Exception as e:
            logger.error(f"Re-indexing failed: {e}")
            return {"updated_count": 0, "status": "error", "error": str(e)}


knowledge_base_service = KnowledgeBaseService()
