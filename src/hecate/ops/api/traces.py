"""API router for trace querying endpoints.

Provides list and detail endpoints for traces with filtering support:

- ``GET /api/traces`` — list authorized root traces (paginated)
- ``GET /api/traces/{trace_id}`` — trace detail with span tree

Both entries sit behind the read access boundary: every identity — system
scope included — needs an explicit active workspace (403
``NO_WORKSPACE`` before any query; an explicit zero-UUID workspace stays a
legal value), reads require at least VIEWER from the server-resolved auth
context, and authorization runs through live Session/Agent row
association into the caller's workspace (session links verify the
session's own agent binding; every association present on a row must be
valid). The list checks workspace and role before any query; the detail
probes ownership and structure with a minimal field set first, then
applies the role floor, and only then loads full content. Missing,
foreign, soft-deleted, orphaned, and structurally unsafe traces share one
constant evidence-free 404.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.auth_context import AuthContext
from hecate.core.database import get_db
from hecate.core.deps_workspace import (
    WorkspaceRole,
    WorkspaceRoleRequiredError,
    ensure_workspace_role,
    get_auth_context,
)
from hecate.models.trace import TraceDetailSchema, TraceListSchema, TraceReadSchema
from hecate.ops.api.trace_access import TRACE_NOT_FOUND, TraceReadService

router = APIRouter()

_session_id_q = Query(default=None)
_agent_id_q = Query(default=None)
_start_time_q = Query(default=None)
_end_time_q = Query(default=None)
_limit_q = Query(default=20, ge=1, le=100)
_offset_q = Query(default=0, ge=0)


def _workspace_id(ctx: AuthContext) -> uuid.UUID:
    """The caller's explicit workspace; never a silent zero-UUID default."""
    if ctx.workspace_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": {"code": "NO_WORKSPACE", "message": "An active workspace is required", "details": None}},
        )
    return ctx.workspace_id


def _require_role(ctx: AuthContext, minimum: WorkspaceRole) -> None:
    """Server-side role floor from the resolved auth context only."""
    try:
        ensure_workspace_role(ctx, minimum)
    except WorkspaceRoleRequiredError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": {"code": "FORBIDDEN", "message": e.message, "details": None}},
        ) from e


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"error": {"code": "NOT_FOUND", "message": TRACE_NOT_FOUND, "details": None}},
    )


@router.get("/traces", response_model=list[TraceListSchema])
async def list_traces(
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    session_id: uuid.UUID | None = _session_id_q,
    agent_id: uuid.UUID | None = _agent_id_q,
    start_time: datetime | None = _start_time_q,
    end_time: datetime | None = _end_time_q,
    limit: int = _limit_q,
    offset: int = _offset_q,
) -> Any:
    """List root traces of the caller's workspace with optional filters.

    Workspace and role gates run before any query; filters only narrow
    the authorized population, and pagination applies after authorization
    filtering.
    """
    workspace_id = _workspace_id(ctx)
    _require_role(ctx, WorkspaceRole.VIEWER)
    service = TraceReadService(db)
    return await service.list_traces(
        workspace_id,
        session_id=session_id,
        agent_id=agent_id,
        start_time=start_time,
        end_time=end_time,
        limit=limit,
        offset=offset,
    )


@router.get("/traces/{trace_id}", response_model=TraceDetailSchema)
async def get_trace(
    trace_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> Any:
    """Get trace detail with the verified span tree.

    Visibility (ownership + structure) is settled with a minimal field
    probe before the role decision; full content is only read after both
    gates pass.
    """
    workspace_id = _workspace_id(ctx)
    service = TraceReadService(db)
    try:
        authorized = await service.authorize_trace(trace_id, workspace_id)
    except LookupError as e:
        raise _not_found() from e
    _require_role(ctx, WorkspaceRole.VIEWER)
    try:
        root, spans = await service.load_trace_detail(authorized)
    except LookupError as e:
        raise _not_found() from e
    return TraceDetailSchema(
        trace=TraceReadSchema.model_validate(root),
        spans=[TraceReadSchema.model_validate(s) for s in spans],
    )
