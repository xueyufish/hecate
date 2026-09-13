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
from hecate.core.deps_workspace import get_auth_context
from hecate.models.evaluation import (
    EvaluationDatasetVersionCreateSchema,
    EvaluationDatasetVersionReadSchema,
)
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
    try:
        version = await svc.create_version(
            dataset_id,
            name=data.name,
            description=data.description,
            workspace_id=ctx.workspace_id,
            created_by=ctx.user_id,
        )
    except DatasetNotFoundError as exc:
        raise _not_found(str(exc)) from exc
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
    """List the dataset's named versions, newest first (items excluded)."""
    svc = EvaluationDatasetVersionService(db)
    versions, total = await svc.list_versions(dataset_id, workspace_id=ctx.workspace_id, page=page, page_size=page_size)
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
    version = await _get_version_or_404(svc, dataset_id, version_id, ctx.workspace_id)
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
    await _get_version_or_404(svc, dataset_id, version_id, ctx.workspace_id)
    await svc.delete_version(version_id, workspace_id=ctx.workspace_id)


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
    await _get_version_or_404(svc, dataset_id, version_id, ctx.workspace_id)
    try:
        return await svc.checkout(dataset_id, version_id, workspace_id=ctx.workspace_id)
    except DatasetNotFoundError as exc:
        raise _not_found(str(exc)) from exc


@router.get("/datasets/{dataset_id}/versions/{version_id}/diff")
async def diff_dataset_version(
    dataset_id: uuid.UUID,
    version_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    against: Annotated[str, Query(description="Other version id, or 'live'")] = "live",
) -> dict:
    """Diff one version against another version or the live dataset."""
    svc = EvaluationDatasetVersionService(db)
    await _get_version_or_404(svc, dataset_id, version_id, ctx.workspace_id)
    against_id: uuid.UUID | None
    if against == "live":
        against_id = None
    else:
        try:
            against_id = uuid.UUID(against)
        except ValueError as exc:
            raise _validation_error("against must be a version id or 'live'") from exc
    try:
        return await svc.diff(dataset_id, version_id, against_id, workspace_id=ctx.workspace_id)
    except DatasetNotFoundError as exc:
        raise _not_found(str(exc)) from exc


# ---------------------------------------------------------------------------
# Response shaping + error mapping
# ---------------------------------------------------------------------------


async def _get_version_or_404(
    svc: EvaluationDatasetVersionService,
    dataset_id: uuid.UUID,
    version_id: uuid.UUID,
    workspace_id: uuid.UUID | None,
):
    try:
        version = await svc.get_version(version_id, workspace_id=workspace_id)
    except DatasetVersionNotFoundError as exc:
        raise _not_found(str(exc)) from exc
    if version.dataset_id != dataset_id:
        raise _not_found(f"Dataset version {version_id} not found")
    return version


def _version_to_dict(version, include_items: bool = True) -> dict:
    data = EvaluationDatasetVersionReadSchema.model_validate(version).model_dump(mode="json")
    if not include_items:
        data.pop("items", None)
    return data


def _not_found(message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"error": {"code": "NOT_FOUND", "message": message, "details": None}},
    )


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
