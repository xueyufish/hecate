"""Tests for Qdrant backup engine."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from hecate.ops.backup.storage import BackupStorage


async def test_qdrant_backup_all_collections():
    """backup_qdrant creates snapshots for all collections."""
    from hecate.ops.backup.qdrant_backup import backup_qdrant

    mock_storage = MagicMock(spec=BackupStorage)
    mock_storage.upload = AsyncMock(return_value="path")

    with (
        patch(
            "hecate.ops.backup.qdrant_backup._list_collections",
            return_value=["kb_001", "kb_002"],
        ),
        patch(
            "hecate.ops.backup.qdrant_backup._create_and_download_snapshot",
            return_value=b"snapshot data",
        ),
        patch(
            "hecate.ops.backup.qdrant_backup._get_vector_count",
            return_value=500,
        ),
    ):
        result = await backup_qdrant(mock_storage, datetime(2026, 7, 30, tzinfo=UTC))

    assert len(result.snapshots) == 2
    assert "kb_001" in result.snapshots
    assert "kb_002" in result.snapshots
    assert result.vector_counts["kb_001"] == 500
    assert result.total_size == len(b"snapshot data") * 2
    assert len(result.failed_collections) == 0


async def test_qdrant_backup_single_collection_failure():
    """backup_qdrant continues when one collection fails."""
    from hecate.ops.backup.qdrant_backup import backup_qdrant

    mock_storage = MagicMock(spec=BackupStorage)
    mock_storage.upload = AsyncMock(return_value="path")

    async def mock_snapshot(url, headers, coll_name):
        if coll_name == "kb_bad":
            raise RuntimeError("connection refused")
        return b"snapshot"

    with (
        patch(
            "hecate.ops.backup.qdrant_backup._list_collections",
            return_value=["kb_ok", "kb_bad"],
        ),
        patch(
            "hecate.ops.backup.qdrant_backup._create_and_download_snapshot",
            side_effect=mock_snapshot,
        ),
        patch(
            "hecate.ops.backup.qdrant_backup._get_vector_count",
            return_value=100,
        ),
    ):
        result = await backup_qdrant(mock_storage, datetime(2026, 7, 30, tzinfo=UTC))

    assert "kb_ok" in result.snapshots
    assert "kb_bad" not in result.snapshots
    assert "kb_bad" in result.failed_collections
    assert "connection refused" in result.failed_collections["kb_bad"]


async def test_qdrant_backup_empty_collections():
    """backup_qdrant handles empty collection list."""
    from hecate.ops.backup.qdrant_backup import backup_qdrant

    mock_storage = MagicMock(spec=BackupStorage)
    mock_storage.upload = AsyncMock(return_value="path")

    with patch(
        "hecate.ops.backup.qdrant_backup._list_collections",
        return_value=[],
    ):
        result = await backup_qdrant(mock_storage, datetime(2026, 7, 30, tzinfo=UTC))

    assert len(result.snapshots) == 0
    assert result.total_size == 0


async def test_qdrant_backup_vector_count_metadata():
    """backup_qdrant records vector counts per collection."""
    from hecate.ops.backup.qdrant_backup import backup_qdrant

    mock_storage = MagicMock(spec=BackupStorage)
    mock_storage.upload = AsyncMock(return_value="path")

    vector_counts = {"kb_a": 100, "kb_b": 200, "kb_c": 300}
    call_count = [0]

    async def mock_count(url, headers, coll_name):
        counts = list(vector_counts.values())
        val = counts[call_count[0]]
        call_count[0] += 1
        return val

    with (
        patch(
            "hecate.ops.backup.qdrant_backup._list_collections",
            return_value=list(vector_counts.keys()),
        ),
        patch(
            "hecate.ops.backup.qdrant_backup._create_and_download_snapshot",
            return_value=b"snap",
        ),
        patch(
            "hecate.ops.backup.qdrant_backup._get_vector_count",
            side_effect=mock_count,
        ),
    ):
        result = await backup_qdrant(mock_storage, datetime(2026, 7, 30, tzinfo=UTC))

    assert sum(result.vector_counts.values()) == 600
