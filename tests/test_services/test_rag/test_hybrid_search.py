"""Tests for hybrid search (dense + sparse retrieval).

Tests cover:
- Hybrid search mode (dense + sparse with RRF fusion)
- Dense-only search mode
- Sparse-only search mode
- Fallback when sparse vectors unavailable
"""

from __future__ import annotations

from typing import Any

import pytest

from hecate.services.rag.searcher import HybridSearcher, HybridSearchResult
from hecate.services.rag.types import SearchResult
from hecate.services.rag.vector_store import VectorStore


class _InMemoryVectorStore(VectorStore):
    """Minimal in-memory store for unit tests."""

    def __init__(self) -> None:
        self._data: dict[str, SearchResult] = {}

    @property
    def supports_hybrid(self) -> bool:
        return False

    async def create_collection(self, collection_name: str, vector_size: int = 1024, with_sparse: bool = True) -> bool:
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
        payloads: list[dict[str, Any]],
        sparse_vectors: list[dict[int, float]] | None = None,
    ) -> bool:
        for id_, payload in zip(ids, payloads, strict=False):
            self._data[id_] = SearchResult(id=id_, score=1.0, payload=payload)
        return True

    async def delete_by_ids(self, collection_name: str, ids: list[str]) -> bool:
        for id_ in ids:
            self._data.pop(id_, None)
        return True

    async def search_dense(
        self,
        collection_name: str,
        query_vector: list[float],
        limit: int = 10,
        workspace_id: str | None = None,
    ) -> list[SearchResult]:
        return sorted(self._data.values(), key=lambda r: r.score, reverse=True)[:limit]

    async def search_sparse(
        self,
        collection_name: str,
        query_sparse: dict[int, float],
        limit: int = 10,
        workspace_id: str | None = None,
    ) -> list[SearchResult]:
        return sorted(self._data.values(), key=lambda r: r.score, reverse=True)[:limit]

    async def count(self, collection_name: str) -> int:
        return len(self._data)

    async def scroll(
        self, collection_name: str, offset: str | None = None, limit: int = 20
    ) -> tuple[list[SearchResult], str | None]:
        items = list(self._data.values())
        start = int(offset) if offset else 0
        page = items[start : start + limit]
        return page, str(start + len(page)) if len(page) == limit else None


@pytest.fixture
def store() -> _InMemoryVectorStore:
    s = _InMemoryVectorStore()
    import asyncio

    loop = asyncio.get_event_loop()
    loop.run_until_complete(
        s.upsert(
            collection_name="test_collection",
            ids=["doc_1", "doc_2", "doc_3"],
            vectors=[[0.1] * 1024, [0.2] * 1024, [0.3] * 1024],
            payloads=[
                {"text": "first document", "metadata": {"source": "a.pdf"}},
                {"text": "second document", "metadata": {"source": "b.pdf"}},
                {"text": "third document", "metadata": {"source": "c.pdf"}},
            ],
        )
    )
    return s


@pytest.fixture
def searcher(store: _InMemoryVectorStore) -> HybridSearcher:
    return HybridSearcher(store=store)


@pytest.mark.asyncio
async def test_hybrid_search_mock_mode(searcher: HybridSearcher) -> None:
    """Test hybrid search in mock mode returns results."""
    results = await searcher.search(
        collection_name="test_collection",
        query="test query",
        limit=5,
        mode="hybrid",
    )
    assert len(results) > 0
    assert all(isinstance(r, HybridSearchResult) for r in results)
    assert all(r.score > 0 for r in results)


@pytest.mark.asyncio
async def test_dense_search_mock_mode(searcher: HybridSearcher) -> None:
    """Test dense-only search in mock mode."""
    results = await searcher.search(
        collection_name="test_collection",
        query="test query",
        limit=5,
        mode="dense",
    )
    assert len(results) > 0
    assert all(r.sparse_score == 0.0 for r in results)


@pytest.mark.asyncio
async def test_sparse_search_mock_mode(searcher: HybridSearcher) -> None:
    """Test sparse-only search in mock mode."""
    results = await searcher.search(
        collection_name="test_collection",
        query="test query",
        limit=5,
        mode="sparse",
    )
    assert len(results) > 0
    assert all(r.sparse_score > 0 for r in results)


@pytest.mark.asyncio
async def test_search_result_has_content_and_metadata(searcher: HybridSearcher) -> None:
    """Test that search results include content and metadata."""
    results = await searcher.search(
        collection_name="test_collection",
        query="test query",
        limit=3,
        mode="hybrid",
    )
    for r in results:
        assert r.id is not None
        assert r.score > 0
        assert isinstance(r.content, str)
        assert isinstance(r.metadata, dict)


@pytest.mark.asyncio
async def test_search_respects_limit(searcher: HybridSearcher) -> None:
    """Test that search respects the limit parameter."""
    results = await searcher.search(
        collection_name="test_collection",
        query="test query",
        limit=2,
        mode="hybrid",
    )
    assert len(results) <= 2


def test_hybrid_search_result_fields() -> None:
    """Test HybridSearchResult dataclass fields."""
    result = HybridSearchResult(
        id="test_id",
        score=0.95,
        content="test content",
        metadata={"source": "test.pdf"},
        sparse_score=0.8,
    )
    assert result.id == "test_id"
    assert result.score == 0.95
    assert result.content == "test content"
    assert result.metadata == {"source": "test.pdf"}
    assert result.sparse_score == 0.8


def test_hybrid_search_result_default_sparse_score() -> None:
    """Test HybridSearchResult default sparse_score is 0.0."""
    result = HybridSearchResult(
        id="test_id",
        score=0.95,
        content="test content",
        metadata={},
    )
    assert result.sparse_score == 0.0


@pytest.mark.asyncio
async def test_hybrid_search_populates_score_breakdown(searcher: HybridSearcher) -> None:
    """Test hybrid search populates both dense_score and sparse_score."""
    results = await searcher.search(
        collection_name="test_collection",
        query="test query",
        limit=5,
        mode="hybrid",
    )
    for r in results:
        assert hasattr(r, "dense_score")
        assert hasattr(r, "sparse_score")
        assert r.dense_score >= 0.0
        assert r.sparse_score >= 0.0


@pytest.mark.asyncio
async def test_dense_search_populates_dense_score(searcher: HybridSearcher) -> None:
    """Test dense search populates dense_score on results."""
    results = await searcher.search(
        collection_name="test_collection",
        query="test query",
        limit=5,
        mode="dense",
    )
    for r in results:
        assert r.dense_score > 0
        assert r.sparse_score == 0.0


def test_hybrid_search_result_dense_score_default() -> None:
    """Test HybridSearchResult default dense_score is 0.0."""
    result = HybridSearchResult(
        id="test_id",
        score=0.95,
        content="test content",
        metadata={},
    )
    assert result.dense_score == 0.0
