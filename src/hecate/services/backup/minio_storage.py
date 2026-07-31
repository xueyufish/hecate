"""MinIO backend for backup storage.

Uses the existing MinIO connection to store backup files in a dedicated
bucket (``hecate-backups``), physically isolated from the primary data bucket.
"""

from __future__ import annotations

import io
import logging

from hecate.core.config import settings

from .storage import BackupFile, BackupStorage

logger = logging.getLogger(__name__)


class MinIOBackupStorage(BackupStorage):
    """Backup storage backend using MinIO dedicated bucket."""

    def __init__(self) -> None:
        self._bucket = settings.BACKUP_MINIO_BUCKET
        self._client = None

    def _get_client(self):
        if self._client is None:
            from minio import Minio

            self._client = Minio(
                settings.MINIO_URL,
                access_key=settings.MINIO_ACCESS_KEY,
                secret_key=settings.MINIO_SECRET_KEY,
                secure=False,
            )
            self._ensure_bucket()
        return self._client

    def _ensure_bucket(self) -> None:
        try:
            if not self._client.bucket_exists(self._bucket):
                self._client.make_bucket(self._bucket)
                logger.info("Created backup bucket: %s", self._bucket)
        except Exception as e:
            logger.warning("Could not ensure backup bucket: %s", e)

    async def upload(self, path: str, data: bytes) -> str:
        client = self._get_client()
        client.put_object(
            bucket_name=self._bucket,
            object_name=path,
            data=io.BytesIO(data),
            length=len(data),
            content_type="application/octet-stream",
        )
        logger.info("Uploaded backup file: %s/%s", self._bucket, path)
        return path

    async def download(self, path: str) -> bytes:
        client = self._get_client()
        response = client.get_object(self._bucket, path)
        try:
            return response.read()
        finally:
            response.close()
            response.release_conn()

    async def list_objects(self, prefix: str = "") -> list[BackupFile]:
        client = self._get_client()
        result: list[BackupFile] = []
        for obj in client.list_objects(self._bucket, prefix=prefix, recursive=True):
            result.append(
                BackupFile(
                    path=obj.object_name,
                    size_bytes=obj.size if obj.size is not None else 0,
                    last_modified=obj.last_modified.isoformat() if obj.last_modified else "",
                )
            )
        return result

    async def delete(self, path: str) -> bool:
        client = self._get_client()
        try:
            client.stat_object(self._bucket, path)
        except Exception:
            return False
        client.remove_object(self._bucket, path)
        return True

    async def exists(self, path: str) -> bool:
        client = self._get_client()
        try:
            client.stat_object(self._bucket, path)
            return True
        except Exception:
            return False
