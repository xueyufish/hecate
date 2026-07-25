"""Security finding service — query and retention for SecurityFindingModel.

Provides query methods for the REST API and retention cleanup.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.config import settings
from hecate.core.database import async_session_factory
from hecate.models.security_finding import (
    SecurityFindingModel,
    SecurityFindingQuerySchema,
    SecurityFindingReadSchema,
)

logger = logging.getLogger(__name__)


_SEVERITY_ORDER = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}


class SecurityFindingService:
    """Service for querying and managing security findings.

    Args:
        retention_days: Findings older than this are auto-deleted.
    """

    def __init__(self, retention_days: int | None = None) -> None:
        self._retention_days = retention_days or settings.SECURITY_FINDING_RETENTION_DAYS

    @staticmethod
    def _severities_at_or_above(min_sev: str) -> list[str]:
        """Return all severity levels at or above the given level."""
        threshold = _SEVERITY_ORDER.get(min_sev.lower(), 0)
        return [sev for sev, rank in _SEVERITY_ORDER.items() if rank >= threshold]

    async def query(
        self,
        params: SecurityFindingQuerySchema,
        session: AsyncSession | None = None,
    ) -> tuple[list[SecurityFindingReadSchema], int]:
        """Query security findings with filtering."""
        if session is not None:
            return await self._query_with_session(session, params)
        async with async_session_factory() as sess:
            return await self._query_with_session(sess, params)

    async def _query_with_session(
        self,
        session: AsyncSession,
        params: SecurityFindingQuerySchema,
    ) -> tuple[list[SecurityFindingReadSchema], int]:
        stmt = select(SecurityFindingModel)

        if params.org_id:
            stmt = stmt.where(SecurityFindingModel.org_id == params.org_id)
        if params.workspace_id:
            stmt = stmt.where(SecurityFindingModel.workspace_id == params.workspace_id)
        if params.user_id:
            stmt = stmt.where(SecurityFindingModel.user_id == params.user_id)
        if params.rule_name:
            stmt = stmt.where(SecurityFindingModel.rule_name == params.rule_name)
        if params.severity:
            allowed = self._severities_at_or_above(params.severity)
            stmt = stmt.where(SecurityFindingModel.severity.in_(allowed))
        if params.start:
            stmt = stmt.where(SecurityFindingModel.created_at >= params.start)
        if params.end:
            stmt = stmt.where(SecurityFindingModel.created_at <= params.end)

        # Exclude soft-deleted
        stmt = stmt.where(SecurityFindingModel.deleted == False)  # noqa: E712

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await session.execute(count_stmt)).scalar_one()

        stmt = stmt.order_by(SecurityFindingModel.created_at.desc()).limit(params.limit).offset(params.offset)
        result = await session.execute(stmt)
        rows = result.scalars().all()
        findings = [SecurityFindingReadSchema.model_validate(row) for row in rows]

        return findings, total

    async def cleanup_expired(self) -> int:
        """Delete findings older than the retention period.

        Returns:
            Number of findings deleted.
        """
        cutoff = datetime.now(UTC) - timedelta(days=self._retention_days)
        async with async_session_factory() as session:
            stmt = delete(SecurityFindingModel).where(
                SecurityFindingModel.created_at < cutoff,
            )
            result = await session.execute(stmt)
            await session.commit()
            deleted = result.rowcount
            if deleted:
                logger.info("Cleaned up %d expired security findings", deleted)
            return deleted
