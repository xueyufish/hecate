"""Retry policy with exponential backoff and circuit breaker.

Provides configurable retry strategies for tool execution failures:
- Exponential backoff with jitter
- Error classification (retryable vs non-retryable)
- Circuit breaker pattern (open/half-open/closed)
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)


class CircuitState(StrEnum):
    """Circuit breaker states."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class RetryResult:
    """Result of a retry attempt."""

    success: bool
    result: Any = None
    error: str | None = None
    attempts: int = 0
    circuit_state: str = "closed"


class ErrorClassifier:
    """Classifies errors as retryable or non-retryable."""

    RETRYABLE_KEYWORDS = [
        "timeout",
        "connection",
        "network",
        "temporary",
        "rate limit",
        "429",
        "503",
        "504",
    ]

    NON_RETRYABLE_KEYWORDS = [
        "invalid",
        "not found",
        "permission",
        "unauthorized",
        "forbidden",
        "400",
        "401",
        "403",
        "404",
    ]

    def is_retryable(self, error: str) -> bool:
        """Classify if an error is retryable.

        Args:
            error: Error message string.

        Returns:
            True if the error is retryable.
        """
        error_lower = error.lower()

        for keyword in self.NON_RETRYABLE_KEYWORDS:
            if keyword in error_lower:
                return False

        for keyword in self.RETRYABLE_KEYWORDS:
            if keyword in error_lower:
                return True

        return True


class CircuitBreaker:
    """Circuit breaker pattern for tool execution.

    States:
    - CLOSED: Normal operation, requests pass through
    - OPEN: Too many failures, requests fail immediately
    - HALF_OPEN: Testing if service recovered
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time: float = 0

    @property
    def state(self) -> CircuitState:
        """Get current circuit state."""
        if self._state == CircuitState.OPEN and time.time() - self._last_failure_time > self.recovery_timeout:
            return CircuitState.HALF_OPEN
        return self._state

    def record_success(self) -> None:
        """Record a successful execution."""
        self._failure_count = 0
        self._state = CircuitState.CLOSED

    def record_failure(self) -> None:
        """Record a failed execution."""
        self._failure_count += 1
        self._last_failure_time = time.time()

        if self._failure_count >= self.failure_threshold:
            self._state = CircuitState.OPEN
            logger.warning(f"Circuit breaker opened after {self._failure_count} failures")

    def allow_request(self) -> bool:
        """Check if a request should be allowed.

        Returns:
            True if request should proceed.
        """
        state = self.state
        if state == CircuitState.CLOSED:
            return True
        return state == CircuitState.HALF_OPEN


class RetryPolicy:
    """Retry policy with exponential backoff and circuit breaker."""

    def __init__(
        self,
        max_retries: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 30.0,
        multiplier: float = 2.0,
        circuit_breaker: CircuitBreaker | None = None,
        error_classifier: ErrorClassifier | None = None,
    ) -> None:
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.multiplier = multiplier
        self.circuit_breaker = circuit_breaker or CircuitBreaker()
        self.error_classifier = error_classifier or ErrorClassifier()

    async def execute(
        self,
        func: Any,
        *args: Any,
        **kwargs: Any,
    ) -> RetryResult:
        """Execute a function with retry logic.

        Args:
            func: Async function to execute.
            *args: Positional arguments.
            **kwargs: Keyword arguments.

        Returns:
            RetryResult with success status and result/error.
        """
        if not self.circuit_breaker.allow_request():
            return RetryResult(
                success=False,
                error="Circuit breaker is open",
                attempts=0,
                circuit_state=self.circuit_breaker.state.value,
            )

        last_error: str | None = None
        for attempt in range(self.max_retries + 1):
            try:
                result = await func(*args, **kwargs)
                self.circuit_breaker.record_success()
                return RetryResult(
                    success=True,
                    result=result,
                    attempts=attempt + 1,
                    circuit_state=self.circuit_breaker.state.value,
                )
            except Exception as e:
                last_error = str(e)

                if not self.error_classifier.is_retryable(last_error):
                    self.circuit_breaker.record_failure()
                    return RetryResult(
                        success=False,
                        error=last_error,
                        attempts=attempt + 1,
                        circuit_state=self.circuit_breaker.state.value,
                    )

                if attempt < self.max_retries:
                    delay = min(
                        self.base_delay * (self.multiplier**attempt),
                        self.max_delay,
                    )
                    delay *= 0.5 + random.random()  # noqa: S311
                    logger.debug(f"Retry {attempt + 1}/{self.max_retries} after {delay:.1f}s")
                    await asyncio.sleep(delay)

        self.circuit_breaker.record_failure()
        return RetryResult(
            success=False,
            error=last_error,
            attempts=self.max_retries + 1,
            circuit_state=self.circuit_breaker.state.value,
        )
