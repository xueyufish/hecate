"""Unit tests for GarbageCollectorAgent."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.checkpoint import CheckpointModel
from hecate.models.session import SessionModel
from hecate.services.meta_agents.garbage_collector import (
    CleanupReport,
    GarbageCollectorAgent,
)


@pytest.fixture
def agent() -> GarbageCollectorAgent:
    return GarbageCollectorAgent()


async def test_scan_expired_sessions_finds_old(db_session: AsyncSession, agent: GarbageCollectorAgent) -> None:
    old_id = uuid4()
    session = SessionModel(
        id=old_id,
        agent_id=uuid4(),
        status="completed",
    )
    session.created_at = datetime.now(UTC) - timedelta(days=60)
    db_session.add(session)
    await db_session.flush()

    result = await agent.scan_expired_sessions(db_session, retention_days=30)
    assert old_id in result


async def test_scan_expired_sessions_skips_active(db_session: AsyncSession, agent: GarbageCollectorAgent) -> None:
    active_id = uuid4()
    session = SessionModel(
        id=active_id,
        agent_id=uuid4(),
        status="active",
    )
    session.created_at = datetime.now(UTC) - timedelta(days=60)
    db_session.add(session)
    await db_session.flush()

    result = await agent.scan_expired_sessions(db_session, retention_days=30)
    assert active_id not in result


async def test_scan_expired_sessions_skips_recent(db_session: AsyncSession, agent: GarbageCollectorAgent) -> None:
    recent_id = uuid4()
    session = SessionModel(
        id=recent_id,
        agent_id=uuid4(),
        status="completed",
    )
    db_session.add(session)
    await db_session.flush()

    result = await agent.scan_expired_sessions(db_session, retention_days=30)
    assert recent_id not in result


async def test_scan_orphaned_checkpoints(db_session: AsyncSession, agent: GarbageCollectorAgent) -> None:
    orphan_id = uuid4()
    checkpoint = CheckpointModel(
        id=orphan_id,
        session_id=uuid4(),
        superstep=0,
        channel_state={},
    )
    db_session.add(checkpoint)
    await db_session.flush()

    result = await agent.scan_orphaned_checkpoints(db_session)
    assert orphan_id in result


async def test_scan_orphaned_checkpoints_skips_linked(db_session: AsyncSession, agent: GarbageCollectorAgent) -> None:
    session_id = uuid4()
    session = SessionModel(
        id=session_id,
        agent_id=uuid4(),
        status="active",
    )
    checkpoint_id = uuid4()
    checkpoint = CheckpointModel(
        id=checkpoint_id,
        session_id=session_id,
        superstep=0,
        channel_state={},
    )
    db_session.add_all([session, checkpoint])
    await db_session.flush()

    result = await agent.scan_orphaned_checkpoints(db_session)
    assert checkpoint_id not in result


async def test_generate_cleanup_report(db_session: AsyncSession, agent: GarbageCollectorAgent) -> None:
    report = await agent.generate_cleanup_report(db_session, retention_days=30)
    assert isinstance(report, CleanupReport)
    assert report.expired_sessions >= 0
    assert report.orphaned_checkpoints >= 0
    assert isinstance(report.details, list)


async def test_run_convenience(db_session: AsyncSession, agent: GarbageCollectorAgent) -> None:
    report = await agent.run(db_session, retention_days=30)
    assert isinstance(report, CleanupReport)
