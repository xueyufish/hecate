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
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.auth_context import AuthContext
from hecate.core.deps import get_db
from hecate.core.deps_workspace import get_auth_context
from hecate.models.prompt import PromptCreateSchema, PromptUpdateSchema
from hecate.services.prompt_service import PromptService

router = APIRouter()


@router.post("/prompts", status_code=status.HTTP_201_CREATED)
async def create_prompt(
    data: PromptCreateSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Create a new prompt with initial version."""
    service = PromptService(db)
    try:
        result = await service.create_prompt(data, workspace_id=ctx.workspace_id or uuid.UUID(int=0))
        return result.model_dump()
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
    """List prompts with pagination."""
    service = PromptService(db)
    result = await service.list_prompts(
        workspace_id=ctx.workspace_id or uuid.UUID(int=0),
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
    """Get a prompt by ID."""
    service = PromptService(db)
    try:
        result = await service.get_prompt(prompt_id)
        return result.model_dump()
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": str(e), "details": None}},
        ) from e


@router.put("/prompts/{prompt_id}")
async def update_prompt(
    prompt_id: uuid.UUID,
    data: PromptUpdateSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Update an existing prompt."""
    service = PromptService(db)
    try:
        result = await service.update_prompt(prompt_id, data)
        return result.model_dump()
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
    """Soft delete a prompt."""
    service = PromptService(db)
    try:
        await service.delete_prompt(prompt_id)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": str(e), "details": None}},
        ) from e


@router.get("/prompts/{prompt_id}/versions")
async def list_prompt_versions(
    prompt_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> list[dict]:
    """List all versions of a prompt."""
    service = PromptService(db)
    versions = await service.list_versions(prompt_id)
    return [v.model_dump() for v in versions]


@router.get("/prompts/{prompt_id}/versions/{version}")
async def get_prompt_version(
    prompt_id: uuid.UUID,
    version: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Get a specific version of a prompt."""
    service = PromptService(db)
    try:
        result = await service.get_version(prompt_id, version)
        return result.model_dump()
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": str(e), "details": None}},
        ) from e


@router.post("/prompts/{prompt_id}/rollback/{version}")
async def rollback_prompt(
    prompt_id: uuid.UUID,
    version: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Rollback a prompt to a specific version."""
    service = PromptService(db)
    try:
        result = await service.rollback_to_version(prompt_id, version)
        return result.model_dump()
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": str(e), "details": None}},
        ) from e


@router.get("/prompts/by-label/{label}")
async def get_prompt_by_label(
    label: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Get a prompt by deployment label."""
    service = PromptService(db)
    result = await service.get_by_label(label)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": f"No prompt with label '{label}'", "details": None}},
        )
    return result.model_dump()
