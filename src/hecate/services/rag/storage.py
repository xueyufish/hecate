"""MinIO object storage service for document management.

Provides file upload, download, and management operations
using MinIO/S3-compatible object storage.
"""

from __future__ import annotations

import logging
from typing import BinaryIO

from hecate.core.config import settings

logger = logging.getLogger(__name__)


class MinIOStorage:
    """Manage files in MinIO object storage.

    Supports:
    - File upload with content type
    - File download
    - File deletion
    - Bucket management
    """

    def __init__(self):
        self.endpoint = settings.MINIO_URL
        self.access_key = settings.MINIO_ACCESS_KEY
        self.secret_key = settings.MINIO_SECRET_KEY
        self.bucket = settings.MINIO_BUCKET
        self._client = None

    def _get_client(self):
        """Lazy load the MinIO client."""
        if self._client is None:
            try:
                from minio import Minio

                self._client = Minio(
                    self.endpoint,
                    access_key=self.access_key,
                    secret_key=self.secret_key,
                    secure=False,
                )
                self._ensure_bucket()
                logger.info(f"Connected to MinIO at {self.endpoint}")
            except ImportError:
                logger.warning("minio not installed. Using mock storage.")
                self._client = "mock"
        return self._client

    def _ensure_bucket(self):
        """Ensure the bucket exists."""
        if self._client == "mock":
            return
        try:
            if not self._client.bucket_exists(self.bucket):
                self._client.make_bucket(self.bucket)
                logger.info(f"Created bucket: {self.bucket}")
        except Exception as e:
            logger.warning(f"Could not ensure bucket: {e}")

    async def upload_file(
        self,
        file_path: str,
        file_data: BinaryIO,
        content_type: str = "application/octet-stream",
    ) -> str:
        """Upload a file to MinIO.

        Args:
            file_path: The object path in MinIO.
            file_data: The file data to upload.
            content_type: The MIME content type.

        Returns:
            str: The MinIO path of the uploaded file.
        """
        client = self._get_client()

        if client == "mock":
            logger.info(f"Mock: Uploaded file to {file_path}")
            return file_path

        try:
            import os

            file_size = os.fstat(file_data.fileno()).st_size
            client.put_object(
                bucket_name=self.bucket,
                object_name=file_path,
                data=file_data,
                length=file_size,
                content_type=content_type,
            )
            logger.info(f"Uploaded file to {self.bucket}/{file_path}")
            return file_path
        except Exception as e:
            logger.error(f"Failed to upload file: {e}")
            raise

    async def download_file(self, file_path: str) -> bytes:
        """Download a file from MinIO.

        Args:
            file_path: The object path in MinIO.

        Returns:
            bytes: The file content.
        """
        client = self._get_client()

        if client == "mock":
            return b"mock file content"

        try:
            response = client.get_object(
                bucket_name=self.bucket,
                object_name=file_path,
            )
            return response.read()
        except Exception as e:
            logger.error(f"Failed to download file: {e}")
            raise

    async def delete_file(self, file_path: str) -> bool:
        """Delete a file from MinIO.

        Args:
            file_path: The object path in MinIO.

        Returns:
            bool: True if deletion was successful.
        """
        client = self._get_client()

        if client == "mock":
            logger.info(f"Mock: Deleted file {file_path}")
            return True

        try:
            client.remove_object(
                bucket_name=self.bucket,
                object_name=file_path,
            )
            logger.info(f"Deleted file {self.bucket}/{file_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete file: {e}")
            return False


minio_storage = MinIOStorage()
