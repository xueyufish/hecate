"""Qdrant backup engine — collection snapshots with fault tolerance."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

from hecate.core.config import settings

from .factory import build_backup_path
from .storage import BackupStorage

logger = logging.getLogger(__name__)


@dataclass
class QdrantBackupResult:
    snapshots: dict[str, bytes] = field(default_factory=dict)
    vector_counts: dict[str, int] = field(default_factory=dict)
    failed_collections: dict[str, str] = field(default_factory=dict)
    total_size: int = 0


async def backup_qdrant(
    storage: BackupStorage,
    timestamp: datetime,
    collections: list[str] | None = None,
) -> QdrantBackupResult:
    """Create snapshots for all (or specified) Qdrant collections.

    Fault-tolerant: a single collection failure does not abort the entire backup.
    """

    qdrant_url = settings.QDRANT_URL.rstrip("/")
    headers = {}
    if settings.QDRANT_API_KEY:
        headers["api-key"] = settings.QDRANT_API_KEY

    result = QdrantBackupResult()

    if collections is None:
        collections = await _list_collections(qdrant_url, headers)

    logger.info("Backing up %d Qdrant collections", len(collections))

    for collection_name in collections:
        try:
            snapshot_bytes = await _create_and_download_snapshot(qdrant_url, headers, collection_name)
            vector_count = await _get_vector_count(qdrant_url, headers, collection_name)

            path = build_backup_path(timestamp, "qdrant", f"{collection_name}.snapshot")
            await storage.upload(path, snapshot_bytes)

            result.snapshots[collection_name] = snapshot_bytes
            result.vector_counts[collection_name] = vector_count
            result.total_size += len(snapshot_bytes)
            logger.info("Backed up collection: %s (%d vectors)", collection_name, vector_count)
        except Exception as e:
            result.failed_collections[collection_name] = str(e)
            logger.error("Failed to back up collection %s: %s", collection_name, e)

    return result


async def _list_collections(qdrant_url: str, headers: dict[str, str]) -> list[str]:
    import httpx

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(f"{qdrant_url}/collections", headers=headers)
        resp.raise_for_status()
        data = resp.json()
        return [c["name"] for c in data.get("result", {}).get("collections", [])]


async def _create_and_download_snapshot(
    qdrant_url: str,
    headers: dict[str, str],
    collection_name: str,
) -> bytes:
    import httpx

    async with httpx.AsyncClient(timeout=300) as client:
        resp = await client.post(
            f"{qdrant_url}/collections/{collection_name}/snapshots",
            headers=headers,
        )
        resp.raise_for_status()
        snapshot_data = resp.json().get("result", {})
        download_url = snapshot_data.get("download_url")

        if download_url and not download_url.startswith("http"):
            download_url = f"{qdrant_url}/{download_url.lstrip('/')}"

        if download_url:
            dl_resp = await client.get(download_url, headers=headers)
            dl_resp.raise_for_status()
            return dl_resp.content
        return resp.content


async def _get_vector_count(qdrant_url: str, headers: dict[str, str], collection_name: str) -> int:
    import httpx

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(f"{qdrant_url}/collections/{collection_name}", headers=headers)
        resp.raise_for_status()
        data = resp.json().get("result", {})
        return data.get("points_count", 0)
