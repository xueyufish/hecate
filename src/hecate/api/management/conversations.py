"""Conversation management API endpoints.

Provides operations for conversations:
- ``GET /api/conversations`` — List conversations (paginated, filterable by agent_id)
- ``GET /api/conversations/{id}`` — Get conversation with messages
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from hecate.core.deps import get_db, verify_api_key
from hecate.models.conversation import ConversationModel, ConversationReadSchema
from hecate.models.message import MessageModel, MessageReadSchema

router = APIRouter()


@router.get("/conversations")
async def list_conversations(
    db: Annotated[AsyncSession, Depends(get_db)],
    api_key: Annotated[str, Depends(verify_api_key)],
    agent_id: uuid.UUID | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    """List conversations with optional agent_id filter and pagination.

    Args:
        db: The async database session.
        api_key: The validated API key.
        agent_id: Optional filter by agent ID.
        page: Page number (1-indexed).
        page_size: Number of items per page.

    Returns:
        dict: ``{"items": [...], "total": int}`` with conversation list and total count.
    """
    base_query = select(ConversationModel).where(ConversationModel.deleted_at.is_(None))
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
    api_key: Annotated[str, Depends(verify_api_key)],
) -> dict:
    """Get a conversation by ID with its messages.

    Args:
        conversation_id: The UUID of the conversation to retrieve.
        db: The async database session.
        api_key: The validated API key.

    Returns:
        dict: The conversation data with nested messages.

    Raises:
        HTTPException: 404 if conversation not found or deleted.
    """
    result = await db.execute(
        select(ConversationModel)
        .where(
            ConversationModel.id == conversation_id,
            ConversationModel.deleted_at.is_(None),
        )
        .options(selectinload(ConversationModel.messages))
    )
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
