"""Tests for hybrid search (dense + sparse retrieval).

Tests cover:
- Hybrid search mode (dense + sparse with RRF fusion)
- Dense-only search mode
- Sparse-only search mode
- Fallback when sparse vectors unavailable
"""

from __future__ import annotations

import pytest

from hecate.services.rag.searcher import HybridSearcher, HybridSearchResult


@pytest.mark.asyncio
async def test_hybrid_search_mock_mode() -> None:
    """Test hybrid search in mock mode returns results."""
    searcher = HybridSearcher()
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
async def test_dense_search_mock_mode() -> None:
    """Test dense-only search in mock mode."""
    searcher = HybridSearcher()
    results = await searcher.search(
        collection_name="test_collection",
        query="test query",
        limit=5,
        mode="dense",
    )
    assert len(results) > 0
    assert all(r.sparse_score == 0.0 for r in results)


@pytest.mark.asyncio
async def test_sparse_search_mock_mode() -> None:
    """Test sparse-only search in mock mode."""
    searcher = HybridSearcher()
    results = await searcher.search(
        collection_name="test_collection",
        query="test query",
        limit=5,
        mode="sparse",
    )
    assert len(results) > 0
    assert all(r.sparse_score > 0 for r in results)


@pytest.mark.asyncio
async def test_search_result_has_content_and_metadata() -> None:
    """Test that search results include content and metadata."""
    searcher = HybridSearcher()
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
async def test_search_respects_limit() -> None:
    """Test that search respects the limit parameter."""
    searcher = HybridSearcher()
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
