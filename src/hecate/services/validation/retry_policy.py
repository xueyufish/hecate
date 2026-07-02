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

from hecate.engine.errors import (
    ChannelError,
    EngineError,
    ErrorCategory,
    SecurityError,
)
from hecate.engine.retry import RetryStrategy

# Provider SDK exception types — imported conditionally to avoid hard dependency
_PROVIDER_EXCEPTIONS: dict[type, ErrorCategory] = {}
try:
    import openai

    _PROVIDER_EXCEPTIONS = {
        openai.RateLimitError: ErrorCategory.LLM_RATE_LIMIT,
        openai.AuthenticationError: ErrorCategory.LLM_AUTH,
        openai.APITimeoutError: ErrorCategory.LLM_TIMEOUT,
        openai.APIConnectionError: ErrorCategory.LLM_TIMEOUT,
        openai.InternalServerError: ErrorCategory.LLM_RATE_LIMIT,
    }
except ImportError:
    pass

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
    """Classifies errors as retryable or non-retryable.

    Supports two classification modes:
    1. isinstance-based: for typed exceptions (HecateError subtypes, provider SDK errors)
    2. string-based: fallback for unrecognized exceptions using keyword matching
    """

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

    _HECATE_CATEGORY_MAP: dict[type, ErrorCategory] = {
        EngineError: ErrorCategory.ENGINE,
        ChannelError: ErrorCategory.CHANNEL,
        SecurityError: ErrorCategory.SECURITY,
    }

    _RETRYABLE_CATEGORIES = frozenset(
        {
            ErrorCategory.LLM_RATE_LIMIT,
            ErrorCategory.LLM_TIMEOUT,
            ErrorCategory.TOOL_TIMEOUT,
        }
    )

    def classify(self, error: Exception) -> ErrorCategory:
        """Classify an exception into an ErrorCategory.

        Checks in order:
        1. HecateError subtypes (EngineError, ChannelError, SecurityError)
        2. Provider SDK exceptions (openai.RateLimitError, etc.)
        3. String-based keyword fallback

        Args:
            error: The exception to classify.

        Returns:
            ErrorCategory for the exception.
        """
        # 1. HecateError subtypes
        for exc_type, category in self._HECATE_CATEGORY_MAP.items():
            if isinstance(error, exc_type):
                return category

        # 2. Provider SDK exceptions
        for exc_type, category in _PROVIDER_EXCEPTIONS.items():
            if isinstance(error, exc_type):
                return category

        # 3. String-based fallback
        return self._classify_by_string(str(error))

    def _classify_by_string(self, error_str: str) -> ErrorCategory:
        """Classify by keyword matching on error message string."""
        error_lower = error_str.lower()

        if "rate limit" in error_lower or "429" in error_lower:
            return ErrorCategory.LLM_RATE_LIMIT
        if "unauthorized" in error_lower or "401" in error_lower:
            return ErrorCategory.LLM_AUTH
        if "timeout" in error_lower:
            return ErrorCategory.LLM_TIMEOUT
        if "not found" in error_lower or "404" in error_lower:
            return ErrorCategory.TOOL_NOT_FOUND
        if "forbidden" in error_lower or "403" in error_lower:
            return ErrorCategory.LLM_AUTH

        return ErrorCategory.UNKNOWN

    def is_retryable_exception(self, error: Exception) -> bool:
        """Classify if an exception is retryable using isinstance checks.

        Args:
            error: The exception to check.

        Returns:
            True if the error is retryable.
        """
        category = self.classify(error)
        if category in self._RETRYABLE_CATEGORIES:
            return True
        if category != ErrorCategory.UNKNOWN:
            return False
        # Fallback to string matching for unknown errors
        return self.is_retryable(str(error))

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


class DefaultRetryStrategy(RetryStrategy):
    """Engine-level retry strategy using ErrorClassifier for decisions.

    Bridges the existing ErrorClassifier (from 1.3.5g) with the engine's
    RetryStrategy ABC. Uses exponential backoff with jitter, identical to
    RetryPolicy's delay calculation.

    Configurable parameters:
        max_attempts: Maximum retry attempts (0 = no retry, 3 = up to 4 total calls).
        base_delay: Initial backoff delay in seconds.
        max_delay: Upper bound on backoff delay in seconds.
        multiplier: Exponential growth factor for backoff.
    """

    def __init__(
        self,
        max_attempts: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 30.0,
        multiplier: float = 2.0,
        error_classifier: ErrorClassifier | None = None,
    ) -> None:
        self.max_attempts = max_attempts
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.multiplier = multiplier
        self._classifier = error_classifier or ErrorClassifier()

    def should_retry(self, error: Exception, attempt: int) -> bool:
        """Check if the error is retryable and attempts remain."""
        if attempt >= self.max_attempts:
            return False
        return self._classifier.is_retryable_exception(error)

    def get_backoff(self, attempt: int) -> float:
        """Calculate exponential backoff with jitter (50%-150% of base)."""
        delay = min(self.base_delay * (self.multiplier**attempt), self.max_delay)
        return delay * (0.5 + random.random())  # noqa: S311

    def with_config(self, **overrides: Any) -> DefaultRetryStrategy:
        """Create a new strategy with merged configuration overrides."""
        return DefaultRetryStrategy(
            max_attempts=overrides.get("max_attempts", self.max_attempts),
            base_delay=overrides.get("base_delay", self.base_delay),
            max_delay=overrides.get("max_delay", self.max_delay),
            multiplier=overrides.get("multiplier", self.multiplier),
            error_classifier=self._classifier,
        )
