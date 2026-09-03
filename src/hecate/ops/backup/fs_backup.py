"""Filesystem backup engine — rsync/tar for WORKSPACE_ROOT and PLUGINS_DIR."""

from __future__ import annotations

import io
import logging
import os
import tarfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from hecate.core.config import settings

from .factory import build_backup_path
from .storage import BackupStorage

logger = logging.getLogger(__name__)


@dataclass
class FsBackupResult:
    file_count: int = 0
    total_size: int = 0
    paths_backed_up: list[str] = field(default_factory=list)


async def backup_filesystem(
    storage: BackupStorage,
    timestamp: datetime,
    workspace_id: str | None = None,
) -> FsBackupResult:
    """Create tar.gz archives of WORKSPACE_ROOT and PLUGINS_DIR."""
    result = FsBackupResult()

    targets: list[tuple[str, Path]] = [
        ("workspace", Path(settings.WORKSPACE_ROOT)),
        ("plugins", Path(settings.PLUGINS_DIR)),
    ]

    for label, target_path in targets:
        if workspace_id and label == "workspace":
            target_path = target_path / f"workspace_{workspace_id}"
        if not target_path.exists():
            logger.info("Skipping %s (does not exist): %s", label, target_path)
            continue

        try:
            archive_bytes, file_count = await _create_tarball(target_path)
            backup_path = build_backup_path(timestamp, "fs", f"{label}.tar.gz")
            await storage.upload(backup_path, archive_bytes)

            result.file_count += file_count
            result.total_size += len(archive_bytes)
            result.paths_backed_up.append(str(target_path))
            logger.info("Backed up %s: %d files, %d bytes", label, file_count, len(archive_bytes))
        except Exception as e:
            logger.error("Failed to back up %s: %s", label, e)

    return result


async def _create_tarball(source: Path) -> tuple[bytes, int]:
    """Create an in-memory tar.gz of the given directory.

    Returns (archive_bytes, file_count).
    """
    buf = io.BytesIO()
    file_count = 0

    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for root, _dirs, files in os.walk(source):
            for fname in files:
                fpath = Path(root) / fname
                arcname = fpath.relative_to(source.parent)
                tar.add(fpath, arcname=arcname)
                file_count += 1

    return buf.getvalue(), file_count
