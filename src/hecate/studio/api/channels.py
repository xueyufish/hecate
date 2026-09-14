"""Publishing channel management API endpoints (1.3.20).

Studio-facing CRUD for publishing channels:

- ``POST   /api/channels`` — create (agent must have a published version)
- ``GET    /api/channels`` — list (optionally filtered by ``agent_id``)
- ``GET    /api/channels/{id}`` — detail
- ``PUT    /api/channels/{id}`` — update metadata / repoint binding
- ``DELETE /api/channels/{id}`` — soft delete

The invocation surface lives at ``POST /v1/channels/{id}/chat/completions``
(channel domain), not here.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.auth_context import AuthContext
from hecate.core.deps import get_db
from hecate.core.deps_workspace import get_auth_context
from hecate.models.channels import (
    ChannelCreateSchema,
    ChannelModel,
    ChannelReadSchema,
    ChannelUpdateSchema,
)

if TYPE_CHECKING:
    from hecate.channel.publishing import ChannelPublishingService


def _service(db: AsyncSession) -> ChannelPublishingService:
    """Lazy channel-domain import (studio must not import other domains at
    module level — enforced by ``test_layering_domain``)."""
    from hecate.channel.publishing import ChannelPublishingService

    return ChannelPublishingService(db)


router = APIRouter()


def _not_found(exc: ValueError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"error": {"code": "NOT_FOUND", "message": str(exc), "details": None}},
    )


def _bad_request(exc: ValueError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={"error": {"code": "INVALID_CHANNEL", "message": str(exc), "details": None}},
    )


def _is_entity_missing(exc: ValueError) -> bool:
    """True only when the *referenced entity* (agent/channel) is missing.

    Constraint violations (e.g. a pinned version that does not exist) are
    client errors, not 404s.
    """
    message = str(exc)
    return message.startswith(("Agent", "Channel")) and "not found" in message


@router.post("/channels", status_code=status.HTTP_201_CREATED, response_model=ChannelReadSchema)
async def create_channel(
    data: ChannelCreateSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> ChannelModel:
    """Create a publishing channel bound to an agent's published version."""
    service = _service(db)
    try:
        return await service.create_channel(
            workspace_id=ctx.workspace_id or uuid.UUID(int=0),
            created_by=ctx.user_id,
            name=data.name,
            channel_type=data.type,
            agent_id=data.agent_id,
            bind_mode=data.bind_mode,
            pinned_version=data.pinned_version,
            config=data.config,
        )
    except ValueError as e:
        if _is_entity_missing(e):
            raise _not_found(e) from e
        raise _bad_request(e) from e


@router.get("/channels", response_model=list[ChannelReadSchema])
async def list_channels(
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    agent_id: Annotated[uuid.UUID | None, Query()] = None,
) -> list[ChannelModel]:
    """List publishing channels, optionally filtered by agent."""
    workspace_id = ctx.workspace_id or uuid.UUID(int=0)
    return await _service(db).list_channels(workspace_id=workspace_id, agent_id=agent_id)


@router.get("/channels/{channel_id}", response_model=ChannelReadSchema)
async def get_channel(
    channel_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> ChannelModel:
    """Fetch one publishing channel."""
    try:
        return await _service(db).get_channel(channel_id)
    except ValueError as e:
        raise _not_found(e) from e


@router.put("/channels/{channel_id}", response_model=ChannelReadSchema)
async def update_channel(
    channel_id: uuid.UUID,
    data: ChannelUpdateSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> ChannelModel:
    """Update channel metadata or repoint its version binding."""
    service = _service(db)
    try:
        return await service.update_channel(
            channel_id,
            name=data.name,
            bind_mode=data.bind_mode,
            pinned_version=data.pinned_version,
            config=data.config,
            status=data.status,
        )
    except ValueError as e:
        if _is_entity_missing(e):
            raise _not_found(e) from e
        raise _bad_request(e) from e


@router.delete("/channels/{channel_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_channel(
    channel_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> None:
    """Soft-delete a publishing channel."""
    try:
        await _service(db).delete_channel(channel_id)
    except ValueError as e:
        raise _not_found(e) from e
