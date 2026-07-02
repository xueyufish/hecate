"""Prometheus-compatible metrics collector.

Collects and exposes metrics for:
- Request count, latency, error rate
- Token usage (input, output, cost)
- Agent and session metrics
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class RequestMetrics:
    """Metrics for a single request."""

    endpoint: str
    method: str
    status_code: int
    latency_ms: float
    timestamp: float = field(default_factory=time.time)


@dataclass
class TokenMetrics:
    """Metrics for token usage."""

    session_id: str
    agent_id: str
    input_tokens: int
    output_tokens: int
    cost_usd: float = 0.0
    model: str = ""
    timestamp: float = field(default_factory=time.time)


class MetricsCollector:
    """Collects and aggregates metrics for observability.

    Provides:
    - Request metrics (count, latency, error rate)
    - Token metrics (input, output, cost)
    - Prometheus-compatible export format
    """

    def __init__(self) -> None:
        """Initialize the metrics collector."""
        self._request_metrics: list[RequestMetrics] = []
        self._token_metrics: list[TokenMetrics] = []
        self._counters: dict[str, int] = {}
        self._gauges: dict[str, float] = {}

    def record_request(
        self,
        endpoint: str,
        method: str,
        status_code: int,
        latency_ms: float,
    ) -> None:
        """Record a request metric.

        Args:
            endpoint: API endpoint path.
            method: HTTP method.
            status_code: Response status code.
            latency_ms: Request latency in milliseconds.
        """
        metric = RequestMetrics(
            endpoint=endpoint,
            method=method,
            status_code=status_code,
            latency_ms=latency_ms,
        )
        self._request_metrics.append(metric)

        # Update counters
        key = f"requests_{method}_{endpoint}"
        self._counters[key] = self._counters.get(key, 0) + 1

        if status_code >= 400:
            error_key = f"errors_{method}_{endpoint}"
            self._counters[error_key] = self._counters.get(error_key, 0) + 1

    def record_token_usage(
        self,
        session_id: str,
        agent_id: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float = 0.0,
        model: str = "",
    ) -> None:
        """Record token usage metric.

        Args:
            session_id: Session identifier.
            agent_id: Agent identifier.
            input_tokens: Number of input tokens.
            output_tokens: Number of output tokens.
            cost_usd: Cost in USD.
            model: Model name.
        """
        metric = TokenMetrics(
            session_id=session_id,
            agent_id=agent_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
            model=model,
        )
        self._token_metrics.append(metric)

        # Update counters
        self._counters["total_input_tokens"] = self._counters.get("total_input_tokens", 0) + input_tokens
        self._counters["total_output_tokens"] = self._counters.get("total_output_tokens", 0) + output_tokens

    def increment_counter(self, name: str, value: int = 1) -> None:
        """Increment a named counter.

        Args:
            name: Counter name.
            value: Increment value.
        """
        self._counters[name] = self._counters.get(name, 0) + value

    def set_gauge(self, name: str, value: float) -> None:
        """Set a named gauge value.

        Args:
            name: Gauge name.
            value: Gauge value.
        """
        self._gauges[name] = value

    def get_request_count(self, endpoint: str | None = None) -> int:
        """Get total request count.

        Args:
            endpoint: Optional endpoint filter.

        Returns:
            Request count.
        """
        if endpoint:
            return sum(1 for m in self._request_metrics if m.endpoint == endpoint)
        return len(self._request_metrics)

    def get_error_rate(self, endpoint: str | None = None) -> float:
        """Get error rate (0.0 to 1.0).

        Args:
            endpoint: Optional endpoint filter.

        Returns:
            Error rate.
        """
        metrics = self._request_metrics
        if endpoint:
            metrics = [m for m in metrics if m.endpoint == endpoint]

        if not metrics:
            return 0.0

        errors = sum(1 for m in metrics if m.status_code >= 400)
        return errors / len(metrics)

    def get_average_latency(self, endpoint: str | None = None) -> float:
        """Get average latency in milliseconds.

        Args:
            endpoint: Optional endpoint filter.

        Returns:
            Average latency.
        """
        metrics = self._request_metrics
        if endpoint:
            metrics = [m for m in metrics if m.endpoint == endpoint]

        if not metrics:
            return 0.0

        total_latency = sum(m.latency_ms for m in metrics)
        return total_latency / len(metrics)

    def get_total_cost(self, agent_id: str | None = None) -> float:
        """Get total cost in USD.

        Args:
            agent_id: Optional agent filter.

        Returns:
            Total cost.
        """
        metrics = self._token_metrics
        if agent_id:
            metrics = [m for m in metrics if m.agent_id == agent_id]

        return sum(m.cost_usd for m in metrics)

    def export_prometheus(self) -> str:
        """Export metrics in Prometheus format.

        Returns:
            Prometheus text format string.
        """
        lines: list[str] = []

        # Counters
        for name, value in self._counters.items():
            lines.append(f"# TYPE {name} counter")
            lines.append(f"{name} {value}")

        # Gauges
        for name, value in self._gauges.items():
            lines.append(f"# TYPE {name} gauge")
            lines.append(f"{name} {value}")

        return "\n".join(lines)
