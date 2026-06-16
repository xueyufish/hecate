"""Skill management API endpoints.

Provides CRUD operations for skills:
- ``POST /api/skills`` — Create a new skill
- ``GET /api/skills`` — List skills (paginated, workspace-scoped)
- ``GET /api/skills/{id}`` — Get skill by ID
- ``PUT /api/skills/{id}`` — Update skill
- ``DELETE /api/skills/{id}`` — Soft delete skill
- ``POST /api/skills/import`` — Import SKILL.md file
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.auth_context import AuthContext
from hecate.core.deps import get_db
from hecate.core.deps_workspace import get_auth_context
from hecate.models.skill import SkillCreateSchema, SkillModel, SkillReadSchema, SkillUpdateSchema

router = APIRouter()


async def _get_skill_with_ownership_check(
    db: AsyncSession,
    skill_id: uuid.UUID,
    workspace_id: uuid.UUID,
) -> SkillModel:
    """Load a skill and verify workspace ownership.

    Args:
        db: The async database session.
        skill_id: UUID of the skill to load.
        workspace_id: UUID of the requesting workspace.

    Returns:
        The SkillModel if found and owned by workspace.

    Raises:
        HTTPException: 404 if not found, 403 if not owned by workspace.
    """
    result = await db.execute(
        select(SkillModel).where(
            SkillModel.id == skill_id,
            ~SkillModel.deleted,
        )
    )
    skill = result.scalar_one_or_none()
    if skill is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": "Skill not found", "details": None}},
        )

    if skill.workspace_id != workspace_id and skill.workspace_id != uuid.UUID(int=0):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": {
                    "code": "FORBIDDEN",
                    "message": "Skill belongs to a different workspace",
                    "details": None,
                }
            },
        )

    return skill


@router.post("/skills", status_code=status.HTTP_201_CREATED)
async def create_skill(
    data: SkillCreateSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Create a new skill.

    Args:
        data: The skill creation data.
        db: The async database session.
        ctx: The authenticated context.

    Returns:
        dict: The created skill data.

    Raises:
        HTTPException: 409 if skill name already exists in workspace.
    """
    workspace_id = ctx.workspace_id or uuid.UUID(int=0)

    # Check for duplicate name in workspace
    existing = await db.execute(
        select(SkillModel).where(
            SkillModel.name == data.name,
            SkillModel.workspace_id == workspace_id,
            ~SkillModel.deleted,
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": {
                    "code": "DUPLICATE_NAME",
                    "message": f"Skill '{data.name}' already exists in this workspace",
                    "details": None,
                }
            },
        )

    skill = SkillModel(
        workspace_id=workspace_id,
        name=data.name,
        description=data.description,
        source=data.source,
        instructions=data.instructions,
        allowed_tools=data.allowed_tools,
        metadata_=data.metadata,
        scripts=data.scripts,
        references=data.references,
        max_tokens=data.max_tokens,
        auto_load=data.auto_load,
    )
    db.add(skill)
    await db.flush()
    await db.refresh(skill)
    return SkillReadSchema.model_validate(skill).model_dump()


@router.get("/skills")
async def list_skills(
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    source: str | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    """List skills with optional source filter and pagination.

    Returns skills from the current workspace plus system skills
    (workspace_id = zero UUID).

    Args:
        db: The async database session.
        ctx: The authenticated context.
        source: Optional filter by skill source (system, user, project).
        page: Page number (1-indexed).
        page_size: Number of items per page.

    Returns:
        dict: ``{"items": [...], "total": int}`` with skill list and total count.
    """
    workspace_id = ctx.workspace_id or uuid.UUID(int=0)

    # Include workspace skills + system skills
    base_query = select(SkillModel).where(
        ~SkillModel.deleted,
        or_(
            SkillModel.workspace_id == workspace_id,
            SkillModel.workspace_id == uuid.UUID(int=0),
        ),
    )
    if source is not None:
        base_query = base_query.where(SkillModel.source == source)

    count_stmt = select(func.count()).select_from(base_query.subquery())
    total = (await db.execute(count_stmt)).scalar_one()

    offset = (page - 1) * page_size
    stmt = base_query.order_by(SkillModel.name).offset(offset).limit(page_size)
    result = await db.execute(stmt)
    skills = result.scalars().all()

    return {
        "items": [SkillReadSchema.model_validate(s).model_dump() for s in skills],
        "total": total,
    }


@router.get("/skills/{skill_id}")
async def get_skill(
    skill_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Get a skill by ID.

    Args:
        skill_id: The UUID of the skill to retrieve.
        db: The async database session.
        ctx: The authenticated context.

    Returns:
        dict: The skill data.

    Raises:
        HTTPException: 404 if skill not found or deleted.
    """
    result = await db.execute(
        select(SkillModel).where(
            SkillModel.id == skill_id,
            ~SkillModel.deleted,
        )
    )
    skill = result.scalar_one_or_none()
    if skill is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": "Skill not found", "details": None}},
        )
    return SkillReadSchema.model_validate(skill).model_dump()


@router.put("/skills/{skill_id}")
async def update_skill(
    skill_id: uuid.UUID,
    data: SkillUpdateSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Update an existing skill.

    Args:
        skill_id: The UUID of the skill to update.
        data: The update data (all fields optional).
        db: The async database session.
        ctx: The authenticated context.

    Returns:
        dict: The updated skill data.

    Raises:
        HTTPException: 404 if skill not found, 403 if not owned by workspace.
    """
    workspace_id = ctx.workspace_id or uuid.UUID(int=0)
    skill = await _get_skill_with_ownership_check(db, skill_id, workspace_id)

    if skill.source == "system":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": {
                    "code": "FORBIDDEN",
                    "message": "Cannot modify system skills",
                    "details": None,
                }
            },
        )

    update_data = data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        if field == "metadata":
            skill.metadata_ = value
        else:
            setattr(skill, field, value)

    await db.flush()
    await db.refresh(skill)
    return SkillReadSchema.model_validate(skill).model_dump()


@router.delete("/skills/{skill_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_skill(
    skill_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> None:
    """Soft delete a skill.

    Args:
        skill_id: The UUID of the skill to delete.
        db: The async database session.
        ctx: The authenticated context.

    Raises:
        HTTPException: 404 if skill not found, 403 if not owned by workspace.
    """
    workspace_id = ctx.workspace_id or uuid.UUID(int=0)
    skill = await _get_skill_with_ownership_check(db, skill_id, workspace_id)

    if skill.source == "system":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": {
                    "code": "FORBIDDEN",
                    "message": "Cannot delete system skills",
                    "details": None,
                }
            },
        )

    skill.deleted = True
    skill.deleted_at = datetime.now(UTC)
    await db.flush()


@router.post("/skills/import", status_code=status.HTTP_201_CREATED)
async def import_skill(
    file: Annotated[UploadFile, File(...)],
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Import a skill from a SKILL.md file.

    Accepts a multipart file upload containing a SKILL.md file with
    YAML frontmatter and Markdown body.

    Args:
        file: The uploaded SKILL.md file.
        db: The async database session.
        ctx: The authenticated context.

    Returns:
        dict: The created skill data.

    Raises:
        HTTPException: 422 for invalid format, 413 for file too large.
    """
    from hecate.services.skill.parser import parse_skill_md

    # File size check (100KB limit)
    max_file_size = 100 * 1024

    content_bytes = await file.read()
    if len(content_bytes) > max_file_size:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={
                "error": {
                    "code": "FILE_TOO_LARGE",
                    "message": f"File exceeds {max_file_size // 1024}KB limit",
                    "details": None,
                }
            },
        )

    # Decode content
    try:
        content = content_bytes.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": {
                    "code": "INVALID_ENCODING",
                    "message": "File must be UTF-8 encoded",
                    "details": None,
                }
            },
        ) from None

    # Parse SKILL.md
    try:
        parsed = parse_skill_md(content)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": {
                    "code": "INVALID_SKILL_MD",
                    "message": str(e),
                    "details": None,
                }
            },
        ) from None

    workspace_id = ctx.workspace_id or uuid.UUID(int=0)

    # Check for duplicate name
    existing = await db.execute(
        select(SkillModel).where(
            SkillModel.name == parsed["name"],
            SkillModel.workspace_id == workspace_id,
            ~SkillModel.deleted,
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": {
                    "code": "DUPLICATE_NAME",
                    "message": f"Skill '{parsed['name']}' already exists in this workspace",
                    "details": None,
                }
            },
        )

    skill = SkillModel(
        workspace_id=workspace_id,
        name=parsed["name"],
        description=parsed["description"],
        source=parsed.get("source", "user"),
        instructions=parsed.get("instructions", ""),
        metadata_=parsed.get("metadata", {}),
    )
    db.add(skill)
    await db.flush()
    await db.refresh(skill)
    return SkillReadSchema.model_validate(skill).model_dump()
