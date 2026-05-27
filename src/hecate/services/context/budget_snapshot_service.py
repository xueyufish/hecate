"""Budget snapshot recording service.

Persists budget usage snapshots to the database after each LLM call
for observability and cost tracking integration.
"""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.budget import BudgetSnapshotModel

logger = logging.getLogger(__name__)


class BudgetSnapshotService:
    """Service for persisting budget snapshots to the database.

    Records budget usage after each LLM call for observability,
    cost tracking, and debugging context engineering decisions.
    """

    def __init__(self, db_session: AsyncSession) -> None:
        """Initialize with a database session.

        Args:
            db_session: Async SQLAlchemy session for database operations.
        """
        self.db = db_session

    async def record_snapshot(
        self,
        session_id: UUID,
        total_budget: int,
        tokens_used: int,
        tokens_remaining: int,
        degradation_level: str = "none",
    ) -> BudgetSnapshotModel:
        """Record a budget snapshot after an LLM call.

        Args:
            session_id: The session this snapshot belongs to.
            total_budget: Total token budget allocated.
            tokens_used: Tokens used in this call.
            tokens_remaining: Remaining tokens in budget.
            degradation_level: Degradation level applied (none/drop/compress/emergency).

        Returns:
            The created BudgetSnapshotModel.
        """
        snapshot = BudgetSnapshotModel(
            session_id=session_id,
            total_budget=total_budget,
            tokens_used=tokens_used,
            tokens_remaining=tokens_remaining,
            degradation_level=degradation_level,
        )
        self.db.add(snapshot)
        await self.db.flush()
        logger.debug(f"Recorded budget snapshot for session {session_id}: {tokens_used}/{total_budget} tokens")
        return snapshot

    async def get_snapshots(
        self,
        session_id: UUID,
        limit: int = 100,
    ) -> list[BudgetSnapshotModel]:
        """Get budget snapshots for a session.

        Args:
            session_id: The session to get snapshots for.
            limit: Maximum number of snapshots to return.

        Returns:
            List of budget snapshots ordered by creation time.
        """
        stmt = (
            select(BudgetSnapshotModel)
            .where(
                BudgetSnapshotModel.session_id == session_id,
                BudgetSnapshotModel.deleted_at.is_(None),
            )
            .order_by(BudgetSnapshotModel.created_at.desc())
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_latest_snapshot(
        self,
        session_id: UUID,
    ) -> BudgetSnapshotModel | None:
        """Get the most recent budget snapshot for a session.

        Args:
            session_id: The session to get snapshot for.

        Returns:
            Latest BudgetSnapshotModel or None if no snapshots exist.
        """
        stmt = (
            select(BudgetSnapshotModel)
            .where(
                BudgetSnapshotModel.session_id == session_id,
                BudgetSnapshotModel.deleted_at.is_(None),
            )
            .order_by(BudgetSnapshotModel.created_at.desc())
            .limit(1)
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()
