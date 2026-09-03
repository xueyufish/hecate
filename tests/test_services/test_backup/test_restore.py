"""Tests for restore engine."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from hecate.models.backup import RestoreConflict


async def test_restore_backup_not_found():
    """restore_backup returns failed when backup record not found."""
    from hecate.ops.backup.restore import restore_backup

    backup_id = uuid.uuid4()
    mock_storage = MagicMock()

    with patch("hecate.ops.backup.restore.async_session_factory") as mock_factory:
        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=None)
        mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await restore_backup(backup_id=backup_id, storage=mock_storage)

    assert result.status == "failed"
    assert "not found" in result.error


async def test_restore_resolve_scopes():
    """_resolve_scopes expands 'all' to 4 scopes."""
    from hecate.ops.backup.restore import _resolve_scopes

    assert _resolve_scopes("all") == ["pg", "qdrant", "minio", "fs"]
    assert _resolve_scopes("pg") == ["pg"]


def test_restore_conflict_enum():
    """RestoreConflict has expected values."""
    assert RestoreConflict.REPLACE == "replace"
    assert RestoreConflict.MERGE == "merge"
    assert RestoreConflict.FAIL == "fail"


def test_tenant_scoped_tables_includes_core_models():
    """TENANT_SCOPED_TABLES includes expected table names."""
    from hecate.ops.backup.restore import TENANT_SCOPED_TABLES

    assert "agents" in TENANT_SCOPED_TABLES
    assert "conversations" in TENANT_SCOPED_TABLES
    assert "messages" in TENANT_SCOPED_TABLES
    assert "knowledge_bases" in TENANT_SCOPED_TABLES
    assert "documents" in TENANT_SCOPED_TABLES
    assert "workflows" in TENANT_SCOPED_TABLES
    assert "organizations" not in TENANT_SCOPED_TABLES
    assert "workspaces" not in TENANT_SCOPED_TABLES
    assert "users" not in TENANT_SCOPED_TABLES
