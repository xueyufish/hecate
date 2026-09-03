"""Factory and path management for backup storage backends."""

from __future__ import annotations

from datetime import UTC, datetime

from hecate.core.config import settings

from .minio_storage import MinIOBackupStorage
from .s3_storage import S3BackupStorage
from .storage import BackupStorage


def create_backup_storage() -> BackupStorage:
    """Create a backup storage instance based on BACKUP_STORAGE_TYPE config."""
    storage_type = settings.BACKUP_STORAGE_TYPE
    if storage_type == "s3":
        if not settings.BACKUP_S3_BUCKET or not settings.BACKUP_S3_ACCESS_KEY:
            raise ValueError(
                "BACKUP_S3_BUCKET, BACKUP_S3_ACCESS_KEY, and BACKUP_S3_SECRET_KEY "
                "must be set when BACKUP_STORAGE_TYPE=s3"
            )
        return S3BackupStorage()
    if storage_type == "minio":
        return MinIOBackupStorage()
    raise ValueError(f"Unknown BACKUP_STORAGE_TYPE: {storage_type}")


def build_backup_path(timestamp: datetime, scope: str, filename: str) -> str:
    """Build a structured backup file path.

    Format: {YYYYMMDD_HHMMSS}/{scope}/{filename}
    """
    ts = timestamp.astimezone(UTC).strftime("%Y%m%d_%H%M%S")
    return f"{ts}/{scope}/{filename}"


def build_wal_path(wal_filename: str) -> str:
    """Build a WAL archive path."""
    return f"wal/{wal_filename}"


def build_manifest_path(timestamp: datetime) -> str:
    """Build a backup manifest path."""
    ts = timestamp.astimezone(UTC).strftime("%Y%m%d_%H%M%S")
    return f"{ts}/manifest.json"
