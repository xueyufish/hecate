"""Lazy cross-package helper for embedding access.

Kept as a private module to make the cross-package import direction
explicit and to defer the ``hecate_memory`` import until first use
(matching the design's ``EmbeddingDedupeFilter`` graceful-degradation
contract).
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_embedding_service: Any | None = None


def get_embedding_service() -> Any | None:
    """Return the shared ``EmbeddingService`` singleton, or None if
    ``hecate_memory`` is not importable (e.g. the embedding extra is
    not installed in this deployment)."""
    global _embedding_service
    if _embedding_service is not None:
        return _embedding_service
    try:
        from hecate_memory.rag.embedding import embedding_service

        _embedding_service = embedding_service
        return _embedding_service
    except ImportError:
        logger.info("hecate_memory not importable — embedding dedupe will fall back to n-gram")
        return None


async def encode_texts(texts: list[str], service: Any | None = None) -> list[list[float] | None]:
    """Encode a list of strings via the embedding service. Returns one
    dense vector per input; ``None`` entries signal encode failures
    (caller decides whether to pass-through)."""
    svc = service or get_embedding_service()
    if svc is None:
        return [None] * len(texts)
    try:
        results = await svc.encode(texts)
    except Exception as exc:
        logger.warning("Embedding encode raised: %s", exc)
        return [None] * len(texts)
    out: list[list[float] | None] = []
    for r in results:
        dense = getattr(r, "dense", None)
        if not dense:
            out.append(None)
        else:
            out.append(list(dense))
    return out
