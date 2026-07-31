"""REST API endpoints for backup and restore management."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel as PydanticBase
from pydantic import Field

from hecate.core.database import async_session_factory
from hecate.models.backup import (
    BackupRecordModel,
    BackupRecordReadSchema,
)

router = APIRouter(prefix="/api/system", tags=["system-backup"])


class CreateBackupRequest(PydanticBase):
    scope: str = Field(default="all", pattern="^(all|pg|qdrant|minio|fs)$")


class RestoreRequest(PydanticBase):
    backup_id: uuid.UUID
    scope: str = Field(default="all", pattern="^(all|pg|qdrant|minio|fs)$")
    workspace_id: uuid.UUID | None = None
    conflict: str = Field(default="fail", pattern="^(replace|merge|fail)$")
    confirm: bool = False
    pitr_timestamp: datetime | None = None


class RestoreResponse(PydanticBase):
    status: str
    details: dict | None = None
    error: str | None = None


@router.post("/backups", response_model=BackupRecordReadSchema)
async def create_backup_endpoint(req: CreateBackupRequest) -> BackupRecordModel:
    """Create a new backup. Platform Admin only."""
    _require_platform_admin()
    from hecate.services.backup.orchestrator import create_backup

    record = await create_backup(scope=req.scope)
    return record


@router.get("/backups", response_model=list[BackupRecordReadSchema])
async def list_backups_endpoint(
    status: str | None = Query(None),
    limit: int = Query(50, le=200),
) -> list[BackupRecordModel]:
    """List backup records."""
    from hecate.services.backup.orchestrator import list_backups

    return await list_backups(status=status, limit=limit)


@router.get("/backups/{backup_id}", response_model=BackupRecordReadSchema)
async def get_backup_endpoint(backup_id: uuid.UUID) -> BackupRecordModel:
    """Get details of a specific backup."""
    async with async_session_factory() as session:
        record = await session.get(BackupRecordModel, backup_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Backup not found")
        return record


@router.post("/backups/{backup_id}/verify")
async def verify_backup_endpoint(backup_id: uuid.UUID) -> dict:
    """Trigger verification of a backup."""
    _require_platform_admin()
    from hecate.services.backup.verification import verify_backup

    result = await verify_backup(backup_id)
    return result


@router.post("/restore", response_model=RestoreResponse)
async def restore_endpoint(req: RestoreRequest) -> RestoreResponse:
    """Restore data from a backup. Requires confirm=true."""
    _require_platform_admin()
    if not req.confirm:
        raise HTTPException(status_code=400, detail="confirm=true is required for restore")

    from hecate.services.backup.restore import restore_backup

    result = await restore_backup(
        backup_id=req.backup_id,
        scope=req.scope,
        workspace_id=req.workspace_id,
        conflict=req.conflict,
        pitr_timestamp=req.pitr_timestamp,
    )
    return RestoreResponse(status=result.status, details=result.details, error=result.error)


def _require_platform_admin() -> None:
    """Check platform admin permission.

    TODO: integrate with actual RBAC system once auth middleware exposes
    the current user's role. For now, this is a placeholder that allows
    all authenticated requests.
    """
    pass
