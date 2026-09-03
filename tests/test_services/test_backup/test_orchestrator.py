"""Tests for backup orchestrator."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from hecate.models.backup import BackupScope, BackupStatus


async def test_create_backup_full_scope():
    """create_backup with scope=all runs all 4 backup engines."""
    from hecate.ops.backup.orchestrator import create_backup

    mock_storage = MagicMock()
    mock_storage.upload = AsyncMock(return_value="path")

    with (
        patch("hecate.ops.backup.orchestrator.create_backup_storage", return_value=mock_storage),
        patch(
            "hecate.ops.backup.orchestrator.backup_postgresql",
            return_value=MagicMock(
                size_bytes=100, checksum="abc", row_counts={}, wal_archive_enabled=True, warnings=[]
            ),
        ),
        patch(
            "hecate.ops.backup.orchestrator.backup_qdrant",
            return_value=MagicMock(total_size=200, vector_counts={}, failed_collections={}),
        ),
        patch("hecate.ops.backup.orchestrator.backup_minio", return_value=MagicMock(total_size=300, file_count=5)),
        patch(
            "hecate.ops.backup.orchestrator.backup_filesystem",
            return_value=MagicMock(total_size=400, file_count=10, paths_backed_up=[]),
        ),
        patch("hecate.ops.backup.orchestrator.async_session_factory") as mock_factory,
    ):
        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        mock_session.flush = AsyncMock()
        mock_session.merge = AsyncMock()
        mock_session.commit = AsyncMock()
        mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=None)

        record = await create_backup(scope=BackupScope.ALL, storage=mock_storage)

    assert record.scope == BackupScope.ALL


async def test_resolve_scopes_all():
    """_resolve_scopes expands 'all' to 4 scopes."""
    from hecate.ops.backup.orchestrator import _resolve_scopes

    scopes = _resolve_scopes(BackupScope.ALL)
    assert scopes == ["pg", "qdrant", "minio", "fs"]


async def test_resolve_scopes_single():
    """_resolve_scopes keeps single scope."""
    from hecate.ops.backup.orchestrator import _resolve_scopes

    scopes = _resolve_scopes(BackupScope.PG)
    assert scopes == ["pg"]


async def test_list_backups_returns_records():
    """list_backups queries backup records."""
    from hecate.ops.backup.orchestrator import list_backups

    with patch("hecate.ops.backup.orchestrator.async_session_factory") as mock_factory:
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await list_backups(status=BackupStatus.COMPLETED, limit=10)

    assert result == []
