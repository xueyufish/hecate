"""Evaluation report API endpoints (7.2e Evaluation Report Dashboard).

Read-only aggregation over the existing evaluation tables:

- ``GET /api/evaluation/reports/overview`` — four-card overview
- ``GET /api/evaluation/reports/trends`` — pass-rate / score timeseries
- ``GET /api/evaluation/reports/distributions`` — per-metric histograms
- ``GET /api/evaluation/reports/breakdowns`` — online scores grouped by
  agent / task / session / source
- ``GET /api/evaluation/reports/sessions`` — session-level rollup

Aggregations are computed on demand in one workspace. Distributions
resolve the selected resource's workspace for explicit system callers
(see ``ops.evaluation.reports.service`` for the conventions).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.auth_context import AuthContext
from hecate.core.deps import get_db
from hecate.core.deps_workspace import (
    WorkspaceRole,
    WorkspaceRoleRequiredError,
    ensure_workspace_role,
    get_auth_context,
)
from hecate.models.evaluation import EvaluationTaskModel
from hecate.ops.api.evaluation import _forbidden, _get_visible_run_or_404
from hecate.ops.evaluation.reports.service import (
    EvaluationReportNotFoundError,
    EvaluationReportService,
    EvaluationReportValidationError,
)

router = APIRouter(prefix="/evaluation", tags=["evaluation"])

_TREND_DIMENSIONS = "^(dataset|workflow|agent)$"
_TREND_BUCKETS = "^(day|hour)$"
_BREAKDOWN_DIMENSIONS = "^(agent|task|session|source)$"


def _require_aggregate_viewer(ctx: AuthContext) -> None:
    """Gate the workspace-aggregate report endpoints.

    The missing-workspace check runs explicitly and first:
    ``ensure_workspace_role`` waives system-scope identities, which would
    otherwise fall through to the service's null-workspace filter and get
    an empty report instead of a denial. These endpoints take no
    target-resource or workspace-selection parameter, so no active
    workspace is 403 NO_WORKSPACE for every identity; a member below
    viewer is 403 FORBIDDEN.
    """
    if ctx.workspace_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": {
                    "code": "NO_WORKSPACE",
                    "message": "request has no workspace scope",
                    "details": None,
                }
            },
        )
    try:
        ensure_workspace_role(ctx, WorkspaceRole.VIEWER)
    except WorkspaceRoleRequiredError as e:
        raise _forbidden(e) from e


@router.get("/reports/overview")
async def overview_report(
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    start_date: datetime | None = None,
    end_date: datetime | None = None,
) -> dict:
    """Aggregate the four overview cards (quality/volume/coverage/error rate)."""
    _require_aggregate_viewer(ctx)
    svc = EvaluationReportService(db)
    report = await _run(svc.overview(ctx.workspace_id, start_date=start_date, end_date=end_date))
    return report.model_dump(mode="json")


@router.get("/reports/trends")
async def trends_report(
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    dimension: Annotated[str, Query(pattern=_TREND_DIMENSIONS)],
    bucket: Annotated[str, Query(pattern=_TREND_BUCKETS)] = "day",
    metric_name: str | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
) -> dict:
    """Per-bucket pass-rate/score timeseries by dataset, workflow, or agent."""
    _require_aggregate_viewer(ctx)
    svc = EvaluationReportService(db)
    report = await _run(
        svc.trends(
            ctx.workspace_id,
            dimension=dimension,
            bucket=bucket,
            metric_name=metric_name,
            start_date=start_date,
            end_date=end_date,
        )
    )
    return report.model_dump(mode="json")


@router.get("/reports/distributions")
async def distributions_report(
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    run_id: uuid.UUID | None = None,
    task_id: uuid.UUID | None = None,
    metric_name: str | None = None,
) -> dict:
    """Per-metric score histograms for one run (offline) or one task (online)."""
    if run_id is not None and task_id is not None:
        raise _invalid_request("only one of run_id or task_id may be provided")
    if run_id is None and task_id is None:
        raise _invalid_request("exactly one of run_id or task_id is required")

    if not ctx.is_system_scope and ctx.workspace_id is None:
        raise _report_scope_not_found()

    if run_id is not None:
        try:
            run = await _get_visible_run_or_404(run_id, db, ctx)
        except HTTPException as exc:
            if exc.status_code != status.HTTP_404_NOT_FOUND:
                raise
            raise _report_scope_not_found() from exc
        resource_workspace_id = run.workspace_id
    else:
        resource_workspace_id = (await _get_visible_task_or_404(task_id, db, ctx)).workspace_id
    try:
        ensure_workspace_role(ctx, WorkspaceRole.VIEWER)
    except WorkspaceRoleRequiredError as e:
        raise _forbidden(e) from e

    svc = EvaluationReportService(db)
    report = await _run(
        svc.distributions(resource_workspace_id, run_id=run_id, task_id=task_id, metric_name=metric_name)
    )
    return report.model_dump(mode="json")


@router.get("/reports/breakdowns")
async def breakdowns_report(
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    group_by: Annotated[str, Query(pattern=_BREAKDOWN_DIMENSIONS)],
    metric_name: str | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
) -> dict:
    """Online task scores grouped by agent, task, session, or source."""
    _require_aggregate_viewer(ctx)
    svc = EvaluationReportService(db)
    report = await _run(
        svc.breakdowns(
            ctx.workspace_id,
            group_by=group_by,
            metric_name=metric_name,
            start_date=start_date,
            end_date=end_date,
        )
    )
    return report.model_dump(mode="json")


@router.get("/reports/sessions")
async def sessions_report(
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    task_id: uuid.UUID | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    """Online task scores rolled up to session granularity, newest-scored first."""
    svc = EvaluationReportService(db)
    report = await _run(
        svc.session_rollup(
            ctx.workspace_id,
            task_id=task_id,
            start_date=start_date,
            end_date=end_date,
            page=page,
            page_size=page_size,
        )
    )
    return report.model_dump(mode="json")


async def _run(coroutine):  # noqa: ANN201 — return type mirrors the awaited schema
    """Await a service call, mapping report errors to HTTP statuses."""
    try:
        return await coroutine
    except EvaluationReportValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": "INVALID_REQUEST", "message": str(exc), "details": None}},
        ) from exc
    except EvaluationReportNotFoundError as exc:
        raise _report_scope_not_found() from exc


def _invalid_request(message: str) -> HTTPException:
    """Keep the parameter contract's 400 envelope and message verbatim."""
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={"error": {"code": "INVALID_REQUEST", "message": message, "details": None}},
    )


def _report_scope_not_found() -> HTTPException:
    """Identifier-free 404 shared by every invisible distributions scope."""
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"error": {"code": "NOT_FOUND", "message": "Report scope not found", "details": None}},
    )


async def _get_visible_task_or_404(
    task_id: uuid.UUID,
    db: AsyncSession,
    ctx: AuthContext,
) -> EvaluationTaskModel:
    """Resolve task ownership before callers enforce the workspace role.

    Missing, soft-deleted, and cross-workspace tasks surface as the same
    identifier-free not-found, mirroring the run resolver in evaluation.py.
    """
    result = await db.execute(
        select(EvaluationTaskModel).where(
            EvaluationTaskModel.id == task_id,
            ~EvaluationTaskModel.deleted,
        )
    )
    task = result.scalar_one_or_none()
    if task is None or (not ctx.is_system_scope and task.workspace_id != ctx.workspace_id):
        raise _report_scope_not_found()
    return task
