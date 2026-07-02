"""Scheduled task API endpoints.

Provides CRUD and lifecycle management for cron-based scheduled tasks:

- ``POST /api/schedules`` — Create a new scheduled task
- ``GET /api/schedules`` — List scheduled tasks (paginated)
- ``GET /api/schedules/{task_id}`` — Get a single task
- ``PUT /api/schedules/{task_id}`` — Update a task
- ``DELETE /api/schedules/{task_id}`` — Delete a task
- ``POST /api/schedules/{task_id}/pause`` — Pause a task
- ``POST /api/schedules/{task_id}/resume`` — Resume a task
- ``POST /api/schedules/{task_id}/trigger`` — Manually trigger execution
- ``GET /api/schedules/{task_id}/executions`` — List executions for a task
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.auth_context import AuthContext
from hecate.core.deps import get_db
from hecate.core.deps_workspace import get_auth_context
from hecate.models.scheduled_task import (
    ScheduledTaskCreateSchema,
    ScheduledTaskExecutionReadSchema,
    ScheduledTaskReadSchema,
    ScheduledTaskUpdateSchema,
)
from hecate.services.scheduling.manager import ScheduleManager
from hecate.services.scheduling.service import ScheduledTaskService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/schedules", tags=["schedules"])


async def _get_task_or_404(
    task_id: uuid.UUID,
    service: ScheduledTaskService,
) -> object:
    """Look up a scheduled task or raise 404."""

    task = await service.get_task(task_id)
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": "Scheduled task not found", "details": None}},
        )
    return task


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_schedule(
    data: ScheduledTaskCreateSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Create a new scheduled task."""
    # Validate cron expression
    scheduler = ScheduleManager()
    if not scheduler.validate_cron(data.cron_expression):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": {
                    "code": "INVALID_CRON",
                    "message": f"Invalid cron expression: {data.cron_expression}",
                    "details": None,
                }
            },
        )

    service = ScheduledTaskService(db, scheduler)
    task = await service.create_task(
        name=data.name,
        cron_expression=data.cron_expression,
        org_id=ctx.org_id or uuid.UUID(int=0),
        workspace_id=ctx.workspace_id,
        agent_id=data.agent_id,
        workflow_id=data.workflow_id,
        execution_config=data.execution_config or {},
        max_concurrent_runs=data.max_concurrent_runs,
        catch_up=data.catch_up,
        timezone=data.timezone,
        description=data.description,
    )
    await db.commit()
    return ScheduledTaskReadSchema.model_validate(task).model_dump()


@router.get("")
async def list_schedules(
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    """List scheduled tasks with pagination."""
    service = ScheduledTaskService(db)
    items, total = await service.list_tasks(
        org_id=ctx.org_id or uuid.UUID(int=0),
        workspace_id=ctx.workspace_id,
        page=page,
        page_size=page_size,
    )
    return {
        "items": [ScheduledTaskReadSchema.model_validate(t).model_dump() for t in items],
        "total": total,
    }


@router.get("/{task_id}")
async def get_schedule(
    task_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Get a single scheduled task."""
    service = ScheduledTaskService(db)
    task = await _get_task_or_404(task_id, service)
    return ScheduledTaskReadSchema.model_validate(task).model_dump()


@router.put("/{task_id}")
async def update_schedule(
    task_id: uuid.UUID,
    data: ScheduledTaskUpdateSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Update a scheduled task."""
    scheduler = ScheduleManager()
    service = ScheduledTaskService(db, scheduler)

    # Validate cron if provided
    if data.cron_expression and not scheduler.validate_cron(data.cron_expression):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": {
                    "code": "INVALID_CRON",
                    "message": f"Invalid cron expression: {data.cron_expression}",
                    "details": None,
                }
            },
        )

    update_data = data.model_dump(exclude_none=True)
    task = await service.update_task(task_id, **update_data)
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": "Scheduled task not found", "details": None}},
        )
    await db.commit()
    return ScheduledTaskReadSchema.model_validate(task).model_dump()


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_schedule(
    task_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> None:
    """Delete a scheduled task."""
    scheduler = ScheduleManager()
    service = ScheduledTaskService(db, scheduler)
    deleted = await service.delete_task(task_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": "Scheduled task not found", "details": None}},
        )
    await db.commit()


@router.post("/{task_id}/pause")
async def pause_schedule(
    task_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Pause a scheduled task."""
    service = ScheduledTaskService(db)
    task = await service.pause_task(task_id)
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": "Scheduled task not found", "details": None}},
        )
    await db.commit()
    return ScheduledTaskReadSchema.model_validate(task).model_dump()


@router.post("/{task_id}/resume")
async def resume_schedule(
    task_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Resume a paused scheduled task."""
    service = ScheduledTaskService(db)
    task = await service.resume_task(task_id)
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": "Scheduled task not found", "details": None}},
        )
    await db.commit()
    return ScheduledTaskReadSchema.model_validate(task).model_dump()


@router.post("/{task_id}/trigger")
async def trigger_schedule(
    task_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Manually trigger a scheduled task execution."""
    service = ScheduledTaskService(db)
    execution = await service.trigger_manual_run(task_id)
    if execution is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": "Scheduled task not found", "details": None}},
        )
    await db.commit()
    return ScheduledTaskExecutionReadSchema.model_validate(execution).model_dump()


@router.get("/{task_id}/executions")
async def list_executions(
    task_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    """List executions for a scheduled task."""
    service = ScheduledTaskService(db)
    items, total = await service.list_executions(task_id, page=page, page_size=page_size)
    return {
        "items": [ScheduledTaskExecutionReadSchema.model_validate(e).model_dump() for e in items],
        "total": total,
    }
