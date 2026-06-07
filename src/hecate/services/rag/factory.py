"""Factory for creating the configured vector store backend.

Reads ``VECTOR_STORE_TYPE`` from settings and returns the appropriate
``VectorStore`` instance.  Supported backends: ``qdrant``, ``chroma``.
"""

from __future__ import annotations

from hecate.core.config import settings
from hecate.services.rag.vector_store import VectorStore


def get_vector_store() -> VectorStore:
    """Return the vector store for the configured backend.

    Returns:
        A ``VectorStore`` instance matching ``settings.vector_store_type``.

    Raises:
        ValueError: If the configured type is not supported.
    """
    match settings.VECTOR_STORE_TYPE:
        case "qdrant":
            from hecate.services.rag.qdrant_store import QdrantVectorStore

            return QdrantVectorStore(
                url=settings.QDRANT_URL,
                api_key=settings.QDRANT_API_KEY,
            )
        case "chroma":
            from hecate.services.rag.chroma_store import ChromaVectorStore

            return ChromaVectorStore(persist_dir=settings.CHROMA_PERSIST_DIR)
        case other:
            raise ValueError(f"Unsupported VECTOR_STORE_TYPE: {other!r}. Supported types: 'qdrant', 'chroma'.")
