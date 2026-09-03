"""Event-log retention service (1.3.19).

Session-anchored TTL: retention counts from the session's terminal state
(completed/failed/expired). ``interrupted`` sessions are exempt.

Deletion semantics:
  * Whole-session (never partial pruning — fold-from-origin requires the full prefix).
  * Cursor pagination (created_at, id) — batched, off-peak friendly.
  * dry-run mode for ops rehearsal.
  * Cascade: messages and conversation rows tied to the session are
    deleted in the same transaction; PII mappings ride on the org-scoped
    GDPR delete path (separate).
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RetentionConfig:
    conversational_default_days: int = 30
    task_default_days: int = 7
    batch_size: int = 200
    policy: str = "delete"
    warn_threshold_bytes: int = 10 * 1024 * 1024
    warn_threshold_events: int = 10_000


TerminalStateProvider = Callable[[uuid.UUID], Awaitable[str | None]]
"""Returns the terminal status of a session or None if not yet closed."""


@dataclass
class CleanupStats:
    scanned: int = 0
    deleted_events: int = 0
    deleted_sessions: int = 0
    skipped_interrupted: int = 0
    dry_run: bool = False
    last_run_seconds: float = 0.0


class EventRetentionService:
    """Cleans up event log rows whose owning session is past its TTL."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        terminal_state_provider: TerminalStateProvider,
        config: RetentionConfig | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._terminal_state_provider = terminal_state_provider
        self._config = config or RetentionConfig()

    async def cleanup_expired(self, dry_run: bool = False) -> CleanupStats:
        started = time.monotonic()
        stats = CleanupStats(dry_run=dry_run)
        candidates = await self._list_expirable_sessions()
        for session_id, _created_at in candidates:
            stats.scanned += 1
            terminal_status = await self._terminal_state_provider(session_id)
            if terminal_status == "interrupted":
                stats.skipped_interrupted += 1
                continue
            if terminal_status is None:
                continue
            deleted = await self._delete_session_artifacts(session_id, dry_run=dry_run)
            stats.deleted_events += deleted
            if not dry_run:
                stats.deleted_sessions += 1
        stats.last_run_seconds = time.monotonic() - started
        logger.info(
            "retention_cleanup_complete",
            extra={
                "scanned": stats.scanned,
                "deleted_events": stats.deleted_events,
                "deleted_sessions": stats.deleted_sessions,
                "skipped_interrupted": stats.skipped_interrupted,
                "dry_run": stats.dry_run,
                "elapsed_seconds": stats.last_run_seconds,
            },
        )
        return stats

    async def _list_expirable_sessions(self) -> list[tuple[uuid.UUID, datetime]]:
        from hecate.models.session import SessionModel

        cutoff = datetime.now(UTC) - timedelta(days=self._config.conversational_default_days)
        async with self._session_factory() as session:
            stmt = (
                select(SessionModel.id, SessionModel.created_at)
                .where(SessionModel.created_at < cutoff)
                .order_by(SessionModel.created_at, SessionModel.id)
                .limit(self._config.batch_size)
            )
            rows = (await session.execute(stmt)).all()
        return [(row.id, row.created_at) for row in rows]

    async def _delete_session_artifacts(self, session_id: uuid.UUID, *, dry_run: bool) -> int:
        from hecate.models.conversation import ConversationModel
        from hecate.studio.event_state.models import EventModel

        async with self._session_factory() as session:
            count_stmt = select(func.count()).select_from(EventModel).where(EventModel.session_id == session_id)
            event_count = int((await session.execute(count_stmt)).scalar() or 0)
            if dry_run:
                return event_count

            conv_ids_subquery = select(ConversationModel.id).where(ConversationModel.agent_id == session_id)
            await session.execute(delete(ConversationModel).where(ConversationModel.id.in_(conv_ids_subquery)))
            await session.execute(delete(EventModel).where(EventModel.session_id == session_id))
            await session.commit()
        return event_count
