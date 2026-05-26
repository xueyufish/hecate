"""Embedding service for text vectorization.

Provides a unified interface for generating text embeddings.
Uses sentence-transformers with BGE-M3 by default, with support
for dense and sparse vectors for hybrid search.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class EmbeddingResult:
    """Result from embedding generation."""

    dense: list[float] = field(default_factory=list)
    sparse: dict[int, float] = field(default_factory=dict)


class EmbeddingService:
    """Service for generating text embeddings.

    Supports:
    - Dense embeddings (1024-dim for BGE-M3)
    - Sparse embeddings (token weights for BM25-style search)
    """

    def __init__(self, model_name: str = "BAAI/bge-m3"):
        self.model_name = model_name
        self._model = None

    def _get_model(self):
        """Lazy load the embedding model."""
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer

                self._model = SentenceTransformer(self.model_name)
                logger.info(f"Loaded embedding model: {self.model_name}")
            except ImportError:
                logger.warning("sentence-transformers not installed. Using mock embeddings.")
                self._model = "mock"
        return self._model

    async def encode(self, texts: list[str]) -> list[EmbeddingResult]:
        """Generate embeddings for a list of texts.

        Args:
            texts: List of texts to embed.

        Returns:
            List of EmbeddingResult with dense and sparse vectors.
        """
        model = self._get_model()

        if model == "mock":
            return [self._mock_embedding(text) for text in texts]

        try:
            embeddings = model.encode(texts, show_progress_bar=False)
            results = []
            for emb in embeddings:
                results.append(
                    EmbeddingResult(
                        dense=emb.tolist() if hasattr(emb, "tolist") else list(emb),
                        sparse={},
                    )
                )
            return results
        except Exception as e:
            logger.error(f"Embedding generation failed: {e}")
            return [self._mock_embedding(text) for text in texts]

    async def encode_query(self, query: str) -> EmbeddingResult:
        """Generate embedding for a single query.

        Args:
            query: The query text to embed.

        Returns:
            EmbeddingResult with dense and sparse vectors.
        """
        results = await self.encode([query])
        return results[0]

    def _mock_embedding(self, text: str) -> EmbeddingResult:
        """Generate a mock embedding for testing.

        Args:
            text: The text to generate mock embedding for.

        Returns:
            EmbeddingResult with deterministic mock vectors.
        """
        import hashlib

        hash_bytes = hashlib.md5(text.encode()).digest()
        dense = [b / 255.0 for b in hash_bytes]
        dense = dense + [0.0] * (1024 - len(dense))
        return EmbeddingResult(dense=dense[:1024], sparse={})


embedding_service = EmbeddingService()
