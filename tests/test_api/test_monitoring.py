"""Tests for the monitoring REST API endpoints."""

from __future__ import annotations

from unittest.mock import patch

from hecate.engine.metrics_store import InMemoryMetricsStore


async def test_query_metrics_by_name(client: object) -> None:
    """GET /api/monitoring/metrics?name=X returns aggregated metrics."""
    store = InMemoryMetricsStore()
    store.record_counter("requests", value=10.0)
    store.record_counter("requests", value=5.0)

    with patch(
        "hecate.api.management.monitoring.get_metrics_store",
        return_value=store,
    ):
        response = await client.get("/api/monitoring/metrics?name=requests")
        assert response.status_code == 200
        data = response.json()
        assert len(data["metrics"]) == 1
        assert data["metrics"][0]["name"] == "requests"
        assert data["metrics"][0]["value"] == 15.0


async def test_query_metrics_nonexistent(client: object) -> None:
    """GET /api/monitoring/metrics?name=X returns empty for unknown metric."""
    store = InMemoryMetricsStore()

    with patch(
        "hecate.api.management.monitoring.get_metrics_store",
        return_value=store,
    ):
        response = await client.get("/api/monitoring/metrics?name=nonexistent")
        assert response.status_code == 200
        data = response.json()
        assert data["metrics"] == []


async def test_query_metrics_snapshot(client: object) -> None:
    """GET /api/monitoring/metrics without name returns full snapshot."""
    store = InMemoryMetricsStore()
    store.record_counter("requests", value=5.0)
    store.record_gauge("cpu", 80.0)

    with patch(
        "hecate.api.management.monitoring.get_metrics_store",
        return_value=store,
    ):
        response = await client.get("/api/monitoring/metrics")
        assert response.status_code == 200
        data = response.json()
        assert len(data["metrics"]) == 2


async def test_get_snapshot_endpoint(client: object) -> None:
    """GET /api/monitoring/snapshot returns snapshot with specified windows."""
    store = InMemoryMetricsStore()
    store.record_counter("requests", value=3.0)

    with patch(
        "hecate.api.management.monitoring.get_metrics_store",
        return_value=store,
    ):
        response = await client.get("/api/monitoring/snapshot?windows=1m,5m")
        assert response.status_code == 200
        data = response.json()
        assert "metrics" in data
        assert "timestamp" in data
        assert "window" in data


async def test_query_metrics_custom_window(client: object) -> None:
    """GET /api/monitoring/metrics with custom window parameter."""
    store = InMemoryMetricsStore()
    store.record_counter("requests", value=1.0)

    with patch(
        "hecate.api.management.monitoring.get_metrics_store",
        return_value=store,
    ):
        response = await client.get("/api/monitoring/metrics?name=requests&window=1h")
        assert response.status_code == 200
        data = response.json()
        assert data["window"] == "1h"


async def test_query_metrics_custom_aggregation(client: object) -> None:
    """GET /api/monitoring/metrics with custom aggregation."""
    store = InMemoryMetricsStore()
    store.record_histogram("latency", value=10.0)
    store.record_histogram("latency", value=30.0)

    with patch(
        "hecate.api.management.monitoring.get_metrics_store",
        return_value=store,
    ):
        response = await client.get("/api/monitoring/metrics?name=latency&aggregation=avg")
        assert response.status_code == 200
        data = response.json()
        assert data["metrics"][0]["value"] == 20.0
