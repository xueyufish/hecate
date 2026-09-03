"""Tool result caching with TTL, LRU eviction, and cacheability priority chain.

Integrates into ToolRegistry.execute() to cache results of cacheable tool calls.
Cache keys are session-scoped by default and use canonical JSON for deterministic
matching regardless of argument key ordering.

Design decisions (see openspec/changes/tool-caching/design.md):
- In-memory OrderedDict with LRU eviction (CrewAI pattern)
- 5-priority cacheability chain (LangGraph-Redis inspired)
- Canonical JSON key with ignored_args stripping
- Session-scoped entries by default
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

SIDE_EFFECT_PREFIXES: frozenset[str] = frozenset(
    {
        "write_",
        "create_",
        "delete_",
        "send_",
        "update_",
        "remove_",
        "post_",
        "put_",
        "patch_",
    }
)

DANGEROUS_BUILTINS: frozenset[str] = frozenset(
    {
        "execute_code",
        "bash",
        "write_file",
        "edit_file",
    }
)

DEFAULT_IGNORED_ARGS: frozenset[str] = frozenset(
    {
        "request_id",
        "trace_id",
        "correlation_id",
    }
)


@dataclass
class CacheEntry:
    """A cached tool result."""

    result: Any
    created_at: float
    ttl: float
    tool_name: str = ""
    last_accessed: float = field(default_factory=time.monotonic)

    @property
    def expired(self) -> bool:
        """Whether this entry has exceeded its TTL."""
        return (time.monotonic() - self.created_at) >= self.ttl


class ToolCache:
    """In-memory tool result cache with TTL and LRU eviction.

    Args:
        max_entries: Maximum number of cache entries (LRU eviction).
        default_ttl: Default TTL in seconds when tool has no explicit cache_ttl.
        ignored_args: Argument names to strip from cache keys.
    """

    def __init__(
        self,
        max_entries: int = 10000,
        default_ttl: int = 300,
        ignored_args: set[str] | None = None,
    ) -> None:
        self._entries: OrderedDict[str, CacheEntry] = OrderedDict()
        self._max_entries = max_entries
        self._default_ttl = default_ttl
        self._ignored_args = ignored_args or set(DEFAULT_IGNORED_ARGS)
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> Any | None:
        """Retrieve a cached result by key.

        Updates last_accessed for LRU ordering. Returns None on miss or expiry.

        Args:
            key: Cache key.

        Returns:
            Cached result, or None.
        """
        entry = self._entries.get(key)
        if entry is None:
            self._misses += 1
            return None
        if entry.expired:
            del self._entries[key]
            self._misses += 1
            return None
        entry.last_accessed = time.monotonic()
        self._entries.move_to_end(key)
        self._hits += 1
        return entry.result

    def set(self, key: str, result: Any, ttl: int | None = None, tool_name: str = "") -> None:
        """Store a result in the cache.

        Args:
            key: Cache key.
            result: Result to cache.
            ttl: TTL in seconds; None uses default_ttl.
            tool_name: Tool name for per-tool invalidation.
        """
        effective_ttl = ttl if ttl is not None else self._default_ttl
        self._entries[key] = CacheEntry(
            result=result,
            created_at=time.monotonic(),
            ttl=effective_ttl,
            tool_name=tool_name,
        )
        self._evict_if_needed()

    def invalidate(self, tool_name: str | None = None) -> int:
        """Invalidate cache entries.

        Args:
            tool_name: If set, only entries containing this tool name are removed.
                If None, all entries are cleared.

        Returns:
            Number of entries removed.
        """
        if tool_name is None:
            count = len(self._entries)
            self._entries.clear()
            return count

        keys_to_remove = [k for k, v in self._entries.items() if v.tool_name == tool_name]
        for k in keys_to_remove:
            del self._entries[k]
        return len(keys_to_remove)

    def stats(self) -> dict[str, int | float]:
        """Return cache statistics.

        Returns:
            Dict with hits, misses, entries, and hit_rate.
        """
        total = self._hits + self._misses
        return {
            "hits": self._hits,
            "misses": self._misses,
            "entries": len(self._entries),
            "hit_rate": self._hits / total if total > 0 else 0.0,
        }

    def make_key(
        self,
        tool_name: str,
        args: dict[str, Any],
        session_id: str | None = None,
    ) -> str:
        """Generate a deterministic cache key.

        Args:
            tool_name: Tool name.
            args: Tool arguments.
            session_id: Optional session ID for session-scoped entries.

        Returns:
            SHA256 hash key.
        """
        canonical = self._canonical_json(args)
        scope = session_id or "global"
        raw = f"{scope}:{tool_name}:{canonical}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def _canonical_json(self, args: dict[str, Any]) -> str:
        """Serialize args to canonical JSON with sorted keys and ignored args stripped.

        Args:
            args: Tool arguments dict.

        Returns:
            Canonical JSON string.
        """
        filtered = {k: v for k, v in args.items() if k not in self._ignored_args}
        return json.dumps(filtered, sort_keys=True, default=str)

    def _evict_if_needed(self) -> None:
        """Evict least-recently-used entries if exceeding max_entries."""
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)


def is_cacheable(tool_meta: dict[str, Any]) -> bool:
    """Determine if a tool call should be cached using a 5-priority chain.

    Priority order:
    1. Explicit ``cacheable`` flag on tool — overrides everything
    2. Side-effect name prefix (write_, create_, delete_, etc.) — skip
    3. Dangerous builtin set (execute_code, bash, etc.) — skip
    4. Risk level + sandbox heuristic — cache if LOW/MEDIUM and no sandbox
    5. Default — not cached

    Args:
        tool_meta: Dict with keys: name, source, risk_level, sandbox_enabled,
            cacheable (optional).

    Returns:
        True if the tool call should be cached.
    """
    name = tool_meta.get("name", "")
    cacheable = tool_meta.get("cacheable")

    if cacheable is not None:
        return cacheable

    for prefix in SIDE_EFFECT_PREFIXES:
        if name.startswith(prefix):
            return False

    if name in DANGEROUS_BUILTINS:
        return False

    risk_level = str(tool_meta.get("risk_level", "low")).lower()
    sandbox_enabled = tool_meta.get("sandbox_enabled", False)

    return risk_level in ("low", "medium") and not sandbox_enabled
