"""Tool management API endpoints.

Provides operations for tools:
- ``GET /api/tools`` — List all tools (builtin, custom, mcp)
- ``GET /api/tools/{id}`` — Get tool by ID
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.deps import get_db, verify_api_key
from hecate.models.tool import ToolModel, ToolReadSchema

router = APIRouter()


@router.get("/tools")
async def list_tools(
    db: Annotated[AsyncSession, Depends(get_db)],
    api_key: Annotated[str, Depends(verify_api_key)],
    source: str | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    """List tools with optional source filter and pagination.

    Args:
        db: The async database session.
        api_key: The validated API key.
        source: Optional filter by tool source (builtin, custom, mcp).
        page: Page number (1-indexed).
        page_size: Number of items per page.

    Returns:
        dict: ``{"items": [...], "total": int}`` with tool list and total count.
    """
    base_query = select(ToolModel).where(ToolModel.deleted_at.is_(None))
    if source is not None:
        base_query = base_query.where(ToolModel.source == source)

    count_stmt = select(func.count()).select_from(base_query.subquery())
    total = (await db.execute(count_stmt)).scalar_one()

    offset = (page - 1) * page_size
    stmt = base_query.order_by(ToolModel.created_at.desc()).offset(offset).limit(page_size)
    result = await db.execute(stmt)
    tools = result.scalars().all()

    return {
        "items": [ToolReadSchema.model_validate(t).model_dump() for t in tools],
        "total": total,
    }


@router.get("/tools/{tool_id}")
async def get_tool(
    tool_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    api_key: Annotated[str, Depends(verify_api_key)],
) -> dict:
    """Get a tool by ID.

    Args:
        tool_id: The UUID of the tool to retrieve.
        db: The async database session.
        api_key: The validated API key.

    Returns:
        dict: The tool data.

    Raises:
        HTTPException: 404 if tool not found or deleted.
    """
    result = await db.execute(
        select(ToolModel).where(
            ToolModel.id == tool_id,
            ToolModel.deleted_at.is_(None),
        )
    )
    tool = result.scalar_one_or_none()
    if tool is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": "Tool not found", "details": None}},
        )
    return ToolReadSchema.model_validate(tool).model_dump()
