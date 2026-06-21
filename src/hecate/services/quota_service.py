"""Business logic for quota definitions, usage tracking, and enforcement checks."""

from __future__ import annotations

import calendar
import logging
import time
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.config import settings
from hecate.models.alert import AlertState
from hecate.models.quota import (
    EnforcementMode,
    QuotaModel,
    QuotaUsageModel,
    QuotaWindowType,
)

logger = logging.getLogger(__name__)

_DEFAULT_WORKSPACE = uuid.UUID("00000000-0000-0000-0000-000000000000")

_cache: dict[uuid.UUID, tuple[float, list[QuotaModel]]] = {}


def _period_bounds(window_type: str, now: datetime | None = None) -> tuple[datetime, datetime]:
    """Compute (period_start, period_end) for a window type at the given time."""
    now = now or datetime.now(UTC)
    if window_type == QuotaWindowType.DAILY:
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return start, start + timedelta(days=1)
    if window_type == QuotaWindowType.MONTHLY:
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        days = calendar.monthrange(now.year, now.month)[1]
        return start, start + timedelta(days=days)
    return now - timedelta(seconds=60), now


class QuotaService:
    """Service for quota management and enforcement.

    Args:
        db: Async SQLAlchemy session.
        workspace_id: Optional workspace scope.
    """

    def __init__(self, db: AsyncSession, workspace_id: uuid.UUID | None = None) -> None:
        self._db = db
        self._workspace_id = workspace_id or _DEFAULT_WORKSPACE

    async def create_quota(self, **kwargs: object) -> QuotaModel:
        """Create a new quota definition."""
        quota = QuotaModel(workspace_id=self._workspace_id, **kwargs)
        self._db.add(quota)
        await self._db.flush()
        _invalidate_cache(self._workspace_id)
        return quota

    async def list_quotas(
        self,
        *,
        resource_type: str | None = None,
        scope: str | None = None,
    ) -> list[QuotaModel]:
        """List quota definitions for the current workspace."""
        stmt = select(QuotaModel).where(
            QuotaModel.workspace_id == self._workspace_id,
            QuotaModel.deleted == False,  # noqa: E712
        )
        if resource_type:
            stmt = stmt.where(QuotaModel.resource_type == resource_type)
        if scope:
            stmt = stmt.where(QuotaModel.scope == scope)
        result = await self._db.execute(stmt)
        return list(result.scalars().all())

    async def get_quota(self, quota_id: uuid.UUID) -> QuotaModel | None:
        """Get a single quota by ID."""
        stmt = select(QuotaModel).where(
            QuotaModel.id == quota_id,
            QuotaModel.deleted == False,  # noqa: E712
        )
        return (await self._db.execute(stmt)).scalar_one_or_none()

    async def update_quota(self, quota_id: uuid.UUID, **kwargs: object) -> QuotaModel | None:
        """Update a quota definition."""
        quota = await self.get_quota(quota_id)
        if quota is None:
            return None
        for key, value in kwargs.items():
            if value is not None and hasattr(quota, key):
                setattr(quota, key, value)
        await self._db.flush()
        _invalidate_cache(self._workspace_id)
        return quota

    async def delete_quota(self, quota_id: uuid.UUID) -> bool:
        """Soft-delete a quota definition."""
        quota = await self.get_quota(quota_id)
        if quota is None:
            return False
        quota.deleted = True
        quota.deleted_at = datetime.now(UTC)
        await self._db.flush()
        _invalidate_cache(self._workspace_id)
        return True

    async def get_active_quotas(self) -> list[QuotaModel]:
        """Get cached active quotas for the workspace, refreshing if stale."""
        now = time.monotonic()
        cached = _cache.get(self._workspace_id)
        if cached and (now - cached[0]) < settings.QUOTA_CACHE_TTL:
            return cached[1]
        quotas = await self.list_quotas()
        filtered = [q for q in quotas if q.enabled]
        _cache[self._workspace_id] = (now, filtered)
        return filtered

    async def get_or_create_usage(self, quota: QuotaModel) -> QuotaUsageModel:
        """Get the current-period usage record, creating a new one if expired."""
        now = datetime.now(UTC)
        period_start, period_end = _period_bounds(quota.window_type, now)

        stmt = (
            select(QuotaUsageModel)
            .where(
                QuotaUsageModel.quota_id == quota.id,
                QuotaUsageModel.period_start <= now,
                QuotaUsageModel.period_end > now,
                QuotaUsageModel.deleted == False,  # noqa: E712
            )
            .order_by(QuotaUsageModel.period_start.desc())
            .limit(1)
        )
        existing = (await self._db.execute(stmt)).scalar_one_or_none()
        if existing:
            return existing

        usage = QuotaUsageModel(
            quota_id=quota.id,
            period_start=period_start,
            period_end=period_end,
            used_value=0.0,
            workspace_id=self._workspace_id,
        )
        self._db.add(usage)
        await self._db.flush()
        return usage

    async def check_quota(
        self,
        resource_type: str,
        scope: str,
        scope_id: uuid.UUID,
        window_type: str,
    ) -> tuple[bool, float, datetime | None]:
        """Check if a request is within quota. Returns (allowed, remaining, reset_at)."""
        quotas = await self.get_active_quotas()
        matching = [
            q
            for q in quotas
            if q.resource_type == resource_type
            and q.scope == scope
            and q.scope_id == scope_id
            and q.window_type == window_type
        ]
        if not matching:
            return True, float("inf"), None

        quota = matching[0]
        usage = await self.get_or_create_usage(quota)
        remaining = max(0.0, quota.limit_value - usage.used_value)

        if quota.enforcement == EnforcementMode.SOFT_ALLOW:
            return True, remaining, usage.period_end
        return remaining > 0, remaining, usage.period_end

    async def record_usage(
        self,
        resource_type: str,
        scope: str,
        scope_id: uuid.UUID,
        window_type: str,
        amount: float,
    ) -> None:
        """Record resource consumption against applicable quotas."""
        quotas = await self.get_active_quotas()
        matching = [
            q
            for q in quotas
            if q.resource_type == resource_type
            and q.scope == scope
            and q.scope_id == scope_id
            and q.window_type == window_type
        ]
        for quota in matching:
            usage = await self.get_or_create_usage(quota)
            usage.used_value += amount
            usage.last_updated = datetime.now(UTC)
            await self._db.flush()

            if quota.soft_limit is not None and not usage.soft_limit_triggered and usage.used_value >= quota.soft_limit:
                usage.soft_limit_triggered = True
                await self._trigger_soft_limit_alert(quota, usage)
                logger.info(
                    "Soft limit crossed for quota %s: %.2f / %.2f",
                    quota.name,
                    usage.used_value,
                    quota.soft_limit,
                )

    async def list_usage(self, resource_type: str | None = None) -> list[dict]:
        """List current usage for all quotas in the workspace."""
        quotas = await self.get_active_quotas()
        if resource_type:
            quotas = [q for q in quotas if q.resource_type == resource_type]

        results: list[dict] = []
        for quota in quotas:
            usage = await self.get_or_create_usage(quota)
            remaining = max(0.0, quota.limit_value - usage.used_value)
            utilization = round((usage.used_value / quota.limit_value) * 100, 2) if quota.limit_value > 0 else 0.0
            results.append(
                {
                    "quota_id": str(quota.id),
                    "name": quota.name,
                    "resource_type": quota.resource_type,
                    "limit_value": quota.limit_value,
                    "used_value": usage.used_value,
                    "soft_limit": quota.soft_limit,
                    "remaining": remaining,
                    "utilization_pct": utilization,
                    "period_start": usage.period_start.isoformat(),
                    "period_end": usage.period_end.isoformat(),
                    "enforcement": quota.enforcement,
                }
            )
        return results

    async def reset_quota(self, quota_id: uuid.UUID) -> bool:
        """Reset the current period's usage to zero."""
        quota = await self.get_quota(quota_id)
        if quota is None:
            return False
        usage = await self.get_or_create_usage(quota)
        usage.used_value = 0.0
        usage.soft_limit_triggered = False
        usage.last_updated = datetime.now(UTC)
        await self._db.flush()
        return True

    async def _trigger_soft_limit_alert(self, quota: QuotaModel, usage: QuotaUsageModel) -> None:
        """Create an alert event when soft limit is crossed."""
        from hecate.services.alert_service import AlertService

        alert_service = AlertService(self._db, workspace_id=self._workspace_id)
        utilization = round((usage.used_value / quota.limit_value) * 100, 2) if quota.limit_value > 0 else 0.0
        existing = await alert_service.get_active_event_for_rule(quota.id)
        if existing is not None:
            return

        await alert_service.create_event(
            rule_id=quota.id,
            state=AlertState.FIRING,
            current_value=utilization,
            fired_at=datetime.now(UTC),
        )
        logger.info(
            "Quota soft limit alert created for '%s' at %.1f%% utilization",
            quota.name,
            utilization,
        )


def _invalidate_cache(workspace_id: uuid.UUID) -> None:
    """Invalidate the quota cache for a workspace."""
    _cache.pop(workspace_id, None)
