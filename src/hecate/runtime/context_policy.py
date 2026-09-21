"""Context processor chain policy resolution (4.13).

Resolves the per-node chain configuration into a validated, instantiated
``ContextProcessorChain``:

- **Priority**: node configuration > agent-level policy > model-capability
  defaults > platform defaults. Cache-capable models default to a
  KV-cache-aware-first policy; everything else to the plain recency window.
- **Fail-fast validation**: unknown processor types, unknown fields, and
  mutually exclusive parameters are rejected at load time with an error
  naming the invalid field — misconfiguration never reaches runtime.
- **Canonical hash**: the fully resolved policy serializes to a stable
  SHA-256 hash (identical policies → identical hashes) for agent versioning
  and audit.

The ``ContextChainFactory`` is the object placed in the execution context
(``context_chain``); ``LLMWorker`` asks it for the chain of each node, and
chains are cached per canonical hash so session-scoped latches (warn crossing,
time hints, usage anchors) persist across invocations of the same policy.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any

from hecate.runtime.context_processors import (
    BudgetWarnProcessor,
    CompressionProcessor,
    ContextProcessor,
    ContextProcessorChain,
    FailurePolicy,
    HintProcessor,
    KVCacheAwareProcessor,
    MemoryPressureNudgeProcessor,
    OffloadProcessor,
    RoundWindowProcessor,
    TerminationProcessor,
    ToolResultTruncationProcessor,
)

logger = logging.getLogger(__name__)


class ChainPolicyError(ValueError):
    """Raised at load time for invalid chain policy configuration."""


# Platform-default policy: the legacy five-step ladder (warn/hints opt-in via
# node or agent configuration). Cache-capable models get the KV-cache-aware
# guard which constrains window selection to the droppable tail.
_PLATFORM_DEFAULT_POLICY: list[str] = [
    "tool_result_truncation",
    "kv_cache_aware",
    "round_window",
    "offload",
    "compression",
    "terminate",
]

# Processor registry: only registered types pass load-time validation. This
# is also the trust boundary — third-party code cannot enter the in-process
# T0 chain by naming itself in configuration (ADR-029).
PROCESSOR_REGISTRY: dict[str, type[ContextProcessor]] = {
    BudgetWarnProcessor.name: BudgetWarnProcessor,
    MemoryPressureNudgeProcessor.name: MemoryPressureNudgeProcessor,
    ToolResultTruncationProcessor.name: ToolResultTruncationProcessor,
    KVCacheAwareProcessor.name: KVCacheAwareProcessor,
    RoundWindowProcessor.name: RoundWindowProcessor,
    OffloadProcessor.name: OffloadProcessor,
    CompressionProcessor.name: CompressionProcessor,
    TerminationProcessor.name: TerminationProcessor,
    HintProcessor.name: HintProcessor,
}

# Allowed constructor params per processor type (name → spec). Used for
# fail-fast validation of unknown fields and bad values.
_PARAM_SPECS: dict[str, dict[str, tuple[type, ...]]] = {
    "budget_warn": {"threshold": (int, float)},
    "memory_pressure_nudge": {"threshold": (int, float)},
    "tool_result_truncation": {},
    "kv_cache_aware": {"window_units": (int,)},
    "round_window": {"ranking": (str,)},
    "offload": {"threshold_tokens": (int,)},
    "compression": {"backend": (str,), "trigger_ratio": (int, float), "retain_ratio": (int, float)},
    "terminate": {},
    "hint": {"time_interval_minutes": (int,), "usage_buffer_ratio": (int, float)},
}


def _validate_compression_params(params: dict[str, Any], index: int) -> None:
    """Range checks for compression parameters (types already validated)."""
    trigger = params.get("trigger_ratio")
    if trigger is not None and not 0 < float(trigger) <= 1:
        raise ChainPolicyError(
            f"context_processors[{index}] (compression): trigger_ratio must be in (0, 1], got {trigger!r}"
        )
    retain = params.get("retain_ratio")
    if retain is not None and not 0 < float(retain) < 1:
        raise ChainPolicyError(
            f"context_processors[{index}] (compression): retain_ratio must be in (0, 1), got {retain!r}"
        )
    backend = params.get("backend")
    if backend is not None and backend not in ("projection", "surface_replacement"):
        raise ChainPolicyError(
            f"context_processors[{index}] (compression): backend must be 'projection' or "
            f"'surface_replacement', got {backend!r}"
        )


def _validate_spec_entry(entry: Any, index: int) -> dict[str, Any]:
    """Validate one policy entry (fail-fast) and return it normalized."""
    if isinstance(entry, str):
        name = entry
        params: dict[str, Any] = {}
    elif isinstance(entry, dict):
        extras = set(entry.keys()) - {"type", "params"}
        if extras:
            raise ChainPolicyError(
                f"context_processors[{index}]: unknown field(s) {sorted(extras)}; expected 'type' and 'params'"
            )
        name = entry.get("type")
        params = entry.get("params") or {}
        if not isinstance(params, dict):
            raise ChainPolicyError(f"context_processors[{index}].params must be an object")
    else:
        raise ChainPolicyError(f"context_processors[{index}]: entries must be strings or objects")
    if not isinstance(name, str) or name not in PROCESSOR_REGISTRY:
        raise ChainPolicyError(f"context_processors[{index}]: unknown processor type {name!r}")
    allowed = _PARAM_SPECS.get(name, {})
    for key, value in params.items():
        if key not in allowed:
            raise ChainPolicyError(
                f"context_processors[{index}] ({name}): unknown param {key!r}; allowed: {sorted(allowed)}"
            )
        if not isinstance(value, allowed[key]) or isinstance(value, bool):
            raise ChainPolicyError(
                f"context_processors[{index}] ({name}): param {key!r} has invalid type "
                f"{type(value).__name__}; expected {allowed[key]}"
            )
    if name == "compression":
        _validate_compression_params(params, index)
    return {"type": name, "params": params}


def validate_policy_spec(spec: list[Any]) -> list[dict[str, Any]]:
    """Validate a full policy spec (list of processor entries), fail-fast."""
    if not isinstance(spec, list):
        raise ChainPolicyError("context_processors must be a list")
    normalized = [_validate_spec_entry(entry, i) for i, entry in enumerate(spec)]
    _check_cross_processor_constraints(normalized)
    return normalized


def _build_processor(name: str, params: dict[str, Any]) -> ContextProcessor:
    cls = PROCESSOR_REGISTRY[name]
    try:
        return cls(**params)
    except (TypeError, ValueError) as e:
        raise ChainPolicyError(f"processor {name!r}: invalid params {params}: {e}") from e


def _check_cross_processor_constraints(normalized: list[dict[str, Any]]) -> None:
    """Spec'd mutex: the usage-buffer hint must sit strictly below the warn threshold."""
    warn_threshold: float | None = None
    for entry in normalized:
        if entry["type"] == "budget_warn":
            warn_threshold = float(entry["params"].get("threshold", 0.8))
    for entry in normalized:
        if entry["type"] == "hint" and "usage_buffer_ratio" in entry["params"]:
            ratio = float(entry["params"]["usage_buffer_ratio"])
            limit = warn_threshold if warn_threshold is not None else 0.8
            if ratio >= limit:
                raise ChainPolicyError(
                    f"hint.usage_buffer_ratio ({ratio}) must be strictly below the budget_warn threshold ({limit})"
                )
        if entry["type"] == "memory_pressure_nudge":
            # The pressure nudge lives above the warn band — crossing it must
            # imply the warn hint already fired.
            threshold = float(entry["params"].get("threshold", 0.9))
            if threshold <= (warn_threshold if warn_threshold is not None else 0.8):
                raise ChainPolicyError(
                    f"memory_pressure_nudge.threshold ({threshold}) must be strictly above the "
                    f"budget_warn threshold ({warn_threshold if warn_threshold is not None else 0.8})"
                )


# Prompt-caching support by model family (prefix match, case-insensitive).
# Anthropic and OpenAI-compatible endpoints cache prefixes automatically;
# models without prefix caching make suffix-stability pointless.
_CACHE_CAPABLE_PREFIXES = (
    "claude",
    "gpt-4o",
    "gpt-4.1",
    "gpt-5",
    "o3",
    "o4",
    "deepseek",
    "glm",
    "gemini-2",
    "gemini-3",
    "kimi",
)

# Conservative context-window table for model-capability default budgets
# (budget ≈ window // 8, floored at 8000). Unknown models fall through to
# the platform default budget.
_WINDOW_HINTS: tuple[tuple[str, int], ...] = (
    ("claude-opus-4", 200_000),
    ("claude-sonnet-4", 200_000),
    ("claude-haiku-4", 200_000),
    ("claude-3", 200_000),
    ("gpt-5", 400_000),
    ("gpt-4.1", 1_000_000),
    ("gpt-4o", 128_000),
    ("gpt-4", 8_000),
    ("o3", 200_000),
    ("o4", 200_000),
    ("deepseek", 128_000),
    ("glm-5", 200_000),
    ("glm-4", 128_000),
    ("gemini-2", 1_000_000),
    ("gemini-3", 1_000_000),
    ("kimi", 256_000),
)


def _normalized_model(model: str | None) -> str:
    if not model:
        return ""
    return str(model).rsplit("/", 1)[-1].strip().lower()


def model_supports_prompt_cache(model: str | None) -> bool:
    """Whether the model family supports provider-side prefix caching."""
    candidate = _normalized_model(model)
    return any(candidate.startswith(p) for p in _CACHE_CAPABLE_PREFIXES)


def model_default_budget(model: str | None) -> int | None:
    """Model-capability default budget (window // 8), or None when unknown."""
    candidate = _normalized_model(model)
    for prefix, window in _WINDOW_HINTS:
        if candidate.startswith(prefix):
            return max(8000, window // 8)
    return None


def model_default_window(model: str | None) -> int | None:
    """Model-capability context window (capacity axis), or None when unknown."""
    candidate = _normalized_model(model)
    for prefix, window in _WINDOW_HINTS:
        if candidate.startswith(prefix):
            return window
    return None


def uses_surface_replacement(spec: list[Any] | None) -> bool:
    """Whether a chain policy spec selects the durable compaction backend."""
    if not spec:
        return False
    for entry in spec:
        if isinstance(entry, str):
            continue
        if isinstance(entry, dict) and entry.get("type") == "compression":
            return (entry.get("params") or {}).get("backend") == "surface_replacement"
    return False


def validate_compaction_exclusivity(eviction_policy: Any, specs: list[list[Any] | None]) -> None:
    """Fail-fast when durable compaction shares a session with message eviction.

    Ledger ranges are message ordinals of the messages channel; an eviction
    that drops channel entries shifts ordinals and would silently misapply
    recorded ranges (ADR-033 design D4). ``eviction_policy`` is the runtime's
    policy — anything beyond the no-op default disqualifies the backend.
    """
    from hecate.runtime.eviction import NoEviction

    if eviction_policy is None or isinstance(eviction_policy, NoEviction):
        return
    if any(uses_surface_replacement(spec) for spec in specs):
        raise ChainPolicyError(
            "compression backend 'surface_replacement' is incompatible with an eviction policy "
            f"({type(eviction_policy).__name__}) that mutates the messages channel; use the default "
            "no-op eviction or the projection backend"
        )


@dataclass
class ResolvedPolicy:
    """A fully resolved chain policy for one node/agent."""

    processors: list[ContextProcessor]
    canonical_hash: str
    names: list[str] = field(default_factory=list)
    scope: dict[str, Any] = field(default_factory=dict)

    def metadata_source(self) -> str:
        """Which resolution layer supplied the policy (node/agent/model_capability/platform)."""
        return str(self.scope.get("source", "platform"))


def canonical_policy_hash(names: list[str], params: list[dict[str, Any]], scope: dict[str, Any]) -> str:
    """Stable SHA-256 of the resolved policy (identical policies → identical hash)."""
    payload = json.dumps(
        {"processors": [{"type": n, "params": p} for n, p in zip(names, params, strict=True)], "scope": scope},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


class ContextChainFactory:
    """Resolves per-node chains; placed in the execution context as ``context_chain``.

    The factory owns the agent-level policy override and the legacy engine /
    offloader handles (chains consume them via the execution context, which
    the factory mirrors into every chain it builds — chains read engine and
    offloader from the execution context at apply time, so this object only
    needs to resolve processor lists and cache chains per canonical hash).

    Chains are cached per canonical hash so session-scoped state (warn latch,
    time hints, usage anchors) survives across invocations of the same policy.
    """

    def __init__(
        self,
        agent_policy: list[Any] | None = None,
        platform_default: list[str] | None = None,
        failure_policy: FailurePolicy | None = None,
        compaction_summarizer: Any = None,
        model_window_provider: Any = None,
    ) -> None:
        self._agent_policy = agent_policy
        self._platform_default = platform_default or list(_PLATFORM_DEFAULT_POLICY)
        self._failure_policy = failure_policy or FailurePolicy()
        # ADR-033 durable compaction wiring: production summarizer adapter
        # and model-window lookup are composition-layer concerns; the factory
        # forwards them to every chain it builds.
        self._compaction_summarizer = compaction_summarizer
        self._model_window_provider = model_window_provider
        self._last_compaction: dict[str, str] = {}
        self._chain_cache: dict[str, ContextProcessorChain] = {}

    def _note_compaction(self, session_id: str, compaction_id: str) -> None:
        """Record the newest completed compaction per session (checkpoint metadata)."""
        self._last_compaction[session_id] = compaction_id

    def last_compaction_id(self, session_id: str) -> str | None:
        """The newest recorded compaction id for a session, if any."""
        return self._last_compaction.get(session_id)

    @property
    def configures_surface_replacement(self) -> bool:
        """Whether a factory-level policy (agent/platform) selects the durable backend."""
        return uses_surface_replacement(self._agent_policy)

    def resolve(
        self,
        node_config: dict[str, Any] | None = None,
        model: str | None = None,
    ) -> ResolvedPolicy:
        """Resolve the policy in priority order: node > agent > model-capability > platform."""
        raw_spec: list[Any] | None = None
        source = "platform"
        node_spec = (node_config or {}).get("context_processors")
        if node_spec is not None:
            raw_spec = node_spec
            source = "node"
        elif self._agent_policy is not None:
            raw_spec = self._agent_policy
            source = "agent"
        elif model is not None and not model_supports_prompt_cache(model):
            # Non-cache-capable models gain nothing from the KV guard; use a
            # plain recency window so importance-free suffix selection keeps
            # behavior predictable.
            raw_spec = [name for name in self._platform_default if name != "kv_cache_aware"]
            source = "model_capability"

        normalized = validate_policy_spec(raw_spec if raw_spec is not None else self._platform_default)
        names = [entry["type"] for entry in normalized]
        params = [entry["params"] for entry in normalized]
        processors = [_build_processor(n, p) for n, p in zip(names, params, strict=True)]
        scope: dict[str, Any] = {"source": source}
        if model:
            scope["model"] = _normalized_model(model)
        return ResolvedPolicy(
            processors=processors,
            canonical_hash=canonical_policy_hash(names, params, scope),
            names=names,
            scope=scope,
        )

    def chain_for_node(
        self,
        node_config: dict[str, Any] | None = None,
        model: str | None = None,
    ) -> ContextProcessorChain:
        """Return the (cached) chain for a node's resolved policy."""
        policy = self.resolve(node_config=node_config, model=model)
        chain = self._chain_cache.get(policy.canonical_hash)
        if chain is None:
            chain = ContextProcessorChain(
                processors=list(policy.processors),
                failure_policy=self._failure_policy,
                compaction_summarizer=self._compaction_summarizer,
                model_window_provider=self._model_window_provider,
                compaction_listener=self._note_compaction,
            )
            self._chain_cache[policy.canonical_hash] = chain
        return chain
