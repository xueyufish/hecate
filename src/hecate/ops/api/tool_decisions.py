"""Tool decision API endpoints.

Provides read-only access to structured tool policy decision events:

- ``GET /api/security/decisions`` — Query tool decision events with filters (paginated)
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from hecate.core.auth_context import AuthContext
from hecate.core.deps_workspace import get_auth_context
from hecate.models.tool_decision import (
    ToolDecisionQuerySchema,
)
from hecate.ops.tool_decisions import ToolDecisionService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/security", tags=["security"])

_singleton_service: ToolDecisionService | None = None


def get_tool_decision_service() -> ToolDecisionService:
    """Return the singleton tool decision service."""
    global _singleton_service  # noqa: PLW0603
    if _singleton_service is None:
        _singleton_service = ToolDecisionService()
    return _singleton_service


def set_tool_decision_service(service: ToolDecisionService) -> None:
    """Set the singleton tool decision service (called at startup)."""
    global _singleton_service  # noqa: PLW0603
    _singleton_service = service


@router.get("/decisions")
async def query_tool_decisions(
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    agent_id: str | None = None,
    workspace_id: str | None = None,
    session_id: str | None = None,
    decision: str | None = None,
    tool_name: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    """Query structured tool policy decision events.

    Returns filtered, paginated tool decision events emitted by
    ToolPolicyPipeline, ToolAccessPolicy, and SandboxEnforcementRouter.
    """
    service = get_tool_decision_service()
    params = ToolDecisionQuerySchema(
        agent_id=agent_id,
        workspace_id=workspace_id,
        session_id=session_id,
        decision=decision,
        tool_name=tool_name,
        start=start,
        end=end,
        limit=limit,
        offset=offset,
    )
    events, total = await service.query(params)
    return {
        "events": [e.model_dump(mode="json") for e in events],
        "total": total,
        "limit": limit,
        "offset": offset,
    }
