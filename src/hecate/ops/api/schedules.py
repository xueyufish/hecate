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

Every entry sits behind the schedule access boundary: an active workspace
is required for every identity — system scope included (403
``NO_WORKSPACE``; an explicit zero-UUID workspace stays a legal value).
List/detail/execution-history check the VIEWER floor; create/update/
delete/pause/resume/trigger check the EDITOR floor — roles come only
from the server-resolved auth context, and system scope keeps its role
exemption inside an explicit workspace without gaining any global query.
By-id entries resolve schedule visibility first with a minimal query
(missing, soft-deleted, and cross-workspace schedules — including
same-org and cross-org — share one 404 that leaks neither the reason
nor any content), then apply the role floor, and only then read config,
validate cron/business parameters, or mutate. Historical org-level rows
(``workspace_id IS NULL``) are preserved but never implicitly opened to
a current workspace. Create derives workspace/org attribution from the
live workspace row and validates explicit agent/workflow bindings plus
execution_config reserved keys before any persistence or scheduler
registration; resume and trigger re-verify the binding before
re-enabling work. Denials are side-effect free: no schedule/execution
rows, timestamps, or scheduler registrations change. The manual trigger
endpoint records a trigger entry — it does not certify that the
underlying execution ran.
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.auth_context import AuthContext
from hecate.core.deps import get_db
from hecate.core.deps_workspace import (
    WorkspaceRole,
    WorkspaceRoleRequiredError,
    ensure_workspace_role,
    get_auth_context,
)
from hecate.models.scheduled_task import (
    ScheduledTaskCreateSchema,
    ScheduledTaskExecutionReadSchema,
    ScheduledTaskReadSchema,
    ScheduledTaskUpdateSchema,
)
from hecate.ops.scheduling.manager import ScheduleManager
from hecate.ops.scheduling.service import (
    ExecutionConfigError,
    ScheduledTaskService,
    ScheduleTargetError,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/schedules", tags=["schedules"])


def get_schedule_manager() -> ScheduleManager:
    """Process scheduler handle for external registrations (DI seam)."""
    return ScheduleManager()


def _workspace_id(ctx: AuthContext) -> uuid.UUID:
    """The caller's explicit workspace; never a silent global fallback."""
    if ctx.workspace_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": {"code": "NO_WORKSPACE", "message": "An active workspace is required", "details": None}},
        )
    return ctx.workspace_id


def _require_role(ctx: AuthContext, minimum: WorkspaceRole) -> None:
    """Server-side role floor from the resolved auth context only."""
    try:
        ensure_workspace_role(ctx, minimum)
    except WorkspaceRoleRequiredError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": {"code": "FORBIDDEN", "message": e.message, "details": None}},
        ) from e


def _schedule_not_found() -> HTTPException:
    """Uniform 404 for missing, soft-deleted and foreign schedules."""
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"error": {"code": "NOT_FOUND", "message": "Scheduled task not found", "details": None}},
    )


def _invalid_cron(expression: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={
            "error": {
                "code": "INVALID_CRON",
                "message": f"Invalid cron expression: {expression}",
                "details": None,
            }
        },
    )


def _target_rejected(exc: ScheduleTargetError, *, creation: bool) -> HTTPException:
    code = "INVALID_TARGET_BINDING" if creation else "TARGET_BINDING_UNAVAILABLE"
    http_status = status.HTTP_422_UNPROCESSABLE_ENTITY if creation else status.HTTP_409_CONFLICT
    return HTTPException(
        status_code=http_status,
        detail={"error": {"code": code, "message": exc.message, "details": None}},
    )


def _execution_config_rejected(exc: ExecutionConfigError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={"error": {"code": "INVALID_EXECUTION_CONFIG", "message": exc.message, "details": None}},
    )


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_schedule(
    data: ScheduledTaskCreateSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    scheduler: Annotated[ScheduleManager, Depends(get_schedule_manager)],
) -> dict:
    """Create a new scheduled task bound to the caller's workspace."""
    # Workspace requirement and the editor floor run before any cron or
    # binding validation, storage access, or scheduler registration.
    workspace_id = _workspace_id(ctx)
    _require_role(ctx, WorkspaceRole.EDITOR)
    if not scheduler.validate_cron(data.cron_expression):
        raise _invalid_cron(data.cron_expression)

    service = ScheduledTaskService(db, scheduler)
    try:
        task = await service.create_task(
            workspace_id=workspace_id,
            name=data.name,
            cron_expression=data.cron_expression,
            agent_id=data.agent_id,
            workflow_id=data.workflow_id,
            execution_config=data.execution_config or {},
            max_concurrent_runs=data.max_concurrent_runs,
            catch_up=data.catch_up,
            timezone=data.timezone,
            description=data.description,
        )
    except LookupError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": "Workspace not found", "details": None}},
        ) from e
    except ExecutionConfigError as e:
        raise _execution_config_rejected(e) from e
    except ScheduleTargetError as e:
        raise _target_rejected(e, creation=True) from e
    await db.commit()
    return ScheduledTaskReadSchema.model_validate(task).model_dump()


@router.get("")
async def list_schedules(
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    """List live scheduled tasks of the caller's workspace with pagination."""
    # Workspace and role gates run before the query; the scope itself
    # excludes soft-deleted and historical org-level rows.
    workspace_id = _workspace_id(ctx)
    _require_role(ctx, WorkspaceRole.VIEWER)
    service = ScheduledTaskService(db)
    items, total = await service.list_tasks(
        workspace_id=workspace_id,
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
    workspace_id = _workspace_id(ctx)
    service = ScheduledTaskService(db)
    if not await service.is_task_visible(task_id, workspace_id=workspace_id):
        raise _schedule_not_found()
    _require_role(ctx, WorkspaceRole.VIEWER)
    task = await service.get_task(task_id, workspace_id=workspace_id)
    if task is None:
        raise _schedule_not_found()
    return ScheduledTaskReadSchema.model_validate(task).model_dump()


@router.put("/{task_id}")
async def update_schedule(
    task_id: uuid.UUID,
    data: ScheduledTaskUpdateSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    scheduler: Annotated[ScheduleManager, Depends(get_schedule_manager)],
) -> dict:
    """Update a scheduled task."""
    workspace_id = _workspace_id(ctx)
    service = ScheduledTaskService(db, scheduler)
    if not await service.is_task_visible(task_id, workspace_id=workspace_id):
        raise _schedule_not_found()
    _require_role(ctx, WorkspaceRole.EDITOR)
    if data.cron_expression and not scheduler.validate_cron(data.cron_expression):
        raise _invalid_cron(data.cron_expression)

    update_data = data.model_dump(exclude_none=True)
    try:
        task = await service.update_task(task_id, workspace_id=workspace_id, **update_data)
    except ExecutionConfigError as e:
        raise _execution_config_rejected(e) from e
    except ScheduleTargetError as e:
        raise _target_rejected(e, creation=False) from e
    if task is None:
        raise _schedule_not_found()
    await db.commit()
    return ScheduledTaskReadSchema.model_validate(task).model_dump()


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_schedule(
    task_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    scheduler: Annotated[ScheduleManager, Depends(get_schedule_manager)],
) -> None:
    """Soft-delete a scheduled task (binding liveness not required)."""
    workspace_id = _workspace_id(ctx)
    service = ScheduledTaskService(db, scheduler)
    if not await service.is_task_visible(task_id, workspace_id=workspace_id):
        raise _schedule_not_found()
    _require_role(ctx, WorkspaceRole.EDITOR)
    deleted = await service.delete_task(task_id, workspace_id=workspace_id)
    if not deleted:
        raise _schedule_not_found()
    await db.commit()


@router.post("/{task_id}/pause")
async def pause_schedule(
    task_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Pause a scheduled task (binding liveness not required)."""
    workspace_id = _workspace_id(ctx)
    service = ScheduledTaskService(db)
    if not await service.is_task_visible(task_id, workspace_id=workspace_id):
        raise _schedule_not_found()
    _require_role(ctx, WorkspaceRole.EDITOR)
    task = await service.pause_task(task_id, workspace_id=workspace_id)
    if task is None:
        raise _schedule_not_found()
    await db.commit()
    return ScheduledTaskReadSchema.model_validate(task).model_dump()


@router.post("/{task_id}/resume")
async def resume_schedule(
    task_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Resume a paused scheduled task; the target binding is re-verified."""
    workspace_id = _workspace_id(ctx)
    service = ScheduledTaskService(db)
    if not await service.is_task_visible(task_id, workspace_id=workspace_id):
        raise _schedule_not_found()
    _require_role(ctx, WorkspaceRole.EDITOR)
    try:
        task = await service.resume_task(task_id, workspace_id=workspace_id)
    except ScheduleTargetError as e:
        raise _target_rejected(e, creation=False) from e
    if task is None:
        raise _schedule_not_found()
    await db.commit()
    return ScheduledTaskReadSchema.model_validate(task).model_dump()


@router.post("/{task_id}/trigger")
async def trigger_schedule(
    task_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Record a manual trigger for a scheduled task.

    The record entry is bookkeeping only — it does not certify that the
    agent/workflow execution ran (a known limitation tracked outside
    this API).
    """
    workspace_id = _workspace_id(ctx)
    service = ScheduledTaskService(db)
    if not await service.is_task_visible(task_id, workspace_id=workspace_id):
        raise _schedule_not_found()
    _require_role(ctx, WorkspaceRole.EDITOR)
    try:
        execution = await service.trigger_manual_run(task_id, workspace_id=workspace_id)
    except ScheduleTargetError as e:
        raise _target_rejected(e, creation=False) from e
    if execution is None:
        raise _schedule_not_found()
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
    # An invisible parent never enters the history query.
    workspace_id = _workspace_id(ctx)
    service = ScheduledTaskService(db)
    if not await service.is_task_visible(task_id, workspace_id=workspace_id):
        raise _schedule_not_found()
    _require_role(ctx, WorkspaceRole.VIEWER)
    result = await service.list_executions(task_id, workspace_id=workspace_id, page=page, page_size=page_size)
    if result is None:
        raise _schedule_not_found()
    items, total = result
    return {
        "items": [ScheduledTaskExecutionReadSchema.model_validate(e).model_dump() for e in items],
        "total": total,
    }
