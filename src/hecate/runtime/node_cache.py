"""Node-level result cache with TTL, LRU eviction, and a key-function registry.

Implements the 1.3.21IV ride-along: per-graph-node ``cache`` policies that let
the Pregel engine skip a node's worker when an identical input was recently
served. The cache is advisory only (ADR-030: optimization, never authority) —
a hit and a miss produce identical event-log trajectories, and clearing the
cache never changes any state rebuild.

Pattern reference: ``tools/tool/cache.py`` (5.7 ToolCache). Deliberately a
sibling implementation, not an import — the runtime self-sufficiency guard
forbids module-level cross-domain imports.

Design decisions (openspec/changes/node-cache-policy/design.md):
- In-memory per-replica OrderedDict with LRU eviction and per-entry TTL
  (monotonic clock — immune to wall-clock adjustments)
- Default key = scope namespace + node id + node-type identity hash
  (CONVERSATION nodes fold in their model config so a model upgrade
  invalidates entries naturally) + canonical JSON of the node's input slice
- Key functions are registered by name, mirroring the accumulator reducer
  registry in ``channel.py``; unknown names fail at compile time
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_MAX_ENTRIES = 4096

#: Config keys that participate in the CONVERSATION identity hash. A change
#: to any of them yields a different cache key, so model/parameter upgrades
#: never serve stale outputs.
_CONVERSATION_IDENTITY_KEYS = ("model", "temperature", "top_p", "system_prompt")


class UnknownKeyFuncError(Exception):
    """Raised when a node cache policy references an unregistered key function.

    Mirrors ``channel.UnknownReducerError`` — the compile-time check
    distinguishes "unknown key function" from other validation failures
    without string-matching error messages.
    """

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(
            f"Node cache key function '{name}' is not registered. "
            f"Register it via node_cache.register_node_key_func(name, fn) before compiling the graph. "
            f"Registered: {sorted(_KEY_FUNCS) or '(none)'}"
        )


@dataclass(frozen=True)
class CachePolicy:
    """Parsed ``cache`` block from a node config.

    Attributes:
        ttl: entry time-to-live in seconds (required by the DSL schema,
            positive integer — no silent default).
        key_func: registered key-function name overriding the default key
            derivation, or ``None`` to use the default.
        scope: ``"session"`` (default; key includes the session id) or
            ``"tenant"`` (key includes the tenant id and omits the session
            id — for read-only KB-retrieval style nodes).
    """

    ttl: int
    key_func: str | None = None
    scope: str = "session"

    @classmethod
    def from_config(cls, raw: dict[str, Any]) -> CachePolicy:
        """Build a policy from the raw ``cache`` config block.

        The JSON schema already rejects malformed blocks at parse time and
        the compiler rejects unknown ``key_func`` names; this constructor
        only applies defaults (``scope="session"``) for direct GraphConfig
        construction that bypassed the schema.
        """
        return cls(
            ttl=int(raw["ttl"]),
            key_func=raw.get("key_func"),
            scope=raw.get("scope", "session"),
        )


_KEY_FUNCS: dict[str, Any] = {}


def register_node_key_func(name: str, fn: Any) -> None:
    """Register a named key function for node cache policies.

    Args:
        name: The name referenced by ``cache.key_func`` in a node config.
        fn: Callable ``(node_id, snapshot) -> str`` returning the key material
            for the node's input. The scope namespace and node id are still
            prepended by the engine — isolation is never opt-out.
    """
    _KEY_FUNCS[name] = fn
    logger.debug("Registered node cache key function '%s' → %s", name, fn)


def get_node_key_func(name: str) -> Any:
    """Return the key function registered under ``name``.

    Raises:
        UnknownKeyFuncError: when no key function is registered under ``name``.
    """
    if name not in _KEY_FUNCS:
        raise UnknownKeyFuncError(name)
    return _KEY_FUNCS[name]


def list_node_key_funcs() -> list[str]:
    """Return the names of all registered key functions."""
    return sorted(_KEY_FUNCS)


class InMemoryNodeCache:
    """In-memory node result cache with per-entry TTL and LRU eviction.

    Args:
        max_entries: maximum number of entries; the least-recently-used
            entry is evicted first when the cap is exceeded.
    """

    def __init__(self, max_entries: int = DEFAULT_MAX_ENTRIES) -> None:
        self._entries: OrderedDict[str, tuple[Any, float]] = OrderedDict()
        self._max_entries = max_entries
        self._hits = 0
        self._misses = 0

    def get(self, key: str, ttl: int) -> Any | None:
        """Return the cached value for ``key`` if present and unexpired.

        Expired entries are dropped lazily on access; both expiry and absence
        count as a miss.
        """
        entry = self._entries.get(key)
        if entry is None:
            self._misses += 1
            return None
        value, created_at = entry
        if (time.monotonic() - created_at) >= ttl:
            del self._entries[key]
            self._misses += 1
            return None
        self._entries.move_to_end(key)
        self._hits += 1
        return value

    def set(self, key: str, value: Any) -> None:
        """Store ``value`` under ``key`` (fresh creation timestamp, LRU bump)."""
        self._entries[key] = (value, time.monotonic())
        self._entries.move_to_end(key)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)

    def clear(self) -> int:
        """Drop every entry; returns the number of entries removed."""
        count = len(self._entries)
        self._entries.clear()
        return count

    def stats(self) -> dict[str, int | float]:
        """Return hit/miss/entry statistics (ops-layer hit-rate signal)."""
        total = self._hits + self._misses
        return {
            "hits": self._hits,
            "misses": self._misses,
            "entries": len(self._entries),
            "hit_rate": self._hits / total if total > 0 else 0.0,
        }


def _canonical_hash(payload: Any) -> str:
    """SHA-256 over canonical JSON (sorted keys, ``default=str`` fallback)."""
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def short_key_hash(full_key: str) -> str:
    """Short hash of a full cache key for the NODE_END ``cache_key`` marker.

    The marker must not embed session/tenant ids (they are already carried by
    the event's own columns); a truncated hash is enough to correlate
    hit/miss pairs in ops tooling.
    """
    return hashlib.sha256(full_key.encode()).hexdigest()[:16]


def derive_cache_key(
    policy: CachePolicy,
    node_id: str,
    node_config: dict[str, Any],
    snapshot: dict[str, Any],
    *,
    session_id: Any,
    tenant_id: str | None = None,
    readable: list[str] | None = None,
    branch_slice: dict[str, Any] | None = None,
) -> str:
    """Derive the namespaced cache key for one node dispatch.

    Layout: ``{scope}:{scope_id}:{node_id}:{identity_hash}:{slice_hash}``.

    - ``identity_hash``: CONVERSATION nodes fold in their model-affecting
      config so model/parameter upgrades self-invalidate; other node types
      hash to a constant.
    - ``slice_hash``: canonical JSON of the node's input — the declared
      readable channels, overridden by ``branch_slice`` for fan-out branch
      invocations (the packet state IS the branch input), falling back to
      the full snapshot when the node declares no readable channels
      (never under-key: an undeclared input must not produce spurious hits).
    - A registered ``key_func`` replaces identity+slice derivation; the
      scope namespace and node id prefix always remain (isolation is
      never opt-out).

    Raises:
        RuntimeError: when ``scope="tenant"`` but no ``tenant_id`` was
            supplied to the run (fail-closed — an unwired tenant scope must
            not silently widen to session scope).
    """
    scope_id: Any
    if policy.scope == "tenant":
        if not tenant_id:
            raise RuntimeError(
                f"Node '{node_id}' declares cache scope='tenant' but the execution "
                "was started without a tenant_id. Pass tenant_id to PregelRuntime.execute()."
            )
        scope_id = tenant_id
    else:
        scope_id = str(session_id)

    if policy.key_func is not None:
        material = str(get_node_key_func(policy.key_func)(node_id, snapshot))
        return f"{policy.scope}:{scope_id}:{node_id}:{material}"

    # Identity: model-affecting config keys are absent on non-CONVERSATION
    # nodes, so their identity hash is a constant — only CONVERSATION nodes
    # currently carry self-invalidating identity material.
    identity_payload = {key: node_config.get(key) for key in _CONVERSATION_IDENTITY_KEYS}
    identity_hash = _canonical_hash(identity_payload)

    if branch_slice is not None:
        slice_payload: Any = branch_slice
    elif readable:
        slice_payload = {ch: snapshot.get(ch) for ch in readable}
    else:
        slice_payload = snapshot
    slice_hash = _canonical_hash(slice_payload)

    return f"{policy.scope}:{scope_id}:{node_id}:{identity_hash}:{slice_hash}"
