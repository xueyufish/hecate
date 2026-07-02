"""Unit tests for RetryPolicy, ErrorClassifier, and CircuitBreaker."""

from __future__ import annotations

import pytest

from hecate.services.validation.retry_policy import (
    CircuitBreaker,
    CircuitState,
    ErrorClassifier,
    RetryPolicy,
)


class TestErrorClassifier:
    """Tests for the ErrorClassifier class."""

    def test_retryable_timeout(self) -> None:
        """Test that timeout errors are retryable."""
        classifier = ErrorClassifier()
        assert classifier.is_retryable("Connection timeout") is True

    def test_retryable_network(self) -> None:
        """Test that network errors are retryable."""
        classifier = ErrorClassifier()
        assert classifier.is_retryable("Network error occurred") is True

    def test_retryable_rate_limit(self) -> None:
        """Test that rate limit errors are retryable."""
        classifier = ErrorClassifier()
        assert classifier.is_retryable("Rate limit exceeded (429)") is True

    def test_non_retryable_not_found(self) -> None:
        """Test that not found errors are not retryable."""
        classifier = ErrorClassifier()
        assert classifier.is_retryable("Resource not found") is False

    def test_non_retryable_permission(self) -> None:
        """Test that permission errors are not retryable."""
        classifier = ErrorClassifier()
        assert classifier.is_retryable("Permission denied") is False

    def test_non_retryable_invalid(self) -> None:
        """Test that invalid input errors are not retryable."""
        classifier = ErrorClassifier()
        assert classifier.is_retryable("Invalid input format") is False

    def test_unknown_retryable(self) -> None:
        """Test that unknown errors default to retryable."""
        classifier = ErrorClassifier()
        assert classifier.is_retryable("Something went wrong") is True


class TestCircuitBreaker:
    """Tests for the CircuitBreaker class."""

    def test_initial_state_closed(self) -> None:
        """Test that circuit breaker starts closed."""
        cb = CircuitBreaker()
        assert cb.state == CircuitState.CLOSED
        assert cb.allow_request() is True

    def test_opens_after_threshold(self) -> None:
        """Test that circuit opens after failure threshold."""
        cb = CircuitBreaker(failure_threshold=3)

        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.CLOSED

        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        assert cb.allow_request() is False

    def test_resets_on_success(self) -> None:
        """Test that circuit resets on success."""
        cb = CircuitBreaker(failure_threshold=3)

        cb.record_failure()
        cb.record_failure()
        cb.record_success()

        assert cb.state == CircuitState.CLOSED
        assert cb.allow_request() is True

    def test_half_open_after_timeout(self) -> None:
        """Test that circuit goes half-open after recovery timeout."""
        import time

        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.1)

        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

        time.sleep(0.2)
        assert cb.state == CircuitState.HALF_OPEN
        assert cb.allow_request() is True


class TestRetryPolicy:
    """Tests for the RetryPolicy class."""

    @pytest.mark.asyncio
    async def test_success_on_first_try(self) -> None:
        """Test successful execution on first try."""
        policy = RetryPolicy(max_retries=3)

        async def success_func():
            return "result"

        result = await policy.execute(success_func)

        assert result.success is True
        assert result.result == "result"
        assert result.attempts == 1

    @pytest.mark.asyncio
    async def test_success_after_retry(self) -> None:
        """Test successful execution after retry."""
        policy = RetryPolicy(max_retries=3, base_delay=0.01)
        call_count = 0

        async def failing_then_success():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise TimeoutError("Timeout")
            return "result"

        result = await policy.execute(failing_then_success)

        assert result.success is True
        assert result.attempts == 3

    @pytest.mark.asyncio
    async def test_failure_after_max_retries(self) -> None:
        """Test failure after max retries exceeded."""
        policy = RetryPolicy(max_retries=2, base_delay=0.01)

        async def always_fail():
            raise TimeoutError("Timeout")

        result = await policy.execute(always_fail)

        assert result.success is False
        assert result.attempts == 3

    @pytest.mark.asyncio
    async def test_non_retryable_error(self) -> None:
        """Test that non-retryable errors fail immediately."""
        policy = RetryPolicy(max_retries=3)

        async def non_retryable():
            raise ValueError("Invalid input")

        result = await policy.execute(non_retryable)

        assert result.success is False
        assert result.attempts == 1

    @pytest.mark.asyncio
    async def test_circuit_breaker_blocks(self) -> None:
        """Test that circuit breaker blocks requests when open."""
        cb = CircuitBreaker(failure_threshold=1)
        cb.record_failure()

        policy = RetryPolicy(circuit_breaker=cb)

        async def success_func():
            return "result"

        result = await policy.execute(success_func)

        assert result.success is False
        assert "Circuit breaker" in result.error
