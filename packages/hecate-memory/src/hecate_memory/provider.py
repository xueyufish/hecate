"""Memory provider entry-point factory.

Registered under the ``hecate.memory_providers`` group as ``builtin``. The core
package discovers this entry point via ``importlib.metadata`` and, when
``HECATE_MEMORY_PROVIDER`` is unset (or set to ``"builtin"``), uses the returned
object as the default memory backend.

Third-party memory packages (e.g. ``hecate-memory-mem0``) should declare their
own entry under the same group with a distinct name.

The factory returns the ``BuiltinMemoryProvider`` singleton, which implements
the full tiered ``MemoryProvider`` contract (search / fact CRUD / lifecycle
hooks) — see ``hecate_memory.memory.provider_impl``. ``search`` delegates to
the ``KnowledgeBaseService`` singleton, preserving the pre-existing
knowledge-query behavior.

The returned object is consumed by the core package through the
``MemoryProvider`` Protocol in ``hecate.core.composition.memory_provider``.
"""

from __future__ import annotations

from hecate_memory.memory.provider_impl import BuiltinMemoryProvider

_provider: BuiltinMemoryProvider | None = None


def provider() -> BuiltinMemoryProvider:
    """Zero-arg factory returning the builtin memory provider singleton.

    Called by the core package's resolver when ``HECATE_MEMORY_PROVIDER``
    selects this entry point. The singleton avoids re-resolving the vector
    store per request; the resolver caches the result module-wide.
    """
    global _provider
    if _provider is None:
        _provider = BuiltinMemoryProvider()
    return _provider


__all__ = ["provider"]
