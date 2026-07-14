"""Tests for MCP CircuitBreaker."""

from __future__ import annotations

import time

from hecate.services.mcp.circuit_breaker import CircuitBreaker, CircuitState


def test_initial_state_closed() -> None:
    """Circuit breaker starts in CLOSED state."""
    cb = CircuitBreaker(failure_threshold=5, recovery_timeout=30)
    assert cb.state == CircuitState.CLOSED
    assert cb.can_proceed() is True


def test_closed_to_open_on_threshold() -> None:
    """Circuit opens after consecutive failures reach threshold."""
    cb = CircuitBreaker(failure_threshold=3, recovery_timeout=30)
    cb.record_failure()
    cb.record_failure()
    assert cb.state == CircuitState.CLOSED  # Below threshold
    cb.record_failure()
    assert cb.state == CircuitState.OPEN
    assert cb.can_proceed() is False


def test_open_to_half_open_after_timeout() -> None:
    """Circuit transitions to HALF_OPEN after recovery timeout."""
    cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.1)
    cb.record_failure()
    cb.record_failure()
    assert cb.state == CircuitState.OPEN

    time.sleep(0.15)
    assert cb.state == CircuitState.HALF_OPEN


def test_half_open_allows_one_probe() -> None:
    """HALF_OPEN allows exactly one probe request."""
    cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.01)
    cb.record_failure()
    time.sleep(0.02)

    assert cb.state == CircuitState.HALF_OPEN
    assert cb.can_proceed() is True  # First probe allowed
    assert cb.can_proceed() is False  # Second probe rejected


def test_half_open_success_closes() -> None:
    """Successful half-open probe closes the circuit."""
    cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.01)
    cb.record_failure()
    time.sleep(0.02)

    cb.record_success()
    assert cb.state == CircuitState.CLOSED
    assert cb.can_proceed() is True


def test_half_open_failure_reopens() -> None:
    """Failed half-open probe reopens the circuit."""
    cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.01)
    cb.record_failure()
    time.sleep(0.02)

    cb.record_failure()
    assert cb.state == CircuitState.OPEN


def test_success_resets_failure_count() -> None:
    """Success resets consecutive failure count."""
    cb = CircuitBreaker(failure_threshold=5, recovery_timeout=30)
    cb.record_failure()
    cb.record_failure()
    cb.record_success()
    cb.record_failure()
    cb.record_failure()
    # Should not be open — success reset the count
    assert cb.state == CircuitState.CLOSED


def test_manual_reset() -> None:
    """Manual reset returns circuit to CLOSED."""
    cb = CircuitBreaker(failure_threshold=1, recovery_timeout=30)
    cb.record_failure()
    assert cb.state == CircuitState.OPEN

    cb.reset()
    assert cb.state == CircuitState.CLOSED
    assert cb.can_proceed() is True
