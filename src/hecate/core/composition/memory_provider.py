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
no ABC is required on the third-party side. The contract is tiered: providers
declare the capabilities they implement via ``capabilities()`` and callers
route on that declaration (``provider_supports``); a provider without
``capabilities()`` is treated as search-only for backward compatibility.
Structured result types (``MemoryFactHit``, ``RecallPage``, ``MemoryWriteResult``,
``PrefetchEntry``) are part of the contract — implementations return them (or
attribute-compatible equivalents). See
``docs/integrations/memory/third-party-memory.md`` for the integration guide.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from importlib.metadata import entry_points
from typing import Any, Protocol

from hecate.core.config import settings

logger = logging.getLogger(__name__)

# Capability names of the tiered contract. tier-1: CAP_SEARCH. tier-2:
# CAP_SEARCH_MEMORIES (L3/L4 fact retrieval) + CAP_SEARCH_RECALL (transcript
# recall). tier-3: CAP_ADD_MEMORY / CAP_UPDATE_MEMORY / CAP_FORGET_MEMORY.
# Lifecycle: CAP_PREFETCH (pre-call injection) + CAP_SYNC_TURN (post-turn
# write-back). 4.21 / 4.23 add tier-4 (CAP_TASK_MEMORY), tier-5
# (CAP_CROSS_THREAD), and two new lifecycle hooks
# (CAP_END_EPISODE / CAP_ESCALATE_FAILURE) — all four are *opt-in* and
# absent from legacy providers' capability sets.
CAP_SEARCH = "search"
CAP_SEARCH_MEMORIES = "search_memories"
CAP_SEARCH_RECALL = "search_recall"
CAP_ADD_MEMORY = "add_memory"
CAP_UPDATE_MEMORY = "update_memory"
CAP_FORGET_MEMORY = "forget_memory"
CAP_PREFETCH = "prefetch"
CAP_SYNC_TURN = "sync_turn"
CAP_TASK_MEMORY = "task_memory"
CAP_CROSS_THREAD = "cross_thread"
CAP_END_EPISODE = "end_episode"
CAP_ESCALATE_FAILURE = "escalate_failure"

# Providers predating the capability declaration implement search only.
# The four 4.21 / 4.23 additions are absent: a pre-change provider
# therefore returns False for ``provider_supports(p, CAP_TASK_MEMORY)``
# etc. and the caller sees a structured error rather than a crash.
_DEFAULT_CAPABILITIES = frozenset({CAP_SEARCH})

# Valid tier literals for ``memory_search(tier=...)`` and the new
# ``search_cross_thread`` / ``search_task_memory`` capability checks.
# tier_2 is the default for tier-2 retrieval (L3 + L4 facts);
# tier_4 / tier_5 route to the new memories / work context / cross-thread
# surfaces defined in this change.
TIER_1 = "tier_1"
TIER_2 = "tier_2"
TIER_3 = "tier_3"
TIER_4 = "tier_4"
TIER_5 = "tier_5"
VALID_TIERS: frozenset[str] = frozenset({TIER_1, TIER_2, TIER_3, TIER_4, TIER_5})

# Structured error code returned by callers when a tier is requested
# against a provider that does not declare the corresponding capability.
# Routes use this string verbatim so the chat path can map to a clean
# user-facing message rather than a stack trace.
TIER_UNSUPPORTED_ERROR = "tier_unsupported"


class SearchHitLike(Protocol):
    """Minimum shape returned by a memory provider's ``search`` method.

    Implementations are free to use a richer concrete type (e.g.
    ``hecate_memory.rag.searcher.HybridSearchResult``) — only these three
    attributes are consumed by the core adapter.
    """

    content: str
    score: float
    metadata: dict[str, Any]


@dataclass(frozen=True)
class MemoryFactHit:
    """One fact-memory hit merged across the L3/L4 stores.

    ``score`` has one documented meaning: the fused ranking score. When
    ``MEMORY_FUSION_BIAS_ENABLED`` is on (default off), it is
    ``normalized_relevance × decay_mult × importance_mult`` clamped to the
    configured product floor. When the bias is off, the multipliers are
    inert (1.0) and ``score`` equals the normalized relevance across the
    cross-layer union. Per-signal decomposition lives under
    ``metadata["breakdown"]`` (``relevance``, ``decay_mult``,
    ``importance_mult``) — a single scale is exposed via ``score``, the
    components are exposed for observability.
    """

    memory_id: uuid.UUID
    source_layer: str  # "user_memory" (L3) | "knowledge_memory" (L4)
    content: str
    score: float
    revision: int = 1
    tags: list[str] = field(default_factory=list)
    importance: float = 0.5
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RecallHit:
    """One recalled conversation message (transcript-level)."""

    recall_id: uuid.UUID
    session_id: uuid.UUID
    conversation_id: uuid.UUID | None
    role: str
    content: str
    timestamp: datetime
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RecallPage:
    """Cursor-paged recall search result."""

    hits: list[RecallHit]
    next_cursor: str | None = None
    # True when the page is empty or every hit is below the relevance signal
    # threshold — drives the retrieval escalation hint.
    low_signal: bool = False


@dataclass(frozen=True)
class MemoryWriteResult:
    """Structured outcome of a tier-3 fact write (add/update/forget)."""

    ok: bool
    memory_id: uuid.UUID | None = None
    revision: int | None = None
    # Structured error code (e.g. "not_found", "revision_conflict",
    # "duplicate") — None on success.
    error: str | None = None
    # Extra context for error rendering (e.g. {"current_revision": 4}).
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PrefetchEntry:
    """One memory entry selected for pre-call context injection."""

    content: str
    source: str  # provider-specific provenance label
    score: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EpisodeWriteResult:
    """Structured outcome of an episode write (4.21).

    Returned by ``add_episode`` / ``close_episode`` / ``record_tool_event``
    so callers can branch on a structured ``error`` code instead of an
    exception. ``error`` is one of:

    - ``"none"`` — feature flag off, write was a silent no-op.
    - ``"forbidden"`` — provider declares ``CAP_TASK_MEMORY`` but the
      feature flag is disabled at the call site (shouldn't happen via the
      builtin provider — the provider itself returns the no-op result).
    """

    ok: bool
    episode_id: uuid.UUID | None = None
    closed: bool = False
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TaskMemoryHit:
    """One hit from a tier-4 task-memory retrieval (4.21).

    Wraps either an approved reflection or a work-context-graph node;
    ``hit_kind`` distinguishes the two so consumers can render the
    provenance label without parsing ``metadata``.
    """

    hit_id: uuid.UUID
    hit_kind: str  # "reflection" | "work_context_node"
    content: str
    score: float
    confidence: float = 0.5
    use_cases: list[str] = field(default_factory=list)
    title: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CrossThreadHit:
    """One hit from a tier-5 cross-thread retrieval (4.23).

    ``source_scope`` is the namespace provenance label
    (``actor_scoped`` / ``team_scoped`` / ``workspace_shared``) — kept
    explicit on the wire so retrieval consumers don't have to parse the
    metadata envelope.
    """

    memory_id: uuid.UUID
    source_layer: str  # "user_memory" (L3) | "knowledge_memory" (L4)
    source_scope: str  # "actor_scoped" | "team_scoped" | "workspace_shared"
    content: str
    score: float
    revision: int = 1
    tags: list[str] = field(default_factory=list)
    importance: float = 0.5
    metadata: dict[str, Any] = field(default_factory=dict)


class MemoryProvider(Protocol):
    """Contract for memory backends plugged in via ``hecate.memory_providers``.

    Implementations declare the subset they support via ``capabilities()``;
    callers MUST route through ``provider_supports`` instead of probing with
    calls. Methods outside the declared set are never invoked.
    """

    async def search(
        self,
        collection_name: str,
        query: str,
        *,
        limit: int = 10,
        mode: str = "hybrid",
        workspace_id: str | None = None,
    ) -> list[SearchHitLike]: ...

    async def search_memories(
        self,
        *,
        query: str,
        workspace_id: uuid.UUID,
        agent_id: uuid.UUID | None = None,
        user_id: uuid.UUID | None = None,
        top_k: int = 5,
        tags: list[str] | None = None,
    ) -> list[MemoryFactHit]: ...

    async def search_recall(
        self,
        *,
        query: str,
        workspace_id: uuid.UUID,
        agent_id: uuid.UUID,
        limit: int = 5,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        roles: list[str] | None = None,
        cursor: str | None = None,
        exclude_session_ids: list[uuid.UUID] | None = None,
    ) -> RecallPage: ...

    async def add_memory(
        self,
        *,
        content: str,
        workspace_id: uuid.UUID,
        agent_id: uuid.UUID | None = None,
        user_id: uuid.UUID | None = None,
        tags: list[str] | None = None,
        importance: float = 0.5,
    ) -> MemoryWriteResult: ...

    async def update_memory(
        self,
        *,
        memory_id: uuid.UUID,
        workspace_id: uuid.UUID,
        patch: dict[str, Any],
        expected_revision: int | None = None,
        agent_id: uuid.UUID | None = None,
    ) -> MemoryWriteResult: ...

    async def forget_memory(
        self,
        *,
        memory_id: uuid.UUID,
        workspace_id: uuid.UUID,
        agent_id: uuid.UUID | None = None,
        expected_revision: int | None = None,
    ) -> MemoryWriteResult: ...

    async def prefetch(
        self,
        *,
        query_text: str,
        workspace_id: uuid.UUID,
        agent_id: uuid.UUID | None = None,
        user_id: uuid.UUID | None = None,
        max_entries: int = 5,
        max_tokens: int = 500,
    ) -> list[PrefetchEntry]: ...

    async def sync_turn(
        self,
        *,
        workspace_id: uuid.UUID,
        agent_id: uuid.UUID,
        session_id: uuid.UUID,
        messages: list[dict[str, Any]],
    ) -> None: ...

    # -- tier-4: task memory (4.21) --------------------------------------------
    #
    # These methods are gated on ``CAP_TASK_MEMORY``. Implementations that
    # don't declare the capability MUST return ``EpisodeWriteResult`` /
    # an empty list at the call site — the caller checks ``provider_supports``
    # first and refuses to dispatch, but the stubs remain on the Protocol
    # so duck-typed legacy providers stay discoverable.
    async def add_episode(
        self,
        *,
        workspace_id: uuid.UUID,
        agent_id: uuid.UUID,
        actor_id: uuid.UUID | None = None,
        session_id: uuid.UUID | None = None,
        task_type: str = "",
        situation: str | None = None,
        intent: str | None = None,
    ) -> EpisodeWriteResult: ...

    async def record_tool_event(
        self,
        *,
        workspace_id: uuid.UUID,
        agent_id: uuid.UUID,
        session_id: uuid.UUID,
        tool_name: str,
        args: dict[str, Any],
        result_ref: str | None = None,
        ts: datetime | None = None,
    ) -> EpisodeWriteResult: ...

    async def close_episode(
        self,
        *,
        workspace_id: uuid.UUID,
        agent_id: uuid.UUID,
        episode_id: uuid.UUID,
    ) -> EpisodeWriteResult: ...

    async def search_task_memory(
        self,
        *,
        query: str,
        workspace_id: uuid.UUID,
        agent_id: uuid.UUID | None = None,
        actor_id: uuid.UUID | None = None,
        top_k: int = 5,
        task_type: str | None = None,
    ) -> list[TaskMemoryHit]: ...

    # -- tier-5: cross-thread memory (4.23) ------------------------------------

    async def search_cross_thread(
        self,
        *,
        query: str,
        workspace_id: uuid.UUID,
        team_id: uuid.UUID | None = None,
        actor_id: uuid.UUID | None = None,
        top_k: int = 5,
        tags: list[str] | None = None,
    ) -> list[CrossThreadHit]: ...

    # -- lifecycle hooks (4.21) ----------------------------------------------
    #
    # Both hooks are *opt-in*. A provider that does not declare
    # ``CAP_END_EPISODE`` / ``CAP_ESCALATE_FAILURE`` MUST treat callers'
    # invocations as silent no-ops (the composition layer checks
    # ``provider_supports`` first, but the Protocol stubs remain for
    # duck-typed providers that pre-date the capability declaration).

    async def end_episode(
        self,
        *,
        workspace_id: uuid.UUID,
        agent_id: uuid.UUID,
        episode_id: uuid.UUID,
    ) -> None: ...

    async def escalate_failure(
        self,
        *,
        workspace_id: uuid.UUID,
        agent_id: uuid.UUID,
        task_type: str,
        confidence: float,
    ) -> list[TaskMemoryHit]: ...

    def capabilities(self) -> frozenset[str]: ...


def provider_supports(provider: Any, capability: str) -> bool:
    """Return whether ``provider`` declares ``capability``.

    A provider without a ``capabilities()`` method is treated as search-only
    (backward compatibility with pre-tiering implementations). A raising
    ``capabilities()`` degrades to search-only with a warning — never blocks
    the chat path.
    """
    caps_fn = getattr(provider, "capabilities", None)
    if caps_fn is None:
        return capability in _DEFAULT_CAPABILITIES
    try:
        caps = frozenset(caps_fn())
    except Exception:
        logger.warning(
            "Memory provider capabilities() raised; treating as search-only",
            exc_info=True,
        )
        return capability in _DEFAULT_CAPABILITIES
    return capability in caps


# Mapping: tier literal → (capability required on the provider, optional
# human-readable tier name). Used by ``route_search_by_tier`` and the
# ``memory_search(tier=...)`` tool to centralize the check.
_TIER_TO_CAPABILITY: dict[str, str] = {
    TIER_1: CAP_SEARCH,
    TIER_2: CAP_SEARCH_MEMORIES,
    TIER_3: CAP_ADD_MEMORY,  # tier-3 retrieval rides on the same surface
    TIER_4: CAP_TASK_MEMORY,
    TIER_5: CAP_CROSS_THREAD,
}


@dataclass(frozen=True)
class TierRoutingError(Exception):
    """Structured error raised when a tier is requested against an
    incapable provider.

    The caller (memory tools / runtime) catches this and maps it to a
    clean tool-result error code (``TIER_UNSUPPORTED_ERROR``) rather than
    letting an exception bubble into the chat path. The fields are the
    call-site's debugging handle, not user-facing text.
    """

    requested_tier: str
    provider_name: str
    required_capability: str

    def __str__(self) -> str:  # pragma: no cover — formatted for logs only
        return (
            f"memory provider {self.provider_name!r} does not declare "
            f"{self.required_capability!r} (requested tier={self.requested_tier!r})"
        )


def route_search_by_tier(
    provider: Any,
    tier: str,
    *,
    provider_name: str = "<unknown>",
) -> None:
    """Verify ``provider`` declares the capability that ``tier`` requires.

    Raises ``TierRoutingError`` (with structured fields) when the tier is
    unknown or the provider lacks the capability. Returns ``None`` on
    success — the caller proceeds to invoke the tier-specific method.

    Centralizes the check so the tool layer doesn't duplicate the
    capability-vs-tier mapping, and so the error code is consistent
    across every entry point.
    """
    if tier not in VALID_TIERS:
        raise TierRoutingError(
            requested_tier=tier,
            provider_name=provider_name,
            required_capability="<unknown tier>",
        )
    required = _TIER_TO_CAPABILITY[tier]
    if not provider_supports(provider, required):
        raise TierRoutingError(
            requested_tier=tier,
            provider_name=provider_name,
            required_capability=required,
        )


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


__all__ = [
    "CAP_ADD_MEMORY",
    "CAP_FORGET_MEMORY",
    "CAP_PREFETCH",
    "CAP_SEARCH",
    "CAP_SEARCH_MEMORIES",
    "CAP_SEARCH_RECALL",
    "CAP_SYNC_TURN",
    "CAP_UPDATE_MEMORY",
    # 4.21 / 4.23 capability constants
    "CAP_TASK_MEMORY",
    "CAP_CROSS_THREAD",
    "CAP_END_EPISODE",
    "CAP_ESCALATE_FAILURE",
    # Tier + structured-error constants
    "TIER_1",
    "TIER_2",
    "TIER_3",
    "TIER_4",
    "TIER_5",
    "VALID_TIERS",
    "TIER_UNSUPPORTED_ERROR",
    "MemoryFactHit",
    "MemoryProvider",
    "MemoryWriteResult",
    "PrefetchEntry",
    "RecallHit",
    "RecallPage",
    "SearchHitLike",
    # 4.21 / 4.23 result types
    "EpisodeWriteResult",
    "TaskMemoryHit",
    "CrossThreadHit",
    "provider_supports",
    "reset_memory_provider_cache",
    "resolve_memory_provider",
    # Tier-aware routing helper + structured error type
    "route_search_by_tier",
    "TierRoutingError",
    "_TIER_TO_CAPABILITY",
]
