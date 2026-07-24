"""Security audit service — batch writer and query for SecurityAuditEvent.

Implements ``AuditSink`` from the engine layer, buffering events in an
async queue and flushing to ``SecurityAuditModel`` in batches. Provides
query API for the REST endpoint.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.config import settings
from hecate.core.database import async_session_factory
from hecate.engine.audit_sink import AuditSink
from hecate.models.security_audit import (
    SecurityAuditModel,
    SecurityAuditQuerySchema,
    SecurityAuditReadSchema,
)

logger = logging.getLogger(__name__)


class SecurityAuditService(AuditSink):
    """Service for writing and querying structured security audit events.

    Implements ``AuditSink`` for engine-layer emission (via ``emit()``)
    and provides query methods for the REST API.

    Args:
        batch_size: Max events per flush cycle.
        flush_interval: Seconds between flush cycles.
        retention_days: Events older than this are auto-deleted.
    """

    def __init__(
        self,
        batch_size: int | None = None,
        flush_interval: float | None = None,
        retention_days: int | None = None,
    ) -> None:
        self._batch_size = batch_size or settings.AGENT_ENV_AUDIT_BATCH_SIZE
        self._flush_interval = flush_interval or settings.AGENT_ENV_AUDIT_FLUSH_INTERVAL
        self._retention_days = retention_days or settings.AGENT_ENV_AUDIT_RETENTION_DAYS
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(
            maxsize=settings.AUDIT_QUEUE_MAX_SIZE,
        )
        self._drain_task: asyncio.Task[None] | None = None
        self._cleanup_task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------------
    # AuditSink implementation (called from engine, must be non-blocking)
    # ------------------------------------------------------------------

    def emit(self, event: dict[str, Any]) -> None:
        """Buffer an audit event for async batch write.

        Non-blocking: uses ``put_nowait``. If queue is full, logs a warning
        and drops the event (fail-open to avoid blocking tool execution).
        """
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning(
                "Security audit queue full (%d events), dropping event for tool '%s'",
                self._queue.maxsize,
                event.get("tool_name", "?"),
            )

    # ------------------------------------------------------------------
    # Background tasks
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the background drain and cleanup tasks."""
        if self._drain_task is None or self._drain_task.done():
            self._drain_task = asyncio.create_task(self._drain_loop())
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        logger.info(
            "SecurityAuditService started (batch_size=%d, flush=%.1fs, retention=%dd)",
            self._batch_size,
            self._flush_interval,
            self._retention_days,
        )

    async def stop(self) -> None:
        """Flush remaining events and stop background tasks."""
        for task in (self._drain_task, self._cleanup_task):
            if task and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        await self._flush_batch()
        logger.info("SecurityAuditService stopped")

    async def _drain_loop(self) -> None:
        """Background loop that drains the queue in batches."""
        while True:
            try:
                await asyncio.sleep(self._flush_interval)
                await self._flush_batch()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Security audit drain loop error")
                await asyncio.sleep(1.0)

    async def _flush_batch(self) -> None:
        """Flush queued events to the database."""
        if self._queue.empty():
            return

        batch: list[dict[str, Any]] = []
        while not self._queue.empty() and len(batch) < self._batch_size:
            try:
                batch.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break

        if not batch:
            return

        try:
            async with async_session_factory() as session:
                for event in batch:
                    row = SecurityAuditModel(
                        agent_id=event.get("agent_id", ""),
                        workspace_id=event.get("workspace_id", ""),
                        session_id=event.get("session_id"),
                        tool_name=event.get("tool_name", ""),
                        arguments_hash=event.get("arguments_hash", ""),
                        decision=event.get("decision", ""),
                        reason=event.get("reason", ""),
                        policy_version=event.get("policy_version", ""),
                        on_behalf_of_user=event.get("on_behalf_of_user"),
                        layer_results=event.get("layer_results", []),
                    )
                    session.add(row)
                await session.commit()
            logger.debug("Flushed %d security audit events", len(batch))
        except Exception:
            logger.exception("Failed to flush %d security audit events", len(batch))

    async def _cleanup_loop(self) -> None:
        """Daily cleanup of expired audit events."""
        while True:
            try:
                await asyncio.sleep(86400)  # 24 hours
                cutoff = datetime.now(UTC) - timedelta(days=self._retention_days)
                async with async_session_factory() as session:
                    stmt = delete(SecurityAuditModel).where(
                        SecurityAuditModel.timestamp < cutoff,
                    )
                    result = await session.execute(stmt)
                    await session.commit()
                    deleted = result.rowcount
                    if deleted:
                        logger.info("Cleaned up %d expired security audit events", deleted)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Security audit cleanup error")
                await asyncio.sleep(3600)

    # ------------------------------------------------------------------
    # Query API (for REST endpoints)
    # ------------------------------------------------------------------

    async def query(
        self,
        params: SecurityAuditQuerySchema,
        session: AsyncSession | None = None,
    ) -> tuple[list[SecurityAuditReadSchema], int]:
        """Query security audit events with filtering.

        Args:
            params: Query filter parameters.
            session: Optional session for testing. If None, creates one
                from the global session factory.

        Returns:
            Tuple of (event list, total count).
        """
        if session is not None:
            return await self._query_with_session(session, params)
        async with async_session_factory() as sess:
            return await self._query_with_session(sess, params)

    async def _query_with_session(
        self,
        session: AsyncSession,
        params: SecurityAuditQuerySchema,
    ) -> tuple[list[SecurityAuditReadSchema], int]:
        stmt = select(SecurityAuditModel)

        if params.agent_id:
            stmt = stmt.where(SecurityAuditModel.agent_id == params.agent_id)
        if params.workspace_id:
            stmt = stmt.where(SecurityAuditModel.workspace_id == params.workspace_id)
        if params.session_id:
            stmt = stmt.where(SecurityAuditModel.session_id == params.session_id)
        if params.decision:
            stmt = stmt.where(SecurityAuditModel.decision == params.decision)
        if params.tool_name:
            stmt = stmt.where(SecurityAuditModel.tool_name == params.tool_name)
        if params.start:
            stmt = stmt.where(SecurityAuditModel.timestamp >= params.start)
        if params.end:
            stmt = stmt.where(SecurityAuditModel.timestamp <= params.end)

        # Exclude soft-deleted
        stmt = stmt.where(SecurityAuditModel.deleted == False)  # noqa: E712

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await session.execute(count_stmt)).scalar_one()

        stmt = stmt.order_by(SecurityAuditModel.timestamp.desc()).limit(params.limit).offset(params.offset)
        result = await session.execute(stmt)
        rows = result.scalars().all()
        events = [SecurityAuditReadSchema.model_validate(row) for row in rows]

        return events, total
