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
- ``POST /api/evaluation/workflow-evaluations/{workflow_version}/runs`` —
  shortcut: create an ad-hoc workflow-evaluation task + queue a run (7.3)
- ``GET /api/evaluation/scores`` — query online scores (paginated, filterable)

Offline run execution follows the synthesis-job flow: the handler persists a
``queued`` run row, commits, spawns the background runner, and returns 202.
"""

from __future__ import annotations

import logging
import uuid
from types import SimpleNamespace
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.auth_context import AuthContext
from hecate.core.deps import get_db
from hecate.core.deps_workspace import get_auth_context
from hecate.models.evaluation import (
    EvaluationRunModel,
    EvaluationRunReadSchema,
    EvaluationTaskCreateSchema,
    EvaluationTaskModel,
    EvaluationTaskReadSchema,
    EvaluationTaskScoreReadSchema,
    EvaluationTaskUpdateSchema,
    TaskType,
)
from hecate.ops.evaluation.tasks.runner import (
    CostGuardrailExceededError,
    OfflineTaskRunner,
)
from hecate.ops.evaluation.tasks.service import (
    EvaluationTaskNotFoundError,
    EvaluationTaskService,
    EvaluationTaskValidationError,
    build_task_config,
    validate_task,
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


class WorkflowEvaluationTriggerRequest(BaseModel):
    """Request payload for the workflow-evaluation shortcut endpoint."""

    model_config = ConfigDict(extra="forbid")

    workflow_id: uuid.UUID
    dataset_id: uuid.UUID
    evaluators: list[str] = Field(..., min_length=1)
    threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    baseline_run_id: uuid.UUID | None = None
    regression_threshold: float | None = Field(default=None, gt=0.0, le=1.0)
    repetitions: int | None = Field(default=None, ge=1, le=100)
    max_total_executions: int | None = Field(default=None, ge=1, le=1_000_000)
    max_in_flight: int | None = Field(default=None, ge=1, le=64)


@router.post(
    "/workflow-evaluations/{workflow_version}/runs",
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_workflow_evaluation(
    workflow_version: int,
    body: WorkflowEvaluationTriggerRequest,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Create an ad-hoc workflow-evaluation task and queue a run (7.3).

    The endpoint is a shortcut for the typical publish-prep flow: pick a
    workflow version, pick a dataset, pick evaluators, get back a run id.
    A persistent :class:`EvaluationTaskModel` is created so callers can
    list runs / compare against future versions via the regular task API.
    The pre-flight cost guardrail check is performed synchronously and
    returns 400 with the offending total when ``items × repetitions``
    exceeds ``max_total_executions`` (default 1000).
    """
    from sqlalchemy import func
    from sqlalchemy import select as sa_select

    from hecate.models.workflow import WorkflowModel

    if workflow_version < 1:
        raise _validation_error("workflow_version must be >= 1")

    workflow_row = (
        await db.execute(
            sa_select(WorkflowModel).where(
                WorkflowModel.id == body.workflow_id,
                WorkflowModel.workspace_id == ctx.workspace_id,
                ~WorkflowModel.deleted,
            )
        )
    ).scalar_one_or_none()
    if workflow_row is None:
        raise _not_found()

    version_row = (
        await db.execute(
            sa_select(WorkflowModel.current_version).where(
                WorkflowModel.id == body.workflow_id,
                ~WorkflowModel.deleted,
            )
        )
    ).first()
    current_version = int(version_row[0]) if version_row and version_row[0] is not None else None
    if current_version is not None and workflow_version > current_version:
        raise _validation_error(f"workflow_version={workflow_version} exceeds current_version={current_version}")

    payload = SimpleNamespace(
        task_type=TaskType.OFFLINE.value,
        evaluators=body.evaluators,
        dataset_id=body.dataset_id,
        answer_source="workflow",
        threshold=body.threshold,
        baseline_run_id=body.baseline_run_id,
        regression_threshold=body.regression_threshold,
        tags=None,
        workflow_id=body.workflow_id,
        workflow_version=workflow_version,
        repetitions=body.repetitions,
        max_total_executions=body.max_total_executions,
        max_in_flight=body.max_in_flight,
    )

    config: dict
    try:
        config = build_task_config(payload)
        repetitions = int(config.get("repetitions", 1))
        max_total = int(config.get("max_total_executions", 1000))
        from hecate.models.evaluation import EvaluationItemModel

        item_count = int(
            (
                await db.execute(
                    sa_select(func.count())
                    .select_from(EvaluationItemModel)
                    .where(
                        EvaluationItemModel.dataset_id == body.dataset_id,
                        ~EvaluationItemModel.deleted,
                    )
                )
            ).scalar_one()
            or 0
        )
        if item_count * repetitions > max_total:
            raise CostGuardrailExceededError(
                f"cost guardrail: {item_count} items x {repetitions} repetitions = "
                f"{item_count * repetitions} > max_total_executions={max_total}"
            )
    except EvaluationTaskValidationError as exc:
        raise _validation_error(str(exc)) from exc
    except CostGuardrailExceededError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    task = EvaluationTaskModel(
        task_type=TaskType.OFFLINE.value,
        name=f"workflow-eval-{body.workflow_id}-{workflow_version}",
        description="ad-hoc workflow evaluation (7.3)",
        status="active",
        evaluator_configs=body.evaluators,
        config=config,
        workspace_id=ctx.workspace_id,
    )
    validate_task(task)
    db.add(task)
    await db.flush()

    run = EvaluationRunModel(
        dataset_id=body.dataset_id,
        task_id=task.id,
        evaluator_configs=list(body.evaluators),
        workflow_id=body.workflow_id,
        workflow_version=workflow_version,
        repetitions=repetitions,
        status="pending",
        workspace_id=ctx.workspace_id,
    )
    db.add(run)
    await db.flush()
    await db.commit()

    await OfflineTaskRunner(db).run_in_background(run.id, task.id)
    return {
        "run_id": str(run.id),
        "task_id": str(task.id),
        "workflow_id": str(body.workflow_id),
        "workflow_version": workflow_version,
        "status": run.status,
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
