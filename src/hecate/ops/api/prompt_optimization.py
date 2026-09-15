"""Prompt self-optimization API (6.19).

- ``POST   /api/prompt-optimization/runs``                     — create + start a run (202)
- ``GET    /api/prompt-optimization/runs``                     — list runs
- ``GET    /api/prompt-optimization/runs/{run_id}``            — run detail
- ``POST   /api/prompt-optimization/runs/{run_id}/cancel``     — request cancellation
- ``GET    /api/prompt-optimization/runs/{run_id}/candidates`` — candidates of a run
- ``GET    /api/prompt-optimization/candidates/{candidate_id}``      — candidate evidence
- ``POST   /api/prompt-optimization/candidates/{candidate_id}/approve`` — publish as new version
- ``POST   /api/prompt-optimization/candidates/{candidate_id}/reject``  — reject with reason

All endpoints are gated behind ``PROMPT_OPTIMIZATION_ENABLED`` (404 when
off) and workspace-scoped to the caller's auth context.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.auth_context import AuthContext
from hecate.core.deps import get_db
from hecate.core.deps_workspace import get_auth_context
from hecate.models.prompt_optimization import (
    PromptOptimizationCandidateReadSchema,
    PromptOptimizationRejectSchema,
    PromptOptimizationRunCreateSchema,
    PromptOptimizationRunReadSchema,
)
from hecate.ops.prompt_optimization.review import CandidateReviewError, CandidateReviewService
from hecate.ops.prompt_optimization.runner import PromptOptimizationRunner
from hecate.ops.prompt_optimization.service import (
    PromptOptimizationService,
    RunConflictError,
    feature_enabled,
)

router = APIRouter()


def _workspace_id(ctx: AuthContext) -> uuid.UUID:
    return ctx.workspace_id or uuid.UUID(int=0)


def _require_feature() -> None:
    if not feature_enabled():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="prompt optimization is not available")


@router.post("/prompt-optimization/runs", status_code=status.HTTP_202_ACCEPTED)
async def create_run(
    data: PromptOptimizationRunCreateSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> PromptOptimizationRunReadSchema:
    """Validate, persist, and start an optimization run in the background."""
    _require_feature()
    service = PromptOptimizationService(db)
    try:
        run = await service.create_run(data, _workspace_id(ctx), created_by=ctx.user_id)
    except LookupError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    except RunConflictError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    await db.commit()
    await PromptOptimizationRunner(db).run_in_background(run.id)
    return PromptOptimizationRunReadSchema.model_validate(run)


@router.get("/prompt-optimization/runs")
async def list_runs(
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    prompt_id: Annotated[uuid.UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict:
    _require_feature()
    service = PromptOptimizationService(db)
    rows = await service.list_runs(_workspace_id(ctx), limit=limit, prompt_id=prompt_id)
    items = [PromptOptimizationRunReadSchema.model_validate(row).model_dump(mode="json") for row in rows]
    return {"items": items, "total": len(items)}


@router.get("/prompt-optimization/runs/{run_id}")
async def get_run(
    run_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> PromptOptimizationRunReadSchema:
    _require_feature()
    run = await PromptOptimizationService(db).get_run(run_id, _workspace_id(ctx))
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")
    return PromptOptimizationRunReadSchema.model_validate(run)


@router.post("/prompt-optimization/runs/{run_id}/cancel")
async def cancel_run(
    run_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> PromptOptimizationRunReadSchema:
    _require_feature()
    run = await PromptOptimizationService(db).cancel_run(run_id, _workspace_id(ctx))
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")
    await db.commit()
    return PromptOptimizationRunReadSchema.model_validate(run)


@router.get("/prompt-optimization/runs/{run_id}/candidates")
async def list_candidates(
    run_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    _require_feature()
    service = PromptOptimizationService(db)
    if await service.get_run(run_id, _workspace_id(ctx)) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")
    rows = await CandidateReviewService(db).list_candidates(run_id, _workspace_id(ctx))
    items = [PromptOptimizationCandidateReadSchema.model_validate(row).model_dump(mode="json") for row in rows]
    return {"items": items, "total": len(items)}


@router.get("/prompt-optimization/candidates/{candidate_id}")
async def get_candidate(
    candidate_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> PromptOptimizationCandidateReadSchema:
    _require_feature()
    candidate = await CandidateReviewService(db).get_candidate(candidate_id, _workspace_id(ctx))
    if candidate is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="candidate not found")
    return PromptOptimizationCandidateReadSchema.model_validate(candidate)


@router.post("/prompt-optimization/candidates/{candidate_id}/approve")
async def approve_candidate(
    candidate_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    _require_feature()
    service = CandidateReviewService(db)
    try:
        candidate, version = await service.approve(candidate_id, _workspace_id(ctx), decided_by=ctx.user_id)
    except LookupError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except CandidateReviewError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    await db.commit()
    return {
        "candidate": PromptOptimizationCandidateReadSchema.model_validate(candidate).model_dump(mode="json"),
        "published_prompt_version": version.version,
    }


@router.post("/prompt-optimization/candidates/{candidate_id}/reject")
async def reject_candidate(
    candidate_id: uuid.UUID,
    data: PromptOptimizationRejectSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> PromptOptimizationCandidateReadSchema:
    _require_feature()
    service = CandidateReviewService(db)
    try:
        candidate = await service.reject(candidate_id, _workspace_id(ctx), data.reason, decided_by=ctx.user_id)
    except LookupError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except CandidateReviewError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    await db.commit()
    return PromptOptimizationCandidateReadSchema.model_validate(candidate)
