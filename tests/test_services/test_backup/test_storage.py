"""Tests for backup storage layer (MinIO and S3 backends)."""

from __future__ import annotations

from datetime import UTC
from unittest.mock import MagicMock, patch

import pytest

from hecate.ops.backup.storage import BackupFile, BackupStorage


def test_backup_file_dataclass():
    """BackupFile dataclass stores path, size, and last_modified."""
    f = BackupFile(path="20260730/pg/full.dump", size_bytes=1024, last_modified="2026-07-30T02:00:00Z")
    assert f.path == "20260730/pg/full.dump"
    assert f.size_bytes == 1024
    assert f.last_modified == "2026-07-30T02:00:00Z"


def test_backup_storage_is_abstract():
    """BackupStorage cannot be instantiated directly."""
    with pytest.raises(TypeError):
        BackupStorage()  # type: ignore[abstract]


async def test_minio_storage_upload():
    """MinIOBackupStorage.upload calls minio client put_object."""
    from hecate.ops.backup.minio_storage import MinIOBackupStorage

    storage = MinIOBackupStorage()
    mock_client = MagicMock()
    storage._client = mock_client

    result = await storage.upload("test/path.dump", b"test data")

    mock_client.put_object.assert_called_once()
    assert result == "test/path.dump"


async def test_minio_storage_download():
    """MinIOBackupStorage.download returns file bytes."""
    from hecate.ops.backup.minio_storage import MinIOBackupStorage

    storage = MinIOBackupStorage()
    mock_response = MagicMock()
    mock_response.read.return_value = b"downloaded data"
    mock_client = MagicMock()
    mock_client.get_object.return_value = mock_response
    storage._client = mock_client

    result = await storage.download("test/path.dump")

    assert result == b"downloaded data"
    mock_response.close.assert_called_once()
    mock_response.release_conn.assert_called_once()


async def test_minio_storage_list_objects():
    """MinIOBackupStorage.list_objects returns BackupFile list."""
    from hecate.ops.backup.minio_storage import MinIOBackupStorage

    storage = MinIOBackupStorage()
    mock_obj = MagicMock()
    mock_obj.object_name = "backups/pg.dump"
    mock_obj.size = 2048
    mock_obj.last_modified = None
    mock_client = MagicMock()
    mock_client.list_objects.return_value = [mock_obj]
    storage._client = mock_client

    result = await storage.list_objects(prefix="backups/")

    assert len(result) == 1
    assert result[0].path == "backups/pg.dump"
    assert result[0].size_bytes == 2048


async def test_minio_storage_exists():
    """MinIOBackupStorage.exists returns True when object exists."""
    from hecate.ops.backup.minio_storage import MinIOBackupStorage

    storage = MinIOBackupStorage()
    mock_client = MagicMock()
    storage._client = mock_client

    result = await storage.exists("test/path.dump")
    assert result is True

    mock_client.stat_object.side_effect = Exception("not found")
    result = await storage.exists("missing/path.dump")
    assert result is False


async def test_minio_storage_delete():
    """MinIOBackupStorage.delete removes object if it exists."""
    from hecate.ops.backup.minio_storage import MinIOBackupStorage

    storage = MinIOBackupStorage()
    mock_client = MagicMock()
    storage._client = mock_client

    result = await storage.delete("test/path.dump")
    assert result is True
    mock_client.remove_object.assert_called_once()

    mock_client.stat_object.side_effect = Exception("not found")
    result = await storage.delete("missing/path.dump")
    assert result is False


async def test_s3_storage_upload():
    """S3BackupStorage.upload calls boto3 client put_object."""
    from hecate.ops.backup.s3_storage import S3BackupStorage

    storage = S3BackupStorage()
    mock_client = MagicMock()
    storage._client = mock_client

    result = await storage.upload("test/path.dump", b"s3 data")
    assert result == "test/path.dump"
    mock_client.put_object.assert_called_once_with(Bucket=storage._bucket, Key="test/path.dump", Body=b"s3 data")


async def test_factory_creates_minio_storage():
    """create_backup_storage returns MinIOBackupStorage when type=minio."""
    from hecate.ops.backup.factory import create_backup_storage
    from hecate.ops.backup.minio_storage import MinIOBackupStorage

    with patch("hecate.ops.backup.factory.settings") as mock_settings:
        mock_settings.BACKUP_STORAGE_TYPE = "minio"
        storage = create_backup_storage()
        assert isinstance(storage, MinIOBackupStorage)


async def test_factory_creates_s3_storage():
    """create_backup_storage returns S3BackupStorage when type=s3."""
    from hecate.ops.backup.factory import create_backup_storage
    from hecate.ops.backup.s3_storage import S3BackupStorage

    with patch("hecate.ops.backup.factory.settings") as mock_settings:
        mock_settings.BACKUP_STORAGE_TYPE = "s3"
        mock_settings.BACKUP_S3_BUCKET = "test-bucket"
        mock_settings.BACKUP_S3_ACCESS_KEY = "test-key"
        mock_settings.BACKUP_S3_SECRET_KEY = "test-secret"
        storage = create_backup_storage()
        assert isinstance(storage, S3BackupStorage)


async def test_factory_rejects_invalid_type():
    """create_backup_storage raises ValueError for unknown type."""
    from hecate.ops.backup.factory import create_backup_storage

    with patch("hecate.ops.backup.factory.settings") as mock_settings:
        mock_settings.BACKUP_STORAGE_TYPE = "invalid"
        with pytest.raises(ValueError, match="Unknown BACKUP_STORAGE_TYPE"):
            create_backup_storage()


def test_build_backup_path():
    """build_backup_path creates structured path."""
    from datetime import datetime

    from hecate.ops.backup.factory import build_backup_path

    ts = datetime(2026, 7, 30, 2, 0, 0, tzinfo=UTC)
    path = build_backup_path(ts, "pg", "full.dump")
    assert path == "20260730_020000/pg/full.dump"


def test_build_manifest_path():
    """build_manifest_path creates manifest path."""
    from datetime import datetime

    from hecate.ops.backup.factory import build_manifest_path

    ts = datetime(2026, 7, 30, 2, 0, 0, tzinfo=UTC)
    path = build_manifest_path(ts)
    assert path == "20260730_020000/manifest.json"


def test_build_wal_path():
    """build_wal_path creates WAL path."""
    from hecate.ops.backup.factory import build_wal_path

    path = build_wal_path("000000010000000000000001")
    assert path == "wal/000000010000000000000001"
