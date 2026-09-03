"""MinIO bucket backup engine — incremental mirror to backup storage."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

from hecate.core.config import settings

from .factory import build_backup_path
from .storage import BackupStorage

logger = logging.getLogger(__name__)


@dataclass
class MinIOBackupResult:
    file_count: int = 0
    total_size: int = 0
    prefixes_backed_up: list[str] = field(default_factory=list)


async def backup_minio(
    storage: BackupStorage,
    timestamp: datetime,
    prefix: str = "",
) -> MinIOBackupResult:
    """Mirror MinIO bucket contents to backup storage."""
    from hecate_memory.rag.storage import MinIOStorage

    source = MinIOStorage()
    client = source._get_client()  # noqa: SLF001

    if client == "mock":
        logger.warning("MinIO client is mock — skipping backup")
        return MinIOBackupResult()

    result = MinIOBackupResult(prefixes_backed_up=[prefix] if prefix else ["(all)"])

    objects = client.list_objects(settings.MINIO_BUCKET, prefix=prefix, recursive=True)
    for obj in objects:
        try:
            response = client.get_object(settings.MINIO_BUCKET, obj.object_name)
            try:
                data = response.read()
            finally:
                response.close()
                response.release_conn()

            backup_path = build_backup_path(timestamp, "minio", obj.object_name)
            await storage.upload(backup_path, data)

            result.file_count += 1
            result.total_size += len(data)
        except Exception as e:
            logger.error("Failed to back up MinIO object %s: %s", obj.object_name, e)

    logger.info("MinIO backup complete: %d files, %d bytes", result.file_count, result.total_size)
    return result
