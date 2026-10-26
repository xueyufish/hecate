"""Prompt management API endpoints.

Provides CRUD operations for prompts and version management:
- ``POST /api/prompts`` — Create a new prompt
- ``GET /api/prompts`` — List prompts (paginated)
- ``GET /api/prompts/{id}`` — Get prompt by ID
- ``PUT /api/prompts/{id}`` — Update prompt
- ``DELETE /api/prompts/{id}`` — Soft delete prompt
- ``GET /api/prompts/{id}/versions`` — List versions
- ``GET /api/prompts/{id}/versions/{version}`` — Get specific version
- ``POST /api/prompts/{id}/rollback/{version}`` — Rollback to version
- ``GET /api/prompts/by-label/{label}`` — Get prompt by deployment label
- ``GET /api/prompts/{id}/diff`` — Diff two versions
- ``GET /api/prompts/{id}/analytics`` — Per-version trace analytics
- ``GET /api/prompts/{id}/compare`` — Compare two versions' analytics
- ``POST /api/prompts/{id}/versions/{version}/summary`` — AI change summary

Every entry sits behind the prompt access boundary: an active workspace is
required for every identity — system scope included (403 ``NO_WORKSPACE``;
an explicit zero-UUID workspace stays a legal value). Create, list, and
by-label check the role floor (EDITOR to create, VIEWER to read) before
any business query. By-id entries resolve visibility first with a minimal
metadata probe (missing, foreign, soft-deleted, and workspace-inconsistent
prompts/versions share one 404 that leaks neither the reason nor any
content), then apply the role floor, and only then read templates, compute
diffs/statistics, invoke the summary model, or write. Writes carry the
caller's role so protected labels stay admin-only on every version path.
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
from hecate.models.prompt import PromptCreateSchema, PromptUpdateSchema
from hecate.studio.prompts.service import PromptService, PromptWriteConflictError

router = APIRouter()

_PROMPT_NOT_FOUND = "Prompt not found"
_VERSION_NOT_FOUND = "Prompt or version not found"


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


def _not_found(message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"error": {"code": "NOT_FOUND", "message": message, "details": None}},
    )


@router.post("/prompts", status_code=status.HTTP_201_CREATED)
async def create_prompt(
    data: PromptCreateSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Create a new prompt with initial version.

    Workspace requirement and the editor floor run before any validation;
    protected labels additionally require the admin role.
    """
    workspace_id = _workspace_id(ctx)
    _require_role(ctx, WorkspaceRole.EDITOR)
    service = PromptService(db)
    try:
        result = await service.create_prompt(data, workspace_id, user_role=ctx.role)
        return result.model_dump()
    except PermissionError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": {"code": "FORBIDDEN", "message": str(e), "details": None}},
        ) from e
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": {"code": "VALIDATION_ERROR", "message": str(e), "details": None}},
        ) from e


@router.get("/prompts")
async def list_prompts(
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    """List prompts of the caller's workspace with pagination."""
    workspace_id = _workspace_id(ctx)
    _require_role(ctx, WorkspaceRole.VIEWER)
    service = PromptService(db)
    result = await service.list_prompts(
        workspace_id=workspace_id,
        page=page,
        page_size=page_size,
    )
    return {
        "items": [item.model_dump() for item in result["items"]],
        "total": result["total"],
    }


@router.get("/prompts/{prompt_id}")
async def get_prompt(
    prompt_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Get a prompt by ID (visibility first, then role, then content)."""
    workspace_id = _workspace_id(ctx)
    service = PromptService(db)
    if not await service.is_prompt_visible(prompt_id, workspace_id, require_current_version=True):
        raise _not_found(_PROMPT_NOT_FOUND)
    _require_role(ctx, WorkspaceRole.VIEWER)
    try:
        result = await service.get_prompt(prompt_id, workspace_id)
        return result.model_dump()
    except LookupError as e:
        raise _not_found(_PROMPT_NOT_FOUND) from e


@router.put("/prompts/{prompt_id}")
async def update_prompt(
    prompt_id: uuid.UUID,
    data: PromptUpdateSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Update an existing prompt (visibility first, then editor floor)."""
    workspace_id = _workspace_id(ctx)
    service = PromptService(db)
    if not await service.is_prompt_visible(prompt_id, workspace_id, require_current_version=True):
        raise _not_found(_PROMPT_NOT_FOUND)
    _require_role(ctx, WorkspaceRole.EDITOR)
    try:
        result = await service.update_prompt(prompt_id, data, workspace_id, user_role=ctx.role)
        return result.model_dump()
    except PermissionError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": {"code": "FORBIDDEN", "message": str(e), "details": None}},
        ) from e
    except LookupError as e:
        raise _not_found(_PROMPT_NOT_FOUND) from e
    except PromptWriteConflictError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": {"code": "CONFLICT", "message": str(e), "details": None}},
        ) from e
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": {"code": "VALIDATION_ERROR", "message": str(e), "details": None}},
        ) from e


@router.delete("/prompts/{prompt_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_prompt(
    prompt_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> None:
    """Soft delete a prompt (visibility first, then editor floor)."""
    workspace_id = _workspace_id(ctx)
    service = PromptService(db)
    if not await service.is_prompt_visible(prompt_id, workspace_id):
        raise _not_found(_PROMPT_NOT_FOUND)
    _require_role(ctx, WorkspaceRole.EDITOR)
    try:
        await service.delete_prompt(prompt_id, workspace_id)
    except LookupError as e:
        raise _not_found(_PROMPT_NOT_FOUND) from e


@router.get("/prompts/{prompt_id}/versions")
async def list_prompt_versions(
    prompt_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> list[dict]:
    """List workspace-consistent versions of a prompt."""
    workspace_id = _workspace_id(ctx)
    service = PromptService(db)
    if not await service.is_prompt_visible(prompt_id, workspace_id):
        raise _not_found(_PROMPT_NOT_FOUND)
    _require_role(ctx, WorkspaceRole.VIEWER)
    try:
        versions = await service.list_versions(prompt_id, workspace_id)
    except LookupError as e:
        raise _not_found(_PROMPT_NOT_FOUND) from e
    return [v.model_dump() for v in versions]


@router.get("/prompts/{prompt_id}/versions/{version}")
async def get_prompt_version(
    prompt_id: uuid.UUID,
    version: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Get a specific version of a prompt (binding checked before role)."""
    workspace_id = _workspace_id(ctx)
    service = PromptService(db)
    if not await service.are_versions_visible(prompt_id, workspace_id, [version]):
        raise _not_found(_VERSION_NOT_FOUND)
    _require_role(ctx, WorkspaceRole.VIEWER)
    try:
        result = await service.get_version(prompt_id, version, workspace_id)
        return result.model_dump()
    except LookupError as e:
        raise _not_found(_VERSION_NOT_FOUND) from e


@router.post("/prompts/{prompt_id}/rollback/{version}")
async def rollback_prompt(
    prompt_id: uuid.UUID,
    version: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Rollback a prompt to a specific version (copy as new version)."""
    workspace_id = _workspace_id(ctx)
    service = PromptService(db)
    if not await service.are_versions_visible(prompt_id, workspace_id, [version]):
        raise _not_found(_VERSION_NOT_FOUND)
    _require_role(ctx, WorkspaceRole.EDITOR)
    try:
        result = await service.rollback_to_version(prompt_id, version, workspace_id, user_role=ctx.role)
        return result.model_dump()
    except PermissionError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": {"code": "FORBIDDEN", "message": str(e), "details": None}},
        ) from e
    except LookupError as e:
        raise _not_found(_VERSION_NOT_FOUND) from e
    except PromptWriteConflictError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": {"code": "CONFLICT", "message": str(e), "details": None}},
        ) from e


@router.get("/prompts/by-label/{label}")
async def get_prompt_by_label(
    label: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Get a prompt by deployment label within the caller's workspace."""
    workspace_id = _workspace_id(ctx)
    _require_role(ctx, WorkspaceRole.VIEWER)
    service = PromptService(db)
    result = await service.get_by_label(label, workspace_id)
    if result is None:
        raise _not_found(f"No prompt with label '{label}'")
    return result.model_dump()


@router.get("/prompts/{prompt_id}/diff")
async def diff_prompt_versions(
    prompt_id: uuid.UUID,
    from_version: int,
    to_version: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Compute a line-level diff between two prompt versions."""
    from hecate.studio.prompts.analytics import PromptAnalyticsService

    workspace_id = _workspace_id(ctx)
    service = PromptService(db)
    if not await service.are_versions_visible(prompt_id, workspace_id, [from_version, to_version]):
        raise _not_found(_VERSION_NOT_FOUND)
    _require_role(ctx, WorkspaceRole.VIEWER)
    svc = PromptAnalyticsService(db)
    try:
        return await svc.compute_diff(prompt_id, from_version, to_version, workspace_id)
    except LookupError as e:
        raise _not_found(_VERSION_NOT_FOUND) from e


@router.get("/prompts/{prompt_id}/analytics")
async def get_prompt_analytics(
    prompt_id: uuid.UUID,
    version: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    days: Annotated[int, Query(ge=1, le=365)] = 7,
) -> dict:
    """Get per-version performance analytics from trace data."""
    from hecate.studio.prompts.analytics import PromptAnalyticsService

    workspace_id = _workspace_id(ctx)
    service = PromptService(db)
    if not await service.are_versions_visible(prompt_id, workspace_id, [version]):
        raise _not_found(_VERSION_NOT_FOUND)
    _require_role(ctx, WorkspaceRole.VIEWER)
    svc = PromptAnalyticsService(db)
    try:
        return await svc.get_version_analytics(prompt_id, version, workspace_id, days=days)
    except LookupError as e:
        raise _not_found(_VERSION_NOT_FOUND) from e


@router.get("/prompts/{prompt_id}/compare")
async def compare_prompt_versions(
    prompt_id: uuid.UUID,
    from_version: int,
    to_version: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    days: Annotated[int, Query(ge=1, le=365)] = 7,
) -> dict:
    """Compare analytics for two prompt versions side by side."""
    from hecate.studio.prompts.analytics import PromptAnalyticsService

    workspace_id = _workspace_id(ctx)
    service = PromptService(db)
    if not await service.are_versions_visible(prompt_id, workspace_id, [from_version, to_version]):
        raise _not_found(_VERSION_NOT_FOUND)
    _require_role(ctx, WorkspaceRole.VIEWER)
    svc = PromptAnalyticsService(db)
    try:
        return await svc.compare_versions(prompt_id, from_version, to_version, workspace_id, days=days)
    except LookupError as e:
        raise _not_found(_VERSION_NOT_FOUND) from e


@router.post("/prompts/{prompt_id}/versions/{version}/summary")
async def generate_prompt_summary(
    prompt_id: uuid.UUID,
    version: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Generate an AI-assisted change summary for a prompt version.

    The summary invokes a model, so the floor is EDITOR, not VIEWER; the
    version (and, for diffs, its predecessor) must be visible before any
    shortcut or model call.
    """
    from hecate.studio.prompts.analytics import PromptAnalyticsService

    workspace_id = _workspace_id(ctx)
    needed = [version] + ([version - 1] if version > 1 else [])
    service = PromptService(db)
    if not await service.are_versions_visible(prompt_id, workspace_id, needed):
        raise _not_found(_VERSION_NOT_FOUND)
    _require_role(ctx, WorkspaceRole.EDITOR)
    svc = PromptAnalyticsService(db)
    try:
        return await svc.generate_change_summary(prompt_id, version, workspace_id)
    except LookupError as e:
        raise _not_found(_VERSION_NOT_FOUND) from e
