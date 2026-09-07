"""Text chunker for splitting documents into smaller pieces.

Splits text into chunks suitable for embedding and retrieval. Supports four
strategies (catalog 3.2.6):

- ``character`` — fixed-size chunks by character count with a smart
  breakpoint (sentence/line boundary) and overlap. Default; preserves the
  historical behavior.
- ``separator`` — split on structural separators (paragraph breaks, line
  breaks), merging fragments up to ``chunk_size``.
- ``auto`` — pick per document: separator-based when the text is visibly
  structured (blank-line paragraphs / markdown headers), otherwise
  character-based.
- ``semantic`` — sentence-level chunking with embedding-similarity
  breakpoints: adjacent sentences whose embeddings diverge beyond
  ``similarity_threshold`` start a new chunk.
"""

from __future__ import annotations

import inspect
import logging
import math
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)


class ChunkingStrategy(StrEnum):
    """Document chunking strategies (catalog 3.2.6)."""

    AUTO = "auto"
    CHARACTER = "character"
    SEPARATOR = "separator"
    SEMANTIC = "semantic"


@dataclass
class Chunk:
    """A text chunk with metadata."""

    content: str
    index: int
    start_char: int
    end_char: int
    metadata: dict = field(default_factory=dict)


_SENTENCE_SPLIT = re.compile(r"(?<=[.!?。！？])\s+")
_MARKDOWN_HEADER = re.compile(r"^#{1,6}\s", re.MULTILINE)
_DEFAULT_SEPARATORS = ["\n\n", "\n"]


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two equal-length vectors."""
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


class TextChunker:
    """Split text into chunks for embedding.

    Supports:
    - Fixed-size chunking (by character count) with smart breakpoints
    - Separator-based chunking for structured documents
    - Auto strategy selection per document
    - Semantic chunking with embedding-similarity breakpoints
    - Overlap between chunks for context preservation (character/separator)
    - Metadata tracking for each chunk
    """

    def __init__(
        self,
        chunk_size: int = 1000,
        chunk_overlap: int = 200,
        strategy: str | ChunkingStrategy = ChunkingStrategy.CHARACTER,
        separators: list[str] | None = None,
        embedding_service: Any = None,
        similarity_threshold: float = 0.35,
    ):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.strategy = ChunkingStrategy(strategy)
        self.separators = separators or _DEFAULT_SEPARATORS
        self.similarity_threshold = similarity_threshold
        # Optional EmbeddingService; lazily constructed for SEMANTIC when absent.
        self._embedding_service = embedding_service

    def chunk_text(
        self,
        text: str,
        metadata: dict | None = None,
    ) -> list[Chunk]:
        """Split text into chunks using the configured strategy (sync).

        CHARACTER / SEPARATOR / AUTO are fully synchronous. SEMANTIC needs a
        sync ``embedding_service.encode(texts) -> list[EmbeddingResult]``; an
        awaitable encode (e.g. ``EmbeddingService``) requires
        :meth:`chunk_text_async`.

        Args:
            text: The text to split.
            metadata: Optional metadata to attach to each chunk.

        Returns:
            List of Chunk objects.
        """
        if not text:
            return []

        if self.strategy == ChunkingStrategy.SEMANTIC:
            chunks = self._chunk_semantic(text)
        elif self.strategy == ChunkingStrategy.SEPARATOR:
            chunks = self._chunk_by_separator(text)
        elif self.strategy == ChunkingStrategy.AUTO:
            strategy = self._detect_strategy(text)
            if strategy == ChunkingStrategy.SEPARATOR:
                chunks = self._chunk_by_separator(text)
            else:
                chunks = self._chunk_by_character(text)
        else:
            chunks = self._chunk_by_character(text)

        metadata = metadata or {}
        for chunk in chunks:
            chunk.metadata = metadata.copy()
        return chunks

    async def chunk_text_async(
        self,
        text: str,
        metadata: dict | None = None,
    ) -> list[Chunk]:
        """Split text into chunks, supporting awaitable embedding services.

        Identical to :meth:`chunk_text` except SEMANTIC mode awaits
        ``embedding_service.encode(...)``. Non-semantic strategies delegate to
        the sync path.
        """
        if not text:
            return []
        if self.strategy != ChunkingStrategy.SEMANTIC:
            return self.chunk_text(text, metadata)

        sentences = [s.strip() for s in _SENTENCE_SPLIT.split(text) if s.strip()]
        if not sentences:
            sentences = [text.strip()]
        if len(sentences) == 1 and len(sentences[0]) > self.chunk_size:
            chunks = self._chunk_by_character(text)
        else:
            if self._embedding_service is None:
                logger.warning(
                    "Semantic chunking requires an embedding_service instance; falling back to separator strategy."
                )
                chunks = self._chunk_by_separator(text)
            else:
                try:
                    embeddings = await self._embedding_service.encode(sentences)
                except Exception:
                    logger.warning("Semantic chunking: embedding failed, falling back to separator mode", exc_info=True)
                    chunks = self._chunk_by_separator(text)
                else:
                    chunks = self._pack_semantic(sentences, [list(r.embedding) for r in embeddings])

        metadata = metadata or {}
        for chunk in chunks:
            chunk.metadata = metadata.copy()
        return chunks

    def chunk_documents(
        self,
        documents: list[dict],
    ) -> list[Chunk]:
        """Chunk multiple documents.

        Args:
            documents: List of dicts with 'text' and optional 'metadata'.

        Returns:
            List of all chunks from all documents.
        """
        all_chunks = []
        for doc in documents:
            chunks = self.chunk_text(
                text=doc["text"],
                metadata=doc.get("metadata", {}),
            )
            all_chunks.extend(chunks)
        return all_chunks

    # ------------------------------------------------------------------
    # character strategy (historical behavior, kept verbatim)
    # ------------------------------------------------------------------

    def _chunk_by_character(self, text: str) -> list[Chunk]:
        chunks: list[Chunk] = []
        start = 0
        index = 0

        while start < len(text):
            end = min(start + self.chunk_size, len(text))

            if end < len(text):
                last_period = text.rfind(".", start, end)
                last_newline = text.rfind("\n", start, end)
                break_point = max(last_period, last_newline)

                if break_point > start + self.chunk_size // 2:
                    end = break_point + 1

            chunk_text = text[start:end].strip()
            if chunk_text:
                chunks.append(
                    Chunk(
                        content=chunk_text,
                        index=index,
                        start_char=start,
                        end_char=end,
                    )
                )
                index += 1

            # Overlap must never move `start` backwards — when chunk_overlap
            # >= chunk_size (e.g. small chunks inside separator fallback),
            # `end - overlap` would loop forever.
            start = max(end - self.chunk_overlap, start + 1) if end < len(text) else end

        return chunks

    # ------------------------------------------------------------------
    # separator strategy
    # ------------------------------------------------------------------

    def _split_by_separators(self, text: str) -> list[str]:
        """Split text into fragments on the configured separators."""
        fragments = [text]
        for separator in self.separators:
            next_fragments: list[str] = []
            for fragment in fragments:
                next_fragments.extend(part for part in fragment.split(separator) if part.strip())
            fragments = next_fragments
        return [fragment.strip() for fragment in fragments if fragment.strip()]

    def _chunk_by_separator(self, text: str) -> list[Chunk]:
        fragments = self._split_by_separators(text)
        chunks: list[Chunk] = []
        index = 0
        current_parts: list[str] = []
        current_len = 0

        def _flush() -> None:
            nonlocal index, current_parts, current_len
            content = "\n\n".join(current_parts).strip()
            if content:
                chunks.append(Chunk(content=content, index=index, start_char=0, end_char=0))
                index += 1
            current_parts = []
            current_len = 0

        for fragment in fragments:
            # Oversized fragment: fall back to character splitting inside it.
            if len(fragment) > self.chunk_size:
                if current_parts:
                    _flush()
                for piece in self._chunk_by_character(fragment):
                    chunks.append(Chunk(content=piece.content, index=index, start_char=0, end_char=0))
                    index += 1
                continue
            extra = len(fragment) + (2 if current_parts else 0)
            if current_parts and current_len + extra > self.chunk_size:
                _flush()
            current_parts.append(fragment)
            current_len += extra

        _flush()
        return chunks

    # ------------------------------------------------------------------
    # auto strategy
    # ------------------------------------------------------------------

    def _detect_strategy(self, text: str) -> ChunkingStrategy:
        """Choose separator mode for visibly structured text, else character."""
        if text.count("\n\n") >= 2 or _MARKDOWN_HEADER.search(text):
            return ChunkingStrategy.SEPARATOR
        return ChunkingStrategy.CHARACTER

    # ------------------------------------------------------------------
    # semantic strategy
    # ------------------------------------------------------------------

    def _chunk_semantic(self, text: str) -> list[Chunk]:
        """Sync semantic chunking: split into sentences, embed, pack.

        Requires an explicitly injected ``embedding_service`` with a **sync**
        ``encode(texts) -> list[EmbeddingResult]``; constructing a
        model-loading service as a side effect of chunking would be surprising.
        Without one, or when encode returns an awaitable, this falls back to
        separator mode with guidance — use :meth:`chunk_text_async` for
        awaitable services like ``EmbeddingService``.
        """
        if self._embedding_service is None:
            logger.warning(
                "Semantic chunking requires an embedding_service instance; falling back to separator strategy."
            )
            return self._chunk_by_separator(text)

        sentences, oversized = self._split_sentences(text)
        if oversized:
            return self._chunk_by_character(text)

        try:
            embeddings = self._embedding_service.encode(sentences)
        except Exception:
            logger.warning("Semantic chunking: embedding failed, falling back to separator mode", exc_info=True)
            return self._chunk_by_separator(text)
        if inspect.isawaitable(embeddings):
            raise TypeError(
                "embedding_service.encode returned a coroutine; "
                "use `await chunker.chunk_text_async(text, metadata)` for async services."
            )
        return self._pack_semantic(sentences, [list(result.embedding) for result in embeddings])

    def _split_sentences(self, text: str) -> tuple[list[str], bool]:
        """Split into sentences; ``oversized`` flags a single over-limit one."""
        sentences = [s.strip() for s in _SENTENCE_SPLIT.split(text) if s.strip()]
        if not sentences:
            sentences = [text.strip()]
        # A single sentence longer than chunk_size degrades to character mode.
        return sentences, len(sentences) == 1 and len(sentences[0]) > self.chunk_size

    def _pack_semantic(self, sentences: list[str], vectors: list[list[float]]) -> list[Chunk]:
        """Pack sentences into chunks, cutting at similarity drops or size cap."""
        chunks: list[Chunk] = []
        current: list[str] = [sentences[0]]
        current_len = len(sentences[0])

        for position in range(1, len(sentences)):
            sentence = sentences[position]
            similarity = _cosine_similarity(vectors[position - 1], vectors[position])
            if current_len + len(sentence) + 1 > self.chunk_size or similarity < self.similarity_threshold:
                chunks.append(Chunk(content=" ".join(current), index=len(chunks), start_char=0, end_char=0))
                current = [sentence]
                current_len = len(sentence)
            else:
                current.append(sentence)
                current_len += len(sentence) + 1
        if current:
            chunks.append(Chunk(content=" ".join(current), index=len(chunks), start_char=0, end_char=0))
        return chunks


text_chunker = TextChunker()
