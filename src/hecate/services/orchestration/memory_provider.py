"""Memory provider resolver — discovers ``hecate.memory_providers`` entry points.

A third-party memory backend (mem0, zep, letta, ...) plugs in by registering a
zero-arg ``provider`` factory under the ``hecate.memory_providers`` entry-point
group. The core package selects the active backend via
``settings.MEMORY_PROVIDER`` (env: ``HECATE_MEMORY_PROVIDER``), defaulting to
``"builtin"`` — the in-process backend registered by the shipped
``hecate-memory`` package.

Selection is single-valued: unlike ``auth/resolver.py`` and ``vault/resolver.py``
(which iterate every installed entry point to build a fallback chain),
memory has one active backend at a time — retrieval is global, not
per-request chained. A misconfigured or unknown name degrades to ``None`` and
the caller treats it as an empty result rather than raising, so a missing wheel
never crashes the chat path.

The contract is duck-typed via ``MemoryProvider`` / ``SearchHitLike`` Protocols;
no ABC is required on the third-party side. See
``docs/integrations/memory/third-party-memory.md`` for the five-step integration.
"""

from __future__ import annotations

import logging
from importlib.metadata import entry_points
from typing import Any, Protocol

from hecate.core.config import settings

logger = logging.getLogger(__name__)


class SearchHitLike(Protocol):
    """Minimum shape returned by a memory provider's ``search`` method.

    Implementations are free to use a richer concrete type (e.g.
    ``hecate_memory.rag.searcher.HybridSearchResult``) — only these three
    attributes are consumed by the core adapter.
    """

    content: str
    score: float
    metadata: dict[str, Any]


class MemoryProvider(Protocol):
    """Contract for memory backends plugged in via ``hecate.memory_providers``."""

    async def search(
        self,
        collection_name: str,
        query: str,
        *,
        limit: int = 10,
        mode: str = "hybrid",
        workspace_id: str | None = None,
    ) -> list[SearchHitLike]: ...


_module_cache: MemoryProvider | None = None
_resolved: bool = False


def resolve_memory_provider() -> MemoryProvider | None:
    """Return the memory provider selected by ``settings.MEMORY_PROVIDER``.

    Discovers entries in the ``hecate.memory_providers`` group via
    ``importlib.metadata``, picks the one whose ``name`` matches
    ``settings.MEMORY_PROVIDER`` (default ``"builtin"``), and invokes its
    zero-arg factory. The result is cached module-wide — first call decides
    for the process lifetime; use ``reset_memory_provider_cache()`` in tests.

    Returns ``None`` if the named entry is missing, the factory raises, or the
    group itself cannot be scanned. Callers must treat ``None`` as
    "no memory backend available" and degrade gracefully (e.g. return ``[]``).
    """
    global _module_cache, _resolved
    if _resolved:
        return _module_cache
    _resolved = True

    name = settings.MEMORY_PROVIDER
    try:
        eps = entry_points(group="hecate.memory_providers")
    except Exception as e:  # pragma: no cover — defensive, metadata DB corruption
        logger.warning("Memory provider entry-point scan failed: %s", e)
        _module_cache = None
        return None

    for ep in eps:
        if ep.name != name:
            continue
        try:
            _module_cache = ep.load()()
        except Exception:
            logger.exception("Memory provider %r factory raised; knowledge_query will return []", name)
            _module_cache = None
        break
    else:
        logger.warning(
            "Memory provider %r not found in hecate.memory_providers group "
            "(available: %s); knowledge_query will return []",
            name,
            [ep.name for ep in eps],
        )
        _module_cache = None

    return _module_cache


def reset_memory_provider_cache() -> None:
    """Clear the resolver cache. Test-only."""
    global _module_cache, _resolved
    _module_cache = None
    _resolved = False


__all__ = ["MemoryProvider", "SearchHitLike", "reset_memory_provider_cache", "resolve_memory_provider"]
