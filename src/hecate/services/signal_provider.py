"""Signal providers for alert evaluation.

Each provider queries TraceModel or CostService for a specific metric type.
The SignalProviderRegistry maps AlertType to the appropriate provider instance.
"""

from __future__ import annotations

import calendar
import logging
import uuid
from abc import ABC, abstractmethod
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.alert import AlertRuleModel, AlertType
from hecate.models.trace import TraceModel
from hecate.services.cost_service import CostService

logger = logging.getLogger(__name__)


def _apply_filters(stmt, filters: dict) -> object:
    """Apply agent_id and model filters to a TraceModel query."""
    agent_id = filters.get("agent_id")
    if agent_id:
        stmt = stmt.where(TraceModel.agent_id == uuid.UUID(agent_id) if isinstance(agent_id, str) else agent_id)
    model_name = filters.get("model")
    if model_name:
        stmt = stmt.where(TraceModel.metadata_["model"].as_string() == model_name)
    return stmt


class SignalProvider(ABC):
    """Abstract base class for alert signal providers."""

    @abstractmethod
    async def get_value(self, db: AsyncSession, rule: AlertRuleModel) -> float:
        """Query the signal value for the given rule.

        Args:
            db: Async database session.
            rule: Alert rule with threshold, window_minutes, and filters.

        Returns:
            The current signal value.
        """


class ErrorRateProvider(SignalProvider):
    """Error rate signal: COUNT(status='error') / COUNT(*)."""

    async def get_value(self, db: AsyncSession, rule: AlertRuleModel) -> float:
        cutoff = datetime.now(UTC) - timedelta(minutes=rule.window_minutes)
        total_stmt = select(func.count()).select_from(TraceModel).where(TraceModel.start_time >= cutoff)
        total_stmt = _apply_filters(total_stmt, rule.filters)
        total = (await db.execute(total_stmt)).scalar() or 0
        if total == 0:
            return 0.0
        error_stmt = (
            select(func.count())
            .select_from(TraceModel)
            .where(TraceModel.start_time >= cutoff, TraceModel.status == "error")
        )
        error_stmt = _apply_filters(error_stmt, rule.filters)
        errors = (await db.execute(error_stmt)).scalar() or 0
        return errors / total


class LatencyP95Provider(SignalProvider):
    """P95 latency signal: 95th percentile of (end_time - start_time) in ms."""

    async def get_value(self, db: AsyncSession, rule: AlertRuleModel) -> float:
        cutoff = datetime.now(UTC) - timedelta(minutes=rule.window_minutes)
        stmt = (
            select(
                func.extract(
                    "epoch",
                    (TraceModel.end_time - TraceModel.start_time),
                )
                * 1000
            )
            .where(
                TraceModel.start_time >= cutoff,
                TraceModel.end_time.isnot(None),
                TraceModel.status == "completed",
            )
            .order_by(
                func.extract(
                    "epoch",
                    (TraceModel.end_time - TraceModel.start_time),
                )
            )
        )
        stmt = _apply_filters(stmt, rule.filters)
        latencies = [row[0] for row in (await db.execute(stmt)).all() if row[0] is not None]
        if not latencies:
            return 0.0
        p95_index = int(len(latencies) * 0.95)
        return float(latencies[min(p95_index, len(latencies) - 1)])


class LatencyTTFTProvider(SignalProvider):
    """TTFT signal: AVG(metadata.ttft_ms) for GENERATION traces."""

    async def get_value(self, db: AsyncSession, rule: AlertRuleModel) -> float:
        cutoff = datetime.now(UTC) - timedelta(minutes=rule.window_minutes)
        stmt = select(func.avg(TraceModel.metadata_["ttft_ms"].as_float())).where(
            TraceModel.start_time >= cutoff,
            TraceModel.type == "generation",
            TraceModel.metadata_["ttft_ms"].isnot(None),
        )
        stmt = _apply_filters(stmt, rule.filters)
        result = (await db.execute(stmt)).scalar()
        return float(result) if result is not None else 0.0


class TokenUsageProvider(SignalProvider):
    """Token usage signal: SUM(usage.total_tokens) for GENERATION traces."""

    async def get_value(self, db: AsyncSession, rule: AlertRuleModel) -> float:
        cutoff = datetime.now(UTC) - timedelta(minutes=rule.window_minutes)
        stmt = select(func.sum(TraceModel.usage["total_tokens"].as_integer())).where(
            TraceModel.start_time >= cutoff,
            TraceModel.type == "generation",
            TraceModel.usage["total_tokens"].isnot(None),
        )
        stmt = _apply_filters(stmt, rule.filters)
        result = (await db.execute(stmt)).scalar()
        return float(result) if result is not None else 0.0


class CostDailyProvider(SignalProvider):
    """Daily cost signal: delegates to CostService.get_cost_summary()."""

    async def get_value(self, db: AsyncSession, rule: AlertRuleModel) -> float:
        cost_service = CostService(db)
        now = datetime.now(UTC)
        start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        summary = await cost_service.get_cost_summary(
            workspace_id=rule.workspace_id,
            start_date=start_of_day,
            end_date=now,
        )
        return summary.total_cost


class CostMonthlyForecastProvider(SignalProvider):
    """Monthly cost forecast: EWMA of last 7 days extrapolated to month length."""

    async def get_value(self, db: AsyncSession, rule: AlertRuleModel) -> float:
        cost_service = CostService(db)
        now = datetime.now(UTC)
        daily_costs: list[float] = []
        for i in range(7, 0, -1):
            day_end = now - timedelta(days=i - 1)
            day_start = day_end.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
            day_end_normalized = day_start + timedelta(days=1)
            summary = await cost_service.get_cost_summary(
                workspace_id=rule.workspace_id,
                start_date=day_start,
                end_date=day_end_normalized,
            )
            daily_costs.append(summary.total_cost)

        if not daily_costs or all(c == 0 for c in daily_costs):
            return 0.0

        weights = [0.5 ** (7 - i) for i in range(7)]
        weighted_sum = sum(c * w for c, w in zip(daily_costs, weights, strict=False))
        weight_total = sum(weights)
        ewma_daily = weighted_sum / weight_total if weight_total > 0 else 0.0

        days_in_month = calendar.monthrange(now.year, now.month)[1]
        return ewma_daily * days_in_month


class ToolFailureRateProvider(SignalProvider):
    """Tool failure rate: COUNT(error AND TOOL) / COUNT(TOOL)."""

    async def get_value(self, db: AsyncSession, rule: AlertRuleModel) -> float:
        cutoff = datetime.now(UTC) - timedelta(minutes=rule.window_minutes)
        total_stmt = (
            select(func.count())
            .select_from(TraceModel)
            .where(TraceModel.start_time >= cutoff, TraceModel.type == "tool")
        )
        total_stmt = _apply_filters(total_stmt, rule.filters)
        total = (await db.execute(total_stmt)).scalar() or 0
        if total == 0:
            return 0.0
        error_stmt = (
            select(func.count())
            .select_from(TraceModel)
            .where(
                TraceModel.start_time >= cutoff,
                TraceModel.type == "tool",
                TraceModel.status == "error",
            )
        )
        error_stmt = _apply_filters(error_stmt, rule.filters)
        errors = (await db.execute(error_stmt)).scalar() or 0
        return errors / total


class SuccessRateProvider(SignalProvider):
    """Success rate: COUNT(completed) / COUNT(*)."""

    async def get_value(self, db: AsyncSession, rule: AlertRuleModel) -> float:
        cutoff = datetime.now(UTC) - timedelta(minutes=rule.window_minutes)
        total_stmt = select(func.count()).select_from(TraceModel).where(TraceModel.start_time >= cutoff)
        total_stmt = _apply_filters(total_stmt, rule.filters)
        total = (await db.execute(total_stmt)).scalar() or 0
        if total == 0:
            return 1.0
        success_stmt = (
            select(func.count())
            .select_from(TraceModel)
            .where(TraceModel.start_time >= cutoff, TraceModel.status == "completed")
        )
        success_stmt = _apply_filters(success_stmt, rule.filters)
        completed = (await db.execute(success_stmt)).scalar() or 0
        return completed / total


class SignalProviderRegistry:
    """Maps AlertType to SignalProvider instances."""

    def __init__(self) -> None:
        self._providers: dict[AlertType, SignalProvider] = {
            AlertType.ERROR_RATE: ErrorRateProvider(),
            AlertType.LATENCY_P95: LatencyP95Provider(),
            AlertType.LATENCY_TTFT: LatencyTTFTProvider(),
            AlertType.TOKEN_USAGE: TokenUsageProvider(),
            AlertType.COST_DAILY: CostDailyProvider(),
            AlertType.COST_MONTHLY_FORECAST: CostMonthlyForecastProvider(),
            AlertType.TOOL_FAILURE_RATE: ToolFailureRateProvider(),
            AlertType.SUCCESS_RATE: SuccessRateProvider(),
        }

    def get_provider(self, alert_type: AlertType) -> SignalProvider | None:
        """Get the signal provider for an alert type."""
        return self._providers.get(alert_type)
