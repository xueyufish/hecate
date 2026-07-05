"""SkillRegistry management API endpoints."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel as PydanticBase
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.database import get_db
from hecate.skill_registry.registry import SkillRegistry
from hecate.skill_registry.types import SkillNotFoundError, SkillRef, SkillRefType

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/skills", tags=["skill-registry"])


class SkillRefSchema(PydanticBase):
    """Schema for a skill reference."""

    ref_type: str
    ref_id: str


class ResolvedSkillSchema(PydanticBase):
    """Schema for a resolved skill."""

    name: str
    description: str
    source: str
    parameters: dict | None = None
    metadata: dict = {}


class ResolveRequest(PydanticBase):
    """Request to resolve skill references."""

    refs: list[SkillRefSchema]


class ResolveResponse(PydanticBase):
    """Response with resolved skills."""

    skills: list[ResolvedSkillSchema]


@router.post("/resolve", response_model=ResolveResponse)
async def resolve_skills(
    request: ResolveRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ResolveResponse:
    """Resolve skill references to their metadata.

    Args:
        request: The resolve request with skill references.
        db: Database session.

    Returns:
        Resolved skills with metadata.
    """
    registry = SkillRegistry(db)

    refs = []
    for ref_schema in request.refs:
        try:
            ref_type = SkillRefType(ref_schema.ref_type)
        except ValueError as e:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid ref_type: {ref_schema.ref_type}",
            ) from e
        refs.append(SkillRef(ref_type=ref_type, ref_id=ref_schema.ref_id))

    try:
        resolved = await registry.resolve(refs)
    except SkillNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e

    return ResolveResponse(
        skills=[
            ResolvedSkillSchema(
                name=s.name,
                description=s.description,
                source=s.source.value,
                parameters=s.parameters,
                metadata=s.metadata,
            )
            for s in resolved
        ]
    )


@router.get("/format")
async def format_skills_for_llm(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Format resolved skills as XML for LLM context injection.

    Args:
        db: Database session.

    Returns:
        Skills formatted as XML string.
    """
    registry = SkillRegistry(db)
    # Empty list returns empty string
    return {"skills_xml": registry.format_for_llm([])}
