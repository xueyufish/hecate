"""Embedding service for text vectorization.

Provides a unified interface for generating text embeddings.
Uses FlagEmbedding BGE-M3 by default, with support for dense and
sparse vectors for hybrid search.
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

    @property
    def is_mock(self) -> bool:
        """True when the real model is unavailable and mocks are served.

        Consumers that need semantic fidelity (e.g. consolidation similarity
        scoring) check this and degrade instead of trusting mock vectors.
        """
        if self._using_api_provider():
            return False
        return self._get_model() == "mock"

    def _using_api_provider(self) -> bool:
        """True when an OpenAI-compatible embeddings endpoint is configured."""
        from hecate.core.config import settings

        return (
            settings.EMBEDDING_PROVIDER == "openai_compatible"
            and bool(settings.EMBEDDING_API_BASE)
            and bool(settings.EMBEDDING_MODEL)
        )

    def _get_model(self):
        """Lazy load the embedding model."""
        if self._model is None:
            try:
                from FlagEmbedding import BGEM3FlagModel

                self._model = BGEM3FlagModel(self.model_name, use_fp16=False)
                logger.info(f"Loaded embedding model: {self.model_name}")
            except ImportError:
                logger.warning("FlagEmbedding not installed. Using mock embeddings.")
                self._model = "mock"
        return self._model

    async def _encode_via_api(self, texts: list[str]) -> list[EmbeddingResult]:
        """Embed via an OpenAI-compatible /embeddings endpoint.

        Direct HTTP rather than litellm: provider-side param validation
        (e.g. litellm rejecting `dimensions` for model names it maps to
        OpenAI's own models) breaks third-party endpoints that do support
        the parameter, such as Zhipu's embedding-3.
        """
        import httpx

        from hecate.core.config import settings

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    f"{settings.EMBEDDING_API_BASE.rstrip('/')}/embeddings",
                    headers={"Authorization": f"Bearer {settings.EMBEDDING_API_KEY}"},
                    json={
                        "model": settings.EMBEDDING_MODEL,
                        "input": texts,
                        "dimensions": settings.EMBEDDING_DIMENSIONS,
                    },
                )
                response.raise_for_status()
            items = sorted(response.json()["data"], key=lambda d: d.get("index", 0))
            return [EmbeddingResult(dense=[float(x) for x in item["embedding"]], sparse={}) for item in items]
        except Exception as e:
            logger.error(f"API embedding call failed: {e}")
            return [self._mock_embedding(text) for text in texts]

    async def encode(self, texts: list[str]) -> list[EmbeddingResult]:
        """Generate embeddings for a list of texts.

        Args:
            texts: List of texts to embed.

        Returns:
            List of EmbeddingResult with dense and sparse vectors.
        """
        if self._using_api_provider():
            return await self._encode_via_api(texts)

        model = self._get_model()

        if model == "mock":
            return [self._mock_embedding(text) for text in texts]

        try:
            output = model.encode(
                texts,
                batch_size=32,
                max_length=512,
                return_dense=True,
                return_sparse=True,
                return_colbert_vecs=False,
            )
            dense_vecs = output["dense_vecs"]
            sparse_weights = output["lexical_weights"]

            results = []
            for dense, sparse in zip(dense_vecs, sparse_weights, strict=True):
                dense_list = dense.tolist() if hasattr(dense, "tolist") else list(dense)
                sparse_dict = {int(k): float(v) for k, v in sparse.items()}
                results.append(EmbeddingResult(dense=dense_list, sparse=sparse_dict))
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

        hash_bytes = hashlib.md5(text.encode()).digest()  # noqa: S324
        dense = [b / 255.0 for b in hash_bytes]
        dense = dense + [0.0] * (1024 - len(dense))

        sparse: dict[int, float] = {}
        words = text.lower().split()
        for _i, word in enumerate(words):
            token_id = hash(word) % 10000
            sparse[token_id] = sparse.get(token_id, 0.0) + 1.0

        return EmbeddingResult(dense=dense[:1024], sparse=sparse)


embedding_service = EmbeddingService()
