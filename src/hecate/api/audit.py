"""Audit log API endpoints.

Provides read-only access to audit logs with filtering and export:

- ``GET /api/audit/logs`` — Query audit logs with filters (paginated)
- ``GET /api/audit/logs/export`` — Export audit logs as CSV or JSON
- ``GET /api/audit/policies`` — List active security policies
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from hecate.core.auth_context import AuthContext
from hecate.core.deps_workspace import get_auth_context
from hecate.models.audit import AuditLogQuerySchema
from hecate.services.audit.service import AuditService, create_default_audit_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/audit", tags=["audit"])

# Singleton audit service with built-in policies
_audit_service: AuditService | None = None


def _get_audit_service() -> AuditService:
    """Return the singleton audit service with default policies."""
    global _audit_service  # noqa: PLW0603
    if _audit_service is None:
        _audit_service = create_default_audit_service()
    return _audit_service


@router.get("/logs")
async def query_logs(
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    org_id: str | None = None,
    workspace_id: str | None = None,
    user_id: str | None = None,
    action: str | None = None,
    resource_type: str | None = None,
    success: bool | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    """Query audit logs with filters and pagination."""
    import uuid
    from datetime import datetime

    filters = AuditLogQuerySchema(
        org_id=uuid.UUID(org_id) if org_id else None,
        workspace_id=uuid.UUID(workspace_id) if workspace_id else None,
        user_id=uuid.UUID(user_id) if user_id else None,
        action=action,
        resource_type=resource_type,
        success=success,
        start_time=datetime.fromisoformat(start_time) if start_time else None,
        end_time=datetime.fromisoformat(end_time) if end_time else None,
        page=page,
        page_size=page_size,
    )

    service = _get_audit_service()
    items, total = await service.query(filters)

    return {
        "items": [item.model_dump(mode="json") for item in items],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/logs/export")
async def export_logs(
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    fmt: str = Query("json", pattern="^(csv|json)$"),
    org_id: str | None = None,
    action: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
) -> Response:
    """Export audit logs as CSV or JSON."""
    import uuid
    from datetime import datetime

    filters = AuditLogQuerySchema(
        org_id=uuid.UUID(org_id) if org_id else None,
        action=action,
        start_time=datetime.fromisoformat(start_time) if start_time else None,
        end_time=datetime.fromisoformat(end_time) if end_time else None,
        page=1,
        page_size=10000,
    )

    service = _get_audit_service()
    data = await service.export(fmt, filters)

    if fmt == "csv":
        return Response(
            content=data if isinstance(data, str) else data.decode(),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=audit_logs.csv"},
        )
    return Response(
        content=data if isinstance(data, str) else data.decode(),
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=audit_logs.json"},
    )


@router.get("/policies")
async def list_policies(
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """List active security policies."""
    service = _get_audit_service()
    engine = service.policy_engine
    if engine is None:
        return {"policies": []}
    return {
        "policies": [{"name": p.name, "description": p.description} for p in engine.policies],
    }
