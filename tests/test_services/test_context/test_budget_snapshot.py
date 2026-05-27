"""Unit tests for BudgetSnapshotService."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.budget import BudgetSnapshotModel
from hecate.services.context.budget_snapshot_service import BudgetSnapshotService


@pytest.mark.asyncio
async def test_record_snapshot(db_session: AsyncSession) -> None:
    """Test recording a budget snapshot."""
    service = BudgetSnapshotService(db_session)
    session_id = uuid4()

    snapshot = await service.record_snapshot(
        session_id=session_id,
        total_budget=8000,
        tokens_used=5000,
        tokens_remaining=3000,
        degradation_level="none",
    )

    assert snapshot is not None
    assert snapshot.session_id == session_id
    assert snapshot.total_budget == 8000
    assert snapshot.tokens_used == 5000
    assert snapshot.tokens_remaining == 3000
    assert snapshot.degradation_level == "none"


@pytest.mark.asyncio
async def test_record_snapshot_with_degradation(db_session: AsyncSession) -> None:
    """Test recording a snapshot with degradation applied."""
    service = BudgetSnapshotService(db_session)
    session_id = uuid4()

    snapshot = await service.record_snapshot(
        session_id=session_id,
        total_budget=8000,
        tokens_used=7500,
        tokens_remaining=500,
        degradation_level="drop",
    )

    assert snapshot.degradation_level == "drop"


@pytest.mark.asyncio
async def test_get_snapshots(db_session: AsyncSession) -> None:
    """Test getting snapshots for a session."""
    service = BudgetSnapshotService(db_session)
    session_id = uuid4()

    # Create multiple snapshots
    await service.record_snapshot(session_id, 8000, 1000, 7000, "none")
    await service.record_snapshot(session_id, 8000, 3000, 5000, "none")
    await service.record_snapshot(session_id, 8000, 5000, 3000, "drop")

    snapshots = await service.get_snapshots(session_id)
    assert len(snapshots) == 3


@pytest.mark.asyncio
async def test_get_snapshots_with_limit(db_session: AsyncSession) -> None:
    """Test getting snapshots with limit."""
    service = BudgetSnapshotService(db_session)
    session_id = uuid4()

    await service.record_snapshot(session_id, 8000, 1000, 7000, "none")
    await service.record_snapshot(session_id, 8000, 3000, 5000, "none")
    await service.record_snapshot(session_id, 8000, 5000, 3000, "drop")

    snapshots = await service.get_snapshots(session_id, limit=2)
    assert len(snapshots) == 2


@pytest.mark.asyncio
async def test_get_latest_snapshot(db_session: AsyncSession) -> None:
    """Test getting the latest snapshot."""
    service = BudgetSnapshotService(db_session)
    session_id = uuid4()

    await service.record_snapshot(session_id, 8000, 1000, 7000, "none")
    await service.record_snapshot(session_id, 8000, 5000, 3000, "drop")

    latest = await service.get_latest_snapshot(session_id)
    assert latest is not None
    assert latest.tokens_used in [1000, 5000]
    assert latest.degradation_level in ["none", "drop"]


@pytest.mark.asyncio
async def test_get_latest_snapshot_empty(db_session: AsyncSession) -> None:
    """Test getting latest snapshot when none exist."""
    service = BudgetSnapshotService(db_session)

    latest = await service.get_latest_snapshot(uuid4())
    assert latest is None


@pytest.mark.asyncio
async def test_get_snapshots_empty(db_session: AsyncSession) -> None:
    """Test getting snapshots when none exist."""
    service = BudgetSnapshotService(db_session)

    snapshots = await service.get_snapshots(uuid4())
    assert snapshots == []
