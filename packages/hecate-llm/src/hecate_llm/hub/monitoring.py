"""Model monitoring service for performance aggregation and drift detection.

Aggregates TraceModel data into per-model performance metrics, detects
performance drift using z-score, and provides comparison views.
"""

from __future__ import annotations

import logging
import math
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.model_pricing import ModelPricingModel
from hecate.models.trace import TraceModel

logger = logging.getLogger(__name__)

_DEFAULT_WORKSPACE = uuid.UUID("00000000-0000-0000-0000-000000000000")
_DEFAULT_DRIFT_THRESHOLD = 2.5
_DEFAULT_ROLLING_WINDOW_DAYS = 30


class MonitoringService:
    """Service for model performance monitoring, drift detection, and comparison."""

    def __init__(
        self,
        db: AsyncSession,
        drift_threshold: float = _DEFAULT_DRIFT_THRESHOLD,
        rolling_window_days: int = _DEFAULT_ROLLING_WINDOW_DAYS,
    ) -> None:
        self._db = db
        self._drift_threshold = drift_threshold
        self._rolling_window_days = rolling_window_days

    async def get_model_performance(
        self,
        model_id: str,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        granularity: str = "daily",
    ) -> dict[str, Any]:
        now = datetime.now(UTC)
        start = start_date or (now - timedelta(days=7))
        end = end_date or now

        traces = await self._query_traces(start, end, model_id=model_id)
        buckets = self._bucket_traces(traces, granularity, start, end)

        timeseries = []
        for bucket_start, bucket_traces in buckets.items():
            metrics = self._compute_metrics(bucket_traces)
            metrics["date"] = bucket_start.isoformat()
            timeseries.append(metrics)

        return {
            "model_id": model_id,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "granularity": granularity,
            "timeseries": timeseries,
        }

    async def compare_models(
        self,
        model_ids: list[str],
        days: int = 7,
    ) -> list[dict[str, Any]]:
        now = datetime.now(UTC)
        start = now - timedelta(days=days)

        results = []
        for model_id in model_ids:
            traces = await self._query_traces(start, now, model_id=model_id)
            metrics = self._compute_metrics(traces)
            metrics["model_id"] = model_id
            results.append(metrics)

        return results

    async def detect_drift(
        self,
        model_id: str,
        metric: str = "avg_latency",
    ) -> dict[str, Any]:
        now = datetime.now(UTC)
        start = now - timedelta(days=self._rolling_window_days)
        traces = await self._query_traces(start, now, model_id=model_id)

        daily_metrics: list[dict[str, Any]] = []
        by_date: dict[str, list[dict]] = {}
        for trace in traces:
            date_str = trace.start_time.strftime("%Y-%m-%d") if trace.start_time else "unknown"
            if date_str not in by_date:
                by_date[date_str] = []
            by_date[date_str].append(trace)

        for date_str in sorted(by_date):
            m = self._compute_metrics(by_date[date_str])
            m["date"] = date_str
            daily_metrics.append(m)

        if len(daily_metrics) < 7:
            return {"model_id": model_id, "metric": metric, "drift_detected": False, "reason": "insufficient_data"}

        values = [m.get(metric, 0.0) for m in daily_metrics]
        mean = sum(values) / len(values)
        variance = sum((v - mean) ** 2 for v in values) / len(values)
        stddev = math.sqrt(variance) if variance > 0 else 0.0

        if stddev == 0:
            return {"model_id": model_id, "metric": metric, "drift_detected": False, "reason": "no_variance"}

        latest = values[-1]
        z_score = (latest - mean) / stddev

        drift_detected = abs(z_score) >= self._drift_threshold
        severity = "info"
        if abs(z_score) >= 4.0:
            severity = "critical"
        elif abs(z_score) >= 3.0:
            severity = "warn"

        return {
            "model_id": model_id,
            "metric": metric,
            "drift_detected": drift_detected,
            "current_value": round(latest, 4),
            "baseline_mean": round(mean, 4),
            "baseline_stddev": round(stddev, 4),
            "z_score": round(z_score, 3),
            "severity": severity if drift_detected else "none",
        }

    async def get_cost_trends_by_model(
        self,
        workspace_id: uuid.UUID | None = None,
        days: int = 30,
        granularity: str = "daily",
    ) -> list[dict[str, Any]]:
        ws_id = workspace_id or _DEFAULT_WORKSPACE
        now = datetime.now(UTC)
        start = now - timedelta(days=days)
        traces = await self._query_traces(start, now)

        model_costs: dict[str, dict[str, Any]] = {}
        for trace in traces:
            model = (trace.metadata_ or {}).get("model", "unknown")
            date_key = trace.start_time.strftime("%Y-%m-%d") if trace.start_time else "unknown"
            usage = trace.usage or {}
            input_tokens = usage.get("prompt_tokens", 0)
            output_tokens = usage.get("completion_tokens", 0)

            pricing = await self._get_pricing(model, ws_id)
            cost = 0.0
            if pricing:
                cost = input_tokens / 1000 * pricing["input"] + output_tokens / 1000 * pricing["output"]

            bucket_key = f"{date_key}:{model}"
            if bucket_key not in model_costs:
                model_costs[bucket_key] = {"date": date_key, "model": model, "cost": 0.0, "tokens": 0}
            model_costs[bucket_key]["cost"] += cost
            model_costs[bucket_key]["tokens"] += input_tokens + output_tokens

        return sorted(model_costs.values(), key=lambda x: (x["date"], x["model"]))

    async def _query_traces(
        self,
        start: datetime,
        end: datetime,
        model_id: str | None = None,
    ) -> list[TraceModel]:
        stmt = select(TraceModel).where(
            TraceModel.start_time >= start,
            TraceModel.start_time < end,
            TraceModel.deleted.is_(False),
        )
        result = await self._db.execute(stmt)
        traces = list(result.scalars().all())
        if model_id:
            traces = [t for t in traces if (t.metadata_ or {}).get("model") == model_id]
        return traces

    def _bucket_traces(
        self,
        traces: list[TraceModel],
        granularity: str,
        start: datetime,
        end: datetime,
    ) -> dict[datetime, list[TraceModel]]:
        buckets: dict[datetime, list[TraceModel]] = {}
        for trace in traces:
            if trace.start_time is None:
                continue
            if granularity == "hourly":
                key = trace.start_time.replace(minute=0, second=0, microsecond=0)
            elif granularity == "weekly":
                key = trace.start_time - timedelta(days=trace.start_time.weekday())
                key = key.replace(hour=0, minute=0, second=0, microsecond=0)
            else:
                key = trace.start_time.replace(hour=0, minute=0, second=0, microsecond=0)
            if key not in buckets:
                buckets[key] = []
            buckets[key].append(trace)
        return dict(sorted(buckets.items()))

    def _compute_metrics(self, traces: list[TraceModel]) -> dict[str, Any]:
        if not traces:
            return {"avg_latency": 0, "ttft": 0, "error_rate": 0, "request_count": 0, "cost": 0.0}

        total_latency = 0.0
        total_ttft = 0.0
        errors = 0
        total_cost = 0.0
        count = len(traces)

        for trace in traces:
            latency = (trace.end_time - trace.start_time).total_seconds() if trace.end_time and trace.start_time else 0
            total_latency += latency
            ttft = (trace.metadata_ or {}).get("ttft", 0)
            total_ttft += ttft
            if trace.status == "error":
                errors += 1
            usage = trace.usage or {}
            input_tokens = usage.get("prompt_tokens", 0)
            output_tokens = usage.get("completion_tokens", 0)
            total_cost += (input_tokens + output_tokens) / 1000 * 0.01

        return {
            "avg_latency": round(total_latency / count, 4) if count else 0,
            "ttft": round(total_ttft / count, 4) if count else 0,
            "error_rate": round(errors / count, 4) if count else 0,
            "request_count": count,
            "cost": round(total_cost, 6),
        }

    async def _get_pricing(self, model_id: str, workspace_id: uuid.UUID) -> dict[str, float] | None:
        now = datetime.now(UTC)
        stmt = (
            select(ModelPricingModel)
            .where(
                ModelPricingModel.model_id == model_id,
                ModelPricingModel.effective_from <= now,
                ModelPricingModel.deleted.is_(False),
            )
            .order_by(ModelPricingModel.effective_from.desc())
            .limit(1)
        )
        result = await self._db.execute(stmt)
        pricing = result.scalar_one_or_none()
        if pricing and (pricing.effective_until is None or pricing.effective_until > now):
            return {"input": pricing.input_price_per_1k, "output": pricing.output_price_per_1k}
        return None
