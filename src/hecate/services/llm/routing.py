"""Model routing for intelligent LLM model selection.

Selects the best model based on cost, latency, and capability constraints
using configurable routing strategies.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class RoutingStrategy(Enum):
    """Available routing strategies for model selection."""

    COST = "cost"
    LATENCY = "latency"
    CAPABILITY = "capability"
    BALANCED = "balanced"


@dataclass
class ModelInfo:
    """Metadata for a model in the routing pool."""

    name: str
    provider: str
    cost_per_1k_input: float = 0.0
    cost_per_1k_output: float = 0.0
    avg_latency_ms: float = 0.0
    capabilities: list[str] = field(default_factory=list)
    max_context_tokens: int = 4096


@dataclass
class _RoutingConstraints:
    """Internal constraints for filtering candidate models."""

    max_cost_per_1k: float | None = None
    max_latency_ms: float | None = None
    required_capabilities: list[str] | None = None


class ModelRouter:
    """Routes requests to the best model based on a routing strategy.

    Supports cost-aware, latency-aware, capability-aware, and balanced
    routing with optional constraints to filter candidates.
    """

    def __init__(self) -> None:
        self._models: dict[str, ModelInfo] = {}

    def register_model(self, model_info: ModelInfo) -> None:
        """Add a model to the routing pool.

        Args:
            model_info: Model metadata.
        """
        self._models[model_info.name] = model_info
        logger.debug(f"Registered model {model_info.name} for routing")

    def unregister_model(self, model_name: str) -> None:
        """Remove a model from the routing pool.

        Args:
            model_name: Name of the model to remove.
        """
        self._models.pop(model_name, None)

    def get_model(self, model_name: str) -> ModelInfo | None:
        """Get model info by name.

        Args:
            model_name: Name of the model.

        Returns:
            ModelInfo or None if not found.
        """
        return self._models.get(model_name)

    def list_models(self) -> list[str]:
        """List all registered model names.

        Returns:
            List of model names.
        """
        return list(self._models.keys())

    def select_model(
        self,
        strategy: RoutingStrategy,
        required_capabilities: list[str] | None = None,
        max_cost_per_1k: float | None = None,
        max_latency_ms: float | None = None,
    ) -> ModelInfo | None:
        """Select the best model based on strategy and constraints.

        Args:
            strategy: Routing strategy to use.
            required_capabilities: Capabilities the model must have.
            max_cost_per_1k: Maximum cost per 1K tokens (input+output avg).
            max_latency_ms: Maximum average latency in milliseconds.

        Returns:
            Best matching ModelInfo, or None if no candidate found.
        """
        constraints = _RoutingConstraints(
            max_cost_per_1k=max_cost_per_1k,
            max_latency_ms=max_latency_ms,
            required_capabilities=required_capabilities,
        )
        candidates = self._filter_candidates(constraints)

        if not candidates:
            logger.warning("No model candidates found for routing")
            return None

        if strategy == RoutingStrategy.COST:
            return self._select_by_cost(candidates)
        if strategy == RoutingStrategy.LATENCY:
            return self._select_by_latency(candidates)
        if strategy == RoutingStrategy.CAPABILITY:
            return self._select_by_capability(candidates, required_capabilities)
        if strategy == RoutingStrategy.BALANCED:
            return self._select_balanced(candidates, constraints)

        return candidates[0]

    def _filter_candidates(self, constraints: _RoutingConstraints) -> list[ModelInfo]:
        """Filter models by hard constraints.

        Args:
            constraints: Hard limits on cost, latency, and capabilities.

        Returns:
            List of models meeting all constraints.
        """
        candidates = list(self._models.values())

        if constraints.required_capabilities:
            candidates = [
                m for m in candidates if all(cap in m.capabilities for cap in constraints.required_capabilities)
            ]

        if constraints.max_cost_per_1k is not None:
            candidates = [m for m in candidates if self._avg_cost(m) <= constraints.max_cost_per_1k]

        if constraints.max_latency_ms is not None:
            candidates = [m for m in candidates if m.avg_latency_ms <= constraints.max_latency_ms]

        return candidates

    def _select_by_cost(self, models: list[ModelInfo]) -> ModelInfo:
        """Select the cheapest model.

        Args:
            models: Candidate models.

        Returns:
            Model with the lowest average cost per 1K tokens.
        """
        return min(models, key=self._avg_cost)

    def _select_by_latency(self, models: list[ModelInfo]) -> ModelInfo:
        """Select the fastest model.

        Args:
            models: Candidate models.

        Returns:
            Model with the lowest average latency.
        """
        return min(models, key=lambda m: m.avg_latency_ms)

    def _select_by_capability(
        self,
        models: list[ModelInfo],
        required_capabilities: list[str] | None,
    ) -> ModelInfo:
        """Select the model with the most capability coverage.

        Ties are broken by cost (cheaper wins).

        Args:
            models: Candidate models.
            required_capabilities: Required capabilities (already filtered).

        Returns:
            Model with the best capability match.
        """
        if not required_capabilities:
            return min(models, key=self._avg_cost)

        def capability_score(m: ModelInfo) -> float:
            matched = sum(1 for c in required_capabilities if c in m.capabilities)
            return -matched

        # Most matches first, then cheapest
        return min(models, key=lambda m: (capability_score(m), self._avg_cost(m)))

    def _select_balanced(
        self,
        models: list[ModelInfo],
        constraints: _RoutingConstraints,
    ) -> ModelInfo:
        """Select model using a weighted score combining cost, latency, and capability.

        Weights: cost 40%, latency 40%, capability match 20%.

        Args:
            models: Candidate models.
            constraints: Original constraints for capability scoring.

        Returns:
            Model with the best balanced score.
        """
        if not models:
            return models[0]  # Should not happen, but safety net

        # Normalize cost and latency to 0-1 range (lower is better)
        costs = [self._avg_cost(m) for m in models]
        latencies = [m.avg_latency_ms for m in models]

        max_cost = max(costs) if costs else 1.0
        max_latency = max(latencies) if latencies else 1.0

        # Avoid division by zero
        if max_cost == 0:
            max_cost = 1.0
        if max_latency == 0:
            max_latency = 1.0

        def balanced_score(m: ModelInfo) -> float:
            cost_norm = self._avg_cost(m) / max_cost
            latency_norm = m.avg_latency_ms / max_latency

            # Capability bonus: fraction of required capabilities matched
            if constraints.required_capabilities:
                cap_match = sum(1 for c in constraints.required_capabilities if c in m.capabilities) / len(
                    constraints.required_capabilities
                )
            else:
                cap_match = 1.0

            # Lower score is better: cost 40% + latency 40% - capability bonus 20%
            return 0.4 * cost_norm + 0.4 * latency_norm - 0.2 * cap_match

        return min(models, key=balanced_score)

    @staticmethod
    def _avg_cost(m: ModelInfo) -> float:
        """Average cost per 1K tokens (input + output).

        Args:
            m: Model info.

        Returns:
            Average cost.
        """
        return (m.cost_per_1k_input + m.cost_per_1k_output) / 2
