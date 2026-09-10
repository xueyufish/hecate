"""Evaluation task API endpoints (7.2c Online/Offline Evaluation Tasks).

- ``POST /api/evaluation/tasks`` — create a task (offline or online)
- ``GET /api/evaluation/tasks`` — list tasks (paginated, filterable)
- ``GET /api/evaluation/tasks/{task_id}`` — get a task
- ``PUT /api/evaluation/tasks/{task_id}`` — update a task
- ``DELETE /api/evaluation/tasks/{task_id}`` — soft-delete a task
- ``POST /api/evaluation/tasks/{task_id}/enable`` — enable an online task
- ``POST /api/evaluation/tasks/{task_id}/disable`` — disable an online task
- ``POST /api/evaluation/tasks/{task_id}/runs`` — queue an offline run (202)
- ``GET /api/evaluation/tasks/{task_id}/runs`` — list a task's runs
- ``GET /api/evaluation/scores`` — query online scores (paginated, filterable)

Offline run execution follows the synthesis-job flow: the handler persists a
``queued`` run row, commits, spawns the background runner, and returns 202.
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.auth_context import AuthContext
from hecate.core.deps import get_db
from hecate.core.deps_workspace import get_auth_context
from hecate.models.evaluation import (
    EvaluationRunReadSchema,
    EvaluationTaskCreateSchema,
    EvaluationTaskModel,
    EvaluationTaskReadSchema,
    EvaluationTaskScoreReadSchema,
    EvaluationTaskUpdateSchema,
)
from hecate.ops.evaluation.tasks.runner import OfflineTaskRunner
from hecate.ops.evaluation.tasks.service import (
    EvaluationTaskNotFoundError,
    EvaluationTaskService,
    EvaluationTaskValidationError,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/evaluation", tags=["evaluation"])


def _task_to_dict(task: EvaluationTaskModel) -> dict:
    return EvaluationTaskReadSchema.model_validate(task).model_dump(mode="json")


@router.post("/tasks", status_code=status.HTTP_201_CREATED)
async def create_task(
    data: EvaluationTaskCreateSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Create an evaluation task in ``active`` state."""
    svc = EvaluationTaskService(db)
    try:
        task = await svc.create_task(data, workspace_id=ctx.workspace_id)
    except EvaluationTaskValidationError as exc:
        raise _validation_error(str(exc)) from exc
    return _task_to_dict(task)


@router.get("/tasks")
async def list_tasks(
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    task_type: Annotated[str | None, Query(pattern="^(offline|online)$")] = None,
    task_status: Annotated[str | None, Query(alias="status", pattern="^(active|disabled)$")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    """List the workspace's evaluation tasks, newest first."""
    svc = EvaluationTaskService(db)
    tasks, total = await svc.list_tasks(
        workspace_id=ctx.workspace_id,
        task_type=task_type,
        status=task_status,
        page=page,
        page_size=page_size,
    )
    return {"items": [_task_to_dict(t) for t in tasks], "total": total}


@router.get("/tasks/{task_id}")
async def get_task(
    task_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Get a single evaluation task."""
    svc = EvaluationTaskService(db)
    task = await svc.get_task(task_id, workspace_id=ctx.workspace_id)
    if task is None:
        raise _not_found()
    return _task_to_dict(task)


@router.put("/tasks/{task_id}")
async def update_task(
    task_id: uuid.UUID,
    data: EvaluationTaskUpdateSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Update an evaluation task (partial; merged config is re-validated)."""
    svc = EvaluationTaskService(db)
    try:
        task = await svc.update_task(task_id, workspace_id=ctx.workspace_id, data=data)
    except EvaluationTaskNotFoundError as exc:
        raise _not_found() from exc
    except EvaluationTaskValidationError as exc:
        raise _validation_error(str(exc)) from exc
    return _task_to_dict(task)


@router.delete("/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(
    task_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> None:
    """Soft-delete an evaluation task."""
    svc = EvaluationTaskService(db)
    try:
        await svc.delete_task(task_id, workspace_id=ctx.workspace_id)
    except EvaluationTaskNotFoundError as exc:
        raise _not_found() from exc


@router.post("/tasks/{task_id}/enable")
async def enable_task(
    task_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Enable an online task (evaluators re-validated at enable time)."""
    svc = EvaluationTaskService(db)
    try:
        task = await svc.enable_task(task_id, workspace_id=ctx.workspace_id)
    except EvaluationTaskNotFoundError as exc:
        raise _not_found() from exc
    except EvaluationTaskValidationError as exc:
        raise _validation_error(str(exc)) from exc
    return _task_to_dict(task)


@router.post("/tasks/{task_id}/disable")
async def disable_task(
    task_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Disable an online task; produced scores remain queryable."""
    svc = EvaluationTaskService(db)
    try:
        task = await svc.disable_task(task_id, workspace_id=ctx.workspace_id)
    except EvaluationTaskNotFoundError as exc:
        raise _not_found() from exc
    except EvaluationTaskValidationError as exc:
        raise _validation_error(str(exc)) from exc
    return _task_to_dict(task)


@router.post("/tasks/{task_id}/runs", status_code=status.HTTP_202_ACCEPTED)
async def trigger_task_run(
    task_id: uuid.UUID,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Queue an asynchronous offline run and return 202 immediately."""
    svc = EvaluationTaskService(db)
    try:
        run = await svc.trigger_run(task_id, workspace_id=ctx.workspace_id)
    except EvaluationTaskNotFoundError as exc:
        raise _not_found() from exc
    except EvaluationTaskValidationError as exc:
        raise _validation_error(str(exc)) from exc

    # The background runner opens its own session — the queued row must be
    # committed before the handoff (same flow as the synthesis job API).
    await db.commit()
    await OfflineTaskRunner(db).run_in_background(run.id, task_id)
    return {"run_id": str(run.id), "status": run.status}


@router.get("/tasks/{task_id}/runs")
async def list_task_runs(
    task_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    """List a task's runs, newest first, with summaries when computed."""
    svc = EvaluationTaskService(db)
    try:
        runs, total = await svc.list_task_runs(task_id, workspace_id=ctx.workspace_id, page=page, page_size=page_size)
    except EvaluationTaskNotFoundError as exc:
        raise _not_found() from exc
    return {
        "items": [EvaluationRunReadSchema.model_validate(r).model_dump(mode="json") for r in runs],
        "total": total,
    }


@router.get("/scores")
async def list_scores(
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    task_id: uuid.UUID | None = None,
    target_id: uuid.UUID | None = None,
    session_id: uuid.UUID | None = None,
    metric_name: str | None = None,
    score_status: Annotated[str | None, Query(alias="status", pattern="^(completed|error)$")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    """Query online task scores with optional filters, oldest first."""
    svc = EvaluationTaskService(db)
    scores, total = await svc.list_scores(
        workspace_id=ctx.workspace_id,
        task_id=task_id,
        target_id=target_id,
        session_id=session_id,
        metric_name=metric_name,
        status=score_status,
        page=page,
        page_size=page_size,
    )
    return {
        "items": [EvaluationTaskScoreReadSchema.model_validate(s).model_dump(mode="json") for s in scores],
        "total": total,
    }


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"error": {"code": "NOT_FOUND", "message": "Evaluation task not found", "details": None}},
    )


def _validation_error(message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={"error": {"code": "INVALID_REQUEST", "message": message, "details": None}},
    )
