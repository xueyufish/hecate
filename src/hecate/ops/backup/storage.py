"""Backup storage abstraction layer.

Provides a unified interface for uploading, downloading, listing, and
deleting backup files across different storage backends (MinIO, S3).
"""

from __future__ import annotations

import abc
from dataclasses import dataclass


@dataclass
class BackupFile:
    path: str
    size_bytes: int
    last_modified: str


class BackupStorage(abc.ABC):
    """Abstract base class for backup storage backends."""

    @abc.abstractmethod
    async def upload(self, path: str, data: bytes) -> str:
        """Upload data to the storage backend.

        Args:
            path: The object path in storage.
            data: The file content as bytes.

        Returns:
            The storage path of the uploaded file.
        """

    @abc.abstractmethod
    async def download(self, path: str) -> bytes:
        """Download data from the storage backend.

        Args:
            path: The object path in storage.

        Returns:
            The file content as bytes.
        """

    @abc.abstractmethod
    async def list_objects(self, prefix: str = "") -> list[BackupFile]:
        """List objects in the storage backend.

        Args:
            prefix: Optional prefix to filter objects.

        Returns:
            List of BackupFile objects.
        """

    @abc.abstractmethod
    async def delete(self, path: str) -> bool:
        """Delete an object from the storage backend.

        Args:
            path: The object path in storage.

        Returns:
            True if the object was deleted, False if it didn't exist.
        """

    @abc.abstractmethod
    async def exists(self, path: str) -> bool:
        """Check if an object exists in the storage backend.

        Args:
            path: The object path in storage.

        Returns:
            True if the object exists.
        """
