"""Security findings API endpoints.

Provides read and feedback access to security findings produced by
the FindingEngine:

- ``GET  /api/security/findings`` — Query security findings (paginated)
- ``POST /api/security/findings/{id}/feedback`` — Record true-positive /
  false-positive feedback on a finding (writes ``feedback``,
  ``feedback_user``, ``feedback_comment``, ``feedback_at`` into the
  finding's ``metadata_`` JSON column).
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel as PydanticBase
from pydantic import Field

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


class SecurityFindingFeedbackSchema(PydanticBase):
    """Body schema for ``POST /api/security/findings/{id}/feedback``."""

    feedback: str = Field(pattern=r"^(true_positive|false_positive)$")
    feedback_comment: str | None = None


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


@router.post("/findings/{finding_id}/feedback")
async def submit_finding_feedback(
    finding_id: uuid.UUID,
    body: SecurityFindingFeedbackSchema,
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Record true-positive / false-positive feedback on a finding.

    The feedback is stored in the finding's ``metadata_`` JSON column
    under ``feedback``, ``feedback_user``, ``feedback_comment``, and
    ``feedback_at`` keys. Used by the DLP engine to tune
    ``detect-secrets`` and Presidio recognizer thresholds over time.
    """
    service = get_security_finding_service()
    updated = await service.set_feedback(
        finding_id=finding_id,
        feedback=body.feedback,
        feedback_user=str(ctx.user_id) if ctx.user_id else "anonymous",
        feedback_comment=body.feedback_comment,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Finding not found")
    return updated.model_dump(mode="json")
