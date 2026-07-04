"""Model Lifecycle REST API — promotion, approval, deprecation, rollback."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.database import get_db
from hecate.model_hub.lifecycle_service import LifecycleService

router = APIRouter(prefix="/api/models", tags=["model-lifecycle"])


@router.get("/deployments")
async def list_deployments(
    channel: str | None = Query(None, pattern="^(dev|staging|prod)$"),
    approval_status: str | None = Query(None, pattern="^(pending|approved|rejected)$"),
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> list[dict[str, Any]]:
    """List all model deployments."""
    service = LifecycleService(db)
    return await service.list_deployments(channel=channel, approval_status=approval_status)


@router.post("/{model_id}/promote")
async def promote_model(
    model_id: str,
    body: dict[str, str],
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Promote a model from one channel to another."""
    from_channel = body.get("from_channel", "")
    to_channel = body.get("to_channel", "")
    if not from_channel or not to_channel:
        raise HTTPException(status_code=400, detail="from_channel and to_channel required")

    service = LifecycleService(db)
    try:
        return await service.promote(model_id, from_channel, to_channel)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/{model_id}/promote/{deployment_id}/approve")
async def approve_deployment(
    model_id: str,
    deployment_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Approve a pending deployment."""
    service = LifecycleService(db)
    # Use system user ID as default approver
    approver_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    try:
        return await service.approve(deployment_id, approver_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/{model_id}/promote/{deployment_id}/reject")
async def reject_deployment(
    model_id: str,
    deployment_id: uuid.UUID,
    body: dict[str, str] | None = None,
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Reject a pending deployment."""
    service = LifecycleService(db)
    reason = (body or {}).get("reason", "")
    try:
        return await service.reject(deployment_id, reason)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/{model_id}/deprecate")
async def deprecate_model(
    model_id: str,
    body: dict[str, str],
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Schedule model deprecation with sunset date."""
    sunset_str = body.get("sunset_at", "")
    if not sunset_str:
        raise HTTPException(status_code=400, detail="sunset_at required")

    try:
        sunset_at = datetime.fromisoformat(sunset_str.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid datetime format for sunset_at") from None

    service = LifecycleService(db)
    try:
        return await service.deprecate(model_id, sunset_at)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/{model_id}/deprecate/cancel")
async def cancel_deprecation(
    model_id: str,
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Cancel model deprecation."""
    service = LifecycleService(db)
    try:
        return await service.cancel_deprecation(model_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/{model_id}/rollback")
async def rollback_model(
    model_id: str,
    body: dict[str, str],
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Rollback model to a previous version."""
    to_version = body.get("to_version", "")
    if not to_version:
        raise HTTPException(status_code=400, detail="to_version required")

    service = LifecycleService(db)
    try:
        return await service.rollback(model_id, to_version)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
