"""Tool execution analytics service.

Aggregates tool execution metrics from TraceModel records where type="tool".
Provides overview, per-tool details, trends, and top error queries.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.trace import TraceModel


def _compute_p95(values: list[float]) -> float:
    """Compute the 95th percentile from a list of values.

    Args:
        values: List of numeric values.

    Returns:
        P95 value, or 0.0 if empty.
    """
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    idx = int(len(sorted_vals) * 0.95)
    return sorted_vals[min(idx, len(sorted_vals) - 1)]


class ToolAnalyticsService:
    """Service for aggregating tool execution analytics from TraceModel.

    Args:
        db: Async SQLAlchemy session.
    """

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get_overview(
        self,
        start_date: datetime,
        end_date: datetime,
        agent_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        """Aggregate tool execution metrics for a time range.

        Returns:
            Dict with total_executions, success_rate, avg_latency_ms,
            p95_latency_ms, unique_tools, error_count.
        """
        base = select(
            func.count().label("total"),
            func.count().filter(TraceModel.status == "completed").label("completed"),
            func.count().filter(TraceModel.status == "error").label("errors"),
            func.count(func.distinct(TraceModel.name)).label("unique_tools"),
        ).where(
            TraceModel.type == "tool",
            TraceModel.start_time >= start_date,
            TraceModel.start_time <= end_date,
            ~TraceModel.deleted,
        )
        if agent_id is not None:
            base = base.where(TraceModel.agent_id == agent_id)

        result = (await self._db.execute(base)).one()
        total = result.total or 0
        completed = result.completed or 0
        errors = result.errors or 0
        unique_tools = result.unique_tools or 0

        success_rate = completed / total if total > 0 else 1.0

        # Latency: compute in Python for cross-dialect compatibility
        duration_q = select(
            TraceModel.start_time,
            TraceModel.end_time,
        ).where(
            TraceModel.type == "tool",
            TraceModel.start_time >= start_date,
            TraceModel.start_time <= end_date,
            TraceModel.end_time.isnot(None),
            ~TraceModel.deleted,
        )
        if agent_id is not None:
            duration_q = duration_q.where(TraceModel.agent_id == agent_id)

        durations_result = (await self._db.execute(duration_q)).all()
        latencies_ms = [
            (r.end_time - r.start_time).total_seconds() * 1000 for r in durations_result if r.end_time and r.start_time
        ]

        avg_latency = sum(latencies_ms) / len(latencies_ms) if latencies_ms else 0.0
        p95_latency = _compute_p95(latencies_ms)

        return {
            "total_executions": total,
            "success_rate": round(success_rate, 4),
            "avg_latency_ms": round(avg_latency, 2),
            "p95_latency_ms": round(p95_latency, 2),
            "unique_tools": unique_tools,
            "error_count": errors,
        }

    async def get_tool_details(
        self,
        tool_name: str,
        start_date: datetime,
        end_date: datetime,
    ) -> dict[str, Any] | None:
        """Get detailed metrics for a specific tool.

        Returns:
            Dict with tool_name, executions, success_rate, avg_latency_ms,
            p95_latency_ms, last_used_at, top_errors.
            None if tool has no records.
        """
        full_name = f"tool:{tool_name}"

        # Aggregate metrics
        base = select(
            func.count().label("total"),
            func.count().filter(TraceModel.status == "completed").label("completed"),
            func.max(TraceModel.end_time).label("last_used"),
        ).where(
            TraceModel.type == "tool",
            TraceModel.name == full_name,
            TraceModel.start_time >= start_date,
            TraceModel.start_time <= end_date,
            ~TraceModel.deleted,
        )
        result = (await self._db.execute(base)).one()
        total = result.total or 0
        if total == 0:
            return None

        completed = result.completed or 0
        success_rate = completed / total if total > 0 else 1.0

        # Latency: compute in Python
        duration_q = select(
            TraceModel.start_time,
            TraceModel.end_time,
        ).where(
            TraceModel.type == "tool",
            TraceModel.name == full_name,
            TraceModel.start_time >= start_date,
            TraceModel.start_time <= end_date,
            TraceModel.end_time.isnot(None),
            ~TraceModel.deleted,
        )
        durations_result = (await self._db.execute(duration_q)).all()
        latencies_ms = [
            (r.end_time - r.start_time).total_seconds() * 1000 for r in durations_result if r.end_time and r.start_time
        ]
        avg_latency = sum(latencies_ms) / len(latencies_ms) if latencies_ms else 0.0
        p95_latency = _compute_p95(latencies_ms)

        # Top 5 errors
        errors_q = (
            select(
                TraceModel.output_data["error"].as_string().label("message"),
                func.count().label("count"),
            )
            .where(
                TraceModel.type == "tool",
                TraceModel.name == full_name,
                TraceModel.status == "error",
                TraceModel.start_time >= start_date,
                TraceModel.start_time <= end_date,
                ~TraceModel.deleted,
            )
            .group_by(TraceModel.output_data["error"].as_string())
            .order_by(func.count().desc())
            .limit(5)
        )
        errors_result = (await self._db.execute(errors_q)).all()
        top_errors = [{"message": r.message, "count": r.count} for r in errors_result]

        return {
            "tool_name": tool_name,
            "executions": total,
            "success_rate": round(success_rate, 4),
            "avg_latency_ms": round(avg_latency, 2),
            "p95_latency_ms": round(p95_latency, 2),
            "last_used_at": result.last_used.isoformat() if result.last_used else None,
            "top_errors": top_errors,
        }

    async def get_trends(
        self,
        granularity: str,
        days: int,
        tool_name: str | None = None,
    ) -> list[dict[str, Any]]:
        """Get time-series data for tool executions.

        Uses Python-side date bucketing for cross-dialect compatibility.

        Args:
            granularity: "hourly", "daily", or "weekly".
            days: Number of days to look back (1-90).
            tool_name: Optional filter by tool name.

        Returns:
            List of dicts with date, total, errors, avg_latency_ms.
        """
        end_date = datetime.now(UTC)
        start_date = end_date - timedelta(days=days)

        q = select(
            TraceModel.start_time,
            TraceModel.end_time,
            TraceModel.status,
        ).where(
            TraceModel.type == "tool",
            TraceModel.start_time >= start_date,
            TraceModel.start_time <= end_date,
            ~TraceModel.deleted,
        )
        if tool_name is not None:
            q = q.where(TraceModel.name == f"tool:{tool_name}")

        rows = (await self._db.execute(q)).all()

        # Bucket by granularity in Python
        buckets: dict[str, dict[str, Any]] = {}
        for r in rows:
            ts = r.start_time
            if granularity == "hourly":
                key = ts.strftime("%Y-%m-%dT%H:00:00")
            elif granularity == "weekly":
                # ISO week start (Monday)
                week_start = ts - timedelta(days=ts.weekday())
                key = week_start.strftime("%Y-%m-%d")
            else:  # daily
                key = ts.strftime("%Y-%m-%d")

            if key not in buckets:
                buckets[key] = {"date": key, "total": 0, "errors": 0, "latencies": []}

            buckets[key]["total"] += 1
            if r.status == "error":
                buckets[key]["errors"] += 1
            if r.end_time and r.start_time:
                latency_ms = (r.end_time - r.start_time).total_seconds() * 1000
                buckets[key]["latencies"].append(latency_ms)

        result = []
        for key in sorted(buckets.keys()):
            b = buckets[key]
            latencies = b["latencies"]
            avg_latency = sum(latencies) / len(latencies) if latencies else 0.0
            result.append(
                {
                    "date": b["date"],
                    "total": b["total"],
                    "errors": b["errors"],
                    "avg_latency_ms": round(avg_latency, 2),
                }
            )
        return result

    async def get_top_errors(
        self,
        limit: int = 20,
        tool_name: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Get most frequent tool execution errors.

        Args:
            limit: Max results (default 20, max 100).
            tool_name: Optional filter by tool name.
            start_date: Optional start of time range.
            end_date: Optional end of time range.

        Returns:
            List of dicts with tool_name, message, count, last_occurrence.
        """
        limit = min(limit, 100)

        q = (
            select(
                TraceModel.name.label("tool_name"),
                TraceModel.output_data["error"].as_string().label("message"),
                func.count().label("count"),
                func.max(TraceModel.end_time).label("last_occurrence"),
            )
            .where(
                TraceModel.type == "tool",
                TraceModel.status == "error",
                ~TraceModel.deleted,
            )
            .group_by(TraceModel.name, TraceModel.output_data["error"].as_string())
            .order_by(func.count().desc())
            .limit(limit)
        )

        if tool_name is not None:
            q = q.where(TraceModel.name == f"tool:{tool_name}")
        if start_date is not None:
            q = q.where(TraceModel.start_time >= start_date)
        if end_date is not None:
            q = q.where(TraceModel.start_time <= end_date)

        result = (await self._db.execute(q)).all()
        return [
            {
                "tool_name": r.tool_name.removeprefix("tool:"),
                "message": r.message,
                "count": r.count,
                "last_occurrence": r.last_occurrence.isoformat() if r.last_occurrence else None,
            }
            for r in result
        ]
