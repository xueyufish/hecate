"""Evaluation report API endpoints (7.2e Evaluation Report Dashboard).

Read-only aggregation over the existing evaluation tables:

- ``GET /api/evaluation/reports/overview`` — four-card overview
- ``GET /api/evaluation/reports/trends`` — pass-rate / score timeseries
- ``GET /api/evaluation/reports/distributions`` — per-metric histograms
- ``GET /api/evaluation/reports/breakdowns`` — online scores grouped by
  agent / task / session / source
- ``GET /api/evaluation/reports/sessions`` — session-level rollup

All aggregations are scoped to the caller's workspace and computed on
demand (see ``ops.evaluation.reports.service`` for the conventions).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.auth_context import AuthContext
from hecate.core.deps import get_db
from hecate.core.deps_workspace import get_auth_context
from hecate.ops.evaluation.reports.service import (
    EvaluationReportNotFoundError,
    EvaluationReportService,
    EvaluationReportValidationError,
)

router = APIRouter(prefix="/evaluation", tags=["evaluation"])

_TREND_DIMENSIONS = "^(dataset|workflow|agent)$"
_TREND_BUCKETS = "^(day|hour)$"
_BREAKDOWN_DIMENSIONS = "^(agent|task|session|source)$"


@router.get("/reports/overview")
async def overview_report(
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    start_date: datetime | None = None,
    end_date: datetime | None = None,
) -> dict:
    """Aggregate the four overview cards (quality/volume/coverage/error rate)."""
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
    svc = EvaluationReportService(db)
    report = await _run(svc.distributions(ctx.workspace_id, run_id=run_id, task_id=task_id, metric_name=metric_name))
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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": f"Report scope {exc} not found", "details": None}},
        ) from exc
