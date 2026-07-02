"""Tests for the MetricsStore ABC and InMemoryMetricsStore."""

from __future__ import annotations

import time
from datetime import timedelta

from hecate.engine.metrics_store import (
    InMemoryMetricsStore,
    MetricsSnapshot,
    MetricsStore,
    _parse_window,
)


class TestMetricsStoreABC:
    """Verify MetricsStore cannot be instantiated directly."""

    def test_cannot_instantiate_abc(self) -> None:
        """MetricsStore is abstract and should reject direct instantiation."""
        import pytest

        with pytest.raises(TypeError, match="abstract method"):
            MetricsStore()  # type: ignore[abstract]


class TestParseWindow:
    """Tests for the _parse_window helper."""

    def test_seconds(self) -> None:
        assert _parse_window("30s") == timedelta(seconds=30)

    def test_minutes(self) -> None:
        assert _parse_window("5m") == timedelta(minutes=5)

    def test_hours(self) -> None:
        assert _parse_window("1h") == timedelta(hours=1)

    def test_days(self) -> None:
        assert _parse_window("7d") == timedelta(days=7)

    def test_invalid_unit(self) -> None:
        import pytest

        with pytest.raises(ValueError, match="Invalid window format"):
            _parse_window("5x")


class TestInMemoryMetricsStore:
    """Tests for the InMemoryMetricsStore ring-buffer implementation."""

    def test_record_counter(self) -> None:
        store = InMemoryMetricsStore()
        store.record_counter("requests")
        store.record_counter("requests", value=3.0)
        result = store.query_metrics("requests")
        assert result is not None
        assert result.value == 4.0
        assert result.count == 2

    def test_record_counter_with_tags(self) -> None:
        store = InMemoryMetricsStore()
        store.record_counter("requests", tags={"method": "GET"})
        store.record_counter("requests", tags={"method": "POST"})
        store.record_counter("requests", tags={"method": "GET"})

        get_result = store.query_metrics("requests", tags={"method": "GET"})
        assert get_result is not None
        assert get_result.value == 2.0

        post_result = store.query_metrics("requests", tags={"method": "POST"})
        assert post_result is not None
        assert post_result.value == 1.0

    def test_record_gauge(self) -> None:
        store = InMemoryMetricsStore()
        store.record_gauge("cpu_usage", 45.0)
        store.record_gauge("cpu_usage", 52.0)
        result = store.query_metrics("cpu_usage", aggregation="avg")
        assert result is not None
        assert result.value == 48.5
        assert result.count == 2

    def test_record_histogram(self) -> None:
        store = InMemoryMetricsStore()
        for v in [10.0, 20.0, 30.0, 40.0, 50.0]:
            store.record_histogram("latency_ms", v)
        result = store.query_metrics("latency_ms", aggregation="avg")
        assert result is not None
        assert result.value == 30.0
        assert result.count == 5

    def test_query_metrics_no_data(self) -> None:
        store = InMemoryMetricsStore()
        result = store.query_metrics("nonexistent")
        assert result is None

    def test_query_metrics_aggregation_sum(self) -> None:
        store = InMemoryMetricsStore()
        store.record_counter("errors", value=2.0)
        store.record_counter("errors", value=3.0)
        result = store.query_metrics("errors", aggregation="sum")
        assert result is not None
        assert result.value == 5.0

    def test_query_metrics_aggregation_min_max(self) -> None:
        store = InMemoryMetricsStore()
        for v in [10.0, 50.0, 30.0]:
            store.record_histogram("latency", v)

        min_result = store.query_metrics("latency", aggregation="min")
        assert min_result is not None
        assert min_result.value == 10.0

        max_result = store.query_metrics("latency", aggregation="max")
        assert max_result is not None
        assert max_result.value == 50.0

    def test_query_metrics_invalid_aggregation(self) -> None:
        import pytest

        store = InMemoryMetricsStore()
        store.record_counter("test")
        with pytest.raises(ValueError, match="Unknown aggregation method"):
            store.query_metrics("test", aggregation="median")

    def test_get_snapshot(self) -> None:
        store = InMemoryMetricsStore()
        store.record_counter("requests")
        store.record_gauge("cpu", 80.0)
        snapshot = store.get_snapshot()
        assert isinstance(snapshot, MetricsSnapshot)
        assert len(snapshot.metrics) == 2
        names = {m.name for m in snapshot.metrics}
        assert names == {"requests", "cpu"}

    def test_get_snapshot_multiple_windows(self) -> None:
        store = InMemoryMetricsStore()
        store.record_counter("requests")
        snapshot = store.get_snapshot(windows=["1m", "5m"])
        assert len(snapshot.metrics) == 2

    def test_ring_buffer_eviction(self) -> None:
        store = InMemoryMetricsStore(max_buffer_size=3)
        for i in range(5):
            store.record_counter("test", value=float(i))
        result = store.query_metrics("test")
        assert result is not None
        assert result.count == 3
        assert result.value == 3.0 + 4.0 + 5.0 - 2.0 - 1.0  # values 2,3,4

    def test_time_window_filtering(self) -> None:
        store = InMemoryMetricsStore()
        store.record_counter("old_metric")
        time.sleep(0.1)
        store.record_counter("recent_metric")

        result = store.query_metrics("recent_metric", window="5m")
        assert result is not None
        assert result.count == 1
