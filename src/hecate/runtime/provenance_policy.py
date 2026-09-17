"""Citation provenance policy resolution (1.3.5e Stage 1).

Resolves the per-node citation configuration into a validated
``CitationPolicy``:

- **Priority**: node configuration > agent-level policy > disabled default.
  Mirrors the context processor chain precedence (node > agent > platform).
- **Fail-fast validation**: unknown fields and invalid values are rejected
  at load time with an error naming the invalid field — misconfiguration
  never reaches runtime.
- **Canonical hash**: the resolved policy serializes to a stable SHA-256
  hash (identical policies → identical hashes) contributing to agent
  versioning and audit, matching the chain policy hash semantics.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

# Defaults per change design.md: ~200-300 char chunks, results below the
# minimum size pass through unmarked.
DEFAULT_CHUNK_GRANULARITY = 300
DEFAULT_MIN_CHUNK_CHARS = 200


class CitationPolicyError(ValueError):
    """Raised at load time for invalid citation policy configuration."""


@dataclass(frozen=True)
class CitationPolicy:
    """A fully resolved citation provenance policy for one node."""

    enabled: bool
    chunk_granularity: int = DEFAULT_CHUNK_GRANULARITY
    min_chunk_chars: int = DEFAULT_MIN_CHUNK_CHARS

    @property
    def canonical_hash(self) -> str:
        payload = json.dumps(
            {
                "enabled": self.enabled,
                "chunk_granularity": self.chunk_granularity,
                "min_chunk_chars": self.min_chunk_chars,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _validate_policy_spec(spec: Any, source: str) -> dict[str, Any]:
    """Validate one policy spec dict (fail-fast) and return it normalized."""
    if not isinstance(spec, dict):
        raise CitationPolicyError(f"citation_provenance ({source}) must be an object")
    extras = set(spec.keys()) - {"enabled", "chunk_granularity", "min_chunk_chars"}
    if extras:
        raise CitationPolicyError(
            f"citation_provenance ({source}): unknown field(s) {sorted(extras)}; "
            "expected 'enabled', 'chunk_granularity', 'min_chunk_chars'"
        )
    enabled = spec.get("enabled")
    if not isinstance(enabled, bool):
        raise CitationPolicyError(
            f"citation_provenance ({source}): field 'enabled' has invalid type {type(enabled).__name__}; expected bool"
        )
    granularity = spec.get("chunk_granularity", DEFAULT_CHUNK_GRANULARITY)
    if not isinstance(granularity, int) or isinstance(granularity, bool) or granularity <= 0:
        raise CitationPolicyError(
            f"citation_provenance ({source}): field 'chunk_granularity' must be a positive integer, got {granularity!r}"
        )
    min_chars = spec.get("min_chunk_chars", DEFAULT_MIN_CHUNK_CHARS)
    if not isinstance(min_chars, int) or isinstance(min_chars, bool) or min_chars < 0:
        raise CitationPolicyError(
            f"citation_provenance ({source}): field 'min_chunk_chars' must be a non-negative integer, got {min_chars!r}"
        )
    return {
        "enabled": enabled,
        "chunk_granularity": granularity,
        "min_chunk_chars": min_chars,
    }


def resolve_citation_policy(
    node_config: dict[str, Any] | None = None,
    agent_policy: dict[str, Any] | None = None,
) -> CitationPolicy:
    """Resolve the citation policy: node config > agent policy > disabled.

    Validation is fail-fast in both scopes; an invalid agent policy is
    surfaced even when the node overrides it (misconfiguration must be
    visible, not masked).
    """
    normalized_agent: dict[str, Any] | None = None
    if agent_policy is not None:
        normalized_agent = _validate_policy_spec(agent_policy, "agent")
    node_spec = (node_config or {}).get("citation_provenance")
    if node_spec is not None:
        normalized = _validate_policy_spec(node_spec, "node")
    elif normalized_agent is not None:
        normalized = normalized_agent
    else:
        return CitationPolicy(enabled=False)
    return CitationPolicy(**normalized)
