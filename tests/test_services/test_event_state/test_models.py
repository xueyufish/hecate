"""Unit tests for ``EventModel`` ORM mapping.

Validates that the model round-trips a JSON payload through the ORM and that
the composite primary key ``(session_id, version)`` enforces uniqueness. Uses
the shared ``db_session`` fixture (in-memory SQLite via the conftest autouse
setup) — PG-specific types (PG_UUID, JSON) degrade cleanly to SQLite
equivalents under SQLAlchemy 2.0.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from hecate.studio.event_state.models import EventModel


async def test_eventmodel_round_trips_jsonb_payload(db_session):
    """EventModel SHALL persist and reload a dict payload without loss."""
    session_id = uuid.uuid4()
    event = EventModel(
        session_id=session_id,
        version=1,
        id=uuid.uuid4(),
        superstep=0,
        event_type="NODE_START",
        node_id="agent_1",
        trace_id="trace-abc",
        payload={"tool": "search", "latency_ms": 42, "nested": {"k": [1, 2, 3]}},
        org_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
    )
    db_session.add(event)
    await db_session.flush()

    result = await db_session.execute(
        select(EventModel).where(EventModel.session_id == session_id, EventModel.version == 1)
    )
    row = result.scalar_one()
    assert row.payload["tool"] == "search"
    assert row.payload["latency_ms"] == 42
    assert row.payload["nested"] == {"k": [1, 2, 3]}
    assert row.event_type == "NODE_START"
    assert row.node_id == "agent_1"
    assert row.trace_id == "trace-abc"


async def test_eventmodel_org_user_nullable(db_session):
    """EventModel SHALL allow org_id/user_id to be None for test paths."""
    session_id = uuid.uuid4()
    event = EventModel(
        session_id=session_id,
        version=1,
        id=uuid.uuid4(),
        superstep=0,
        event_type="CUSTOM",
        node_id=None,
        trace_id=None,
        payload={},
        org_id=None,
        user_id=None,
    )
    db_session.add(event)
    await db_session.flush()

    result = await db_session.execute(select(EventModel).where(EventModel.session_id == session_id))
    row = result.scalar_one()
    assert row.org_id is None
    assert row.user_id is None


async def test_eventmodel_composite_pk_enforces_uniqueness(db_session):
    """Duplicate (session_id, version) SHALL raise IntegrityError."""
    session_id = uuid.uuid4()
    base = dict(
        session_id=session_id,
        version=5,
        id=uuid.uuid4(),
        superstep=1,
        event_type="NODE_END",
        node_id="n1",
        trace_id=None,
        payload={},
        org_id=None,
        user_id=None,
    )
    db_session.add(EventModel(**base))
    await db_session.flush()

    db_session.add(EventModel(**base))
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_eventmodel_same_session_distinct_versions_coexist(db_session):
    """Same session_id with distinct versions SHALL both persist."""
    session_id = uuid.uuid4()
    for version in (1, 2, 3):
        db_session.add(
            EventModel(
                session_id=session_id,
                version=version,
                id=uuid.uuid4(),
                superstep=version - 1,
                event_type="NODE_START",
                node_id=f"n{version}",
                trace_id=None,
                payload={"i": version},
                org_id=None,
                user_id=None,
            )
        )
    await db_session.flush()

    result = await db_session.execute(select(EventModel).order_by(EventModel.version))
    rows = result.scalars().all()
    assert [r.version for r in rows] == [1, 2, 3]
