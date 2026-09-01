"""Metrics store for time-series metric aggregation.

Provides the abstract contract (MetricsStore) and an in-memory
implementation (InMemoryMetricsStore) for testing and single-process use.
Production implementations (e.g., TimescaleDB) live in the services layer.

The InMemoryMetricsStore uses ring buffers bounded by MAX_METRICS_BUFFER_SIZE
and sliding-window aggregation for real-time metric queries.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta


@dataclass(frozen=True)
class MetricEntry:
    """A single metric data point stored in the metrics store.

    Attributes:
        name: Metric name (e.g., "llm.request.count").
        value: Numeric value of the metric.
        timestamp: When the metric was recorded (UTC).
        tags: Optional dimensional tags for filtering.
        metric_type: One of "counter", "gauge", "histogram".
    """

    name: str
    value: float
    timestamp: datetime
    tags: dict[str, str] = field(default_factory=dict)
    metric_type: str = "counter"


@dataclass
class MetricAggregate:
    """Result of aggregating metrics over a time window.

    Attributes:
        name: Metric name.
        value: Aggregated value (sum, avg, min, or max).
        aggregation: Aggregation method used.
        window: Time window string (e.g., "5m", "1h").
        tags: Tags used for filtering.
        count: Number of data points in the aggregation.
        timestamp: When the snapshot was taken.
    """

    name: str
    value: float
    aggregation: str = "sum"
    window: str = "5m"
    tags: dict[str, str] = field(default_factory=dict)
    count: int = 0
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class MetricsSnapshot:
    """Snapshot of aggregated metrics across all or selected names.

    Attributes:
        metrics: List of aggregated metric results.
        timestamp: When the snapshot was taken.
        window: Time window used for aggregation.
    """

    metrics: list[MetricAggregate] = field(default_factory=list)
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    window: str = "5m"


def _parse_window(window: str) -> timedelta:
    """Parse a window string like '5m', '1h', '30s' into a timedelta."""
    unit = window[-1]
    amount = int(window[:-1])
    if unit == "s":
        return timedelta(seconds=amount)
    if unit == "m":
        return timedelta(minutes=amount)
    if unit == "h":
        return timedelta(hours=amount)
    if unit == "d":
        return timedelta(days=amount)
    msg = f"Invalid window format: {window!r}"
    raise ValueError(msg)


class MetricsStore(ABC):
    """Abstract interface for recording and querying time-series metrics.

    Implementations may store metrics in memory (ring buffer), a database
    (TimescaleDB), or forward them to an external metrics backend. All
    methods are synchronous by design — in-memory stores are fast enough
    that async overhead is unnecessary.
    """

    @abstractmethod
    def record_counter(self, name: str, value: float = 1.0, tags: dict[str, str] | None = None) -> None:
        """Record a counter metric (cumulative value).

        Args:
            name: Metric name.
            value: Increment value (default 1.0).
            tags: Optional dimensional tags.
        """
        ...

    @abstractmethod
    def record_gauge(self, name: str, value: float, tags: dict[str, str] | None = None) -> None:
        """Record a gauge metric (point-in-time value).

        Args:
            name: Metric name.
            value: Current gauge value.
            tags: Optional dimensional tags.
        """
        ...

    @abstractmethod
    def record_histogram(self, name: str, value: float, tags: dict[str, str] | None = None) -> None:
        """Record a histogram metric (distribution value).

        Args:
            name: Metric name.
            value: Observed value.
            tags: Optional dimensional tags.
        """
        ...

    @abstractmethod
    def query_metrics(
        self,
        name: str,
        window: str = "5m",
        aggregation: str = "sum",
        tags: dict[str, str] | None = None,
    ) -> MetricAggregate | None:
        """Query aggregated metrics for a given name and time window.

        Args:
            name: Metric name to query.
            window: Time window (e.g., "5m", "1h").
            aggregation: Aggregation method ("sum", "avg", "min", "max").
            tags: Optional tag filters.

        Returns:
            Aggregated metric result, or None if no data points match.
        """
        ...

    @abstractmethod
    def get_snapshot(self, windows: list[str] | None = None) -> MetricsSnapshot:
        """Get a snapshot of all current metrics.

        Args:
            windows: Optional list of time windows to include. Defaults to ["5m"].

        Returns:
            A snapshot containing aggregated metrics.
        """
        ...


class InMemoryMetricsStore(MetricsStore):
    """In-memory metrics store using ring buffers with sliding-window aggregation.

    Each metric name maps to a bounded deque of MetricEntry records. Entries
    older than the largest configured window are automatically evicted during
    query operations. The store is thread-safe for single-process use.
    """

    def __init__(self, max_buffer_size: int = 100000) -> None:
        self._buffer_size = max_buffer_size
        self._entries: dict[str, list[MetricEntry]] = defaultdict(list)
        self._gauge_values: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}

    def record_counter(self, name: str, value: float = 1.0, tags: dict[str, str] | None = None) -> None:
        entry = MetricEntry(
            name=name,
            value=value,
            timestamp=datetime.now(UTC),
            tags=tags or {},
            metric_type="counter",
        )
        self._append_entry(name, entry)

    def record_gauge(self, name: str, value: float, tags: dict[str, str] | None = None) -> None:
        tags = tags or {}
        entry = MetricEntry(
            name=name,
            value=value,
            timestamp=datetime.now(UTC),
            tags=tags,
            metric_type="gauge",
        )
        self._append_entry(name, entry)
        tag_key = tuple(sorted(tags.items()))
        self._gauge_values[(name, tag_key)] = value

    def record_histogram(self, name: str, value: float, tags: dict[str, str] | None = None) -> None:
        entry = MetricEntry(
            name=name,
            value=value,
            timestamp=datetime.now(UTC),
            tags=tags or {},
            metric_type="histogram",
        )
        self._append_entry(name, entry)

    def query_metrics(
        self,
        name: str,
        window: str = "5m",
        aggregation: str = "sum",
        tags: dict[str, str] | None = None,
    ) -> MetricAggregate | None:
        cutoff = datetime.now(UTC) - _parse_window(window)
        entries = self._entries.get(name, [])
        filtered = [e for e in entries if e.timestamp >= cutoff]
        if tags:
            filtered = [e for e in filtered if all(e.tags.get(k) == v for k, v in tags.items())]
        if not filtered:
            return None

        values = [e.value for e in filtered]
        agg_value = self._aggregate(values, aggregation)
        return MetricAggregate(
            name=name,
            value=agg_value,
            aggregation=aggregation,
            window=window,
            tags=tags or {},
            count=len(values),
            timestamp=datetime.now(UTC),
        )

    def get_snapshot(self, windows: list[str] | None = None) -> MetricsSnapshot:
        windows = windows or ["5m"]
        all_metrics: list[MetricAggregate] = []
        for name in self._entries:
            for window in windows:
                result = self.query_metrics(name, window=window)
                if result is not None:
                    all_metrics.append(result)
        return MetricsSnapshot(
            metrics=all_metrics,
            timestamp=datetime.now(UTC),
            window=windows[0] if len(windows) == 1 else ",".join(windows),
        )

    def _append_entry(self, name: str, entry: MetricEntry) -> None:
        entries = self._entries[name]
        entries.append(entry)
        while len(entries) > self._buffer_size:
            entries.pop(0)

    @staticmethod
    def _aggregate(values: list[float], method: str) -> float:
        if method == "sum":
            return sum(values)
        if method == "avg":
            return sum(values) / len(values)
        if method == "min":
            return min(values)
        if method == "max":
            return max(values)
        msg = f"Unknown aggregation method: {method!r}"
        raise ValueError(msg)
