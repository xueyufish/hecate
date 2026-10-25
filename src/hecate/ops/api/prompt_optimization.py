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
off — structurally valid requests keep that behavior ahead of any
workspace or role decision) and workspace-scoped to the caller's auth
context.

Every entry sits behind the evaluation access boundary: an active
workspace is required for every identity — system scope included (403
``NO_WORKSPACE``; an explicit zero-UUID workspace stays a legal value).
Create and list check the role floor (EDITOR to create, VIEWER to read)
before any business query, config validation, active-run conflict
probe, or background dispatch. By-id entries resolve resource
visibility first with a minimal query (missing, foreign, and
soft-deleted runs/candidates — including candidates whose parent run no
longer matches the workspace — share one 404 that leaks neither the
reason nor any content), then apply the role floor, and only then read
evidence, validate state, or mutate. The review service re-verifies the
candidate→run→prompt bindings inside the write transaction, so a stale
API precheck cannot publish to another object.
"""

from __future__ import annotations

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
    """The caller's explicit workspace; never a silent zero-UUID default."""
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


def _require_feature() -> None:
    if not feature_enabled():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="prompt optimization is not available")


def _not_found(message: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=message)


@router.post("/prompt-optimization/runs", status_code=status.HTTP_202_ACCEPTED)
async def create_run(
    data: PromptOptimizationRunCreateSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> PromptOptimizationRunReadSchema:
    """Validate, persist, and start an optimization run in the background.

    Workspace requirement and the editor floor run before any business
    query, config validation, conflict probe, or dispatch.
    """
    _require_feature()
    workspace_id = _workspace_id(ctx)
    _require_role(ctx, WorkspaceRole.EDITOR)
    service = PromptOptimizationService(db)
    try:
        run = await service.create_run(data, workspace_id, created_by=ctx.user_id)
    except LookupError as e:
        raise _not_found(str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    except RunConflictError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    await db.commit()
    await PromptOptimizationRunner(db).run_in_background(run.id, workspace_id=workspace_id)
    return PromptOptimizationRunReadSchema.model_validate(run)


@router.get("/prompt-optimization/runs")
async def list_runs(
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    prompt_id: Annotated[uuid.UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict:
    _require_feature()
    workspace_id = _workspace_id(ctx)
    _require_role(ctx, WorkspaceRole.VIEWER)
    service = PromptOptimizationService(db)
    rows = await service.list_runs(workspace_id, limit=limit, prompt_id=prompt_id)
    items = [PromptOptimizationRunReadSchema.model_validate(row).model_dump(mode="json") for row in rows]
    return {"items": items, "total": len(items)}


@router.get("/prompt-optimization/runs/{run_id}")
async def get_run(
    run_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> PromptOptimizationRunReadSchema:
    _require_feature()
    workspace_id = _workspace_id(ctx)
    service = PromptOptimizationService(db)
    if not await service.is_run_visible(run_id, workspace_id):
        raise _not_found("run not found")
    _require_role(ctx, WorkspaceRole.VIEWER)
    run = await service.get_run(run_id, workspace_id)
    if run is None:
        raise _not_found("run not found")
    return PromptOptimizationRunReadSchema.model_validate(run)


@router.post("/prompt-optimization/runs/{run_id}/cancel")
async def cancel_run(
    run_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> PromptOptimizationRunReadSchema:
    _require_feature()
    workspace_id = _workspace_id(ctx)
    service = PromptOptimizationService(db)
    if not await service.is_run_visible(run_id, workspace_id):
        raise _not_found("run not found")
    _require_role(ctx, WorkspaceRole.EDITOR)
    cancelled = await service.cancel_run(run_id, workspace_id)
    if cancelled is None:
        raise _not_found("run not found")
    await db.commit()
    return PromptOptimizationRunReadSchema.model_validate(cancelled)


@router.get("/prompt-optimization/runs/{run_id}/candidates")
async def list_candidates(
    run_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    _require_feature()
    workspace_id = _workspace_id(ctx)
    if not await PromptOptimizationService(db).is_run_visible(run_id, workspace_id):
        raise _not_found("run not found")
    _require_role(ctx, WorkspaceRole.VIEWER)
    try:
        rows = await CandidateReviewService(db).list_candidates(run_id, workspace_id)
    except LookupError as e:
        raise _not_found("run not found") from e
    items = [PromptOptimizationCandidateReadSchema.model_validate(row).model_dump(mode="json") for row in rows]
    return {"items": items, "total": len(items)}


@router.get("/prompt-optimization/candidates/{candidate_id}")
async def get_candidate(
    candidate_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> PromptOptimizationCandidateReadSchema:
    _require_feature()
    workspace_id = _workspace_id(ctx)
    service = CandidateReviewService(db)
    if await service.get_candidate_binding(candidate_id, workspace_id) is None:
        raise _not_found("candidate not found")
    _require_role(ctx, WorkspaceRole.VIEWER)
    candidate = await service.get_candidate(candidate_id, workspace_id)
    if candidate is None:
        raise _not_found("candidate not found")
    return PromptOptimizationCandidateReadSchema.model_validate(candidate)


@router.post("/prompt-optimization/candidates/{candidate_id}/approve")
async def approve_candidate(
    candidate_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Publish a pending candidate as a new unlabeled prompt version.

    Approval never grants protected-label management: the created version
    carries ``labels=[]`` and provenance metadata only. The service
    re-verifies the candidate→run→prompt bindings inside the write
    transaction; an invisible or inconsistent chain 404s before any
    mutation.
    """
    _require_feature()
    workspace_id = _workspace_id(ctx)
    service = CandidateReviewService(db)
    binding = await service.get_candidate_binding(candidate_id, workspace_id)
    if binding is None:
        raise _not_found("candidate not found")
    _require_role(ctx, WorkspaceRole.EDITOR)
    try:
        candidate, version = await service.approve(
            candidate_id, workspace_id, decided_by=ctx.user_id, expected_binding=binding
        )
    except LookupError as e:
        raise _not_found("candidate not found") from e
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
    workspace_id = _workspace_id(ctx)
    service = CandidateReviewService(db)
    binding = await service.get_candidate_binding(candidate_id, workspace_id)
    if binding is None:
        raise _not_found("candidate not found")
    _require_role(ctx, WorkspaceRole.EDITOR)
    try:
        candidate = await service.reject(
            candidate_id, workspace_id, data.reason, decided_by=ctx.user_id, expected_binding=binding
        )
    except LookupError as e:
        raise _not_found("candidate not found") from e
    except CandidateReviewError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    await db.commit()
    return PromptOptimizationCandidateReadSchema.model_validate(candidate)
