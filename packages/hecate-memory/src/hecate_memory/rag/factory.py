"""Factory for creating the configured vector store backend.

Reads ``VECTOR_STORE_TYPE`` from settings and returns the appropriate
``VectorStore`` instance.  Supported backends: ``qdrant``, ``chroma``,
``milvus``, ``weaviate``.
"""

from __future__ import annotations

from hecate.core.config import settings
from hecate_memory.rag.vector_store import VectorStore


def get_vector_store() -> VectorStore:
    """Return the vector store for the configured backend.

    Returns:
        A ``VectorStore`` instance matching ``settings.vector_store_type``.

    Raises:
        ValueError: If the configured type is not supported.
    """
    match settings.VECTOR_STORE_TYPE:
        case "qdrant":
            from hecate_memory.rag.qdrant_store import QdrantVectorStore

            return QdrantVectorStore(
                url=settings.QDRANT_URL,
                api_key=settings.QDRANT_API_KEY,
            )
        case "chroma":
            from hecate_memory.rag.chroma_store import ChromaVectorStore

            return ChromaVectorStore(persist_dir=settings.CHROMA_PERSIST_DIR)
        case "milvus":
            from hecate_memory.rag.milvus_store import MilvusVectorStore

            return MilvusVectorStore(uri=settings.MILVUS_URI, token=settings.MILVUS_TOKEN)
        case "weaviate":
            from hecate_memory.rag.weaviate_store import WeaviateVectorStore

            return WeaviateVectorStore(
                url=settings.WEAVIATE_URL,
                grpc_host=settings.WEAVIATE_GRPC_HOST,
                api_key=settings.WEAVIATE_API_KEY,
            )
        case other:
            raise ValueError(
                f"Unsupported VECTOR_STORE_TYPE: {other!r}. Supported types: 'qdrant', 'chroma', 'milvus', 'weaviate'."
            )
