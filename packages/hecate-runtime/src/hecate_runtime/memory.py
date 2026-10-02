"""Kernel-side memory gateway (step5b).

The kernel consumes task-memory/consolidation capabilities through the
provider objects the platform's composition root resolves — but it must not
import the platform composition itself. This module is the seam:

- The platform installs a **provider source** (a zero-argument callable that
  resolves the active memory provider, typically
  ``hecate.core.composition.memory_provider.resolve_memory_provider``).
- Kernel modules import ``CAP_*`` constants, ``resolve_memory_provider`` and
  ``provider_supports`` from *here* with identical call shapes, so the
  capability-check semantics stay in kernel hands.
- The ORM-backed episode lookup and the DB-backed flush-policy resolution
  are injected as callables (``install_episode_lookup`` /
  ``install_memory_policy_resolver``); without an installation the kernel
  treats the corresponding capability as unavailable.

Everything here degrades quietly: no installed source or resolver means the
feature is off, never an ImportError.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)

# Capability names, mirroring ``hecate.core.composition.memory_provider`` so
# platform provider objects and kernel checks agree on the vocabulary.
CAP_TASK_MEMORY = "task_memory"
CAP_PREFETCH = "prefetch"
CAP_END_EPISODE = "end_episode"
CAP_ESCALATE_FAILURE = "escalate_failure"

# Providers without a ``capabilities()`` method are treated as search-only
# (pre-tiering backward compatibility), so none of the kernel's opt-in
# capabilities match.
_SEARCH_ONLY: frozenset[str] = frozenset({"search"})

_provider_source: Callable[[], Any] | None = None
_episode_lookup: Callable[[uuid.UUID, uuid.UUID, uuid.UUID], Awaitable[uuid.UUID | None]] | None = None
_policy_resolver: Callable[[uuid.UUID, uuid.UUID], Awaitable[bool]] | None = None


def install_memory_provider_source(source: Callable[[], Any] | None) -> None:
    """Install the callable that resolves the active memory provider.

    Called by the platform composition root (or a test); ``None`` uninstalls.
    The source is invoked lazily on every kernel resolution so provider
    switches remain dynamic, exactly like the platform-side resolver.
    """
    global _provider_source
    _provider_source = source


def resolve_memory_provider() -> Any | None:
    """Resolve the active memory provider, or ``None`` when none is installed."""
    if _provider_source is None:
        return None
    return _provider_source()


def provider_supports(provider: Any, capability: str) -> bool:
    """Return whether ``provider`` declares ``capability``.

    Mirrors the platform semantics: a provider without ``capabilities()`` is
    search-only; a raising ``capabilities()`` degrades to search-only with a
    warning — never blocks the chat path.
    """
    caps_fn = getattr(provider, "capabilities", None)
    if caps_fn is None:
        return capability in _SEARCH_ONLY
    try:
        caps = frozenset(caps_fn())
    except Exception:  # pragma: no cover — degrade, never block
        logger.warning("provider capabilities() raised; degrading to search-only", exc_info=True)
        return capability in _SEARCH_ONLY
    return capability in caps


def install_episode_lookup(
    lookup: Callable[[uuid.UUID, uuid.UUID, uuid.UUID], Awaitable[uuid.UUID | None]] | None,
) -> None:
    """Install the open-episode lookup (platform DB adapter), or ``None``."""
    global _episode_lookup
    _episode_lookup = lookup


async def find_open_episode(
    workspace_id: uuid.UUID,
    agent_id: uuid.UUID,
    session_id: uuid.UUID,
) -> uuid.UUID | None:
    """Find the open episode for a session, or ``None`` when unavailable."""
    if _episode_lookup is None:
        return None
    return await _episode_lookup(workspace_id, agent_id, session_id)


def install_memory_policy_resolver(
    resolver: Callable[[uuid.UUID, uuid.UUID], Awaitable[bool]] | None,
) -> None:
    """Install the flush-policy resolver (platform DB adapter), or ``None``.

    The resolver answers "is memory flush enabled for this workspace/agent
    scope", wrapping the platform's DB-backed policy query.
    """
    global _policy_resolver
    _policy_resolver = resolver


async def memory_flush_policy_enabled(workspace_id: uuid.UUID, agent_id: uuid.UUID) -> bool:
    """Return the scope's flush policy, ``False`` when no resolver is installed."""
    if _policy_resolver is None:
        return False
    return await _policy_resolver(workspace_id, agent_id)
