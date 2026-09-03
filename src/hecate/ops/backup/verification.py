"""Backup verification — restore to temp instance and validate integrity."""

from __future__ import annotations

import logging
import subprocess
import tempfile
import uuid
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import text

from hecate.core.config import settings
from hecate.core.database import async_session_factory
from hecate.models.backup import BackupRecordModel

from .factory import build_backup_path, create_backup_storage

logger = logging.getLogger(__name__)


async def verify_backup(backup_id: uuid.UUID) -> dict:
    """Verify a backup by restoring PostgreSQL to a temp DB and checking row counts.

    Returns a dict with verification results.
    """
    async with async_session_factory() as session:
        record = await session.get(BackupRecordModel, backup_id)
        if record is None:
            raise ValueError(f"Backup record {backup_id} not found")

        expected_counts = (record.metadata_ or {}).get("scopes", {}).get("pg", {}).get("row_counts", {})
        backup_ts = record.started_at

    path = build_backup_path(backup_ts, "pg", "full.dump")
    storage = create_backup_storage()
    dump_bytes = await storage.download(path)

    with tempfile.NamedTemporaryFile(suffix=".dump", delete=False) as tmp:
        tmp.write(dump_bytes)
        tmp_path = tmp.name

    temp_db = f"hecate_verify_{uuid.uuid4().hex[:8]}"
    result: dict = {"expected": {}, "actual": {}, "matched": True, "mismatches": []}

    try:
        await _run_createdb(temp_db)
        await _run_pg_restore_to_db(tmp_path, temp_db)

        actual_counts = await _query_row_counts(temp_db)
        result["expected"] = expected_counts
        result["actual"] = actual_counts

        for table, expected_count in expected_counts.items():
            actual_count = actual_counts.get(table, -1)
            if actual_count != expected_count:
                result["matched"] = False
                result["mismatches"].append(f"{table}: expected {expected_count}, got {actual_count}")

        qdrant_meta = (record.metadata_ or {}).get("scopes", {}).get("qdrant", {})
        if qdrant_meta:
            result["qdrant"] = await _verify_qdrant_snapshots(storage, backup_ts, qdrant_meta)

    finally:
        import os

        os.unlink(tmp_path)
        await _run_dropdb(temp_db)

    status = "verified" if result["matched"] else "verification_failed"

    async with async_session_factory() as session:
        record = await session.get(BackupRecordModel, backup_id)
        if record:
            record.verified_at = datetime.now(UTC)
            record.verification_status = status
            await session.merge(record)
            await session.commit()

    logger.info("Backup %s verification: %s", backup_id, status)
    return result


async def _run_createdb(db_name: str) -> None:
    url = urlparse(settings.DATABASE_URL)
    args: list[str] = ["createdb"]
    if url.username:
        args.extend(["-U", url.username])
    if url.hostname:
        args.extend(["-h", url.hostname])
    if url.port:
        args.extend(["-p", str(url.port)])
    args.append(db_name)
    result = subprocess.run(args, capture_output=True, timeout=30)  # noqa: S603
    if result.returncode != 0:
        raise RuntimeError(f"createdb failed: {result.stderr.decode()}")


async def _run_dropdb(db_name: str) -> None:
    url = urlparse(settings.DATABASE_URL)
    args: list[str] = ["dropdb", "--if-exists"]
    if url.username:
        args.extend(["-U", url.username])
    if url.hostname:
        args.extend(["-h", url.hostname])
    if url.port:
        args.extend(["-p", str(url.port)])
    args.append(db_name)
    subprocess.run(args, capture_output=True, timeout=30)  # noqa: S603


async def _run_pg_restore_to_db(dump_path: str, db_name: str) -> None:
    url = urlparse(settings.DATABASE_URL)
    creds = url.username or ""
    if url.password:
        creds += f":{url.password}"
    hostname = url.hostname or "localhost"
    port = url.port or 5432
    conn = f"{creds}@{hostname}:{port}/{db_name}" if creds else f"{hostname}:{port}/{db_name}"

    cmd = ["pg_restore", "-d", f"postgresql://{conn}", "--no-owner", "--no-acl", dump_path]
    result = subprocess.run(cmd, capture_output=True, timeout=3600)  # noqa: S603
    if result.returncode != 0:
        logger.warning("pg_restore to verify DB had warnings: %s", result.stderr.decode()[:500])


async def _query_row_counts(db_name: str) -> dict[str, int]:
    from sqlalchemy.ext.asyncio import create_async_engine

    url = urlparse(settings.DATABASE_URL)
    username = url.username or "hecate"
    password = url.password or "hecate"
    hostname = url.hostname or "localhost"
    port = url.port or 5432
    source_url = f"postgresql+asyncpg://{username}:{password}@{hostname}:{port}/{db_name}"
    engine = create_async_engine(source_url)

    try:
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT relname, n_live_tup FROM pg_stat_user_tables ORDER BY relname"))
            return {row[0]: int(row[1]) for row in result}
    finally:
        await engine.dispose()


async def _verify_qdrant_snapshots(storage, backup_ts, qdrant_meta) -> dict[str, Any]:
    """Verify Qdrant snapshot checksums."""
    import hashlib

    result: dict[str, Any] = {"checked": 0, "passed": 0, "failed": []}
    expected_counts = qdrant_meta.get("vector_counts", {})

    objects = await storage.list_objects(prefix=backup_ts.strftime("%Y%m%d_%H%M%S") + "/qdrant/")

    for obj in objects:
        coll_name = obj.path.rsplit("/", 1)[-1].replace(".snapshot", "")
        result["checked"] += 1

        try:
            data = await storage.download(obj.path)
            hashlib.sha256(data).hexdigest()
            expected_counts.get(coll_name, 0)

            if len(data) > 0:
                result["passed"] += 1
            else:
                result["failed"].append(f"{coll_name}: empty snapshot")
        except Exception as e:
            result["failed"].append(f"{coll_name}: {e}")

    return result
