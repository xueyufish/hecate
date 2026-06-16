"""Conversation management API endpoints.

Provides operations for conversations:
- ``POST /api/conversations`` — Create a new conversation
- ``GET /api/conversations`` — List conversations (paginated, filterable by agent_id)
- ``GET /api/conversations/{id}`` — Get conversation with messages
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.auth_context import AuthContext
from hecate.core.deps import get_db
from hecate.core.deps_workspace import get_auth_context
from hecate.models.conversation import (
    ConversationCreateSchema,
    ConversationModel,
    ConversationReadSchema,
)
from hecate.models.message import MessageModel, MessageReadSchema

router = APIRouter()


@router.post("/conversations", status_code=status.HTTP_201_CREATED)
async def create_conversation(
    data: ConversationCreateSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Create a new conversation for an agent.

    Args:
        data: The conversation creation data (agent_id required, title optional).
        db: The async database session.
        ctx: The authenticated context with workspace_id.

    Returns:
        dict: The created conversation data.
    """
    conversation = ConversationModel(
        agent_id=data.agent_id,
        title=data.title,
        workspace_id=ctx.workspace_id or uuid.UUID(int=0),
    )
    db.add(conversation)
    await db.flush()
    await db.refresh(conversation)
    return ConversationReadSchema.model_validate(conversation).model_dump()


@router.get("/conversations")
async def list_conversations(
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    agent_id: uuid.UUID | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    """List conversations with optional agent_id filter and pagination.

    Args:
        db: The async database session.
        ctx: The authenticated context with workspace_id.
        agent_id: Optional filter by agent ID.
        page: Page number (1-indexed).
        page_size: Number of items per page.

    Returns:
        dict: ``{"items": [...], "total": int}`` with conversation list and total count.
    """
    base_query = select(ConversationModel).where(~ConversationModel.deleted)
    if ctx.workspace_id is not None:
        base_query = base_query.where(ConversationModel.workspace_id == ctx.workspace_id)
    if agent_id is not None:
        base_query = base_query.where(ConversationModel.agent_id == agent_id)

    count_stmt = select(func.count()).select_from(base_query.subquery())
    total = (await db.execute(count_stmt)).scalar_one()

    offset = (page - 1) * page_size
    stmt = base_query.order_by(ConversationModel.created_at.desc()).offset(offset).limit(page_size)
    result = await db.execute(stmt)
    conversations = result.scalars().all()

    return {
        "items": [ConversationReadSchema.model_validate(c).model_dump() for c in conversations],
        "total": total,
    }


@router.get("/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Get a conversation by ID with its messages.

    Args:
        conversation_id: The UUID of the conversation to retrieve.
        db: The async database session.
        ctx: The authenticated context with workspace_id.

    Returns:
        dict: The conversation data with nested messages.

    Raises:
        HTTPException: 404 if conversation not found or deleted.
    """
    conditions = [
        ConversationModel.id == conversation_id,
        ~ConversationModel.deleted,
    ]
    if ctx.workspace_id is not None:
        conditions.append(ConversationModel.workspace_id == ctx.workspace_id)
    result = await db.execute(select(ConversationModel).where(*conditions))
    conversation = result.scalar_one_or_none()
    if conversation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": "Conversation not found", "details": None}},
        )

    messages_result = await db.execute(
        select(MessageModel).where(MessageModel.conversation_id == conversation_id).order_by(MessageModel.created_at)
    )
    messages = messages_result.scalars().all()

    conv_data = ConversationReadSchema.model_validate(conversation).model_dump()
    conv_data["messages"] = [MessageReadSchema.model_validate(m).model_dump() for m in messages]
    return conv_data
