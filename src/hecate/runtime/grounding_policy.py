"""Grounding scoring policy resolution (1.3.5e Stage 2).

Resolves the per-node grounding scoring configuration into a validated
``GroundingPolicy``:

- **Priority**: node configuration > agent-level policy > disabled default.
  Mirrors the citation provenance precedence (node > agent > platform).
- **Fail-fast validation**: unknown fields and invalid values are rejected
  at load time with an error naming the invalid field — misconfiguration
  never reaches runtime.
- **Canonical hash**: the resolved policy serializes to a stable SHA-256
  hash (identical policies → identical hashes) contributing to agent
  versioning and audit, matching the citation policy hash semantics.

Shadow disposition thresholds default to code constants and MAY be
overridden per node via the ``shadow_thresholds`` section — an advanced
calibration knob for Stage 3 rollout, not a user-facing disposition
control (shadow results never gate delivery).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

DEFAULT_BACKEND = "llm_judge"
DEFAULT_TRIGGER = "on_uncited"
DEFAULT_SAMPLE_RATE = 1.0
DEFAULT_NLI_TIMEOUT_MS = 500
DEFAULT_FALLBACK_TOP_K = 3
DEFAULT_ESCALATION_CONFIDENCE = 0.5
DEFAULT_ESCALATION_MAX_PAIRS = 8
# Shadow disposition defaults (Stage 3 draft thresholds; see design D8).
DEFAULT_SHADOW_CONTRADICTED_ANY = True
DEFAULT_SHADOW_CONTRADICTED_RATIO = 0.2
DEFAULT_SHADOW_UNSUPPORTED_RATIO = 0.5

_VALID_BACKENDS = ("llm_judge", "http_nli")
_VALID_TRIGGERS = ("on_uncited", "always", "sample")


class GroundingPolicyError(ValueError):
    """Raised at load time for invalid grounding scoring configuration."""


@dataclass(frozen=True)
class ShadowThresholds:
    """Thresholds computing the ``would_block`` shadow disposition."""

    contradicted_any: bool = DEFAULT_SHADOW_CONTRADICTED_ANY
    contradicted_ratio: float = DEFAULT_SHADOW_CONTRADICTED_RATIO
    unsupported_ratio: float = DEFAULT_SHADOW_UNSUPPORTED_RATIO


@dataclass(frozen=True)
class FallbackConfig:
    """Fallback knowledge retrieval for uncited claims."""

    enabled: bool = False
    kb_ids: list[str] = field(default_factory=list)
    top_k: int = DEFAULT_FALLBACK_TOP_K


@dataclass(frozen=True)
class EscalationConfig:
    """Upgrade borderline (low-confidence) pairs to the LLM judge backend."""

    enabled: bool = False
    confidence: float = DEFAULT_ESCALATION_CONFIDENCE
    max_pairs: int = DEFAULT_ESCALATION_MAX_PAIRS


@dataclass(frozen=True)
class GroundingPolicy:
    """A fully resolved grounding scoring policy for one node."""

    enabled: bool
    backend: str = DEFAULT_BACKEND
    model: str | None = None
    nli_endpoint: str | None = None
    nli_timeout_ms: int = DEFAULT_NLI_TIMEOUT_MS
    trigger: str = DEFAULT_TRIGGER
    sample_rate: float = DEFAULT_SAMPLE_RATE
    shadow_thresholds: ShadowThresholds = field(default_factory=ShadowThresholds)
    fallback: FallbackConfig = field(default_factory=FallbackConfig)
    escalation: EscalationConfig = field(default_factory=EscalationConfig)

    @property
    def canonical_hash(self) -> str:
        payload = json.dumps(
            {
                "enabled": self.enabled,
                "backend": self.backend,
                "model": self.model,
                "nli_endpoint": self.nli_endpoint,
                "nli_timeout_ms": self.nli_timeout_ms,
                "trigger": self.trigger,
                "sample_rate": self.sample_rate,
                "shadow_thresholds": {
                    "contradicted_any": self.shadow_thresholds.contradicted_any,
                    "contradicted_ratio": self.shadow_thresholds.contradicted_ratio,
                    "unsupported_ratio": self.shadow_thresholds.unsupported_ratio,
                },
                "fallback": {
                    "enabled": self.fallback.enabled,
                    "kb_ids": self.fallback.kb_ids,
                    "top_k": self.fallback.top_k,
                },
                "escalation": {
                    "enabled": self.escalation.enabled,
                    "confidence": self.escalation.confidence,
                    "max_pairs": self.escalation.max_pairs,
                },
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _reject_unknown(spec: dict[str, Any], allowed: set[str], source: str, label: str) -> None:
    extras = set(spec.keys()) - allowed
    if extras:
        raise GroundingPolicyError(
            f"grounding_scoring ({source}): unknown field(s) {sorted(extras)} in '{label}'; expected {sorted(allowed)}"
        )


def _validate_bool(spec: dict[str, Any], key: str, source: str, default: bool) -> bool:
    value = spec.get(key, default)
    if not isinstance(value, bool):
        raise GroundingPolicyError(
            f"grounding_scoring ({source}): field '{key}' has invalid type {type(value).__name__}; expected bool"
        )
    return value


def _validate_float(spec: dict[str, Any], key: str, source: str, default: float, low: float, high: float) -> float:
    value = spec.get(key, default)
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not low <= value <= high:
        raise GroundingPolicyError(
            f"grounding_scoring ({source}): field '{key}' must be a number in [{low}, {high}], got {value!r}"
        )
    return float(value)


def _validate_positive_int(spec: dict[str, Any], key: str, source: str, default: int) -> int:
    value = spec.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise GroundingPolicyError(
            f"grounding_scoring ({source}): field '{key}' must be a positive integer, got {value!r}"
        )
    return value


def _validate_str_choice(spec: dict[str, Any], key: str, source: str, choices: tuple[str, ...], default: str) -> str:
    value = spec.get(key, default)
    if not isinstance(value, str) or value not in choices:
        raise GroundingPolicyError(
            f"grounding_scoring ({source}): field '{key}' must be one of {list(choices)}, got {value!r}"
        )
    return value


def _validate_optional_str(spec: dict[str, Any], key: str, source: str) -> str | None:
    value = spec.get(key)
    if value is not None and not isinstance(value, str):
        raise GroundingPolicyError(
            f"grounding_scoring ({source}): field '{key}' has invalid type {type(value).__name__}; expected string"
        )
    return value


def _validate_str_list(spec: dict[str, Any], key: str, source: str) -> list[str]:
    value = spec.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise GroundingPolicyError(f"grounding_scoring ({source}): field '{key}' must be a list of strings")
    return list(value)


def _validate_shadow_thresholds(spec: Any, source: str) -> ShadowThresholds:
    if spec is None:
        return ShadowThresholds()
    if not isinstance(spec, dict):
        raise GroundingPolicyError(f"grounding_scoring ({source}): field 'shadow_thresholds' must be an object")
    _reject_unknown(spec, {"contradicted_any", "contradicted_ratio", "unsupported_ratio"}, source, "shadow_thresholds")
    return ShadowThresholds(
        contradicted_any=_validate_bool(spec, "contradicted_any", source, DEFAULT_SHADOW_CONTRADICTED_ANY),
        contradicted_ratio=_validate_float(
            spec, "contradicted_ratio", source, DEFAULT_SHADOW_CONTRADICTED_RATIO, 0.0, 1.0
        ),
        unsupported_ratio=_validate_float(
            spec, "unsupported_ratio", source, DEFAULT_SHADOW_UNSUPPORTED_RATIO, 0.0, 1.0
        ),
    )


def _validate_fallback(spec: Any, source: str) -> FallbackConfig:
    if spec is None:
        return FallbackConfig()
    if not isinstance(spec, dict):
        raise GroundingPolicyError(f"grounding_scoring ({source}): field 'fallback' must be an object")
    _reject_unknown(spec, {"enabled", "kb_ids", "top_k"}, source, "fallback")
    return FallbackConfig(
        enabled=_validate_bool(spec, "enabled", source, False),
        kb_ids=_validate_str_list(spec, "kb_ids", source),
        top_k=_validate_positive_int(spec, "top_k", source, DEFAULT_FALLBACK_TOP_K),
    )


def _validate_escalation(spec: Any, source: str) -> EscalationConfig:
    if spec is None:
        return EscalationConfig()
    if not isinstance(spec, dict):
        raise GroundingPolicyError(f"grounding_scoring ({source}): field 'escalation' must be an object")
    _reject_unknown(spec, {"enabled", "confidence", "max_pairs"}, source, "escalation")
    return EscalationConfig(
        enabled=_validate_bool(spec, "enabled", source, False),
        confidence=_validate_float(spec, "confidence", source, DEFAULT_ESCALATION_CONFIDENCE, 0.0, 1.0),
        max_pairs=_validate_positive_int(spec, "max_pairs", source, DEFAULT_ESCALATION_MAX_PAIRS),
    )


def _validate_policy_spec(spec: Any, source: str) -> dict[str, Any]:
    """Validate one policy spec dict (fail-fast) and return it normalized."""
    if not isinstance(spec, dict):
        raise GroundingPolicyError(f"grounding_scoring ({source}) must be an object")
    _reject_unknown(
        spec,
        {
            "enabled",
            "backend",
            "model",
            "nli_endpoint",
            "nli_timeout_ms",
            "trigger",
            "sample_rate",
            "shadow_thresholds",
            "fallback",
            "escalation",
        },
        source,
        "policy",
    )
    backend = _validate_str_choice(spec, "backend", source, _VALID_BACKENDS, DEFAULT_BACKEND)
    nli_endpoint = _validate_optional_str(spec, "nli_endpoint", source)
    if backend == "http_nli" and not nli_endpoint:
        raise GroundingPolicyError(
            f"grounding_scoring ({source}): field 'nli_endpoint' is required when backend is 'http_nli'"
        )
    return {
        "enabled": _validate_bool(spec, "enabled", source, False),
        "backend": backend,
        "model": _validate_optional_str(spec, "model", source),
        "nli_endpoint": nli_endpoint,
        "nli_timeout_ms": _validate_positive_int(spec, "nli_timeout_ms", source, DEFAULT_NLI_TIMEOUT_MS),
        "trigger": _validate_str_choice(spec, "trigger", source, _VALID_TRIGGERS, DEFAULT_TRIGGER),
        "sample_rate": _validate_float(spec, "sample_rate", source, DEFAULT_SAMPLE_RATE, 0.0, 1.0),
        "shadow_thresholds": _validate_shadow_thresholds(spec.get("shadow_thresholds"), source),
        "fallback": _validate_fallback(spec.get("fallback"), source),
        "escalation": _validate_escalation(spec.get("escalation"), source),
    }


def resolve_grounding_policy(
    node_config: dict[str, Any] | None = None,
    agent_policy: dict[str, Any] | None = None,
) -> GroundingPolicy:
    """Resolve the grounding scoring policy: node config > agent policy > disabled.

    Validation is fail-fast in both scopes; an invalid agent policy is
    surfaced even when the node overrides it (misconfiguration must be
    visible, not masked).
    """
    normalized_agent: dict[str, Any] | None = None
    if agent_policy is not None:
        normalized_agent = _validate_policy_spec(agent_policy, "agent")
    node_spec = (node_config or {}).get("grounding_scoring")
    if node_spec is not None:
        normalized = _validate_policy_spec(node_spec, "node")
    elif normalized_agent is not None:
        normalized = normalized_agent
    else:
        return GroundingPolicy(enabled=False)
    return GroundingPolicy(**normalized)
