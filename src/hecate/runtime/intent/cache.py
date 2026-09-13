"""Intent decision cache (6.23).

Process-local LRU over atomic-level decisions. The key binds three
dimensions — normalized utterance fingerprint, evidence version id, and a
context fingerprint — so publishing a new package version rotates the key
space implicitly (no invalidation broadcast) and a changed session context
never reuses a stale decision.

The cache stores the L1 (atomic) decision only; the L2/L3 layers are
computed fresh per call because session state evolves across turns.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

_WHITESPACE = re.compile(r"\s+")


def normalize_utterance(utterance: str) -> str:
    """Lowercase and collapse whitespace — the cache's text normalization."""
    return _WHITESPACE.sub(" ", utterance.strip().lower())


def fingerprint(*parts: Any) -> str:
    """Stable sha256 fingerprint over the JSON-serialized parts."""
    canonical = json.dumps(list(parts), sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CachedDecision:
    """The cached L1 decision (never the layered result)."""

    label: str | None
    confidence: float
    gated: bool
    underlying_source: str


class IntentDecisionCache:
    """TTL-bounded LRU over atomic intent decisions.

    Args:
        max_size: Maximum entries; evicts least-recently-used beyond this.
        ttl_seconds: Entry lifetime; expired entries are ineligible and
            evicted lazily on access.
    """

    def __init__(self, max_size: int = 2048, ttl_seconds: float = 600.0) -> None:
        self._max_size = max_size
        self._ttl_seconds = ttl_seconds
        self._entries: OrderedDict[str, tuple[CachedDecision, float]] = OrderedDict()

    @staticmethod
    def make_key(
        utterance: str,
        evidence_version_id: Any,
        context_fingerprint: str,
    ) -> str:
        """Cache key over (normalized utterance, evidence version, context)."""
        return fingerprint(normalize_utterance(utterance), str(evidence_version_id), context_fingerprint)

    def get(self, key: str) -> CachedDecision | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        decision, expires_at = entry
        if expires_at <= time.monotonic():
            del self._entries[key]
            return None
        self._entries.move_to_end(key)
        return decision

    def put(self, key: str, decision: CachedDecision) -> None:
        self._entries[key] = (decision, time.monotonic() + self._ttl_seconds)
        self._entries.move_to_end(key)
        while len(self._entries) > self._max_size:
            self._entries.popitem(last=False)

    def clear(self) -> None:
        self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)
