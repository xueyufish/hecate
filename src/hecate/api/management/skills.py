"""Skill management API endpoints.

Provides operations for skills:
- ``GET /api/skills`` — List all skills
- ``GET /api/skills/{id}`` — Get skill by ID
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.deps import get_db, verify_api_key
from hecate.models.skill import SkillModel, SkillReadSchema

router = APIRouter()


@router.get("/skills")
async def list_skills(
    db: Annotated[AsyncSession, Depends(get_db)],
    api_key: Annotated[str, Depends(verify_api_key)],
    source: str | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    """List skills with optional source filter and pagination.

    Args:
        db: The async database session.
        api_key: The validated API key.
        source: Optional filter by skill source (system, user, project).
        page: Page number (1-indexed).
        page_size: Number of items per page.

    Returns:
        dict: ``{"items": [...], "total": int}`` with skill list and total count.
    """
    base_query = select(SkillModel).where(SkillModel.deleted_at.is_(None))
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
    api_key: Annotated[str, Depends(verify_api_key)],
) -> dict:
    """Get a skill by ID.

    Args:
        skill_id: The UUID of the skill to retrieve.
        db: The async database session.
        api_key: The validated API key.

    Returns:
        dict: The skill data.

    Raises:
        HTTPException: 404 if skill not found or deleted.
    """
    result = await db.execute(
        select(SkillModel).where(
            SkillModel.id == skill_id,
            SkillModel.deleted_at.is_(None),
        )
    )
    skill = result.scalar_one_or_none()
    if skill is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": "Skill not found", "details": None}},
        )
    return SkillReadSchema.model_validate(skill).model_dump()
