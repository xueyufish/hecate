"""Backup orchestrator — coordinates backups across all storage layers."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from hecate.core.database import async_session_factory
from hecate.models.backup import BackupRecordModel, BackupScope, BackupStatus, BackupType

from .factory import build_manifest_path, create_backup_storage
from .fs_backup import backup_filesystem
from .minio_backup import backup_minio
from .pg_backup import backup_postgresql
from .qdrant_backup import backup_qdrant
from .storage import BackupStorage

logger = logging.getLogger(__name__)


async def create_backup(
    scope: str = BackupScope.ALL,
    storage: BackupStorage | None = None,
) -> BackupRecordModel:
    """Create a backup of the specified scope.

    Args:
        scope: What to back up — "all", "pg", "qdrant", "minio", or "fs".
        storage: Optional pre-configured storage backend. If None, one is created.

    Returns:
        The completed BackupRecordModel.
    """
    if storage is None:
        storage = create_backup_storage()

    timestamp = datetime.now(UTC)
    scopes = _resolve_scopes(scope)

    record = BackupRecordModel(
        id=uuid.uuid4(),
        backup_type=BackupType.FULL,
        scope=scope,
        status=BackupStatus.RUNNING,
        storage_type=_detect_storage_type(),
        storage_path=timestamp.strftime("%Y%m%d_%H%M%S"),
        started_at=timestamp,
    )
    async with async_session_factory() as session:
        session.add(record)
        await session.flush()

    metadata: dict = {"scopes": {}}
    errors: list[str] = []
    total_size = 0

    for s in scopes:
        try:
            result_meta = await _backup_one(s, storage, timestamp)
            metadata["scopes"][s] = result_meta
            total_size += result_meta.get("size_bytes", 0)
        except Exception as e:
            logger.error("Backup scope '%s' failed: %s", s, e)
            errors.append(f"{s}: {e}")
            metadata["scopes"][s] = {"error": str(e)}

    record.size_bytes = total_size
    record.metadata_ = metadata
    record.completed_at = datetime.now(UTC)

    if errors:
        record.status = BackupStatus.PARTIAL if len(errors) < len(scopes) else BackupStatus.FAILED
        record.error_message = "; ".join(errors)
    else:
        record.status = BackupStatus.COMPLETED

    manifest_path = build_manifest_path(timestamp)
    manifest_data = json.dumps(metadata, indent=2, default=str).encode()
    await storage.upload(manifest_path, manifest_data)

    async with async_session_factory() as session:
        await session.merge(record)
        await session.commit()

    logger.info("Backup %s complete: status=%s, size=%d bytes", record.id, record.status, total_size)
    return record


async def _backup_one(
    scope: str,
    storage: BackupStorage,
    timestamp: datetime,
) -> dict:
    """Run a single backup scope and return metadata dict."""
    if scope == "pg":
        result = await backup_postgresql(storage, timestamp)
        return {
            "size_bytes": result.size_bytes,
            "checksum": result.checksum,
            "row_counts": result.row_counts,
            "wal_archive_enabled": result.wal_archive_enabled,
            "warnings": result.warnings,
        }
    if scope == "qdrant":
        result = await backup_qdrant(storage, timestamp)
        return {
            "size_bytes": result.total_size,
            "vector_counts": result.vector_counts,
            "failed_collections": result.failed_collections,
        }
    if scope == "minio":
        result = await backup_minio(storage, timestamp)
        return {
            "size_bytes": result.total_size,
            "file_count": result.file_count,
        }
    if scope == "fs":
        result = await backup_filesystem(storage, timestamp)
        return {
            "size_bytes": result.total_size,
            "file_count": result.file_count,
            "paths": result.paths_backed_up,
        }
    raise ValueError(f"Unknown backup scope: {scope}")


def _resolve_scopes(scope: str) -> list[str]:
    if scope == BackupScope.ALL:
        return ["pg", "qdrant", "minio", "fs"]
    return [scope]


def _detect_storage_type() -> str:
    from hecate.core.config import settings

    return settings.BACKUP_STORAGE_TYPE


async def list_backups(
    status: str | None = None,
    limit: int = 50,
) -> list[BackupRecordModel]:
    """List backup records, optionally filtered by status."""
    async with async_session_factory() as session:
        stmt = select(BackupRecordModel).order_by(BackupRecordModel.started_at.desc())
        if status:
            stmt = stmt.where(BackupRecordModel.status == status)
        stmt = stmt.limit(limit)
        result = await session.execute(stmt)
        return list(result.scalars().all())
