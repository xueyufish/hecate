"""TimescaleDB-backed metrics store for production use.

Persists metric data points to the metrics table and uses PostgreSQL
aggregate functions for time-window queries. Requires TimescaleDB
extension for optimal performance on high-volume metric data.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.engine.metrics_store import (
    MetricAggregate,
    MetricsSnapshot,
    MetricsStore,
    _parse_window,
)
from hecate.models.metric import MetricModel


class TimescaleMetricsStore(MetricsStore):
    """TimescaleDB-backed metrics store for production deployments.

    Persists all metric data points to the metrics table via async
    SQLAlchemy. Aggregation queries use standard SQL functions
    (sum, avg, min, max) with timestamp-based windowing.
    """

    def __init__(self, db: AsyncSession | None = None) -> None:
        self._db = db

    def set_session(self, db: AsyncSession) -> None:
        """Set the database session for operations.

        Args:
            db: AsyncSession to use for persistence.
        """
        self._db = db

    def _ensure_session(self) -> AsyncSession:
        if self._db is None:
            msg = "Database session not configured. Call set_session() first."
            raise RuntimeError(msg)
        return self._db

    def record_counter(self, name: str, value: float = 1.0, tags: dict[str, str] | None = None) -> None:
        self._record(name, value, "counter", tags)

    def record_gauge(self, name: str, value: float, tags: dict[str, str] | None = None) -> None:
        self._record(name, value, "gauge", tags)

    def record_histogram(self, name: str, value: float, tags: dict[str, str] | None = None) -> None:
        self._record(name, value, "histogram", tags)

    def query_metrics(
        self,
        name: str,
        window: str = "5m",
        aggregation: str = "sum",
        tags: dict[str, str] | None = None,
    ) -> MetricAggregate | None:
        cutoff = datetime.now(UTC) - _parse_window(window)
        db = self._ensure_session()

        agg_func = self._get_agg_func(aggregation)
        query = select(agg_func(MetricModel.value)).where(
            MetricModel.name == name,
            MetricModel.timestamp >= cutoff,
        )
        if tags:
            for k, v in tags.items():
                query = query.where(MetricModel.tags[k].as_string() == v)

        result = db.execute(query)
        row = result.scalar_one_or_none()
        if row is None:
            return None

        count_query = (
            select(func.count())
            .select_from(MetricModel)
            .where(
                MetricModel.name == name,
                MetricModel.timestamp >= cutoff,
            )
        )
        count_result = db.execute(count_query)
        count = count_result.scalar_one()

        return MetricAggregate(
            name=name,
            value=float(row),
            aggregation=aggregation,
            window=window,
            tags=tags or {},
            count=count,
            timestamp=datetime.now(UTC),
        )

    def get_snapshot(self, windows: list[str] | None = None) -> MetricsSnapshot:
        windows = windows or ["5m"]
        db = self._ensure_session()

        result = db.execute(select(MetricModel.name).distinct())
        names = [row[0] for row in result.all()]

        all_metrics: list[MetricAggregate] = []
        for name in names:
            for window in windows:
                agg = self.query_metrics(name, window=window)
                if agg is not None:
                    all_metrics.append(agg)

        return MetricsSnapshot(
            metrics=all_metrics,
            timestamp=datetime.now(UTC),
            window=windows[0] if len(windows) == 1 else ",".join(windows),
        )

    def _record(self, name: str, value: float, metric_type: str, tags: dict[str, str] | None = None) -> None:
        db = self._ensure_session()
        record = MetricModel(
            name=name,
            value=value,
            type=metric_type,
            tags=tags or {},
            timestamp=datetime.now(UTC),
        )
        db.add(record)

    @staticmethod
    def _get_agg_func(aggregation: str) -> Any:
        if aggregation == "sum":
            return func.sum
        if aggregation == "avg":
            return func.avg
        if aggregation == "min":
            return func.min
        if aggregation == "max":
            return func.max
        msg = f"Unknown aggregation method: {aggregation!r}"
        raise ValueError(msg)
