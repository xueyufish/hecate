"""Chroma vector store adapter implementing the VectorStore ABC.

Wraps ``chromadb`` for local/embedded vector storage.  Does **not**
support sparse vectors or native hybrid search — inherits the default
application-layer RRF fusion from the ABC.
"""

from __future__ import annotations

import logging
from typing import Any

from hecate.services.rag.types import SearchResult
from hecate.services.rag.vector_store import VectorStore

logger = logging.getLogger(__name__)


class ChromaVectorStore(VectorStore):
    """VectorStore implementation backed by ChromaDB.

    Chroma does not support sparse vectors or native hybrid search,
    so ``supports_hybrid`` returns ``False`` and ``search_sparse``
    returns an empty list.
    """

    def __init__(self, persist_dir: str = "./data/chroma") -> None:
        self.persist_dir = persist_dir
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                import chromadb

                self._client = chromadb.PersistentClient(path=self.persist_dir)
                logger.info(f"Connected to Chroma at {self.persist_dir}")
            except ImportError:
                logger.warning("chromadb not installed. Using mock client.")
                self._client = "mock"
        return self._client

    def _get_or_create_collection(self, collection_name: str) -> Any:
        client = self._get_client()
        if client == "mock":
            return "mock_collection"
        return client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )

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
            client.get_or_create_collection(
                name=collection_name,
                metadata={"hnsw:space": "cosine"},
            )
            logger.info(f"Created collection {collection_name}")
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
            client.delete_collection(name=collection_name)
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
            collections = client.list_collections()
            return any(c.name == collection_name for c in collections)
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
        collection = self._get_or_create_collection(collection_name)

        if collection == "mock_collection":
            logger.info(f"Mock: Upserted {len(ids)} vectors to {collection_name}")
            return True

        try:
            import json

            chroma_ids = list(ids)
            chroma_embeddings = [list(v) for v in vectors]
            chroma_metadatas: list[dict[str, Any]] = []
            chroma_documents: list[str] = []

            for payload in payloads:
                text = payload.get("text", "")
                chroma_documents.append(text)
                metadata = {k: v for k, v in payload.items() if k != "text"}
                chroma_metadatas.append({"metadata_json": json.dumps(metadata)})

            collection.upsert(
                ids=chroma_ids,
                embeddings=chroma_embeddings,
                metadatas=chroma_metadatas,
                documents=chroma_documents,
            )
            logger.info(f"Upserted {len(ids)} vectors to {collection_name}")
            return True
        except Exception as e:
            logger.error(f"Failed to upsert vectors: {e}")
            return False

    async def delete_by_ids(self, collection_name: str, ids: list[str]) -> bool:
        collection = self._get_or_create_collection(collection_name)

        if collection == "mock_collection":
            logger.info(f"Mock: Deleted {len(ids)} points from {collection_name}")
            return True

        try:
            collection.delete(ids=list(ids))
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
        collection = self._get_or_create_collection(collection_name)

        if collection == "mock_collection":
            return [
                SearchResult(
                    id=f"mock_{i}",
                    score=0.9 - i * 0.1,
                    payload={"text": f"Mock result {i}", "metadata": {}},
                )
                for i in range(min(limit, 3))
            ]

        try:
            import json

            query_kwargs: dict[str, Any] = {
                "query_embeddings": [query_vector],
                "n_results": limit,
                "include": ["documents", "metadatas", "distances"],
            }
            if workspace_id is not None:
                query_kwargs["where"] = {"workspace_id": workspace_id}
            else:
                logger.warning("Vector search without workspace_id filter — tenant isolation not enforced")

            results = collection.query(**query_kwargs)

            search_results: list[SearchResult] = []
            if results["ids"] and results["ids"][0]:
                for i, doc_id in enumerate(results["ids"][0]):
                    distance = results["distances"][0][i] if results["distances"] else 0.0
                    score = 1.0 - distance
                    document = results["documents"][0][i] if results["documents"] else ""
                    raw_meta = results["metadatas"][0][i] if results["metadatas"] else {}
                    metadata = json.loads(raw_meta.get("metadata_json", "{}"))
                    metadata["text"] = document
                    search_results.append(SearchResult(id=str(doc_id), score=score, payload=metadata))

            return search_results
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
        logger.warning("Chroma does not support sparse vector search. Returning empty results.")
        return []

    async def count(self, collection_name: str) -> int:
        collection = self._get_or_create_collection(collection_name)

        if collection == "mock_collection":
            return 42

        try:
            return collection.count()
        except Exception as e:
            logger.error(f"Count failed: {e}")
            return 0

    async def scroll(
        self,
        collection_name: str,
        offset: str | None = None,
        limit: int = 20,
    ) -> tuple[list[SearchResult], str | None]:
        collection = self._get_or_create_collection(collection_name)

        if collection == "mock_collection":
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
            import json

            offset_int = int(offset) if offset else 0

            all_results = collection.get(
                include=["documents", "metadatas"],
                offset=offset_int,
                limit=limit,
            )

            search_results: list[SearchResult] = []
            if all_results["ids"]:
                for i, doc_id in enumerate(all_results["ids"]):
                    document = all_results["documents"][i] if all_results["documents"] else ""
                    raw_meta = all_results["metadatas"][i] if all_results["metadatas"] else {}
                    metadata = json.loads(raw_meta.get("metadata_json", "{}"))
                    metadata["text"] = document
                    search_results.append(SearchResult(id=str(doc_id), score=1.0, payload=metadata))

            next_offset = str(offset_int + len(search_results)) if len(search_results) == limit else None
            return search_results, next_offset
        except Exception as e:
            logger.error(f"Scroll failed: {e}")
            return [], None
