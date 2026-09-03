"""S3-compatible backend for backup storage.

Supports AWS S3, Google Cloud Storage (via S3 interop), Cloudflare R2,
and any S3-compatible object storage. Uses boto3 for operations.
"""

from __future__ import annotations

import logging

from hecate.core.config import settings

from .storage import BackupFile, BackupStorage

logger = logging.getLogger(__name__)


class S3BackupStorage(BackupStorage):
    """Backup storage backend using S3-compatible external storage."""

    def __init__(self) -> None:
        self._bucket = settings.BACKUP_S3_BUCKET
        self._client = None

    def _get_client(self):
        if self._client is None:
            from boto3 import client as boto3_client

            self._client = boto3_client(
                "s3",
                endpoint_url=settings.BACKUP_S3_ENDPOINT or None,
                aws_access_key_id=settings.BACKUP_S3_ACCESS_KEY,
                aws_secret_access_key=settings.BACKUP_S3_SECRET_KEY,
                region_name=settings.BACKUP_S3_REGION,
            )
            self._ensure_bucket()
        return self._client

    def _ensure_bucket(self) -> None:
        try:
            self._client.head_bucket(Bucket=self._bucket)
        except Exception:
            try:
                self._client.create_bucket(Bucket=self._bucket)
                logger.info("Created S3 backup bucket: %s", self._bucket)
            except Exception as e:
                logger.warning("Could not create S3 backup bucket: %s", e)

    async def upload(self, path: str, data: bytes) -> str:
        client = self._get_client()
        client.put_object(Bucket=self._bucket, Key=path, Body=data)
        logger.info("Uploaded backup to S3: %s/%s", self._bucket, path)
        return path

    async def download(self, path: str) -> bytes:
        client = self._get_client()
        response = client.get_object(Bucket=self._bucket, Key=path)
        return response["Body"].read()

    async def list_objects(self, prefix: str = "") -> list[BackupFile]:
        client = self._get_client()
        result: list[BackupFile] = []
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                result.append(
                    BackupFile(
                        path=obj["Key"],
                        size_bytes=obj.get("Size", 0),
                        last_modified=obj.get("LastModified", "").isoformat()
                        if hasattr(obj.get("LastModified", ""), "isoformat")
                        else str(obj.get("LastModified", "")),
                    )
                )
        return result

    async def delete(self, path: str) -> bool:
        client = self._get_client()
        try:
            client.head_object(Bucket=self._bucket, Key=path)
        except Exception:
            return False
        client.delete_object(Bucket=self._bucket, Key=path)
        return True

    async def exists(self, path: str) -> bool:
        client = self._get_client()
        try:
            client.head_object(Bucket=self._bucket, Key=path)
            return True
        except Exception:
            return False
