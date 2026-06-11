"""Audit log archiver for cold storage (MinIO/S3).

Exports audit logs older than a threshold to JSON and uploads
to object storage.  Gracefully degrades when minio is not installed.
"""

from __future__ import annotations

import io
import logging
from datetime import datetime

from hecate.services.audit.store import AuditStore

logger = logging.getLogger(__name__)


class AuditArchiver:
    """Archive old audit logs to cold object storage.

    Args:
        store: The audit store to read logs from.
        bucket: Object storage bucket name.
        endpoint: Object storage endpoint URL.
        access_key: Access key for authentication.
        secret_key: Secret key for authentication.
    """

    def __init__(
        self,
        store: AuditStore,
        bucket: str = "hecate-audit-archive",
        endpoint: str = "localhost:9000",
        access_key: str = "",
        secret_key: str = "",
    ) -> None:
        self._store = store
        self._bucket = bucket
        self._endpoint = endpoint
        self._access_key = access_key
        self._secret_key = secret_key

    async def archive_and_upload(self, before_date: datetime) -> int:
        """Export old logs and upload to object storage.

        Args:
            before_date: Archive logs older than this date.

        Returns:
            Number of logs archived.
        """
        from hecate.models.audit import AuditLogQuerySchema

        filters = AuditLogQuerySchema(
            end_time=before_date,
            page=1,
            page_size=100000,
        )
        data = await self._store.export("json", filters)
        count = await self._store.archive(before_date)

        try:
            await self._upload_to_storage(data, before_date)
            logger.info("Archived %d audit logs to %s", count, self._bucket)
        except Exception as e:
            logger.error("Failed to upload audit archive: %s", e)

        return count

    async def _upload_to_storage(self, data: str | bytes, before_date: datetime) -> None:
        """Upload archive data to object storage."""
        try:
            from minio import Minio

            client = Minio(
                self._endpoint,
                access_key=self._access_key,
                secret_key=self._secret_key,
                secure=False,
            )
            if not client.bucket_exists(self._bucket):
                client.make_bucket(self._bucket)

            object_name = f"audit-{before_date.strftime('%Y%m%d')}.json"
            content = data if isinstance(data, bytes) else data.encode()
            client.put_object(
                self._bucket,
                object_name,
                io.BytesIO(content),
                length=len(content),
                content_type="application/json",
            )
        except ImportError:
            logger.warning("minio package not installed — audit archive not uploaded")
