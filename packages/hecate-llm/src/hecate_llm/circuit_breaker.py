"""Per-prefix circuit breaker for LLM model routing.

Manages circuit breakers keyed by routing prefix extracted from LiteLLM model
names (e.g., "openai" from "openai/gpt-4o"). Each prefix gets an independent
breaker with CLOSED → OPEN → HALF_OPEN state transitions.

Reuses CircuitBreaker from services/validation/retry_policy.py.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import TYPE_CHECKING

from hecate.services.validation.retry_policy import CircuitBreaker, CircuitState

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

# Static mapping for short model names without a slash prefix.
_SHORT_NAME_PREFIXES: dict[str, str] = {
    "gpt": "openai",
    "o1": "openai",
    "o3": "openai",
    "chatgpt": "openai",
    "claude": "anthropic",
    "gemini": "gemini",
    "deepseek": "deepseek",
    "qwen": "alibaba",
    "llama": "meta",
    "mistral": "mistral",
    "command": "cohere",
}


def _extract_prefix(model: str) -> str:
    """Extract the routing prefix from a LiteLLM model name.

    Args:
        model: Model name (e.g., "openai/gpt-4o", "gpt-4o", "claude-3.5-sonnet").

    Returns:
        Routing prefix (e.g., "openai", "anthropic"). Unmapped names return "unknown".
    """
    if "/" in model:
        return model.split("/", 1)[0]

    for prefix, provider in _SHORT_NAME_PREFIXES.items():
        if model.startswith(prefix):
            return provider

    return "unknown"


class CircuitBreakerManager:
    """Per-prefix circuit breaker manager for LLM calls.

    Maintains one CircuitBreaker instance per routing prefix, created lazily
    on first use. Thread-safe for concurrent async access.

    Args:
        failure_threshold: Consecutive failures before opening a breaker.
        recovery_timeout: Seconds before OPEN breaker transitions to HALF_OPEN.
        on_state_change: Optional callback invoked on state transitions.
            Signature: (prefix, old_state, new_state) -> None.
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
        on_state_change: Callable[[str, CircuitState, CircuitState], None] | None = None,
    ) -> None:
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._on_state_change = on_state_change
        self._breakers: dict[str, CircuitBreaker] = {}
        self._probe_locks: dict[str, threading.Lock] = {}
        self._creation_lock = asyncio.Lock()

    def get_breaker(self, prefix: str) -> CircuitBreaker:
        """Get or create a circuit breaker for the given prefix.

        Thread-safe: concurrent calls for the same prefix will not create
        duplicate breakers.

        Args:
            prefix: Routing prefix (e.g., "openai").

        Returns:
            CircuitBreaker instance for the prefix.
        """
        if prefix not in self._breakers:
            self._breakers[prefix] = CircuitBreaker(
                failure_threshold=self._failure_threshold,
                recovery_timeout=self._recovery_timeout,
            )
            self._probe_locks[prefix] = threading.Lock()
        return self._breakers[prefix]

    async def get_breaker_safe(self, prefix: str) -> CircuitBreaker:
        """Get or create a breaker protected by an async lock.

        Use in async contexts where concurrent lazy creation is possible.
        """
        if prefix in self._breakers:
            return self._breakers[prefix]

        async with self._creation_lock:
            # Double-check after acquiring lock
            if prefix not in self._breakers:
                self._breakers[prefix] = CircuitBreaker(
                    failure_threshold=self._failure_threshold,
                    recovery_timeout=self._recovery_timeout,
                )
                self._probe_locks[prefix] = threading.Lock()
            return self._breakers[prefix]

    def is_open(self, model: str) -> bool:
        """Check if the breaker for a model's prefix is in OPEN state.

        Args:
            model: LiteLLM model name.

        Returns:
            True if the prefix breaker is OPEN (requests should be rejected).
        """
        prefix = _extract_prefix(model)
        breaker = self.get_breaker(prefix)
        return breaker.state == CircuitState.OPEN and not breaker.allow_request()

    def record_success(self, model: str) -> None:
        """Record a successful call for the model's prefix breaker.

        Args:
            model: LiteLLM model name.
        """
        prefix = _extract_prefix(model)
        breaker = self.get_breaker(prefix)
        old_state = breaker.state
        breaker.record_success()
        new_state = breaker.state
        self._notify_state_change(prefix, old_state, new_state)

    def record_failure(self, model: str) -> None:
        """Record a failed call for the model's prefix breaker.

        Args:
            model: LiteLLM model name.
        """
        prefix = _extract_prefix(model)
        breaker = self.get_breaker(prefix)
        old_state = breaker.state
        breaker.record_failure()
        new_state = breaker.state
        self._notify_state_change(prefix, old_state, new_state)

    def is_half_open(self, model: str) -> bool:
        """Check if the breaker for a model's prefix is in HALF_OPEN state.

        Args:
            model: LiteLLM model name.

        Returns:
            True if the prefix breaker is HALF_OPEN.
        """
        prefix = _extract_prefix(model)
        breaker = self.get_breaker(prefix)
        return breaker.state == CircuitState.HALF_OPEN

    def acquire_probe(self, model: str) -> bool:
        """Try to acquire the probe lock for HALF_OPEN state.

        Only one concurrent request may probe a prefix. Others should
        skip to fallback.

        Args:
            model: LiteLLM model name.

        Returns:
            True if the probe lock was acquired (caller should proceed).
            False if already held (caller should skip to fallback).
        """
        prefix = _extract_prefix(model)
        lock = self._probe_locks.get(prefix)
        if lock is None:
            return False
        return lock.acquire(blocking=False)

    def release_probe(self, model: str) -> None:
        """Release the probe lock after a HALF_OPEN probe completes.

        Args:
            model: LiteLLM model name.
        """
        prefix = _extract_prefix(model)
        lock = self._probe_locks.get(prefix)
        if lock is not None and lock.locked():
            lock.release()

    def _notify_state_change(
        self,
        prefix: str,
        old_state: CircuitState,
        new_state: CircuitState,
    ) -> None:
        """Invoke the on_state_change callback if state actually changed."""
        if old_state != new_state and self._on_state_change is not None:
            try:
                self._on_state_change(prefix, old_state, new_state)
            except Exception:
                logger.warning("on_state_change callback failed", exc_info=True)
