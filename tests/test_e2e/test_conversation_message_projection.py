"""F10 — conversation message projection (A2 closure) end-to-end.

A2 closed the messages-table double-bookkeeping: the messages field
returned by GET /conversations/{id} is now projected from the EventStore
via project_conversation_messages, not read from a stale SQL table.
This test asserts the projection contract end-to-end through the
HTTP layer:

- Seed the EventStore with CHANNEL_WRITE events on the messages
  channel for two sessions attached to one conversation.
- GET /api/conversations/{id}
- Assert the returned messages field equals the projection (role +
  content + created_at), deduped, in chronological order.

A regression here (e.g. if the helper were bypassed and conversations.py
fell back to reading a deleted MessageModel) would surface as an empty
or stale messages field rather than the projected event contents.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from hecate.core.config import settings
from hecate.engine.eventstore import Event, EventType
from hecate.models.conversation import ConversationModel
from hecate.models.session import SessionModel
from hecate.services.event_state import create_event_store


async def test_get_conversation_returns_messages_projected_from_event_log(
    client, db_session, test_user_id, default_workspace
) -> None:
    """Drive the A2 closure through the HTTP layer.

    Sets up two sessions attached to one conversation, writes a mix of
    user + assistant CHANNEL_WRITE events to the event store, and
    asserts GET /conversations/{id} returns the deduped,
    chronologically ordered projection.
    """
    from hecate.main import app

    store = create_event_store(settings)
    app.state.event_store = store
    try:
        conv = ConversationModel(
            workspace_id=default_workspace.id,
            agent_id=uuid.uuid4(),
            title="projection test",
        )
        db_session.add(conv)
        await db_session.flush()

        session_a = SessionModel(id=uuid.uuid4(), conversation_id=conv.id, agent_id=test_user_id)
        session_b = SessionModel(id=uuid.uuid4(), conversation_id=conv.id, agent_id=test_user_id)
        db_session.add_all([session_a, session_b])
        await db_session.flush()

        base = datetime.now(UTC)
        # Session A — user + assistant (chronological)
        await store.append(
            Event(
                session_id=session_a.id,
                superstep=1,
                event_type=EventType.CHANNEL_WRITE,
                payload={"channel": "messages", "value": {"role": "user", "content": "hello"}},
                timestamp=base,
            )
        )
        await store.append(
            Event(
                session_id=session_a.id,
                superstep=2,
                event_type=EventType.CHANNEL_WRITE,
                payload={
                    "channel": "messages",
                    "value": {"role": "assistant", "content": "world"},
                },
                timestamp=base + timedelta(seconds=1),
            )
        )
        # Session B — a later user turn
        await store.append(
            Event(
                session_id=session_b.id,
                superstep=1,
                event_type=EventType.CHANNEL_WRITE,
                payload={"channel": "messages", "value": {"role": "user", "content": "again"}},
                timestamp=base + timedelta(seconds=2),
            )
        )

        response = await client.get(f"/api/conversations/{conv.id}")
        assert response.status_code == 200
        body = response.json()
        messages = body["messages"]
        # 3 distinct (role, content) pairs across both sessions.
        assert len(messages) == 3, f"expected 3 projected messages, got {messages}"
        assert [(m["role"], m["content"]) for m in messages] == [
            ("user", "hello"),
            ("assistant", "world"),
            ("user", "again"),
        ]
        # Chronological: timestamps strictly increasing.
        timestamps = [datetime.fromisoformat(m["created_at"]) for m in messages]
        assert timestamps == sorted(timestamps)
    finally:
        # Lifespan path sets app.state.event_store; restore to None so
        # later tests aren't reading from a transient in-memory store.
        app.state.event_store = None
