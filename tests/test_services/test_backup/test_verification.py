"""Tests for backup verification."""

from __future__ import annotations

import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hecate.models.backup import BackupRecordModel


async def test_verify_backup_not_found():
    """verify_backup raises ValueError for unknown backup ID."""
    from hecate.services.backup.verification import verify_backup

    backup_id = uuid.uuid4()

    with patch("hecate.services.backup.verification.async_session_factory") as mock_factory:
        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=None)
        mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=None)

        with pytest.raises(ValueError, match="not found"):
            await verify_backup(backup_id)


async def test_verify_backup_row_count_match():
    """verify_backup returns matched=True when row counts are equal."""
    from hecate.services.backup.verification import verify_backup

    backup_id = uuid.uuid4()
    mock_record = MagicMock(spec=BackupRecordModel)
    mock_record.id = backup_id
    mock_record.started_at = datetime(2026, 7, 30)
    mock_record.metadata_ = {"scopes": {"pg": {"row_counts": {"agents": 10, "users": 5}}}}

    mock_storage = MagicMock()
    mock_storage.download = AsyncMock(return_value=b"dump data")
    mock_storage.list_objects = AsyncMock(return_value=[])

    with (
        patch("hecate.services.backup.verification.create_backup_storage", return_value=mock_storage),
        patch("hecate.services.backup.verification.async_session_factory") as mock_factory,
        patch("hecate.services.backup.verification._run_createdb", new_callable=AsyncMock),
        patch("hecate.services.backup.verification._run_dropdb", new_callable=AsyncMock),
        patch("hecate.services.backup.verification._run_pg_restore_to_db", new_callable=AsyncMock),
        patch("hecate.services.backup.verification._query_row_counts", return_value={"agents": 10, "users": 5}),
    ):
        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=mock_record)
        mock_session.merge = AsyncMock()
        mock_session.commit = AsyncMock()
        mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await verify_backup(backup_id)

    assert result["matched"] is True
    assert len(result["mismatches"]) == 0


async def test_verify_backup_row_count_mismatch():
    """verify_backup returns matched=False when counts differ."""
    from hecate.services.backup.verification import verify_backup

    backup_id = uuid.uuid4()
    mock_record = MagicMock(spec=BackupRecordModel)
    mock_record.id = backup_id
    mock_record.started_at = datetime(2026, 7, 30)
    mock_record.metadata_ = {"scopes": {"pg": {"row_counts": {"agents": 10}}}}

    mock_storage = MagicMock()
    mock_storage.download = AsyncMock(return_value=b"dump")
    mock_storage.list_objects = AsyncMock(return_value=[])

    with (
        patch("hecate.services.backup.verification.create_backup_storage", return_value=mock_storage),
        patch("hecate.services.backup.verification.async_session_factory") as mock_factory,
        patch("hecate.services.backup.verification._run_createdb", new_callable=AsyncMock),
        patch("hecate.services.backup.verification._run_dropdb", new_callable=AsyncMock),
        patch("hecate.services.backup.verification._run_pg_restore_to_db", new_callable=AsyncMock),
        patch("hecate.services.backup.verification._query_row_counts", return_value={"agents": 8}),
    ):
        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=mock_record)
        mock_session.merge = AsyncMock()
        mock_session.commit = AsyncMock()
        mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await verify_backup(backup_id)

    assert result["matched"] is False
    assert any("agents" in m for m in result["mismatches"])
