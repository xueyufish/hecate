"""Tests for sparse vector generation in EmbeddingService.

Tests cover:
- Dense + sparse vector generation via encode()
- Single query embedding via encode_query()
- Mock embedding with deterministic sparse vectors
"""

from __future__ import annotations

import pytest

from hecate.services.rag.embedding import EmbeddingService


def test_mock_embedding_has_sparse() -> None:
    """Test that mock embedding generates both dense and sparse vectors."""
    service = EmbeddingService()
    result = service._mock_embedding("test text for sparse")
    assert len(result.dense) == 1024
    assert len(result.sparse) > 0
    assert all(isinstance(k, int) for k in result.sparse.keys())
    assert all(isinstance(v, float) for v in result.sparse.values())


def test_mock_embedding_deterministic() -> None:
    """Test that mock embedding is deterministic for same input."""
    service = EmbeddingService()
    result1 = service._mock_embedding("hello world")
    result2 = service._mock_embedding("hello world")
    assert result1.dense == result2.dense
    assert result1.sparse == result2.sparse


def test_mock_embedding_different_inputs() -> None:
    """Test that different inputs produce different sparse vectors."""
    service = EmbeddingService()
    result1 = service._mock_embedding("hello world")
    result2 = service._mock_embedding("goodbye planet")
    assert result1.sparse != result2.sparse


def test_mock_embedding_sparse_word_based() -> None:
    """Test that sparse vector is based on word tokens."""
    service = EmbeddingService()
    result = service._mock_embedding("python java python")
    assert len(result.sparse) == 2  # Two unique words
    assert all(v > 0 for v in result.sparse.values())


@pytest.mark.asyncio
async def test_encode_returns_sparse() -> None:
    """Test that encode() returns sparse vectors (mock mode)."""
    service = EmbeddingService()
    results = await service.encode(["hello", "world"])
    assert len(results) == 2
    for result in results:
        assert len(result.dense) == 1024
        assert len(result.sparse) > 0


@pytest.mark.asyncio
async def test_encode_query_returns_sparse() -> None:
    """Test that encode_query() returns both dense and sparse (mock mode)."""
    service = EmbeddingService()
    result = await service.encode_query("test query")
    assert len(result.dense) == 1024
    assert len(result.sparse) > 0
