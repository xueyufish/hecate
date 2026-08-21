"""A2 closure — project user/assistant messages from the event log.

The ``messages`` table was deleted as part of the A2 cleanup; downstream
consumers (embedding, topic matching, quality scoring, retention) now
read from the EventStore. This module is the single read-side seam for
``(conversation_id, role in [user, assistant])`` projections.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.engine.eventstore import EventStore
from hecate.models.session import SessionModel
from hecate.services.replay.assembler import derive_session_messages


async def project_conversation_messages(
    db: AsyncSession,
    conversation_id: uuid.UUID,
    event_store: EventStore | None,
    *,
    limit: int | None = None,
) -> list[dict[str, str]]:
    """Return user/assistant message dicts for a conversation.

    A2: reads the event log rather than the (deleted) ``messages``
    table. Joins across all sessions attached to the conversation,
    deduplicates, and sorts chronologically.

    Args:
        db: Async DB session for the SessionModel JOIN.
        conversation_id: Conversation whose messages to project.
        event_store: The process-wide EventStore (from lifespan). If
            ``None``, a transient in-memory store is created for the
            lifetime of this call — adequate for tests, fragile for
            production reads (the persistent store must be wired).
        limit: Optional cap on returned message count. ``None`` returns
            the full ordered list.

    Returns:
        List of ``{"role", "content", "created_at"}`` dicts filtered
        to user and assistant turns, deduplicated by ``(role, content)``
        and sorted by ``(superstep, version)``.
    """
    session_rows = (
        (await db.execute(select(SessionModel).where(SessionModel.conversation_id == conversation_id))).scalars().all()
    )

    if event_store is None:
        from hecate.core.config import settings
        from hecate.services.event_state import create_event_store

        event_store = create_event_store(settings)

    messages: list[dict] = []
    for session in session_rows:
        messages.extend(await derive_session_messages(session.id, event_store))

    messages = [m for m in messages if m.get("role") in ("user", "assistant")]
    if limit is not None:
        messages = messages[:limit]
    return messages
