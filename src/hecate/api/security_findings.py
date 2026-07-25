"""Security findings API endpoints.

Provides read-only access to security findings produced by the FindingEngine:

- ``GET /api/security/findings`` — Query security findings with filters (paginated)
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from hecate.core.auth_context import AuthContext
from hecate.core.deps_workspace import get_auth_context
from hecate.models.security_finding import SecurityFindingQuerySchema
from hecate.services.security.finding_service import SecurityFindingService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/security", tags=["security"])

_singleton_service: SecurityFindingService | None = None


def get_security_finding_service() -> SecurityFindingService:
    """Return the singleton security finding service."""
    global _singleton_service  # noqa: PLW0603
    if _singleton_service is None:
        _singleton_service = SecurityFindingService()
    return _singleton_service


def set_security_finding_service(service: SecurityFindingService) -> None:
    """Set the singleton security finding service (called at startup)."""
    global _singleton_service  # noqa: PLW0603
    _singleton_service = service


@router.get("/findings")
async def query_security_findings(
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    org_id: str | None = None,
    workspace_id: str | None = None,
    user_id: str | None = None,
    rule_name: str | None = None,
    severity: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    """Query security findings from the FindingEngine.

    Returns filtered, paginated security findings produced by anomaly
    detection rules (bulk delete, off-hours ops, unusual IP, etc.).
    """
    import uuid

    service = get_security_finding_service()
    params = SecurityFindingQuerySchema(
        org_id=uuid.UUID(org_id) if org_id else None,
        workspace_id=uuid.UUID(workspace_id) if workspace_id else None,
        user_id=uuid.UUID(user_id) if user_id else None,
        rule_name=rule_name,
        severity=severity,
        start=start,
        end=end,
        limit=limit,
        offset=offset,
    )
    findings, total = await service.query(params)
    return {
        "findings": [f.model_dump(mode="json") for f in findings],
        "total": total,
        "limit": limit,
        "offset": offset,
    }
