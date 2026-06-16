"""Tests for KnowledgeBaseService hit testing methods."""

from __future__ import annotations

import pytest

from hecate.services.rag.service import KnowledgeBaseService


@pytest.fixture
def service() -> KnowledgeBaseService:
    return KnowledgeBaseService()


async def test_search_with_score_breakdown(service: KnowledgeBaseService) -> None:
    """Test search_with_score_breakdown returns results with score fields."""
    results = await service.search_with_score_breakdown(
        collection_name="test_collection",
        query="test query",
        limit=5,
        mode="hybrid",
    )
    assert len(results) > 0
    for r in results:
        assert hasattr(r, "dense_score")
        assert hasattr(r, "sparse_score")
        assert r.score > 0


async def test_search_with_score_breakdown_dense_mode(service: KnowledgeBaseService) -> None:
    """Test dense mode populates dense_score."""
    results = await service.search_with_score_breakdown(
        collection_name="test_collection",
        query="test query",
        limit=3,
        mode="dense",
    )
    for r in results:
        assert r.dense_score > 0
        assert r.sparse_score == 0.0


async def test_list_chunks(service: KnowledgeBaseService) -> None:
    """Test list_chunks returns paginated results."""
    result = await service.list_chunks(
        collection_name="test_collection",
        page=1,
        page_size=10,
    )
    assert "items" in result
    assert "total" in result
    assert isinstance(result["items"], list)
    assert isinstance(result["total"], int)


async def test_list_chunks_item_structure(service: KnowledgeBaseService) -> None:
    """Test list_chunks items have correct structure."""
    result = await service.list_chunks(
        collection_name="test_collection",
        page=1,
        page_size=5,
    )
    for item in result["items"]:
        assert "id" in item
        assert "content_preview" in item
        assert "metadata" in item
        assert isinstance(item["content_preview"], str)
        assert len(item["content_preview"]) <= 203  # 200 + "..."


async def test_list_chunks_pagination(service: KnowledgeBaseService) -> None:
    """Test list_chunks respects page and page_size."""
    result = await service.list_chunks(
        collection_name="test_collection",
        page=1,
        page_size=2,
    )
    assert len(result["items"]) <= 2


async def test_compare_modes(service: KnowledgeBaseService) -> None:
    """Test compare_modes returns results for all 3 modes."""
    result = await service.compare_modes(
        collection_name="test_collection",
        query="test query",
        limit=3,
    )
    assert "dense" in result
    assert "sparse" in result
    assert "hybrid" in result
    assert result["query"] == "test query"
    assert isinstance(result["dense"], list)
    assert isinstance(result["sparse"], list)
    assert isinstance(result["hybrid"], list)


async def test_compare_modes_result_structure(service: KnowledgeBaseService) -> None:
    """Test compare_modes results have correct structure."""
    result = await service.compare_modes(
        collection_name="test_collection",
        query="test",
        limit=2,
    )
    for mode in ("dense", "sparse", "hybrid"):
        for item in result[mode]:
            assert "id" in item
            assert "score" in item
            assert "content" in item
            assert "dense_score" in item
            assert "sparse_score" in item


async def test_compare_modes_respects_limit(service: KnowledgeBaseService) -> None:
    """Test compare_modes limits results per mode."""
    result = await service.compare_modes(
        collection_name="test_collection",
        query="test",
        limit=1,
    )
    for mode in ("dense", "sparse", "hybrid"):
        assert len(result[mode]) <= 1
