"""Intelligent router — wraps ModelRouter with caching and cost-aware routing.

Provides cache-before-invoke pattern and budget-aware model selection.
"""

from __future__ import annotations

import logging
from typing import Any

from hecate.model_hub.cache import CacheStrategyABC, InMemoryCacheStrategy, generate_cache_key
from hecate.services.llm.routing import ModelInfo, ModelRouter, RoutingStrategy

logger = logging.getLogger(__name__)


class IntelligentRouter:
    """Wraps ModelRouter with caching and cost-aware routing.

    Args:
        router: Existing ModelRouter instance.
        cache: Cache strategy instance.
        cache_enabled: Whether caching is active.
        cost_aware: Whether to consult budget for routing.
        budget_service: Optional BudgetService for cost-aware routing.
        workspace_id: Workspace scope for budget checks.
    """

    def __init__(
        self,
        router: ModelRouter,
        cache: CacheStrategyABC | None = None,
        cache_enabled: bool = True,
        cost_aware: bool = True,
        budget_service: Any = None,
        workspace_id: Any = None,
    ) -> None:
        self._router = router
        self._cache = cache or InMemoryCacheStrategy()
        self._cache_enabled = cache_enabled
        self._cost_aware = cost_aware
        self._budget_service = budget_service
        self._workspace_id = workspace_id

    async def select_model(
        self,
        strategy: RoutingStrategy = RoutingStrategy.BALANCED,
        required_capabilities: list[str] | None = None,
        max_cost_per_1k: float | None = None,
        max_latency_ms: float | None = None,
    ) -> ModelInfo | None:
        """Select best model, optionally consulting budget for cost-aware routing.

        Args:
            strategy: Routing strategy to use.
            required_capabilities: Required capability tags.
            max_cost_per_1k: Maximum cost constraint.
            max_latency_ms: Maximum latency constraint.

        Returns:
            Best matching ModelInfo or None.
        """
        effective_strategy = strategy

        # Cost-aware routing: switch to COST strategy when budget is low
        if self._cost_aware and self._budget_service and self._workspace_id:
            try:
                utilization = await self._budget_service.get_utilization(
                    scope="workspace",
                    scope_id=self._workspace_id,
                )
                remaining_pct = 100.0 - utilization.get("utilization_pct", 0.0)
                if remaining_pct < 20.0:
                    logger.info("Budget below 20%%, switching to COST strategy")
                    effective_strategy = RoutingStrategy.COST
                elif remaining_pct < 50.0 and strategy == RoutingStrategy.BALANCED:
                    logger.info("Budget below 50%%, using COST strategy")
                    effective_strategy = RoutingStrategy.COST
            except Exception:
                logger.debug("Budget check failed, using original strategy", exc_info=True)

        return self._router.select_model(
            strategy=effective_strategy,
            required_capabilities=required_capabilities,
            max_cost_per_1k=max_cost_per_1k,
            max_latency_ms=max_latency_ms,
        )

    async def get_cached_response(
        self,
        model: str,
        messages: list[dict],
        temperature: float = 0.7,
    ) -> dict[str, Any] | None:
        """Check cache for a cached LLM response.

        Args:
            model: Model identifier.
            messages: Conversation messages.
            temperature: Sampling temperature.

        Returns:
            Cached response or None if cache miss.
        """
        if not self._cache_enabled:
            return None

        key = generate_cache_key(model, messages, temperature)
        return await self._cache.get(key)

    async def cache_response(
        self,
        model: str,
        messages: list[dict],
        temperature: float,
        response: dict[str, Any],
        ttl: int = 300,
    ) -> None:
        """Store an LLM response in cache.

        Args:
            model: Model identifier.
            messages: Conversation messages.
            temperature: Sampling temperature.
            response: LLM response to cache.
            ttl: Cache TTL in seconds.
        """
        if not self._cache_enabled:
            return

        key = generate_cache_key(model, messages, temperature)
        await self._cache.set(key, response, ttl)

    async def invalidate_model(self, model: str) -> int:
        """Invalidate all cached entries for a model.

        Args:
            model: Model identifier.

        Returns:
            Number of entries invalidated.
        """
        return await self._cache.invalidate(f"{model}:*")

    async def cache_stats(self) -> dict[str, Any]:
        """Return cache statistics."""
        return await self._cache.stats()
