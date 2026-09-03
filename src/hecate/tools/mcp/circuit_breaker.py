"""Circuit breaker for MCP server connections.

Implements a 3-state circuit breaker (CLOSED → OPEN → HALF_OPEN) to prevent
cascade failures from unhealthy MCP servers.
"""

from __future__ import annotations

import logging
import time
from enum import StrEnum

logger = logging.getLogger(__name__)


class CircuitState(StrEnum):
    """Circuit breaker states."""

    CLOSED = "closed"
    """Normal operation — requests are allowed."""

    OPEN = "open"
    """Circuit tripped — all requests are rejected."""

    HALF_OPEN = "half_open"
    """Recovery probe — one request allowed to test server health."""


class CircuitBreaker:
    """Per-MCP-server circuit breaker with configurable thresholds.

    Args:
        failure_threshold: Number of consecutive failures before opening circuit.
        recovery_timeout: Seconds to wait before allowing a half-open probe.
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: int = 30,
    ) -> None:
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._last_failure_time: float = 0.0
        self._half_open_probe_sent = False

    @property
    def state(self) -> CircuitState:
        """Current circuit state, with automatic transition from OPEN to HALF_OPEN."""
        if self._state == CircuitState.OPEN:
            elapsed = time.monotonic() - self._last_failure_time
            if elapsed >= self._recovery_timeout:
                logger.info("Circuit breaker transitioning to HALF_OPEN after %.1fs", elapsed)
                self._state = CircuitState.HALF_OPEN
                self._half_open_probe_sent = False
        return self._state

    def can_proceed(self) -> bool:
        """Check if a request is allowed through the circuit breaker.

        Returns:
            True if the request should proceed, False if rejected.
        """
        current = self.state  # triggers auto-transition check
        if current == CircuitState.CLOSED:
            return True
        if current == CircuitState.OPEN:
            return False
        # HALF_OPEN: allow one probe request
        if not self._half_open_probe_sent:
            self._half_open_probe_sent = True
            return True
        return False

    def record_success(self) -> None:
        """Record a successful request — resets failure count and closes circuit."""
        if self._state == CircuitState.HALF_OPEN:
            logger.info("Circuit breaker CLOSED after successful half-open probe")
        self._consecutive_failures = 0
        self._state = CircuitState.CLOSED
        self._half_open_probe_sent = False

    def record_failure(self) -> None:
        """Record a failed request — increments failure count, may open circuit."""
        self._consecutive_failures += 1
        self._last_failure_time = time.monotonic()

        if self._state == CircuitState.HALF_OPEN:
            logger.warning("Circuit breaker REOPENED — half-open probe failed")
            self._state = CircuitState.OPEN
            self._half_open_probe_sent = False
            return

        if self._consecutive_failures >= self._failure_threshold:
            logger.warning(
                "Circuit breaker OPENED after %d consecutive failures",
                self._consecutive_failures,
            )
            self._state = CircuitState.OPEN
            self._half_open_probe_sent = False

    def reset(self) -> None:
        """Manually reset the circuit breaker to CLOSED state."""
        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._half_open_probe_sent = False
