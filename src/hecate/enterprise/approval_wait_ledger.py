"""Ledger construction shared by the approval park/wake paths (step5e).

The dispatcher (park), the wake gate (task control) and the resume consumer
all need the SAME approval authority — the SQL ledger over the session's
bound engine. Stub/test sessions without a bound engine cannot host it;
callers treat ``None`` as fail-closed (park refused, wake rejected) rather
than silently switching to an in-memory authority: a wait whose binding
lives in a different store than the decision is not a durable wait.
"""

from __future__ import annotations

from datetime import UTC

from hecate_governance.approval import ApprovalBinding
from hecate_governance.clock import DEFAULT_TOLERANCE_SECONDS, PROCESS_EPOCH, TrustedClock
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.enterprise.governance_ledgers import SqlApprovalLedger
from hecate.models.approval import ApprovalAuthorityModel


class TaskApprovalLedger(SqlApprovalLedger):
    """Revalidate a restarted task's approval clock with the online database.

    The original reference, decision, expiry and consumer remain unchanged.
    A fresh database timestamp anchors this process; local clock drift and
    database rollback relative to the persisted anchor fail closed.
    """

    async def get(self, approval_ref: str) -> ApprovalBinding | None:
        """Read authority, re-anchoring foreign process clocks when verified."""
        if self._factory is not None:
            async with self._factory.begin() as db:
                return await TaskApprovalLedger(db).get(approval_ref)
        binding = await super().get(approval_ref)
        if binding is None or binding.expires_at is None or binding.clock_epoch == PROCESS_EPOCH:
            return binding
        database_now = await self._db.scalar(select(func.current_timestamp()))
        if database_now is None:
            raise ValueError("approval authority time is unavailable")
        if database_now.tzinfo is None:
            database_now = database_now.replace(tzinfo=UTC)
        anchor = TrustedClock().anchor()
        if abs((anchor.wall - database_now).total_seconds()) > DEFAULT_TOLERANCE_SECONDS:
            raise ValueError("local clock disagrees with approval authority time")
        if (database_now - binding.anchor_wall).total_seconds() < -DEFAULT_TOLERANCE_SECONDS:
            raise ValueError("approval authority time moved backwards")
        result = await self._db.execute(
            update(ApprovalAuthorityModel)
            .where(
                ApprovalAuthorityModel.approval_ref == approval_ref,
                ApprovalAuthorityModel.clock_epoch == binding.clock_epoch,
                ApprovalAuthorityModel.deleted.is_(False),
            )
            .values(anchor_wall=anchor.wall, anchor_monotonic=anchor.monotonic, clock_epoch=anchor.epoch)
        )
        if result.rowcount != 1:
            raise ValueError("approval authority clock changed concurrently")
        await self._db.flush()
        return binding.model_copy(
            update={
                "anchor_wall": anchor.wall,
                "anchor_monotonic": anchor.monotonic,
                "clock_epoch": anchor.epoch,
            }
        )


def task_approval_ledger(db: AsyncSession) -> TaskApprovalLedger | None:
    """The SQL approval ledger over this session's engine, or ``None``."""

    bind = getattr(db, "bind", None)
    if bind is None:
        return None
    from sqlalchemy.ext.asyncio import async_sessionmaker

    return TaskApprovalLedger(db, session_factory=async_sessionmaker(bind, expire_on_commit=False))
