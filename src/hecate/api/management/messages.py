"""Message management API endpoints.

Provides operations for messages:
- ``GET /api/messages/{id}/citations`` — Get citations for a message
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.deps import get_db, verify_api_key
from hecate.models.message import MessageModel
from hecate.services.rag.types import CitationResponse

router = APIRouter()


@router.get("/messages/{message_id}/citations")
async def get_message_citations(
    message_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    api_key: Annotated[str, Depends(verify_api_key)],
) -> dict:
    """Get citations for a message.

    Args:
        message_id: UUID of the message to retrieve citations for.
        db: The async database session.
        api_key: The validated API key or JWT token.

    Returns:
        dict: CitationResponse with citations array and message_id.

    Raises:
        HTTPException: 404 if message not found.
    """
    result = await db.execute(
        select(MessageModel).where(
            MessageModel.id == message_id,
            ~MessageModel.deleted,
        )
    )
    message = result.scalar_one_or_none()

    if message is None:
        raise HTTPException(status_code=404, detail="Message not found")

    citations = message.metadata_.get("citations", []) if message.metadata_ else []
    response = CitationResponse(
        citations=[],
        message_id=message_id,
    )

    from hecate.services.rag.types import Citation

    for c in citations:
        try:
            response.citations.append(Citation(**c))
        except (ValueError, KeyError):
            continue

    return response.model_dump()
