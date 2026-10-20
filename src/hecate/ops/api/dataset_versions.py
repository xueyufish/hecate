"""Named dataset version API endpoints (7.3b).

- ``POST /api/evaluation/datasets/{dataset_id}/versions`` — freeze live items
- ``GET  /api/evaluation/datasets/{dataset_id}/versions`` — list versions
- ``GET  /api/evaluation/datasets/{dataset_id}/versions/{version_id}`` — read one
- ``DELETE /api/evaluation/datasets/{dataset_id}/versions/{version_id}`` — soft delete
- ``POST /api/evaluation/datasets/{dataset_id}/versions/{version_id}/checkout`` —
  restore the version's items as the live dataset
- ``GET  /api/evaluation/datasets/{dataset_id}/versions/{version_id}/diff?against=``
  — diff against another version or the live dataset (``against=live``)

This router only maps errors and shapes responses; freezing, checkout
semantics, and diff alignment live in the dataset-version service.
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
from hecate.models.evaluation import (
    EvaluationDatasetVersionCreateSchema,
    EvaluationDatasetVersionReadSchema,
)
from hecate.ops.api.evaluation import _dataset_not_found, _forbidden, _get_dataset_for_read
from hecate.ops.evaluation.dataset_version_service import (
    DatasetNotFoundError,
    DatasetVersionNameConflictError,
    DatasetVersionNotFoundError,
    EvaluationDatasetVersionService,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/evaluation", tags=["evaluation"])


@router.post("/datasets/{dataset_id}/versions", status_code=status.HTTP_201_CREATED)
async def create_dataset_version(
    dataset_id: uuid.UUID,
    data: EvaluationDatasetVersionCreateSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Freeze the dataset's current live items as a named version."""
    svc = EvaluationDatasetVersionService(db)
    ds = await _authorize_dataset_write(ctx, dataset_id, db)
    try:
        version = await svc.create_version(
            dataset_id,
            name=data.name,
            description=data.description,
            workspace_id=ds.workspace_id,
            created_by=ctx.user_id,
        )
    except DatasetNotFoundError as exc:
        raise _dataset_not_found() from exc
    except DatasetVersionNameConflictError as exc:
        raise _conflict(str(exc)) from exc
    return _version_to_dict(version)


@router.get("/datasets/{dataset_id}/versions")
async def list_dataset_versions(
    dataset_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
) -> dict:
    """List the dataset's named versions, newest first (items excluded).

    Scoped behind the dataset read boundary: an invisible parent dataset
    404s before any role decision, and only explicit system scope reads
    across workspaces.
    """
    await _get_dataset_for_read(ctx, dataset_id, db)
    try:
        ensure_workspace_role(ctx, WorkspaceRole.VIEWER)
    except WorkspaceRoleRequiredError as e:
        raise _forbidden(e) from e
    svc = EvaluationDatasetVersionService(db)
    workspace_filter = None if ctx.is_system_scope else ctx.workspace_id
    versions, total = await svc.list_versions(dataset_id, workspace_id=workspace_filter, page=page, page_size=page_size)
    return {
        "items": [_version_to_dict(v, include_items=False) for v in versions],
        "total": total,
    }


@router.get("/datasets/{dataset_id}/versions/{version_id}")
async def get_dataset_version(
    dataset_id: uuid.UUID,
    version_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Read one named version including its frozen items."""
    svc = EvaluationDatasetVersionService(db)
    version = await _get_version_for_read(svc, ctx, dataset_id, version_id, db)
    try:
        ensure_workspace_role(ctx, WorkspaceRole.VIEWER)
    except WorkspaceRoleRequiredError as e:
        raise _forbidden(e) from e
    return _version_to_dict(version)


@router.delete("/datasets/{dataset_id}/versions/{version_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_dataset_version(
    dataset_id: uuid.UUID,
    version_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> None:
    """Soft-delete a named version; its name stays reserved."""
    svc = EvaluationDatasetVersionService(db)
    ds, _ = await _get_version_for_write(svc, ctx, dataset_id, version_id, db)
    try:
        await svc.delete_version(version_id, workspace_id=ds.workspace_id)
    except DatasetVersionNotFoundError as exc:
        raise _version_not_found() from exc


@router.post("/datasets/{dataset_id}/versions/{version_id}/checkout")
async def checkout_dataset_version(
    dataset_id: uuid.UUID,
    version_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Restore the version's items as the live dataset (destructive).

    The response summarizes the transition; freeze the current live state
    first if it must be kept.
    """
    svc = EvaluationDatasetVersionService(db)
    ds, _ = await _get_version_for_write(svc, ctx, dataset_id, version_id, db)
    try:
        return await svc.checkout(dataset_id, version_id, workspace_id=ds.workspace_id)
    except DatasetNotFoundError as exc:
        raise _dataset_not_found() from exc
    except DatasetVersionNotFoundError as exc:
        raise _version_not_found() from exc


@router.get("/datasets/{dataset_id}/versions/{version_id}/diff")
async def diff_dataset_version(
    dataset_id: uuid.UUID,
    version_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    against: Annotated[str, Query(description="Other version id, or 'live'")] = "live",
) -> dict:
    """Diff one version against another version or the live dataset.

    Both sides resolve behind the dataset read boundary: an invisible
    source or target version is the same constant 404, never a 500, and
    not-found responses never echo resource identifiers.
    """
    svc = EvaluationDatasetVersionService(db)
    await _get_version_for_read(svc, ctx, dataset_id, version_id, db)
    against_id: uuid.UUID | None
    if against == "live":
        against_id = None
    else:
        try:
            against_id = uuid.UUID(against)
        except ValueError as exc:
            raise _validation_error("against must be a version id or 'live'") from exc
        await _get_version_for_read(svc, ctx, dataset_id, against_id, db)
    try:
        ensure_workspace_role(ctx, WorkspaceRole.VIEWER)
    except WorkspaceRoleRequiredError as e:
        raise _forbidden(e) from e
    workspace_filter = None if ctx.is_system_scope else ctx.workspace_id
    try:
        return await svc.diff(dataset_id, version_id, against_id, workspace_id=workspace_filter)
    except DatasetNotFoundError as exc:
        raise _dataset_not_found() from exc
    except DatasetVersionNotFoundError as exc:
        raise _version_not_found() from exc


# ---------------------------------------------------------------------------
# Response shaping + error mapping
# ---------------------------------------------------------------------------


async def _authorize_dataset_write(
    ctx: AuthContext,
    dataset_id: uuid.UUID,
    db: AsyncSession,
):
    """Resolve the parent dataset through the read boundary, then require
    editor. Ownership is decided before role so a cross-workspace caller
    receives the constant not-found, and the returned dataset carries the
    real workspace attribution for the write (never a zero-UUID fallback).
    """
    ds = await _get_dataset_for_read(ctx, dataset_id, db)
    try:
        ensure_workspace_role(ctx, WorkspaceRole.EDITOR)
    except WorkspaceRoleRequiredError as e:
        raise _forbidden(e) from e
    return ds


async def _get_version_for_write(
    svc: EvaluationDatasetVersionService,
    ctx: AuthContext,
    dataset_id: uuid.UUID,
    version_id: uuid.UUID,
    db: AsyncSession,
):
    """Resolve parent and version ownership before requiring the write role.

    Even system writes bind the version to the parent's actual workspace;
    inconsistent legacy attribution must fail before any mutation.
    """
    ds = await _get_dataset_for_read(ctx, dataset_id, db)
    try:
        version = await svc.get_version(version_id, workspace_id=ds.workspace_id)
    except DatasetVersionNotFoundError as exc:
        raise _version_not_found() from exc
    if version.dataset_id != dataset_id:
        raise _version_not_found()
    try:
        ensure_workspace_role(ctx, WorkspaceRole.EDITOR)
    except WorkspaceRoleRequiredError as e:
        raise _forbidden(e) from e
    return ds, version


def _version_not_found() -> HTTPException:
    """Return an identifier-free 404 for every invisible or mismatched version."""
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"error": {"code": "NOT_FOUND", "message": "Dataset version not found", "details": None}},
    )


async def _get_version_for_read(
    svc: EvaluationDatasetVersionService,
    ctx: AuthContext,
    dataset_id: uuid.UUID,
    version_id: uuid.UUID,
    db: AsyncSession,
):
    """Resolve the parent dataset and version binding before role checks.

    The parent dataset gate mirrors the dataset read boundary; version
    level failures (missing, soft-deleted, cross-workspace, or bound to
    another dataset) share one constant 404 that never echoes IDs. Role
    checks stay in callers so ownership is decided first.
    """
    await _get_dataset_for_read(ctx, dataset_id, db)
    workspace_filter = None if ctx.is_system_scope else ctx.workspace_id
    try:
        version = await svc.get_version(version_id, workspace_id=workspace_filter)
    except DatasetVersionNotFoundError as exc:
        raise _version_not_found() from exc
    if version.dataset_id != dataset_id:
        raise _version_not_found()
    return version


def _version_to_dict(version, include_items: bool = True) -> dict:
    data = EvaluationDatasetVersionReadSchema.model_validate(version).model_dump(mode="json")
    if not include_items:
        data.pop("items", None)
    return data


def _conflict(message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"error": {"code": "NAME_CONFLICT", "message": message, "details": None}},
    )


def _validation_error(message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={"error": {"code": "INVALID_REQUEST", "message": message, "details": None}},
    )
