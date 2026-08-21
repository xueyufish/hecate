"""Garbage collector agent for scanning expired and orphaned resources.

Reports resources eligible for cleanup without performing any deletions,
following the "report only, no auto-fix" design decision.

13.4a-7: orphaned-checkpoint scanning was removed together with the
``checkpoints`` table — checkpoints are no longer a persisted resource.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.session import SessionModel

logger = logging.getLogger(__name__)

_ESTIMATED_SESSION_BYTES = 4_096
_ESTIMATED_TOOL_BYTES = 2_048


@dataclass
class CleanupReport:
    """Report of resources eligible for cleanup."""

    expired_sessions: int = 0
    unused_tools: int = 0
    estimated_storage_bytes: int = 0
    details: list[dict[str, Any]] = field(default_factory=list)


class GarbageCollectorAgent:
    """Scans for expired, orphaned, and unused resources.

    Generates cleanup reports without performing any deletions.
    """

    async def scan_expired_sessions(self, db: AsyncSession, retention_days: int = 30) -> list[UUID]:
        """Find sessions older than retention period that are not active."""
        cutoff = datetime.now(UTC) - timedelta(days=retention_days)
        stmt = select(SessionModel.id).where(
            SessionModel.created_at < cutoff,
            SessionModel.status != "active",
        )
        result = await db.execute(stmt)
        ids = [row[0] for row in result.all()]
        logger.info("Found %d expired sessions (retention=%dd)", len(ids), retention_days)
        return ids

    async def generate_cleanup_report(self, db: AsyncSession, retention_days: int = 30) -> CleanupReport:
        """Generate a full cleanup report by scanning all resource types."""
        expired = await self.scan_expired_sessions(db, retention_days)

        storage = len(expired) * _ESTIMATED_SESSION_BYTES

        details: list[dict[str, Any]] = []
        for sid in expired:
            details.append({"type": "expired_session", "id": str(sid)})

        report = CleanupReport(
            expired_sessions=len(expired),
            estimated_storage_bytes=storage,
            details=details,
        )
        logger.info(
            "Cleanup report: %d expired sessions, ~%d bytes",
            report.expired_sessions,
            report.estimated_storage_bytes,
        )
        return report

    async def run(self, db: AsyncSession, retention_days: int = 30) -> CleanupReport:
        """Convenience method to run a full scan and return a report."""
        return await self.generate_cleanup_report(db, retention_days)
