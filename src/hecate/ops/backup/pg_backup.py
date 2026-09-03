"""PostgreSQL backup engine — pg_dump and WAL archive support."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urlparse

from sqlalchemy import text

from hecate.core.config import settings
from hecate.core.database import async_session_factory

from .factory import build_backup_path
from .storage import BackupStorage

logger = logging.getLogger(__name__)


@dataclass
class PgBackupResult:
    dump_bytes: bytes
    checksum: str
    size_bytes: int
    row_counts: dict[str, int] = field(default_factory=dict)
    wal_archive_enabled: bool = False
    warnings: list[str] = field(default_factory=list)


async def backup_postgresql(
    storage: BackupStorage,
    timestamp: datetime,
) -> PgBackupResult:
    """Run pg_dump and upload to backup storage.

    Returns metadata about the backup including checksum and row counts.
    """
    logger.info("Starting PostgreSQL backup (pg_dump -Fc)")

    dump_bytes = await asyncio.to_thread(_run_pg_dump)

    checksum = hashlib.sha256(dump_bytes).hexdigest()
    size_bytes = len(dump_bytes)

    path = build_backup_path(timestamp, "pg", "full.dump")
    await storage.upload(path, dump_bytes)
    logger.info("PostgreSQL backup uploaded: %s (%d bytes)", path, size_bytes)

    row_counts = await _collect_row_counts()
    wal_enabled = await _check_wal_archive()
    warnings: list[str] = []
    if not wal_enabled:
        warnings.append(
            "WAL archiving is not enabled. PITR will not be available. "
            "Set archive_mode=on and configure archive_command in PostgreSQL."
        )
        logger.warning("; ".join(warnings))

    return PgBackupResult(
        dump_bytes=dump_bytes,
        checksum=checksum,
        size_bytes=size_bytes,
        row_counts=row_counts,
        wal_archive_enabled=wal_enabled,
        warnings=warnings,
    )


def _build_pg_connection_args() -> list[str]:
    """Build pg_dump connection arguments from DATABASE_URL."""
    url = urlparse(settings.DATABASE_URL)
    args: list[str] = []
    if url.username:
        args.extend(["-U", url.username])
    if url.host:
        args.extend(["-h", url.host])
    if url.port:
        args.extend(["-p", str(url.port)])
    db_name = url.path.lstrip("/") or "hecate"
    args.append(db_name)
    return args


def _run_pg_dump() -> bytes:
    """Execute pg_dump -Fc synchronously."""
    with tempfile.NamedTemporaryFile(suffix=".dump", delete=True) as tmp:
        cmd = [
            "pg_dump",
            "-Fc",
            "-f",
            tmp.name,
            *_build_pg_connection_args(),
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=3600)  # noqa: S603
        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="replace")
            raise RuntimeError(f"pg_dump failed (exit {result.returncode}): {stderr}")
        tmp.seek(0)
        return tmp.read()


async def _collect_row_counts() -> dict[str, int]:
    """Query row counts for all tables to record in backup metadata."""
    counts: dict[str, int] = {}
    try:
        async with async_session_factory() as session:
            result = await session.execute(
                text(
                    """
                    SELECT relname, n_live_tup
                    FROM pg_stat_user_tables
                    ORDER BY relname
                    """
                )
            )
            for row in result:
                counts[row[0]] = int(row[1])
    except Exception as e:
        logger.warning("Could not collect row counts: %s", e)
    return counts


async def _check_wal_archive() -> bool:
    """Check if PostgreSQL WAL archiving is enabled."""
    try:
        async with async_session_factory() as session:
            result = await session.execute(text("SHOW archive_mode"))
            row = result.first()
            return row is not None and row[0].lower() in ("on", "always")
    except Exception as e:
        logger.warning("Could not check archive_mode: %s", e)
        return False
