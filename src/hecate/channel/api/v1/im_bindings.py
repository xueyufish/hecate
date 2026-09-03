"""IM binding confirmation endpoint.

Routes:
- ``POST /v1/im/bindings/confirm`` — confirm a binding token issued by the
  IM channel webhook handler. The caller must be authenticated; the token
  identifies which IM identity will be bound.

- ``GET /v1/im/bindings/confirm?token=...`` — fetch binding metadata for the
  Web UI confirmation page (Phase 2 — the page itself is task 4.4).

The token is single-use and 10-minute TTL; both constraints are enforced
inside :class:`IMBindingService.confirm_token`.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel as PydanticBase
from pydantic import ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.channel.im.binding import (
    IMBindingService,
    TokenError,
    TokenExpiredError,
    TokenNotFoundError,
    TokenUsedError,
)
from hecate.core.auth_context import AuthContext
from hecate.core.database import get_db
from hecate.core.deps_workspace import get_auth_context

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/im/bindings", tags=["im-bindings"])


class BindConfirmRequestSchema(PydanticBase):
    """Request payload for ``POST /v1/im/bindings/confirm``."""

    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=16, max_length=128)


class BindConfirmResponseSchema(PydanticBase):
    """Response payload confirming a successful binding."""

    model_config = ConfigDict(from_attributes=True)

    workspace_id: str
    user_id: str
    channel_type: str
    im_app_id: str
    im_user_id: str
    bound_at: str | None = None


def get_binding_service(request: Request) -> IMBindingService:
    """Return the process-wide :class:`IMBindingService` (DI hook)."""
    return getattr(request.app.state, "im_binding_service", None) or IMBindingService()


@router.post(
    "/confirm",
    response_model=BindConfirmResponseSchema,
    status_code=status.HTTP_200_OK,
)
async def confirm_binding(
    payload: BindConfirmRequestSchema,
    session: Annotated[AsyncSession, Depends(get_db)],
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    service: Annotated[IMBindingService, Depends(get_binding_service)],
) -> BindConfirmResponseSchema:
    """Confirm a pending IM identity binding.

    The caller must be an authenticated Hecate user; the binding is created
    with that user as the bound Hecate side. A single token can only be
    used once.
    """
    try:
        binding = await service.confirm_token(
            payload.token,
            bound_user_id=auth.user_id,
            _session=session,
        )
    except TokenNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail=str(exc),
        ) from exc
    except TokenExpiredError as exc:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail=str(exc),
        ) from exc
    except TokenUsedError as exc:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail=str(exc),
        ) from exc
    except TokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    return BindConfirmResponseSchema(
        workspace_id=str(binding.workspace_id),
        user_id=str(binding.user_id),
        channel_type=binding.channel_type,
        im_app_id=binding.im_app_id,
        im_user_id=binding.im_user_id,
    )
