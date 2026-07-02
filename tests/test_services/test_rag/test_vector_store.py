"""Tests for VectorStore ABC, QdrantVectorStore, ChromaVectorStore, and factory.

Covers:
- ABC not instantiable directly
- QdrantVectorStore mock fallback
- ChromaVectorStore mock fallback
- _rrf_fuse correctness
- get_vector_store factory
"""

from __future__ import annotations

import pytest

from hecate.services.rag.types import SearchResult
from hecate.services.rag.vector_store import VectorStore, _rrf_fuse


class TestVectorStoreABC:
    def test_abc_not_instantiable(self) -> None:
        with pytest.raises(TypeError):
            VectorStore()  # type: ignore[abstract]

    def test_complete_subclass_works(self) -> None:
        class CompleteStore(VectorStore):
            async def create_collection(
                self, collection_name: str, vector_size: int = 1024, with_sparse: bool = True
            ) -> bool:
                return True

            async def delete_collection(self, collection_name: str) -> bool:
                return True

            async def collection_exists(self, collection_name: str) -> bool:
                return True

            async def upsert(
                self,
                collection_name: str,
                ids: list[str],
                vectors: list[list[float]],
                payloads: list[dict],
                sparse_vectors: list[dict[int, float]] | None = None,
            ) -> bool:
                return True

            async def delete_by_ids(self, collection_name: str, ids: list[str]) -> bool:
                return True

            async def search_dense(
                self, collection_name: str, query_vector: list[float], limit: int = 10
            ) -> list[SearchResult]:
                return []

            async def search_sparse(
                self, collection_name: str, query_sparse: dict[int, float], limit: int = 10
            ) -> list[SearchResult]:
                return []

            async def count(self, collection_name: str) -> int:
                return 0

            async def scroll(
                self, collection_name: str, offset: str | None = None, limit: int = 20
            ) -> tuple[list[SearchResult], str | None]:
                return [], None

        store = CompleteStore()
        assert store.supports_hybrid is False


class TestQdrantVectorStoreMockFallback:
    def test_import_without_qdrant_client(self) -> None:
        from hecate.services.rag.qdrant_store import QdrantVectorStore

        store = QdrantVectorStore()
        assert store.supports_hybrid is True

    @pytest.mark.asyncio
    async def test_mock_create_collection(self) -> None:
        from hecate.services.rag.qdrant_store import QdrantVectorStore

        store = QdrantVectorStore()
        result = await store.create_collection("test")
        assert result is True

    @pytest.mark.asyncio
    async def test_mock_search_dense(self) -> None:
        from hecate.services.rag.qdrant_store import QdrantVectorStore

        store = QdrantVectorStore()
        results = await store.search_dense("test", [0.1] * 1024, limit=3)
        assert len(results) > 0
        assert all(isinstance(r, SearchResult) for r in results)

    @pytest.mark.asyncio
    async def test_mock_search_sparse(self) -> None:
        from hecate.services.rag.qdrant_store import QdrantVectorStore

        store = QdrantVectorStore()
        results = await store.search_sparse("test", {1: 0.5, 2: 0.3}, limit=3)
        assert len(results) > 0

    @pytest.mark.asyncio
    async def test_mock_search_hybrid(self) -> None:
        from hecate.services.rag.qdrant_store import QdrantVectorStore

        store = QdrantVectorStore()
        results = await store.search_hybrid("test", [0.1] * 1024, {1: 0.5}, limit=3)
        assert len(results) > 0

    @pytest.mark.asyncio
    async def test_mock_count(self) -> None:
        from hecate.services.rag.qdrant_store import QdrantVectorStore

        store = QdrantVectorStore()
        count = await store.count("test")
        assert count == 42

    @pytest.mark.asyncio
    async def test_mock_scroll(self) -> None:
        from hecate.services.rag.qdrant_store import QdrantVectorStore

        store = QdrantVectorStore()
        results, offset = await store.scroll("test")
        assert len(results) > 0
        assert offset is None


class TestChromaVectorStoreMockFallback:
    def test_import_without_chromadb(self) -> None:
        from hecate.services.rag.chroma_store import ChromaVectorStore

        store = ChromaVectorStore()
        assert store.supports_hybrid is False

    @pytest.mark.asyncio
    async def test_mock_create_collection(self) -> None:
        from hecate.services.rag.chroma_store import ChromaVectorStore

        store = ChromaVectorStore()
        result = await store.create_collection("test")
        assert result is True

    @pytest.mark.asyncio
    async def test_mock_search_dense(self) -> None:
        from hecate.services.rag.chroma_store import ChromaVectorStore

        store = ChromaVectorStore()
        results = await store.search_dense("test", [0.1] * 1024, limit=3)
        assert len(results) > 0

    @pytest.mark.asyncio
    async def test_search_sparse_returns_empty(self) -> None:
        from hecate.services.rag.chroma_store import ChromaVectorStore

        store = ChromaVectorStore()
        results = await store.search_sparse("test", {1: 0.5}, limit=3)
        assert results == []

    @pytest.mark.asyncio
    async def test_mock_count(self) -> None:
        from hecate.services.rag.chroma_store import ChromaVectorStore

        store = ChromaVectorStore()
        count = await store.count("test")
        assert count == 42


class TestRrfFuse:
    def test_basic_fusion(self) -> None:
        dense = [
            SearchResult(id="a", score=0.9, payload={}),
            SearchResult(id="b", score=0.8, payload={}),
        ]
        sparse = [
            SearchResult(id="b", score=0.85, payload={}),
            SearchResult(id="c", score=0.7, payload={}),
        ]
        results = _rrf_fuse(dense, sparse, k=60, top_k=3)
        assert len(results) == 3
        ids = [r.id for r in results]
        assert "b" in ids
        assert "a" in ids
        assert "c" in ids

    def test_document_in_both_channels_gets_higher_score(self) -> None:
        dense = [
            SearchResult(id="a", score=0.9, payload={}),
            SearchResult(id="b", score=0.8, payload={}),
            SearchResult(id="c", score=0.7, payload={}),
        ]
        sparse = [
            SearchResult(id="a", score=0.85, payload={}),
            SearchResult(id="c", score=0.75, payload={}),
            SearchResult(id="d", score=0.65, payload={}),
        ]
        results = _rrf_fuse(dense, sparse, k=60, top_k=4)
        scores = {r.id: r.score for r in results}
        assert scores["a"] > scores["c"]
        assert scores["c"] > scores["b"]
        assert scores["c"] > scores["d"]

    def test_k_constant_affects_scores(self) -> None:
        dense = [SearchResult(id="a", score=0.9, payload={})]
        sparse = [SearchResult(id="a", score=0.9, payload={})]
        results_k10 = _rrf_fuse(dense, sparse, k=10, top_k=1)
        results_k60 = _rrf_fuse(dense, sparse, k=60, top_k=1)
        assert results_k10[0].score > results_k60[0].score

    def test_top_k_limits_results(self) -> None:
        dense = [SearchResult(id=f"d_{i}", score=1.0 - i * 0.1, payload={}) for i in range(10)]
        sparse = [SearchResult(id=f"s_{i}", score=1.0 - i * 0.1, payload={}) for i in range(10)]
        results = _rrf_fuse(dense, sparse, k=60, top_k=5)
        assert len(results) == 5

    def test_empty_inputs(self) -> None:
        results = _rrf_fuse([], [], k=60, top_k=5)
        assert results == []


class TestGetVectorStoreFactory:
    def test_returns_qdrant_store_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("hecate.services.rag.factory.settings.VECTOR_STORE_TYPE", "qdrant")
        from hecate.services.rag.factory import get_vector_store
        from hecate.services.rag.qdrant_store import QdrantVectorStore

        store = get_vector_store()
        assert isinstance(store, QdrantVectorStore)

    def test_returns_chroma_store(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("hecate.services.rag.factory.settings.VECTOR_STORE_TYPE", "chroma")
        from hecate.services.rag.chroma_store import ChromaVectorStore
        from hecate.services.rag.factory import get_vector_store

        store = get_vector_store()
        assert isinstance(store, ChromaVectorStore)

    def test_raises_on_unknown_type(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("hecate.services.rag.factory.settings.VECTOR_STORE_TYPE", "milvus")
        from hecate.services.rag.factory import get_vector_store

        with pytest.raises(ValueError, match="Unsupported VECTOR_STORE_TYPE"):
            get_vector_store()
