"""Tests for PostgreSQL backup engine."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from hecate.services.backup.storage import BackupStorage


async def test_pg_backup_success():
    """backup_postgresql runs pg_dump and uploads result."""
    from hecate.services.backup.pg_backup import backup_postgresql

    mock_storage = MagicMock(spec=BackupStorage)
    mock_storage.upload = AsyncMock(return_value="path")

    fake_dump = b"fake pg_dump output"

    with (
        patch("hecate.services.backup.pg_backup._run_pg_dump", return_value=fake_dump),
        patch("hecate.services.backup.pg_backup._collect_row_counts", return_value={"agents": 5}),
        patch("hecate.services.backup.pg_backup._check_wal_archive", return_value=True),
    ):
        result = await backup_postgresql(mock_storage, datetime(2026, 7, 30, tzinfo=UTC))

    assert result.dump_bytes == fake_dump
    assert result.checksum == hashlib.sha256(fake_dump).hexdigest()
    assert result.size_bytes == len(fake_dump)
    assert result.row_counts == {"agents": 5}
    assert result.wal_archive_enabled is True
    mock_storage.upload.assert_called_once()


async def test_pg_backup_wal_disabled_warning():
    """backup_postgresql records warning when WAL archiving is disabled."""
    from hecate.services.backup.pg_backup import backup_postgresql

    mock_storage = MagicMock(spec=BackupStorage)
    mock_storage.upload = AsyncMock(return_value="path")

    with (
        patch("hecate.services.backup.pg_backup._run_pg_dump", return_value=b"dump"),
        patch("hecate.services.backup.pg_backup._collect_row_counts", return_value={}),
        patch("hecate.services.backup.pg_backup._check_wal_archive", return_value=False),
    ):
        result = await backup_postgresql(mock_storage, datetime(2026, 7, 30, tzinfo=UTC))

    assert result.wal_archive_enabled is False
    assert len(result.warnings) == 1
    assert "WAL" in result.warnings[0]


async def test_pg_backup_checksum_correctness():
    """backup_postgresql computes correct SHA256 checksum."""
    from hecate.services.backup.pg_backup import backup_postgresql

    mock_storage = MagicMock(spec=BackupStorage)
    mock_storage.upload = AsyncMock(return_value="path")
    test_data = b"x" * 1024

    with (
        patch("hecate.services.backup.pg_backup._run_pg_dump", return_value=test_data),
        patch("hecate.services.backup.pg_backup._collect_row_counts", return_value={}),
        patch("hecate.services.backup.pg_backup._check_wal_archive", return_value=True),
    ):
        result = await backup_postgresql(mock_storage, datetime(2026, 7, 30, tzinfo=UTC))

    expected_checksum = hashlib.sha256(test_data).hexdigest()
    assert result.checksum == expected_checksum
