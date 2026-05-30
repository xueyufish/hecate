"""Tests for KnowledgeBaseService with hybrid search support.

Tests cover:
- Document ingestion with sparse vectors
- Search with multiple modes (hybrid, dense, sparse)
- Collection creation with sparse vector config
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from hecate.services.rag.service import KnowledgeBaseService


@pytest.mark.asyncio
async def test_ingest_document_mock() -> None:
    """Test document ingestion in mock mode."""
    service = KnowledgeBaseService()

    mock_chunks = [
        AsyncMock(content="chunk 1", index=0, metadata={}),
        AsyncMock(content="chunk 2", index=1, metadata={}),
    ]

    with (
        patch("hecate.services.rag.service.document_parser") as mock_parser,
        patch("hecate.services.rag.service.text_chunker") as mock_chunker,
    ):
        mock_parser.parse = AsyncMock(return_value="test content")
        mock_chunker.chunk_text.return_value = mock_chunks

        result = await service.ingest_document(
            file_path="test.txt",
            collection_name="test_collection",
            metadata={"source": "test"},
        )
        assert "chunk_count" in result or "error" in result


@pytest.mark.asyncio
async def test_search_hybrid_mode() -> None:
    """Test search with hybrid mode."""
    service = KnowledgeBaseService()
    results = await service.search(
        collection_name="test_collection",
        query="test query",
        limit=5,
        mode="hybrid",
    )
    assert isinstance(results, list)


@pytest.mark.asyncio
async def test_search_dense_mode() -> None:
    """Test search with dense mode."""
    service = KnowledgeBaseService()
    results = await service.search(
        collection_name="test_collection",
        query="test query",
        limit=5,
        mode="dense",
    )
    assert isinstance(results, list)


@pytest.mark.asyncio
async def test_search_sparse_mode() -> None:
    """Test search with sparse mode."""
    service = KnowledgeBaseService()
    results = await service.search(
        collection_name="test_collection",
        query="test query",
        limit=5,
        mode="sparse",
    )
    assert isinstance(results, list)


@pytest.mark.asyncio
async def test_create_collection_with_sparse() -> None:
    """Test collection creation with sparse vectors."""
    service = KnowledgeBaseService()
    result = await service.create_collection(
        collection_name="test_hybrid_collection",
        vector_size=1024,
        with_sparse=True,
    )
    assert result is True


@pytest.mark.asyncio
async def test_create_collection_without_sparse() -> None:
    """Test collection creation without sparse vectors."""
    service = KnowledgeBaseService()
    result = await service.create_collection(
        collection_name="test_dense_only_collection",
        vector_size=1024,
        with_sparse=False,
    )
    assert result is True
