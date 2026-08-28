"""Sandbox execution services for isolated Docker container tool runs."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hecate.services.sandbox.pool import SandboxPool

logger = logging.getLogger(__name__)

_pool: SandboxPool | None = None
_pool_initialized: bool = False


def get_sandbox_pool() -> SandboxPool | None:
    """Return the singleton SandboxPool instance, or None if disabled.

    On first call with ``SANDBOX_POOL_ENABLED=true``, creates and returns
    the pool. Subsequent calls return the same instance. If disabled,
    always returns None.
    """
    global _pool, _pool_initialized

    if _pool_initialized:
        return _pool

    _pool_initialized = True

    from hecate.core.config import settings

    if not settings.SANDBOX_POOL_ENABLED:
        logger.debug("Sandbox container pool disabled")
        return None

    from hecate.services.sandbox.executor import SandboxExecutor
    from hecate.services.sandbox.pool import SandboxPool

    strategy = settings.SANDBOX_POOL_EXHAUSTION_STRATEGY
    if strategy not in ("wait", "temporary"):
        logger.warning(f"Invalid SANDBOX_POOL_EXHAUSTION_STRATEGY={strategy!r}, falling back to 'wait'")
        strategy = "wait"

    executor = SandboxExecutor()
    _pool = SandboxPool(
        executor=executor,
        pool_size=settings.SANDBOX_POOL_SIZE,
        max_uses=settings.SANDBOX_MAX_USES,
        busy_ttl=settings.SANDBOX_POOL_BUSY_TTL,
        idle_timeout=settings.SANDBOX_POOL_IDLE_TIMEOUT,
        acquire_timeout=settings.SANDBOX_POOL_ACQUIRE_TIMEOUT,
        exhaustion_strategy=strategy,
    )
    logger.info(
        "Sandbox container pool created: size=%d, max_uses=%d, strategy=%s",
        settings.SANDBOX_POOL_SIZE,
        settings.SANDBOX_MAX_USES,
        strategy,
    )
    return _pool


def _reset_pool_for_testing() -> None:
    """Reset the singleton pool state. For testing only."""
    global _pool, _pool_initialized
    _pool = None
    _pool_initialized = False


__all__ = ["get_sandbox_pool", "_reset_pool_for_testing"]
