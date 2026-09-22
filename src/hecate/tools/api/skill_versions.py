"""Skill version API endpoints (5.9d).

Mirrors the agent-versioning endpoint surface on top of skills:

- ``POST /api/skills/{id}/versions`` — commit a new version snapshot
- ``GET .../versions`` — list versions for a skill
- ``GET .../versions/{version}`` — fetch one version (optionally with
  the full snapshot via ``?include_snapshot=true``)
- ``PATCH .../versions/{version}`` — rename / edit release notes
  (metadata only — snapshot content stays immutable)
- ``POST .../versions/{version}/rollback`` — restore the version's
  content as a *new* version and write it back to the live row
- ``DELETE .../versions/{version}`` — soft-delete (refuses a pin)
- ``GET .../versions/{v1}/diff/{v2}`` — diff two versions over the
  frozen field set

Plugin-sourced skills (provider unset) reject commit attempts at the
service layer (409); deletion constraints reuse the agent snapshot's
``(skill_id, version)`` pin check.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.auth_context import AuthContext
from hecate.core.deps import get_db
from hecate.core.deps_workspace import get_auth_context
from hecate.models.skill import SkillModel
from hecate.models.skill_version import (
    SkillVersionCommitSchema,
    SkillVersionDetailSchema,
    SkillVersionDiffSchema,
    SkillVersionReadSchema,
    SkillVersionUpdateSchema,
)
from hecate.tools.skill.versioning import (
    SkillNotVersionableError,
    SkillRollbackConflictError,
    SkillVersionNotFoundError,
    SkillVersionPinnedError,
    SkillVersionService,
)

router = APIRouter()


def _err(code: str, message: str, status_code: int) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"error": {"code": code, "message": message, "details": None}},
    )


async def _load_owned_skill(db: AsyncSession, skill_id: uuid.UUID, workspace_id: uuid.UUID) -> SkillModel:
    """Load a skill honouring workspace ownership (bundled/skills live in zero UUID)."""
    result = await db.execute(select(SkillModel).where(SkillModel.id == skill_id, ~SkillModel.deleted))
    skill = result.scalar_one_or_none()
    if skill is None:
        raise _err("NOT_FOUND", "Skill not found", status.HTTP_404_NOT_FOUND)
    if skill.workspace_id != workspace_id and skill.workspace_id != uuid.UUID(int=0):
        raise _err(
            "FORBIDDEN",
            "Skill belongs to a different workspace",
            status.HTTP_403_FORBIDDEN,
        )
    return skill


@router.post(
    "/skills/{skill_id}/versions",
    status_code=status.HTTP_201_CREATED,
    response_model=SkillVersionDetailSchema,
)
async def commit_skill_version(
    skill_id: uuid.UUID,
    payload: SkillVersionCommitSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict[str, Any]:
    workspace_id = ctx.workspace_id or uuid.UUID(int=0)
    await _load_owned_skill(db, skill_id, workspace_id)
    service = SkillVersionService(db)
    try:
        return await service.commit(
            skill_id,
            name=payload.name,
            change_summary=payload.change_summary,
            actor_user_id=ctx.user_id,
        )
    except SkillVersionNotFoundError as e:
        raise _err("NOT_FOUND", str(e), status.HTTP_404_NOT_FOUND) from e
    except SkillNotVersionableError as e:
        raise _err("NOT_VERSIONABLE", str(e), status.HTTP_409_CONFLICT) from e


@router.get(
    "/skills/{skill_id}/versions",
    response_model=list[SkillVersionReadSchema],
)
async def list_skill_versions(
    skill_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> list[dict[str, Any]]:
    workspace_id = ctx.workspace_id or uuid.UUID(int=0)
    await _load_owned_skill(db, skill_id, workspace_id)
    service = SkillVersionService(db)
    try:
        return await service.list_versions(skill_id)
    except SkillVersionNotFoundError as e:
        raise _err("NOT_FOUND", str(e), status.HTTP_404_NOT_FOUND) from e


@router.get(
    "/skills/{skill_id}/versions/{version}",
    response_model=SkillVersionDetailSchema,
)
async def get_skill_version(
    skill_id: uuid.UUID,
    version: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    include_snapshot: Annotated[bool, Query()] = False,
) -> dict[str, Any]:
    workspace_id = ctx.workspace_id or uuid.UUID(int=0)
    await _load_owned_skill(db, skill_id, workspace_id)
    service = SkillVersionService(db)
    try:
        return await service.get_version(skill_id, version, include_snapshot=include_snapshot)
    except SkillVersionNotFoundError as e:
        raise _err("NOT_FOUND", str(e), status.HTTP_404_NOT_FOUND) from e


@router.patch(
    "/skills/{skill_id}/versions/{version}",
    response_model=SkillVersionDetailSchema,
)
async def update_skill_version(
    skill_id: uuid.UUID,
    version: int,
    payload: SkillVersionUpdateSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict[str, Any]:
    workspace_id = ctx.workspace_id or uuid.UUID(int=0)
    await _load_owned_skill(db, skill_id, workspace_id)
    service = SkillVersionService(db)
    try:
        return await service.update_version(
            skill_id,
            version,
            name=payload.name,
            change_summary=payload.change_summary,
        )
    except SkillVersionNotFoundError as e:
        raise _err("NOT_FOUND", str(e), status.HTTP_404_NOT_FOUND) from e


@router.post(
    "/skills/{skill_id}/versions/{version}/rollback",
    response_model=SkillVersionDetailSchema,
)
async def rollback_skill_version(
    skill_id: uuid.UUID,
    version: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict[str, Any]:
    workspace_id = ctx.workspace_id or uuid.UUID(int=0)
    await _load_owned_skill(db, skill_id, workspace_id)
    service = SkillVersionService(db)
    try:
        return await service.rollback_to_version(skill_id, version, actor_user_id=ctx.user_id)
    except SkillVersionNotFoundError as e:
        raise _err("NOT_FOUND", str(e), status.HTTP_404_NOT_FOUND) from e
    except SkillRollbackConflictError as e:
        raise _err("ROLLBACK_CONFLICT", str(e), status.HTTP_409_CONFLICT) from e


@router.delete(
    "/skills/{skill_id}/versions/{version}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_skill_version(
    skill_id: uuid.UUID,
    version: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> None:
    workspace_id = ctx.workspace_id or uuid.UUID(int=0)
    await _load_owned_skill(db, skill_id, workspace_id)
    service = SkillVersionService(db)
    try:
        await service.delete_version(skill_id, version)
    except SkillVersionNotFoundError as e:
        raise _err("NOT_FOUND", str(e), status.HTTP_404_NOT_FOUND) from e
    except SkillVersionPinnedError as e:
        raise _err("VERSION_PINNED", str(e), status.HTTP_409_CONFLICT) from e


@router.get(
    "/skills/{skill_id}/versions/{v1}/diff/{v2}",
    response_model=SkillVersionDiffSchema,
)
async def diff_skill_versions(
    skill_id: uuid.UUID,
    v1: int,
    v2: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict[str, Any]:
    workspace_id = ctx.workspace_id or uuid.UUID(int=0)
    await _load_owned_skill(db, skill_id, workspace_id)
    service = SkillVersionService(db)
    try:
        return await service.diff_versions(skill_id, v1, v2)
    except SkillVersionNotFoundError as e:
        raise _err("NOT_FOUND", str(e), status.HTTP_404_NOT_FOUND) from e
