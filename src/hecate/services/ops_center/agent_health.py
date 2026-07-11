"""Agent health monitoring service.

Aggregates per-agent health metrics from TraceModel root spans (type="trace").
Provides fleet overview, per-agent health, and health trends.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.config import settings
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


def _classify_health_status(
    error_rate: float,
    p95_latency_ms: float,
) -> str:
    """Classify agent health status based on error rate and latency thresholds.

    Args:
        error_rate: Agent's error rate (0.0-1.0).
        p95_latency_ms: Agent's P95 latency in milliseconds.

    Returns:
        "healthy", "warning", or "critical" based on worst dimension.
    """
    error_warning = settings.AGENT_HEALTH_ERROR_RATE_WARNING
    error_critical = settings.AGENT_HEALTH_ERROR_RATE_CRITICAL
    latency_warning = settings.AGENT_HEALTH_LATENCY_WARNING_MS
    latency_critical = settings.AGENT_HEALTH_LATENCY_CRITICAL_MS

    # Determine error dimension status
    if error_rate > error_critical:
        error_status = "critical"
    elif error_rate > error_warning:
        error_status = "warning"
    else:
        error_status = "healthy"

    # Determine latency dimension status
    if p95_latency_ms > latency_critical:
        latency_status = "critical"
    elif p95_latency_ms > latency_warning:
        latency_status = "warning"
    else:
        latency_status = "healthy"

    # Worst of two dimensions
    priority = {"critical": 3, "warning": 2, "healthy": 1}
    if priority.get(error_status, 0) >= priority.get(latency_status, 0):
        return error_status
    return latency_status


def _compute_health_score(
    error_rate: float,
    p95_latency_ms: float,
    session_count: int,
) -> int | None:
    """Compute weighted health score (0-100) for an agent.

    Args:
        error_rate: Agent's error rate (0.0-1.0).
        p95_latency_ms: Agent's P95 latency in milliseconds.
        session_count: Total session count for the agent.

    Returns:
        Health score 0-100, or None if agent has no sessions (unknown).
    """
    if session_count == 0:
        return None

    weights = settings.AGENT_HEALTH_SCORE_WEIGHTS
    error_weight = weights.get("error_rate", 0.5)
    latency_weight = weights.get("latency", 0.3)
    activity_weight = weights.get("activity", 0.2)

    # Error rate dimension: 0% errors = 100, 20% errors = 0
    error_dim = max(0.0, 100.0 - error_rate * 500)

    # Latency dimension: under warning threshold = ~100, at critical = 0
    critical_ms = settings.AGENT_HEALTH_LATENCY_CRITICAL_MS
    latency_dim = max(0.0, 100.0 - (p95_latency_ms / critical_ms) * 100)

    # Activity dimension: normalized to 10-session baseline
    activity_dim = min(100.0, session_count / 10 * 100)

    score = error_dim * error_weight + latency_dim * latency_weight + activity_dim * activity_weight
    return int(round(score))


class AgentHealthService:
    """Service for aggregating agent health metrics from TraceModel.

    Args:
        db: Async SQLAlchemy session.
    """

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get_fleet_overview(
        self,
        start_date: datetime,
        end_date: datetime,
    ) -> dict[str, Any]:
        """Aggregate fleet-wide health metrics.

        Returns:
            Dict with total_agents, healthy_count, warning_count,
            critical_count, unknown_count, fleet_error_rate,
            fleet_p95_latency_ms, top_degraded.
        """
        # Query per-agent aggregates from root trace spans
        base = (
            select(
                TraceModel.agent_id,
                func.count().label("total"),
                func.count().filter(TraceModel.status == "error").label("errors"),
                func.min(TraceModel.start_time).label("first_active"),
                func.max(TraceModel.end_time).label("last_active"),
            )
            .where(
                TraceModel.type == "trace",
                TraceModel.start_time >= start_date,
                TraceModel.start_time <= end_date,
                TraceModel.agent_id.isnot(None),
                ~TraceModel.deleted,
            )
            .group_by(TraceModel.agent_id)
        )
        result = (await self._db.execute(base)).all()

        if not result:
            return {
                "total_agents": 0,
                "healthy_count": 0,
                "warning_count": 0,
                "critical_count": 0,
                "unknown_count": 0,
                "fleet_error_rate": 0.0,
                "fleet_p95_latency_ms": 0.0,
                "top_degraded": [],
            }

        # Compute per-agent latency and classify
        agents: list[dict[str, Any]] = []
        total_sessions_all = 0
        total_errors_all = 0
        all_latencies: list[float] = []

        for row in result:
            agent_id = row.agent_id
            total = row.total
            errors = row.errors
            total_sessions_all += total
            total_errors_all += errors

            # Compute latency for this agent
            latency_q = select(
                TraceModel.start_time,
                TraceModel.end_time,
            ).where(
                TraceModel.type == "trace",
                TraceModel.agent_id == agent_id,
                TraceModel.start_time >= start_date,
                TraceModel.start_time <= end_date,
                TraceModel.end_time.isnot(None),
                ~TraceModel.deleted,
            )
            latency_rows = (await self._db.execute(latency_q)).all()
            latencies = [
                (r.end_time - r.start_time).total_seconds() * 1000 for r in latency_rows if r.end_time and r.start_time
            ]
            all_latencies.extend(latencies)

            error_rate = errors / total if total > 0 else 0.0
            p95_latency = _compute_p95(latencies)
            avg_latency = sum(latencies) / len(latencies) if latencies else 0.0
            status = _classify_health_status(error_rate, p95_latency)
            score = _compute_health_score(error_rate, p95_latency, total)

            agents.append(
                {
                    "agent_id": str(agent_id),
                    "total_sessions": total,
                    "error_count": errors,
                    "error_rate": round(error_rate, 4),
                    "avg_latency_ms": round(avg_latency, 2),
                    "p95_latency_ms": round(p95_latency, 2),
                    "health_status": status,
                    "health_score": score,
                }
            )

        # Sort by score ascending (most degraded first)
        agents_with_score = [a for a in agents if a["health_score"] is not None]
        agents_with_score.sort(key=lambda a: a["health_score"])
        top_degraded = agents_with_score[:10]

        # Fleet-level aggregates
        healthy = sum(1 for a in agents if a["health_status"] == "healthy")
        warning = sum(1 for a in agents if a["health_status"] == "warning")
        critical = sum(1 for a in agents if a["health_status"] == "critical")
        unknown = sum(1 for a in agents if a["health_status"] == "unknown")

        fleet_error_rate = total_errors_all / total_sessions_all if total_sessions_all > 0 else 0.0
        fleet_p95 = _compute_p95(all_latencies)

        return {
            "total_agents": len(agents),
            "healthy_count": healthy,
            "warning_count": warning,
            "critical_count": critical,
            "unknown_count": unknown,
            "fleet_error_rate": round(fleet_error_rate, 4),
            "fleet_p95_latency_ms": round(fleet_p95, 2),
            "top_degraded": top_degraded,
        }

    async def get_agent_health(
        self,
        agent_id: uuid.UUID,
        start_date: datetime,
        end_date: datetime,
    ) -> dict[str, Any] | None:
        """Get health metrics for a specific agent.

        Returns:
            Dict with agent_id, total_sessions, error_count, error_rate,
            success_rate, avg_latency_ms, p95_latency_ms, last_active_at,
            health_status, health_score, score_breakdown.
            None if agent has no root trace spans in the time range.
        """
        base = select(
            func.count().label("total"),
            func.count().filter(TraceModel.status == "error").label("errors"),
            func.count().filter(TraceModel.status == "completed").label("completed"),
            func.max(TraceModel.end_time).label("last_active"),
        ).where(
            TraceModel.type == "trace",
            TraceModel.agent_id == agent_id,
            TraceModel.start_time >= start_date,
            TraceModel.start_time <= end_date,
            ~TraceModel.deleted,
        )
        result = (await self._db.execute(base)).one()
        total = result.total or 0
        if total == 0:
            return None

        errors = result.errors or 0
        completed = result.completed or 0
        error_rate = errors / total if total > 0 else 0.0
        success_rate = completed / total if total > 0 else 1.0

        # Latency
        latency_q = select(
            TraceModel.start_time,
            TraceModel.end_time,
        ).where(
            TraceModel.type == "trace",
            TraceModel.agent_id == agent_id,
            TraceModel.start_time >= start_date,
            TraceModel.start_time <= end_date,
            TraceModel.end_time.isnot(None),
            ~TraceModel.deleted,
        )
        latency_rows = (await self._db.execute(latency_q)).all()
        latencies = [
            (r.end_time - r.start_time).total_seconds() * 1000 for r in latency_rows if r.end_time and r.start_time
        ]
        avg_latency = sum(latencies) / len(latencies) if latencies else 0.0
        p95_latency = _compute_p95(latencies)

        status = _classify_health_status(error_rate, p95_latency)
        score = _compute_health_score(error_rate, p95_latency, total)

        # Score breakdown
        weights = settings.AGENT_HEALTH_SCORE_WEIGHTS
        error_dim = max(0.0, 100.0 - error_rate * 500)
        critical_ms = settings.AGENT_HEALTH_LATENCY_CRITICAL_MS
        latency_dim = max(0.0, 100.0 - (p95_latency / critical_ms) * 100)
        activity_dim = min(100.0, total / 10 * 100)

        return {
            "agent_id": str(agent_id),
            "total_sessions": total,
            "error_count": errors,
            "error_rate": round(error_rate, 4),
            "success_rate": round(success_rate, 4),
            "avg_latency_ms": round(avg_latency, 2),
            "p95_latency_ms": round(p95_latency, 2),
            "last_active_at": result.last_active.isoformat() if result.last_active else None,
            "health_status": status,
            "health_score": score,
            "score_breakdown": {
                "error_rate_dimension": round(error_dim, 2),
                "latency_dimension": round(latency_dim, 2),
                "activity_dimension": round(activity_dim, 2),
                "weights": weights,
            },
        }

    async def get_agent_trends(
        self,
        agent_id: uuid.UUID,
        days: int = 7,
        granularity: str = "daily",
    ) -> list[dict[str, Any]]:
        """Get time-series health trends for an agent.

        Args:
            agent_id: Agent UUID.
            days: Number of days to look back (1-90).
            granularity: "hourly", "daily", or "weekly".

        Returns:
            List of dicts with date, total_sessions, errors, error_rate,
            avg_latency_ms, p95_latency_ms.
        """
        days = max(1, min(days, 90))
        end_date = datetime.now(UTC)
        start_date = end_date - timedelta(days=days)

        q = select(
            TraceModel.start_time,
            TraceModel.end_time,
            TraceModel.status,
        ).where(
            TraceModel.type == "trace",
            TraceModel.agent_id == agent_id,
            TraceModel.start_time >= start_date,
            TraceModel.start_time <= end_date,
            ~TraceModel.deleted,
        )
        rows = (await self._db.execute(q)).all()

        # Bucket by granularity in Python
        buckets: dict[str, dict[str, Any]] = {}
        for r in rows:
            ts = r.start_time
            if granularity == "hourly":
                key = ts.strftime("%Y-%m-%dT%H:00:00")
            elif granularity == "weekly":
                week_start = ts - timedelta(days=ts.weekday())
                key = week_start.strftime("%Y-%m-%d")
            else:  # daily
                key = ts.strftime("%Y-%m-%d")

            if key not in buckets:
                buckets[key] = {"date": key, "total_sessions": 0, "errors": 0, "latencies": []}

            buckets[key]["total_sessions"] += 1
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
            p95_latency = _compute_p95(latencies)
            error_rate = b["errors"] / b["total_sessions"] if b["total_sessions"] > 0 else 0.0
            result.append(
                {
                    "date": b["date"],
                    "total_sessions": b["total_sessions"],
                    "errors": b["errors"],
                    "error_rate": round(error_rate, 4),
                    "avg_latency_ms": round(avg_latency, 2),
                    "p95_latency_ms": round(p95_latency, 2),
                }
            )
        return result
