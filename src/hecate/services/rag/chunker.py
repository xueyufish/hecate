"""Text chunker for splitting documents into smaller pieces.

Splits text into chunks suitable for embedding and retrieval.
Supports fixed-size chunking with overlap.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class Chunk:
    """A text chunk with metadata."""

    content: str
    index: int
    start_char: int
    end_char: int
    metadata: dict


class TextChunker:
    """Split text into chunks for embedding.

    Supports:
    - Fixed-size chunking (by character count)
    - Overlap between chunks for context preservation
    - Metadata tracking for each chunk
    """

    def __init__(
        self,
        chunk_size: int = 1000,
        chunk_overlap: int = 200,
    ):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    def chunk_text(
        self,
        text: str,
        metadata: dict | None = None,
    ) -> list[Chunk]:
        """Split text into chunks.

        Args:
            text: The text to split.
            metadata: Optional metadata to attach to each chunk.

        Returns:
            List of Chunk objects.
        """
        if not text:
            return []

        metadata = metadata or {}
        chunks = []
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
                        metadata=metadata.copy(),
                    )
                )
                index += 1

            start = end - self.chunk_overlap if end < len(text) else end

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


text_chunker = TextChunker()
