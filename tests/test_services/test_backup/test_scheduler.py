"""Tests for backup scheduling."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch


def test_get_scheduler_returns_singleton():
    """get_scheduler returns the same scheduler instance."""
    from hecate.services.backup.scheduler import get_scheduler

    s1 = get_scheduler()
    s2 = get_scheduler()
    assert s1 is s2


def test_start_backup_scheduler_disabled():
    """start_backup_scheduler is a no-op when BACKUP_SCHEDULE_ENABLED=false."""
    from hecate.services.backup import scheduler as sched_mod

    with patch.object(sched_mod.settings, "BACKUP_SCHEDULE_ENABLED", False):
        sched_mod.start_backup_scheduler()


async def test_apply_retention_policy_no_excess():
    """apply_retention_policy returns 0 when under limit."""
    from hecate.services.backup.scheduler import apply_retention_policy

    with patch("hecate.services.backup.scheduler.async_session_factory") as mock_factory:
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=None)

        count = await apply_retention_policy()

    assert count == 0


async def test_scheduled_backup_calls_create_backup():
    """_scheduled_backup calls create_backup with scope=all."""
    from hecate.services.backup.scheduler import _scheduled_backup

    with patch("hecate.services.backup.scheduler.create_backup", new_callable=AsyncMock) as mock_create:
        await _scheduled_backup()
        mock_create.assert_called_once()
