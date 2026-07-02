"""Tests for RAG pipeline components.

Tests cover:
- Text chunking
- Embedding service
- Document parsing
- Tool calling protocol
"""

from __future__ import annotations

import pytest

from hecate.services.rag.chunker import TextChunker
from hecate.services.rag.embedding import EmbeddingService


def test_text_chunker_basic() -> None:
    """Test basic text chunking."""
    chunker = TextChunker(chunk_size=100, chunk_overlap=20)
    text = "This is a test. " * 20
    chunks = chunker.chunk_text(text)
    assert len(chunks) > 0
    for chunk in chunks:
        assert len(chunk.content) <= 120


def test_text_chunker_empty() -> None:
    """Test chunking empty text."""
    chunker = TextChunker()
    chunks = chunker.chunk_text("")
    assert chunks == []


def test_text_chunker_metadata() -> None:
    """Test that metadata is attached to chunks."""
    chunker = TextChunker(chunk_size=50, chunk_overlap=10)
    text = "Hello world. " * 10
    metadata = {"source": "test.pdf"}
    chunks = chunker.chunk_text(text, metadata)
    assert len(chunks) > 0
    assert chunks[0].metadata["source"] == "test.pdf"


def test_embedding_service_mock() -> None:
    """Test mock embedding generation."""
    service = EmbeddingService()
    result = service._mock_embedding("test text")
    assert len(result.dense) == 1024
    assert all(0 <= v <= 1 for v in result.dense)


@pytest.mark.asyncio
async def test_embedding_service_encode() -> None:
    """Test embedding service encode."""
    service = EmbeddingService()
    results = await service.encode(["hello", "world"])
    assert len(results) == 2
    assert len(results[0].dense) == 1024


@pytest.mark.asyncio
async def test_embedding_service_encode_query() -> None:
    """Test single query embedding."""
    service = EmbeddingService()
    result = await service.encode_query("test query")
    assert len(result.dense) == 1024
