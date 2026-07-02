"""Tests for MetricsModel ORM and Pydantic schemas."""

from __future__ import annotations

from datetime import UTC, datetime

from hecate.models.metric import (
    MetricAggregateSchema,
    MetricCreateSchema,
    MetricModel,
    MetricQuerySchema,
    MetricsSnapshotSchema,
    MetricType,
)


class TestMetricType:
    """Tests for the MetricType enum."""

    def test_counter(self) -> None:
        assert MetricType.COUNTER == "counter"

    def test_gauge(self) -> None:
        assert MetricType.GAUGE == "gauge"

    def test_histogram(self) -> None:
        assert MetricType.HISTOGRAM == "histogram"


class TestMetricModel:
    """Tests for the MetricModel ORM model."""

    async def test_create_metric(self, db_session: object) -> None:
        metric = MetricModel(
            name="llm.request.count",
            value=1.0,
            type=MetricType.COUNTER,
            tags={"model": "gpt-4o"},
            timestamp=datetime.now(UTC),
        )
        assert metric.name == "llm.request.count"
        assert metric.value == 1.0
        assert metric.type == "counter"
        assert metric.tags == {"model": "gpt-4o"}

    async def test_default_type(self, db_session: object) -> None:
        metric = MetricModel(
            name="test",
            value=42.0,
            type="counter",
            timestamp=datetime.now(UTC),
        )
        assert metric.type == "counter"

    async def test_default_tags(self, db_session: object) -> None:
        metric = MetricModel(
            name="test",
            value=1.0,
            tags={},
            timestamp=datetime.now(UTC),
        )
        assert metric.tags == {}


class TestMetricCreateSchema:
    """Tests for the MetricCreateSchema."""

    def test_full_schema(self) -> None:
        schema = MetricCreateSchema(
            name="requests",
            value=5.0,
            type="gauge",
            tags={"endpoint": "/api/agents"},
            timestamp=datetime.now(UTC),
        )
        assert schema.name == "requests"
        assert schema.value == 5.0
        assert schema.type == "gauge"

    def test_minimal_schema(self) -> None:
        schema = MetricCreateSchema(name="requests", value=1.0)
        assert schema.type == MetricType.COUNTER
        assert schema.tags is None
        assert schema.timestamp is None


class TestMetricQuerySchema:
    """Tests for the MetricQuerySchema."""

    def test_defaults(self) -> None:
        schema = MetricQuerySchema(name="requests")
        assert schema.window == "5m"
        assert schema.aggregation == "sum"
        assert schema.tags is None

    def test_custom(self) -> None:
        schema = MetricQuerySchema(
            name="latency",
            window="1h",
            aggregation="avg",
            tags={"endpoint": "/v1/chat"},
        )
        assert schema.window == "1h"
        assert schema.aggregation == "avg"


class TestMetricAggregateSchema:
    """Tests for the MetricAggregateSchema."""

    def test_full(self) -> None:
        schema = MetricAggregateSchema(
            name="requests",
            value=42.0,
            aggregation="sum",
            window="5m",
            count=10,
        )
        assert schema.count == 10


class TestMetricsSnapshotSchema:
    """Tests for the MetricsSnapshotSchema."""

    def test_with_metrics(self) -> None:
        agg = MetricAggregateSchema(name="requests", value=10.0)
        schema = MetricsSnapshotSchema(
            metrics=[agg],
            timestamp=datetime.now(UTC),
            window="5m",
        )
        assert len(schema.metrics) == 1
        assert schema.window == "5m"
