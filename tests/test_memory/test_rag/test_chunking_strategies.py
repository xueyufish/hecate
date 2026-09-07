"""Tests for chunking strategies (catalog 3.2.6).

Covers character (default/back-compat), separator, auto, and semantic
strategies, plus the forward-progress regression for chunk_overlap >=
chunk_size.
"""

from __future__ import annotations

from typing import Any

from hecate_memory.rag.chunker import ChunkingStrategy, TextChunker


class _FakeSyncEmbedding:
    """Sync embedding service returning preset vectors in order."""

    def __init__(self, vectors: list[list[float]]) -> None:
        self.vectors = vectors

    def encode(self, texts: list[str]) -> list[Any]:
        return [type("R", (), {"embedding": vector})() for vector in self.vecs_for(texts)]

    def vecs_for(self, texts: list[str]) -> list[list[float]]:
        return self.vectors[: len(texts)]


class _FakeAsyncEmbedding(_FakeSyncEmbedding):
    async def encode(self, texts: list[str]) -> list[Any]:
        return super().encode(texts)


class TestCharacterStrategy:
    async def test_default_strategy_is_character(self) -> None:
        chunker = TextChunker(chunk_size=100, chunk_overlap=20)
        assert chunker.strategy == ChunkingStrategy.CHARACTER

    async def test_behavior_unchanged_from_legacy(self) -> None:
        chunker = TextChunker(chunk_size=100, chunk_overlap=20)
        chunks = chunker.chunk_text("This is a test. " * 20)
        assert len(chunks) > 0
        assert all(len(chunk.content) <= 120 for chunk in chunks)


class TestSeparatorStrategy:
    async def test_splits_on_blank_lines(self) -> None:
        chunker = TextChunker(chunk_size=30, strategy="separator")
        chunks = chunker.chunk_text("Para one about cats.\n\nPara two about dogs.\n\nPara three about birds.")
        assert len(chunks) == 3
        assert "cats" in chunks[0].content
        assert "dogs" in chunks[1].content
        assert "birds" in chunks[2].content

    async def test_merges_small_fragments_up_to_chunk_size(self) -> None:
        chunker = TextChunker(chunk_size=50, strategy="separator")
        chunks = chunker.chunk_text("alpha\n\nbeta")
        assert len(chunks) == 1
        assert "alpha" in chunks[0].content and "beta" in chunks[0].content

    async def test_oversized_fragment_falls_back_to_character(self) -> None:
        chunker = TextChunker(chunk_size=30, strategy="separator")
        chunks = chunker.chunk_text("x" * 80 + "\n\nshort")
        assert all(len(chunk.content) <= 120 for chunk in chunks)


class TestAutoStrategy:
    async def test_structured_text_routes_to_separator(self) -> None:
        chunker = TextChunker(chunk_size=50, strategy="auto")
        assert chunker._detect_strategy("a\n\nb\n\nc") == ChunkingStrategy.SEPARATOR

    async def test_markdown_headers_route_to_separator(self) -> None:
        chunker = TextChunker(chunk_size=50, strategy="auto")
        assert chunker._detect_strategy("# Title\nbody") == ChunkingStrategy.SEPARATOR

    async def test_plain_text_routes_to_character(self) -> None:
        chunker = TextChunker(chunk_size=50, strategy="auto")
        assert chunker._detect_strategy("plain text only") == ChunkingStrategy.CHARACTER

    async def test_auto_plain_text_matches_character_output(self) -> None:
        text = "This is a test. " * 20
        auto_chunks = TextChunker(chunk_size=100, chunk_overlap=20, strategy="auto").chunk_text(text)
        char_chunks = TextChunker(chunk_size=100, chunk_overlap=20, strategy="character").chunk_text(text)
        assert [c.content for c in auto_chunks] == [c.content for c in char_chunks]


class TestSemanticStrategy:
    async def test_requires_injected_service_falls_back_to_separator(self) -> None:
        chunker = TextChunker(chunk_size=500, strategy="semantic")
        chunks = chunker.chunk_text("Cats are furry. Dogs bark loudly. Birds can fly high.")
        # Without a service this is separator behavior: sentences stay packed.
        assert len(chunks) == 1

    async def test_sync_service_splits_on_similarity_drop(self) -> None:
        cats, dogs = [1.0, 0.0], [0.0, 1.0]
        chunker = TextChunker(
            chunk_size=500,
            strategy="semantic",
            similarity_threshold=0.5,
            embedding_service=_FakeSyncEmbedding([cats, dogs, cats]),
        )
        chunks = chunker.chunk_text("Cats are furry. Dogs bark loudly. Birds can fly high.")
        assert len(chunks) == 3
        assert chunks[0].content == "Cats are furry."
        assert chunks[1].content == "Dogs bark loudly."
        assert chunks[2].content == "Birds can fly high."

    async def test_similar_sentences_stay_packed(self) -> None:
        cats = [1.0, 0.0]
        chunker = TextChunker(
            chunk_size=500,
            strategy="semantic",
            similarity_threshold=0.5,
            embedding_service=_FakeSyncEmbedding([cats, cats, cats]),
        )
        chunks = chunker.chunk_text("Cats are furry. Dogs bark loudly. Birds can fly high.")
        assert len(chunks) == 1

    async def test_async_service_via_chunk_text_async(self) -> None:
        cats = [1.0, 0.0]
        chunker = TextChunker(
            chunk_size=500,
            strategy="semantic",
            similarity_threshold=0.5,
            embedding_service=_FakeAsyncEmbedding([cats, cats]),
        )
        chunks = await chunker.chunk_text_async("Cats are furry. Dogs bark loudly.")
        assert len(chunks) == 1
        assert chunks[0].metadata == {}

    async def test_chunk_size_cap_enforced(self) -> None:
        cats = [1.0, 0.0]
        chunker = TextChunker(
            chunk_size=20,
            strategy="semantic",
            similarity_threshold=0.5,
            embedding_service=_FakeSyncEmbedding([cats] * 5),
        )
        chunks = chunker.chunk_text("one. two. three. four. five.")
        assert len(chunks) >= 2
        assert all(len(chunk.content) <= 20 for chunk in chunks)


class TestOverlapForwardProgressRegression:
    async def test_chunk_overlap_ge_size_terminates(self) -> None:
        """chunk_overlap >= chunk_size must not move `start` backwards (hang)."""
        chunker = TextChunker(chunk_size=30, chunk_overlap=200)
        chunks = chunker.chunk_text("x" * 80)
        assert len(chunks) >= 2
        assert all(len(chunk.content) <= 30 for chunk in chunks)

    async def test_metadata_propagates_all_strategies(self) -> None:
        for strategy in ("character", "separator", "auto"):
            chunker = TextChunker(chunk_size=30, strategy=strategy)
            chunks = chunker.chunk_text("aaa\n\nbbb", {"src": "x"})
            assert all(chunk.metadata == {"src": "x"} for chunk in chunks)
