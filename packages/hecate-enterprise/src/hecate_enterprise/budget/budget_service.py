"""Budget service — cost governance with forecasting and chargeback.

Provides budget utilization tracking, linear trend forecasting,
and chargeback report generation by delegating to QuotaService
and CostService.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.budget import BudgetForecastModel
from hecate.models.quota import QuotaResourceType
from hecate.services.cost_service import CostService
from hecate.services.quota_service import QuotaService

logger = logging.getLogger(__name__)

_DEFAULT_WORKSPACE = uuid.UUID("00000000-0000-0000-0000-000000000000")


class BudgetService:
    """Service for budget governance — utilization, forecasting, chargeback.

    Args:
        db: Async SQLAlchemy session.
        workspace_id: Optional workspace scope.
    """

    def __init__(self, db: AsyncSession, workspace_id: uuid.UUID | None = None) -> None:
        self._db = db
        self._workspace_id = workspace_id or _DEFAULT_WORKSPACE
        self._cost_service = CostService(db)
        self._quota_service = QuotaService(db, workspace_id)

    async def get_utilization(
        self,
        scope: str,
        scope_id: uuid.UUID,
    ) -> dict[str, Any]:
        """Get current budget utilization for a scope.

        Returns:
            Dict with spent, remaining, utilization_pct, soft_limit, hard_limit.
        """
        quotas = await self._quota_service.list_quotas(resource_type=QuotaResourceType.COST)
        matching = [q for q in quotas if q.scope == scope and q.scope_id == scope_id]
        if not matching:
            return {"spent": 0.0, "remaining": float("inf"), "utilization_pct": 0.0}

        quota = matching[0]
        usage = await self._quota_service.get_or_create_usage(quota)
        remaining = max(0.0, quota.limit_value - usage.used_value)
        utilization = round((usage.used_value / quota.limit_value) * 100, 2) if quota.limit_value > 0 else 0.0

        return {
            "spent": round(usage.used_value, 6),
            "remaining": round(remaining, 6),
            "utilization_pct": utilization,
            "soft_limit": quota.soft_limit,
            "hard_limit": quota.limit_value,
            "period_start": usage.period_start.isoformat() if hasattr(usage, "period_start") else None,
            "period_end": usage.period_end.isoformat() if hasattr(usage, "period_end") else None,
        }

    async def forecast_remaining(
        self,
        scope: str,
        scope_id: uuid.UUID,
    ) -> dict[str, Any]:
        """Forecast remaining spend using 7-day moving average.

        Returns:
            Dict with current_spend, avg_daily_cost, projected_total, will_exceed, remaining_days.
        """
        now = datetime.now(UTC)
        seven_days_ago = now - timedelta(days=7)

        # Get 7-day average from forecast snapshots
        stmt = select(
            func.sum(BudgetForecastModel.daily_cost).label("total_cost"),
            func.count().label("days"),
        ).where(
            BudgetForecastModel.scope == scope,
            BudgetForecastModel.scope_id == scope_id,
            BudgetForecastModel.date >= seven_days_ago.date(),
            BudgetForecastModel.deleted.is_(False),
        )
        result = await self._db.execute(stmt)
        row = result.one_or_none()

        total_cost_7d = float(row.total_cost or 0) if row else 0.0
        days_with_data = int(row.days or 0) if row else 0
        avg_daily = total_cost_7d / max(days_with_data, 1)

        # Get current period spend
        utilization = await self.get_utilization(scope, scope_id)
        current_spend = utilization.get("spent", 0.0)
        hard_limit = utilization.get("hard_limit", float("inf"))

        # Project to end of month
        days_remaining = (30 - now.day) + 1
        projected_total = current_spend + (avg_daily * days_remaining)
        will_exceed = projected_total > hard_limit if hard_limit != float("inf") else False

        return {
            "current_spend": round(current_spend, 6),
            "avg_daily_cost": round(avg_daily, 6),
            "projected_total": round(projected_total, 6),
            "will_exceed": will_exceed,
            "remaining_days": days_remaining,
        }

    async def create_chargeback(
        self,
        scope: str,
        scope_id: uuid.UUID,
        group_by: str,
        start_date: datetime,
        end_date: datetime,
    ) -> list[dict[str, Any]]:
        """Create chargeback report by grouping cost data.

        Args:
            scope: Budget scope (org, workspace, agent).
            scope_id: Scope UUID.
            group_by: Grouping dimension (agent, workspace, model).
            start_date: Report start date.
            end_date: Report end date.

        Returns:
            List of chargeback entries with cost and percentage.
        """
        breakdown = await self._cost_service.get_cost_breakdown(
            group_by=group_by,
            start_date=start_date,
            end_date=end_date,
            workspace_id=self._workspace_id,
        )

        entries = []
        for entry in breakdown:
            entries.append(
                {
                    "key": entry.key,
                    "cost": entry.cost,
                    "input_tokens": entry.input_tokens,
                    "output_tokens": entry.output_tokens,
                    "percentage": entry.percentage,
                }
            )

        return entries

    async def record_daily_snapshot(
        self,
        scope: str,
        scope_id: uuid.UUID,
    ) -> BudgetForecastModel | None:
        """Record daily cost snapshot for forecasting.

        Called by scheduled task to build forecast history.
        """
        now = datetime.now(UTC)
        today = now.date()

        existing = await self._db.execute(
            select(BudgetForecastModel).where(
                BudgetForecastModel.scope == scope,
                BudgetForecastModel.scope_id == scope_id,
                BudgetForecastModel.date == today,
                BudgetForecastModel.deleted.is_(False),
            )
        )
        if existing.scalar_one_or_none() is not None:
            return None

        start = datetime.combine(today, datetime.min.time().replace(tzinfo=UTC))
        end = start + timedelta(days=1)

        summary = await self._cost_service.get_cost_summary(
            start_date=start,
            end_date=end,
            workspace_id=self._workspace_id,
        )

        snapshot = BudgetForecastModel(
            scope=scope,
            scope_id=scope_id,
            date=today,
            daily_cost=summary.total_cost,
            daily_input_tokens=summary.total_input_tokens,
            daily_output_tokens=summary.total_output_tokens,
            workspace_id=self._workspace_id,
        )
        self._db.add(snapshot)
        await self._db.flush()
        return snapshot


async def record_all_forecast_snapshots(db: AsyncSession) -> int:
    """Record daily forecast snapshots for all workspaces with cost quotas.

    Called by APScheduler daily at midnight UTC. Iterates all workspaces
    that have cost quotas defined and records a BudgetForecastModel entry
    for each.

    Returns:
        Number of snapshots recorded.
    """
    from hecate.models.quota import QuotaModel

    result = await db.execute(
        select(QuotaModel).where(
            QuotaModel.resource_type == QuotaResourceType.COST,
            QuotaModel.deleted.is_(False),
        )
    )
    quotas = result.scalars().all()

    seen: set[tuple[str, uuid.UUID]] = set()
    count = 0

    for quota in quotas:
        key = (quota.scope, quota.scope_id)
        if key in seen:
            continue
        seen.add(key)

        service = BudgetService(db, workspace_id=quota.workspace_id)
        snapshot = await service.record_daily_snapshot(quota.scope, quota.scope_id)
        if snapshot is not None:
            count += 1

    if count > 0:
        await db.commit()
    logger.info("Recorded %d daily forecast snapshots", count)
    return count
