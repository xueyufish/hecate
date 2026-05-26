"""Knowledge Base service orchestrating the RAG pipeline.

Coordinates document parsing, chunking, embedding, and indexing
for complete knowledge base management.
"""

from __future__ import annotations

import logging
from typing import Any

from hecate.services.rag.chunker import text_chunker
from hecate.services.rag.embedding import embedding_service
from hecate.services.rag.indexer import qdrant_indexer
from hecate.services.rag.parser import document_parser
from hecate.services.rag.searcher import HybridSearchResult, hybrid_searcher

logger = logging.getLogger(__name__)


class KnowledgeBaseService:
    """Manage knowledge base operations.

    Provides:
    - Document ingestion (parse → chunk → embed → index)
    - Similarity search
    - Collection management
    """

    async def ingest_document(
        self,
        file_path: str,
        collection_name: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Ingest a document into the knowledge base.

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
        )

        return {"chunk_count": len(chunks), "collection": collection_name}

    async def search(
        self,
        collection_name: str,
        query: str,
        limit: int = 10,
    ) -> list[HybridSearchResult]:
        """Search the knowledge base.

        Args:
            collection_name: Qdrant collection name.
            query: The search query.
            limit: Maximum number of results.

        Returns:
            List of HybridSearchResult ordered by relevance.
        """
        return await hybrid_searcher.search(
            collection_name=collection_name,
            query=query,
            limit=limit,
        )

    async def create_collection(
        self,
        collection_name: str,
        vector_size: int = 1024,
    ) -> bool:
        """Create a Qdrant collection for a knowledge base.

        Args:
            collection_name: Name of the collection.
            vector_size: Dimension of the dense vectors.

        Returns:
            bool: True if collection was created or already exists.
        """
        return await qdrant_indexer.create_collection(
            collection_name=collection_name,
            vector_size=vector_size,
        )


knowledge_base_service = KnowledgeBaseService()
