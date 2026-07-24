"""Security audit API endpoints.

Provides read-only access to structured security audit events:

- ``GET /api/security/audit`` — Query security audit events with filters (paginated)
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from hecate.core.auth_context import AuthContext
from hecate.core.deps_workspace import get_auth_context
from hecate.models.security_audit import (
    SecurityAuditQuerySchema,
)
from hecate.services.security.audit_service import SecurityAuditService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/security", tags=["security"])

_singleton_service: SecurityAuditService | None = None


def get_security_audit_service() -> SecurityAuditService:
    """Return the singleton security audit service."""
    global _singleton_service  # noqa: PLW0603
    if _singleton_service is None:
        _singleton_service = SecurityAuditService()
    return _singleton_service


def set_security_audit_service(service: SecurityAuditService) -> None:
    """Set the singleton security audit service (called at startup)."""
    global _singleton_service  # noqa: PLW0603
    _singleton_service = service


@router.get("/audit")
async def query_security_audit(
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
    """Query structured security audit events.

    Returns filtered, paginated security audit events emitted by
    ToolPolicyPipeline, ToolAccessPolicy, and SandboxEnforcementRouter.
    """
    service = get_security_audit_service()
    params = SecurityAuditQuerySchema(
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
