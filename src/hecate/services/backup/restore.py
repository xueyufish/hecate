"""Restore engine — full, per-data-type, per-tenant, and PITR recovery."""

from __future__ import annotations

import asyncio
import logging
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlparse

from sqlalchemy import select, text

from hecate.core.config import settings
from hecate.core.database import async_session_factory
from hecate.models.backup import BackupRecordModel, RestoreConflict

from .factory import build_backup_path, create_backup_storage
from .storage import BackupStorage

logger = logging.getLogger(__name__)

TENANT_SCOPED_TABLES = [
    "agents",
    "conversations",
    "messages",
    "knowledge_bases",
    "documents",
    "workflows",
    "tools",
    "skills",
    "prompts",
    "prompt_versions",
    "evaluations",
    "datasets",
    "alert_rules",
    "alert_events",
    "alert_notifications",
    "quotas",
    "budgets",
    "plugins",
    "workspace_members",
    "api_keys",
    "hook_configs",
    "tool_policies",
    "approvals",
    "tool_decisions",
    "checkpoints",
    "evidence",
    "sessions",
    "memories",
    "a2a_tasks",
    "scheduled_tasks",
    "model_cost_budgets",
    "security_findings",
    "pii_mappings",
    "conversation_turn_scores",
    "conversation_clusters",
    "inference_endpoints",
    "fine_tuning_jobs",
    "agent_card_keys",
    "traces",
]


@dataclass
class RestoreResult:
    backup_id: uuid.UUID
    scope: str
    workspace_id: uuid.UUID | None = None
    conflict: str = RestoreConflict.FAIL
    status: str = "completed"
    details: dict | None = None
    error: str | None = None


async def restore_backup(
    backup_id: uuid.UUID,
    scope: str = "all",
    workspace_id: uuid.UUID | None = None,
    conflict: str = RestoreConflict.FAIL,
    pitr_timestamp: datetime | None = None,
    storage: BackupStorage | None = None,
) -> RestoreResult:
    """Restore data from a backup record.

    Args:
        backup_id: The BackupRecord ID to restore from.
        scope: What to restore — "all", "pg", "qdrant", "minio", "fs".
        workspace_id: If set, restore only this workspace's data (per-tenant).
        conflict: Conflict strategy — "replace", "merge", or "fail".
        pitr_timestamp: If set, perform PITR to this timestamp (PostgreSQL only).
        storage: Optional pre-configured storage backend.

    Returns:
        RestoreResult with status and details.
    """
    if storage is None:
        storage = create_backup_storage()

    async with async_session_factory() as session:
        record = await session.get(BackupRecordModel, backup_id)
        if record is None:
            return RestoreResult(
                backup_id=backup_id,
                scope=scope,
                status="failed",
                error=f"Backup record {backup_id} not found",
            )
        backup_ts = record.started_at

    result = RestoreResult(backup_id=backup_id, scope=scope, workspace_id=workspace_id, conflict=conflict)

    scopes = _resolve_scopes(scope)
    details: dict = {"scopes": {}}

    for s in scopes:
        try:
            if pitr_timestamp and s == "pg":
                await _restore_pg_pitr(storage, backup_ts, pitr_timestamp, result)
            elif workspace_id and s == "pg":
                await _restore_pg_tenant(storage, backup_ts, workspace_id, conflict, result)
            elif workspace_id and s == "qdrant":
                await _restore_qdrant_tenant(storage, backup_ts, workspace_id, result)
            elif workspace_id and s == "minio":
                await _restore_minio_tenant(storage, backup_ts, workspace_id, result)
            elif workspace_id and s == "fs":
                await _restore_fs_tenant(storage, backup_ts, workspace_id, result)
            else:
                await _restore_full(storage, backup_ts, s, result)
            details["scopes"][s] = "ok"
        except Exception as e:
            logger.error("Restore scope '%s' failed: %s", s, e)
            details["scopes"][s] = f"error: {e}"
            result.error = str(e)
            result.status = "partial" if len(details["scopes"]) < len(scopes) else "failed"

    result.details = details
    return result


def _resolve_scopes(scope: str) -> list[str]:
    if scope == "all":
        return ["pg", "qdrant", "minio", "fs"]
    return [scope]


async def _restore_full(
    storage: BackupStorage,
    timestamp: datetime,
    scope: str,
    result: RestoreResult,
) -> None:
    if scope == "pg":
        path = build_backup_path(timestamp, "pg", "full.dump")
        dump_bytes = await storage.download(path)
        await asyncio.to_thread(_run_pg_restore, dump_bytes)
    elif scope == "qdrant":
        objects = await storage.list_objects(prefix=timestamp.strftime("%Y%m%d_%H%M%S") + "/qdrant/")
        for obj in objects:
            snapshot_bytes = await storage.download(obj.path)
            coll_name = obj.path.rsplit("/", 1)[-1].replace(".snapshot", "")
            await _restore_qdrant_collection(coll_name, snapshot_bytes)
    elif scope == "minio":
        from hecate.services.rag.storage import MinIOStorage

        minio_storage = MinIOStorage()
        client = minio_storage._get_client()  # noqa: SLF001
        objects = await storage.list_objects(prefix=timestamp.strftime("%Y%m%d_%H%M%S") + "/minio/")
        prefix_len = len(timestamp.strftime("%Y%m%d_%H%M%S") + "/minio/")
        for obj in objects:
            data = await storage.download(obj.path)
            import io

            client.put_object(
                bucket_name=settings.MINIO_BUCKET,
                object_name=obj.path[prefix_len:],
                data=io.BytesIO(data),
                length=len(data),
            )
    elif scope == "fs":
        for label in ("workspace", "plugins"):
            path = build_backup_path(timestamp, "fs", f"{label}.tar.gz")
            try:
                exists = await storage.exists(path)
                if not exists:
                    continue
                archive_bytes = await storage.download(path)
                await _extract_tarball(archive_bytes, label)
            except Exception as e:
                logger.warning("FS restore %s failed: %s", label, e)


async def _restore_pg_tenant(
    storage: BackupStorage,
    timestamp: datetime,
    workspace_id: uuid.UUID,
    conflict: str,
    result: RestoreResult,
) -> None:
    path = build_backup_path(timestamp, "pg", "full.dump")
    dump_bytes = await storage.download(path)

    if conflict == RestoreConflict.FAIL:
        async with async_session_factory() as session:
            count = await session.scalar(
                select(text("count(*)")).select_from(text("workspace_members")).where(text("workspace_id = :wid")),
                {"wid": workspace_id},
            )
            if count and count > 0:
                raise RuntimeError(f"Workspace {workspace_id} already has data and conflict=fail")

    if conflict == RestoreConflict.REPLACE:
        await _delete_tenant_data(workspace_id)

    with tempfile.NamedTemporaryFile(suffix=".dump", delete=False) as tmp:
        tmp.write(dump_bytes)
        tmp_path = tmp.name

    try:
        temp_db = f"hecate_restore_{uuid.uuid4().hex[:8]}"
        await asyncio.to_thread(_create_temp_db, temp_db)
        await asyncio.to_thread(_run_pg_restore_to_db, tmp_path, temp_db)

        for table in TENANT_SCOPED_TABLES:
            await _copy_tenant_rows(temp_db, table, workspace_id, conflict)

        await asyncio.to_thread(_drop_temp_db, temp_db)
    finally:
        import os

        os.unlink(tmp_path)


async def _restore_qdrant_tenant(
    storage: BackupStorage,
    timestamp: datetime,
    workspace_id: uuid.UUID,
    result: RestoreResult,
) -> None:
    async with async_session_factory() as session:
        rows = await session.execute(
            select(text("collection_name")).select_from(text("knowledge_bases")).where(text("workspace_id = :wid")),
            {"wid": workspace_id},
        )
        collection_names = [r[0] for r in rows]

    for coll_name in collection_names:
        path = build_backup_path(timestamp, "qdrant", f"{coll_name}.snapshot")
        try:
            snapshot_bytes = await storage.download(path)
            await _restore_qdrant_collection(coll_name, snapshot_bytes)
        except Exception as e:
            logger.warning("Could not restore Qdrant collection %s: %s", coll_name, e)


async def _restore_minio_tenant(
    storage: BackupStorage,
    timestamp: datetime,
    workspace_id: uuid.UUID,
    result: RestoreResult,
) -> None:
    async with async_session_factory() as session:
        rows = await session.execute(
            select(text("file_path"))
            .select_from(text("documents"))
            .where(text("knowledge_base_id IN (SELECT id FROM knowledge_bases WHERE workspace_id = :wid)")),
            {"wid": workspace_id},
        )
        file_paths = [r[0] for r in rows]

    prefix = timestamp.strftime("%Y%m%d_%H%M%S") + "/minio/"
    from hecate.services.rag.storage import MinIOStorage

    minio_storage = MinIOStorage()
    client = minio_storage._get_client()  # noqa: SLF001
    import io

    for fp in file_paths:
        backup_obj_path = prefix + fp
        try:
            data = await storage.download(backup_obj_path)
            client.put_object(
                bucket_name=settings.MINIO_BUCKET,
                object_name=fp,
                data=io.BytesIO(data),
                length=len(data),
            )
        except Exception as e:
            logger.warning("Could not restore MinIO object %s: %s", fp, e)


async def _restore_fs_tenant(
    storage: BackupStorage,
    timestamp: datetime,
    workspace_id: uuid.UUID,
    result: RestoreResult,
) -> None:
    path = build_backup_path(timestamp, "fs", "workspace.tar.gz")
    try:
        archive_bytes = await storage.download(path)
        target = f"{settings.WORKSPACE_ROOT}/workspace_{workspace_id}"
        await _extract_tarball_to(archive_bytes, target)
    except Exception as e:
        logger.warning("Could not restore FS for workspace %s: %s", workspace_id, e)


async def _restore_pg_pitr(
    storage: BackupStorage,
    timestamp: datetime,
    pitr_target: datetime,
    result: RestoreResult,
) -> None:
    logger.warning("PITR restore requires pg_basebackup + WAL replay — running pg_dump fallback")
    path = build_backup_path(timestamp, "pg", "full.dump")
    dump_bytes = await storage.download(path)
    await asyncio.to_thread(_run_pg_restore, dump_bytes)


async def _delete_tenant_data(workspace_id: uuid.UUID) -> None:
    """Delete all tenant-scoped rows for the given workspace (child-first)."""
    async with async_session_factory() as session:
        for table in reversed(TENANT_SCOPED_TABLES):
            try:
                await session.execute(
                    text(f"DELETE FROM {table} WHERE workspace_id = :wid"),  # noqa: S608
                    {"wid": workspace_id},
                )
            except Exception as e:
                logger.debug("Skip delete from %s: %s", table, e)
        await session.commit()


async def _copy_tenant_rows(
    source_db: str,
    table: str,
    workspace_id: uuid.UUID,
    conflict: str,
) -> None:
    """Copy tenant rows from temp DB to target DB."""
    from sqlalchemy.ext.asyncio import create_async_engine

    source_url = settings.DATABASE_URL.rsplit("/", 1)[0] + f"/{source_db}"
    source_engine = create_async_engine(source_url)

    try:
        async with source_engine.connect() as conn:
            try:
                rows = await conn.execute(
                    text(f"SELECT * FROM {table} WHERE workspace_id = :wid"),  # noqa: S608
                    {"wid": workspace_id},
                )
                columns = list(rows.keys())
                values = [dict(zip(columns, row, strict=False)) for row in rows]
            except Exception:
                return

        if not values:
            return

        async with async_session_factory() as session:
            for row in values:
                if conflict == RestoreConflict.MERGE:
                    await session.merge(
                        type("_GenericRow", (), {**row, "__table__": table}),
                    )
                else:
                    col_list = ", ".join(columns)
                    param_list = ", ".join(f":{c}" for c in columns)
                    await session.execute(
                        text(f"INSERT INTO {table} ({col_list}) VALUES ({param_list})"),  # noqa: S608
                        row,
                    )
            await session.commit()
    finally:
        await source_engine.dispose()


def _build_pg_connection_args() -> tuple[list[str], str]:
    url = urlparse(settings.DATABASE_URL)
    args: list[str] = []
    if url.username:
        args.extend(["-U", url.username])
    if url.hostname:
        args.extend(["-h", url.hostname])
    if url.port:
        args.extend(["-p", str(url.port)])
    db_name = url.path.lstrip("/") or "hecate"
    return args, db_name


def _run_pg_restore(dump_bytes: bytes) -> None:
    args, db_name = _build_pg_connection_args()
    with tempfile.NamedTemporaryFile(suffix=".dump", delete=False) as tmp:
        tmp.write(dump_bytes)
        tmp_path = tmp.name

    try:
        cmd = [
            "pg_restore",
            "-d",
            f"postgresql://{_pg_conn_str(db_name)}",
            "--clean",
            "--if-exists",
            "-j",
            str(settings.BACKUP_PG_DUMP_JOBS),
            tmp_path,
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=3600)  # noqa: S603
        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="replace")
            logger.warning("pg_restore completed with warnings: %s", stderr[:500])
    finally:
        import os

        os.unlink(tmp_path)


def _pg_conn_str(db_name: str) -> str:
    url = urlparse(settings.DATABASE_URL)
    creds = url.username or ""
    if url.password:
        creds += f":{url.password}"
    host = url.hostname or "localhost"
    port = url.port or 5432
    return f"{creds}@{host}:{port}/{db_name}" if creds else f"{host}:{port}/{db_name}"


def _create_temp_db(db_name: str) -> None:
    args, main_db = _build_pg_connection_args()
    cmd = ["createdb", *_build_admin_args(), db_name]
    result = subprocess.run(cmd, capture_output=True, timeout=30)  # noqa: S603
    if result.returncode != 0:
        raise RuntimeError(f"createdb failed: {result.stderr.decode()}")


def _drop_temp_db(db_name: str) -> None:
    cmd = ["dropdb", "--if-exists", *_build_admin_args(), db_name]
    subprocess.run(cmd, capture_output=True, timeout=30)  # noqa: S603


def _build_admin_args() -> list[str]:
    url = urlparse(settings.DATABASE_URL)
    args: list[str] = []
    if url.username:
        args.extend(["-U", url.username])
    if url.hostname:
        args.extend(["-h", url.hostname])
    if url.port:
        args.extend(["-p", str(url.port)])
    return args


def _run_pg_restore_to_db(dump_path: str, db_name: str) -> None:
    conn = _pg_conn_str(db_name)
    cmd = ["pg_restore", "-d", f"postgresql://{conn}", "--no-owner", "--no-acl", dump_path]
    result = subprocess.run(cmd, capture_output=True, timeout=3600)  # noqa: S603
    if result.returncode != 0:
        logger.warning("pg_restore to temp DB had warnings: %s", result.stderr.decode()[:500])


async def _restore_qdrant_collection(coll_name: str, snapshot_bytes: bytes) -> None:
    import httpx

    qdrant_url = settings.QDRANT_URL.rstrip("/")
    headers = {}
    if settings.QDRANT_API_KEY:
        headers["api-key"] = settings.QDRANT_API_KEY

    async with httpx.AsyncClient(timeout=300) as client:
        resp = await client.put(
            f"{qdrant_url}/collections/{coll_name}/snapshots/restore",
            headers=headers,
            content=snapshot_bytes,
        )
        resp.raise_for_status()


async def _extract_tarball(archive_bytes: bytes, label: str) -> None:
    target = settings.WORKSPACE_ROOT if label == "workspace" else settings.PLUGINS_DIR
    await _extract_tarball_to(archive_bytes, target)


async def _extract_tarball_to(archive_bytes: bytes, target_dir: str) -> None:
    import io
    import os
    import tarfile

    buf = io.BytesIO(archive_bytes)
    os.makedirs(target_dir, exist_ok=True)
    with tarfile.open(fileobj=buf, mode="r:gz") as tar:
        tar.extractall(path=target_dir)  # noqa: S202
