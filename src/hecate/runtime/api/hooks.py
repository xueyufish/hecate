"""REST API for hook configuration management."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.deps import get_db
from hecate.models.hook_config import (
    HookConfigCreateSchema,
    HookConfigModel,
    HookConfigReadSchema,
)

router = APIRouter(prefix="/api/hooks", tags=["hooks"])

DEFAULT_WORKSPACE_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")


@router.get("", response_model=list[HookConfigReadSchema])
async def list_hooks(
    db: Annotated[AsyncSession, Depends(get_db)],
    agent_id: str | None = None,
    event: str | None = None,
) -> list[HookConfigModel]:
    """List all hook configurations, optionally filtered.

    Args:
        agent_id: Filter by agent ID.
        event: Filter by event type.
    """
    stmt = select(HookConfigModel).where(HookConfigModel.deleted_at.is_(None))
    if agent_id:
        stmt = stmt.where(HookConfigModel.agent_id == uuid.UUID(agent_id))
    if event:
        stmt = stmt.where(HookConfigModel.event == event)
    result = await db.execute(stmt.order_by(HookConfigModel.created_at.desc()))
    return list(result.scalars().all())


@router.post("", response_model=HookConfigReadSchema, status_code=201)
async def create_hook(
    hook: HookConfigCreateSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> HookConfigModel:
    """Create a new hook configuration."""
    model = HookConfigModel(
        workspace_id=DEFAULT_WORKSPACE_ID,
        agent_id=uuid.UUID(hook.agent_id) if hook.agent_id else None,
        event=hook.event,
        matcher=hook.matcher,
        command=hook.command,
        timeout=hook.timeout,
        enabled=hook.enabled,
    )
    db.add(model)
    await db.flush()
    await db.refresh(model)
    return model


@router.delete("/{hook_id}", status_code=204)
async def delete_hook(
    hook_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    """Delete a hook configuration."""
    stmt = select(HookConfigModel).where(
        HookConfigModel.id == uuid.UUID(hook_id),
        HookConfigModel.deleted_at.is_(None),
    )
    result = await db.execute(stmt)
    hook = result.scalar_one_or_none()
    if hook is None:
        raise HTTPException(status_code=404, detail="Hook config not found")
    await db.delete(hook)
