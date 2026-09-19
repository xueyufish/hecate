"""Tests for the conversation recall indexer and recall search service.

The indexer projects persisted ``CHANNEL_WRITE`` (messages-channel) events
into ``recall_messages`` + vectors; the search service reads that layer back
with cursor pagination, time windows and session exclusion.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select

from hecate.core import database as core_db
from hecate.models.memory import RecallMessageModel
from hecate.models.session import SessionModel  # noqa: F401

# Register the events-table model in the test metadata before create_all runs.
from hecate.studio.event_state.models import EventModel  # noqa: F401
from tests.conftest import test_session_factory


@pytest.fixture(autouse=True)
def _bind_sessions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(core_db, "async_session_factory", test_session_factory)


def _make_indexer() -> Any:
    from hecate_memory.memory.recall_indexer import RecallIndexerService

    return RecallIndexerService(vector_store=None)


async def _seed_session_event(
    db: Any,
    session_id: uuid.UUID,
    version: int,
    role: str,
    content: str,
) -> None:
    db.add(
        EventModel(
            session_id=session_id,
            version=version,
            id=uuid.uuid4(),
            superstep=0,
            event_type="CHANNEL_WRITE",
            payload={"channel": "messages", "value": {"role": role, "content": content}},
        )
    )
    await db.flush()


async def _seed_session_row(db: Any, session_id: uuid.UUID, ws: uuid.UUID, agent: uuid.UUID) -> None:
    db.add(
        SessionModel(
            id=session_id,
            agent_id=agent,
            workspace_id=ws,
            status="completed",
        )
    )
    await db.flush()


async def test_index_messages_idempotent(db_session: Any) -> None:
    ws, agent, sid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    indexer = _make_indexer()
    messages = [
        {"role": "user", "content": "How do I deploy on staging?"},
        {"role": "assistant", "content": "Use the staging pipeline with a tag."},
        {"role": "tool", "content": "SHOULD BE SKIPPED"},
        {"role": "system", "content": "ALSO SKIPPED"},
    ]

    first = await indexer.index_messages(workspace_id=ws, agent_id=agent, session_id=sid, messages=messages)
    assert first == 2

    # Re-running the same batch converges (unique key absorbs duplicates).
    second = await indexer.index_messages(
        workspace_id=ws, agent_id=agent, session_id=sid, messages=messages, start_seq=0
    )
    assert second == 0

    rows = (await db_session.execute(select(RecallMessageModel))).scalars().all()
    assert len(rows) == 2
    assert {r.role for r in rows} == {"user", "assistant"}


async def test_poll_once_indexes_new_events_only(db_session: Any) -> None:
    ws, agent = uuid.uuid4(), uuid.uuid4()
    sid = uuid.uuid4()
    await _seed_session_row(db_session, sid, ws, agent)
    await _seed_session_event(db_session, sid, 1, "user", "First question about qdrant")
    await _seed_session_event(db_session, sid, 2, "assistant", "Qdrant is a vector database")
    await _seed_session_event(db_session, sid, 3, "user", "Follow-up about hybrid search")
    await db_session.flush()

    indexer = _make_indexer()
    # Direct-message route pre-indexes versions 1-2 (start_seq aligns the
    # per-session sequence numbers with the event log versions).
    first = await indexer.index_messages(
        workspace_id=ws,
        agent_id=agent,
        session_id=sid,
        start_seq=1,
        messages=[
            {"role": "user", "content": "First question about qdrant"},
            {"role": "assistant", "content": "Qdrant is a vector database"},
        ],
    )
    assert first == 2

    indexed = await indexer.poll_once()
    # The watermark (version 2) hides events 1-2; only event 3 is new.
    assert indexed == 1

    # A second poll is a no-op — the watermark moved.
    assert await indexer.poll_once() == 0


async def test_purge_conversation_removes_rows(db_session: Any) -> None:
    ws, agent = uuid.uuid4(), uuid.uuid4()
    sid = uuid.uuid4()
    conv = uuid.uuid4()
    indexer = _make_indexer()
    await indexer.index_messages(
        workspace_id=ws,
        agent_id=agent,
        session_id=sid,
        conversation_id=conv,
        messages=[{"role": "user", "content": "to be purged"}],
    )
    rows = (await db_session.execute(select(RecallMessageModel))).scalars().all()
    assert len(rows) == 1

    purged = await indexer.purge_conversation(conv)
    assert purged == 1
    remaining = (await db_session.execute(select(RecallMessageModel))).scalars().all()
    assert remaining == []


async def test_stats_reports_counts(db_session: Any) -> None:
    ws = uuid.uuid4()
    indexer = _make_indexer()
    await indexer.index_messages(
        workspace_id=ws,
        agent_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        messages=[{"role": "user", "content": "counted"}],
    )
    stats = await indexer.stats()
    assert stats["total"] >= 1
    assert stats["last_indexed_at"] is not None
    assert any(n >= 1 for n in stats["by_workspace"].values())


# ---------------------------------------------------------------------------
# RecallService search semantics
# ---------------------------------------------------------------------------


def _make_service(db: Any) -> Any:
    from hecate_memory.memory.recall import RecallService

    return RecallService(db, vector_store=None)


async def test_recall_search_time_window_and_roles(db_session: Any) -> None:
    """Without a vector store the search degrades to low-signal pages; the
    metadata verification path (time window, roles) is exercised via the
    hydrated hits."""

    svc = _make_service(db_session)
    page = await svc.search(
        query="anything",
        workspace_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        start_date=datetime.now(UTC) - timedelta(days=1),
        end_date=datetime.now(UTC) + timedelta(days=1),
        roles=["user"],
    )
    assert page.hits == []
    assert page.low_signal is True


async def test_recall_cursor_roundtrip(db_session: Any) -> None:
    from hecate_memory.memory.recall import _decode_cursor, _encode_cursor

    cursor = _encode_cursor(0.42, str(uuid.uuid4()))
    score, last_id = _decode_cursor(cursor)
    assert score == 0.42
    assert uuid.UUID(last_id)  # parses

    assert _decode_cursor("garbage!!!") == (None, None)
    assert _decode_cursor(None) == (None, None)


# ---------------------------------------------------------------------------
# Retention interplay (spec: retention cleanup never touches the recall layer)
# ---------------------------------------------------------------------------


async def test_event_retention_spares_recall_layer(db_session: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Retention deletes the session's events but recall rows survive."""
    from datetime import timedelta as _td

    from hecate.ops.retention.event_retention_service import EventRetentionService, RetentionConfig
    from hecate.studio.event_state.models import EventModel

    ws, agent = uuid.uuid4(), uuid.uuid4()
    sid = uuid.uuid4()
    await _seed_session_row(db_session, sid, ws, agent)
    await _seed_session_event(db_session, sid, 1, "user", "remember this exchange")
    await db_session.flush()

    indexer = _make_indexer()
    await indexer.index_messages(
        workspace_id=ws,
        agent_id=agent,
        session_id=sid,
        messages=[{"role": "user", "content": "remember this exchange"}],
        start_seq=1,
    )

    # Age the session past the TTL and run retention cleanup.
    session_row = await db_session.get(SessionModel, sid)
    session_row.created_at = datetime.now(UTC) - _td(days=60)
    await db_session.flush()

    service = EventRetentionService(
        test_session_factory,
        terminal_state_provider=lambda _sid: _completed(),
        config=RetentionConfig(conversational_default_days=30),
    )
    stats = await service.cleanup_expired()
    assert stats.deleted_events >= 1

    remaining_events = (
        (await db_session.execute(select(EventModel).where(EventModel.session_id == sid))).scalars().all()
    )
    assert remaining_events == []

    recall_rows = (
        (await db_session.execute(select(RecallMessageModel).where(~RecallMessageModel.deleted))).scalars().all()
    )
    assert len(recall_rows) == 1
    assert recall_rows[0].content == "remember this exchange"


async def _completed() -> str:
    return "completed"
