"""Memory provider entry-point factory.

Registered under the ``hecate.memory_providers`` group as ``builtin``. The core
package discovers this entry point via ``importlib.metadata`` and, when
``HECATE_MEMORY_PROVIDER`` is unset (or set to ``"builtin"``), uses the returned
object as the default knowledge-search backend.

Third-party memory packages (e.g. ``hecate-memory-mem0``) should declare their
own entry under the same group with a distinct name.

Contract (duck-typed — no inheritance required):

- ``async def search(collection_name, query, *, limit=10, mode="hybrid",
  workspace_id=None) -> list[SearchHitLike]``
- ``SearchHitLike`` exposes ``content: str``, ``score: float``, ``metadata: dict``.

The returned object is consumed by the core package through the
``MemoryProvider`` Protocol in ``hecate.services.orchestration.memory_provider``.
"""

from __future__ import annotations

from .rag.service import KnowledgeBaseService, knowledge_base_service


def provider() -> KnowledgeBaseService:
    """Zero-arg factory returning the in-process knowledge base service.

    Called by the core package's resolver when ``HECATE_MEMORY_PROVIDER`` selects
    this entry point. Returning the singleton avoids re-instantiating the
    service per request; the resolver caches the result module-wide.
    """
    return knowledge_base_service


__all__ = ["provider"]
