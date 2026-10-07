"""Platform task control API (step6 platform track).

Task lifecycle surface over :class:`TaskControlService`:

- ``POST /api/tasks`` — idempotent submission; returns the persistent
  task/run references. ``wait=true`` is the synchronous wait view on the
  same Task/Run; otherwise the execution proceeds in-process (dev
  dispatch) and the client follows progress via the event views.
- ``GET /api/tasks`` / ``GET /api/tasks/{id}`` — workspace-scoped queries
  with the lifecycle projection.
- ``GET /api/runs/{run_id}/events`` — cursor-paginated event reads;
  ``GET /api/runs/{run_id}/events/stream`` — the SSE subscribe view on
  the same stream (resume by cursor after a disconnect).
- ``POST /api/tasks/{id}/commands`` / ``POST /api/tasks/{id}/cancel`` /
  ``GET /api/commands/{command_id}`` — control-command receipts. An HTTP
  success here records a request; it never claims the command was
  applied.
- ``GET /api/reconciliation/pending`` — the pending-reconciliation view.

This module is protocol adaptation only (auth context, request/response
shapes, error mapping); all semantics live in the service.
"""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from typing import TYPE_CHECKING, Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.contracts.execution.durable import ControlCommandKind
from hecate.core.auth_context import AuthContext
from hecate.core.composition.durable_platform import get_durable_suite
from hecate.core.deps import get_db
from hecate.core.deps_workspace import get_auth_context

if TYPE_CHECKING:
    # Execution-domain types are annotation-only here; runtime imports stay
    # function-local (channel/ must not import other domains at module level
    # - the same lazy-import pattern as the chat and A2A entry adapters).
    from hecate.execution.task_control import TaskControlService

router = APIRouter(prefix="/tasks", tags=["tasks"])
runs_router = APIRouter(prefix="/runs", tags=["tasks"])
commands_router = APIRouter(prefix="/commands", tags=["tasks"])
reconciliation_router = APIRouter(prefix="/reconciliation", tags=["tasks"])


class TaskSubmitRequest(BaseModel):
    """One task submission body."""

    goal: str = Field(min_length=1, max_length=4096)
    agent_id: uuid.UUID
    input: dict[str, Any]
    idempotency_key: str | None = Field(default=None, max_length=512)
    wait: bool = False
    stream: bool = False


class TaskSubmitResponse(BaseModel):
    """Persistent references plus the state the platform can attest."""

    task_id: uuid.UUID
    run_id: uuid.UUID
    issuer_domain: str
    lifecycle_state: str
    replayed: bool
    result: dict[str, Any] | None = None
    reason: str | None = None


class CommandRequest(BaseModel):
    """One control command body (payload must declare its schema ref)."""

    kind: ControlCommandKind
    payload: dict[str, Any] | None = None
    payload_schema_ref: str | None = None
    expected_revision: int | None = None
    expires_at: str | None = None


def get_task_control_service(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TaskControlService:
    """Bind the service to the request session and the process suite."""
    from hecate.execution.task_control import TaskControlService

    suite = get_durable_suite()
    worker = getattr(_current_request_stack(), "durable_worker", None)
    relay = getattr(_current_request_stack(), "durable_outbox_relay", None)
    return TaskControlService(
        db,
        store=suite.store,
        recorder=suite.recorder,
        backend=suite.backend,
        ledger_source=suite.ledger_source,
        worker=worker,
        relay=relay,
    )


_worker_state: Any = None


def bind_worker_state(state: Any) -> None:
    """Bind the app-state handle for worker/relay lookup (called at startup)."""

    global _worker_state
    _worker_state = state


def _current_request_stack() -> Any:
    return _worker_state or SimpleNamespace()


# FastAPI only needs the dependency here; the service type stays lazy
# (TYPE_CHECKING-only, per the channel layering rule).
ServiceDep = Annotated[Any, Depends(get_task_control_service)]
AuthDep = Annotated[AuthContext, Depends(get_auth_context)]


def _error(code: str, message: str, http_status: int, details: Any = None) -> HTTPException:
    return HTTPException(
        status_code=http_status,
        detail={"error": {"code": code, "message": message, "details": details}},
    )


def _map_service_error(exc: Exception) -> HTTPException:
    from hecate.execution.task_control import (
        SubmissionConflictError,
        TaskControlError,
        TaskControlNotFoundError,
        TaskControlValidationError,
    )

    if isinstance(exc, TaskControlNotFoundError):
        return _error("NOT_FOUND", str(exc), status.HTTP_404_NOT_FOUND)
    if isinstance(exc, SubmissionConflictError):
        return _error("IDEMPOTENCY_CONFLICT", str(exc), status.HTTP_409_CONFLICT)
    if isinstance(exc, TaskControlValidationError):
        return _error("INVALID_REQUEST", str(exc), status.HTTP_422_UNPROCESSABLE_ENTITY)
    if isinstance(exc, TaskControlError):
        return _error("TASK_CONTROL_ERROR", str(exc), status.HTTP_500_INTERNAL_SERVER_ERROR)
    raise exc


def _workspace_id(ctx: AuthContext) -> uuid.UUID:
    if ctx.workspace_id is None:
        raise _error("NO_WORKSPACE", "request has no workspace scope", status.HTTP_403_FORBIDDEN)
    return ctx.workspace_id


def _submit_response(result: Any) -> dict[str, Any]:
    return {
        "task_id": uuid.UUID(result.task_ref.id),
        "run_id": uuid.UUID(result.run_ref.id),
        "issuer_domain": result.task_ref.issuer_domain,
        "lifecycle_state": result.lifecycle_state,
        "replayed": result.replayed,
        "result": result.result,
        "reason": result.reason,
    }


@router.post("", status_code=status.HTTP_201_CREATED)
async def submit_task(
    body: TaskSubmitRequest,
    service: ServiceDep,
    ctx: AuthDep,
    response: Response,
) -> dict[str, Any]:
    """Idempotently submit one task; 201 new, 200 for an idempotent replay."""
    try:
        result = await service.submit(
            workspace_id=_workspace_id(ctx),
            user_id=ctx.user_id,
            goal=body.goal,
            agent_id=body.agent_id,
            input=body.input,
            idempotency_key=body.idempotency_key,
            wait=body.wait,
            stream=body.stream,
        )
    except Exception as exc:  # noqa: BLE001 — narrowed by _map_service_error
        raise _map_service_error(exc) from exc
    if result.replayed:
        # A replay is not a new resource; return the original association.
        response.status_code = status.HTTP_200_OK
    return _submit_response(result)


@router.get("")
async def list_tasks(
    service: ServiceDep,
    ctx: AuthDep,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    lifecycle_state: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    """Workspace-scoped task list with lifecycle projection."""
    try:
        items, total = await service.list_tasks(
            _workspace_id(ctx), state=lifecycle_state, page=page, page_size=page_size
        )
    except Exception as exc:  # noqa: BLE001 — narrowed by _map_service_error
        raise _map_service_error(exc) from exc
    return {"items": items, "total": total}


@router.get("/{task_id}")
async def get_task(task_id: uuid.UUID, service: ServiceDep, ctx: AuthDep) -> dict[str, Any]:
    """Task detail: responsibility record, lifecycle projection, runs."""
    try:
        return await service.get_task_detail(_workspace_id(ctx), task_id)
    except Exception as exc:  # noqa: BLE001 — narrowed by _map_service_error
        raise _map_service_error(exc) from exc


@router.post("/{task_id}/cancel")
async def cancel_task(task_id: uuid.UUID, service: ServiceDep, ctx: AuthDep) -> dict[str, Any]:
    """Request cancellation; success records the request, never applied."""
    cancel = CommandRequest(kind=ControlCommandKind.CANCEL)
    return await _issue(ctx, service, task_id, ControlCommandKind.CANCEL, cancel)


class WorkflowCallbackRequest(BaseModel):
    """One workflow callback asserting a child task's terminal outcome."""

    command_id: str = Field(min_length=1)
    wait_token: str = Field(min_length=1)
    child_task_id: uuid.UUID
    declared_state: Literal["succeeded", "failed", "cancelled"]
    result: dict[str, Any] = Field(default_factory=dict)


@router.post("/{task_id}/workflow-callback")
async def workflow_callback(
    task_id: uuid.UUID, body: WorkflowCallbackRequest, service: ServiceDep, ctx: AuthDep
) -> dict[str, Any]:
    """Wake a workflow's durable wait after verifying the child task's facts.

    The platform trusts its own records, not the callback's assertion: the
    parent's wait contract must name this child, and the child must be in a
    terminal state matching ``declared_state`` — otherwise the callback is
    rejected without consuming the wait token. A settled ``command_id``
    replays its receipt idempotently.
    """
    try:
        issued = await service.submit_workflow_callback(
            workspace_id=_workspace_id(ctx),
            task_id=task_id,
            command_id=body.command_id,
            wait_token=body.wait_token,
            child_task_id=body.child_task_id,
            declared_state=body.declared_state,
            payload={"result": body.result} if body.result else None,
            result_summary=body.result or None,
            issuer=str(ctx.user_id) if ctx.user_id is not None else "anonymous",
        )
    except Exception as exc:  # noqa: BLE001 — narrowed by _map_service_error
        raise _map_service_error(exc) from exc
    record = issued.record
    return {
        "command_id": record.command_id,
        "kind": record.kind.value,
        "state": record.state.value,
        "issued_at": record.issued_at,
        "issuer": record.issuer,
        "detail": issued.detail,
    }


@router.post("/{task_id}/commands", status_code=status.HTTP_201_CREATED)
async def issue_command(task_id: uuid.UUID, body: CommandRequest, service: ServiceDep, ctx: AuthDep) -> dict[str, Any]:
    """Record one control command and act on what the platform can enforce."""
    return await _issue(ctx, service, task_id, body.kind, body)


async def _issue(
    ctx: AuthContext,
    service: TaskControlService,
    task_id: uuid.UUID,
    kind: ControlCommandKind,
    body: CommandRequest,
) -> dict[str, Any]:
    if body.payload and not body.payload_schema_ref:
        raise _error(
            "PAYLOAD_WITHOUT_SCHEMA",
            "a non-empty command payload requires payload_schema_ref",
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    try:
        issued = await service.issue_command(
            workspace_id=_workspace_id(ctx),
            task_id=task_id,
            kind=kind,
            issuer=str(ctx.user_id) if ctx.user_id is not None else "anonymous",
            payload=body.payload,
            payload_schema_ref=body.payload_schema_ref,
            expected_revision=body.expected_revision,
            expires_at=body.expires_at,
        )
    except Exception as exc:  # noqa: BLE001 — narrowed by _map_service_error
        raise _map_service_error(exc) from exc
    record = issued.record
    return {
        "command_id": record.command_id,
        "kind": record.kind.value,
        "state": record.state.value,
        "issued_at": record.issued_at,
        "issuer": record.issuer,
        "task_ref": record.task_ref.to_dict(),
        "run_ref": record.run_ref.to_dict() if record.run_ref is not None else None,
        "expires_at": record.expires_at,
        "expected_revision": record.expected_revision,
        "detail": issued.detail,
    }


# --- runs & events -----------------------------------------------------------


@router.get("/{task_id}/runs")
async def list_task_runs(task_id: uuid.UUID, service: ServiceDep, ctx: AuthDep) -> dict[str, Any]:
    """All execution attempts of one task."""
    try:
        detail = await service.get_task_detail(_workspace_id(ctx), task_id)
    except Exception as exc:  # noqa: BLE001 — narrowed by _map_service_error
        raise _map_service_error(exc) from exc
    return {"items": detail["runs"]}


@runs_router.get("/{run_id}")
async def get_run(run_id: uuid.UUID, service: ServiceDep, ctx: AuthDep) -> dict[str, Any]:
    """Run detail with the platform-side projection."""
    try:
        return await service.get_run_detail(_workspace_id(ctx), run_id)
    except Exception as exc:  # noqa: BLE001 — narrowed by _map_service_error
        raise _map_service_error(exc) from exc


@runs_router.get("/{run_id}/events")
async def read_run_events(
    run_id: uuid.UUID,
    service: ServiceDep,
    ctx: AuthDep,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> dict[str, Any]:
    """Cursor-paginated event read; resume with ``next_cursor``."""
    try:
        page = await service.read_run_events(_workspace_id(ctx), run_id, cursor=cursor, limit=limit)
    except Exception as exc:  # noqa: BLE001 — narrowed by _map_service_error
        raise _map_service_error(exc) from exc
    return {
        "events": [envelope.to_dict() for envelope in page.events],
        "next_cursor": page.next_cursor,
        "has_more": page.has_more,
    }


@runs_router.get("/{run_id}/events/stream")
async def stream_run_events(
    run_id: uuid.UUID,
    service: ServiceDep,
    ctx: AuthDep,
    cursor: Annotated[str | None, Query()] = None,
) -> StreamingResponse:
    """SSE subscribe view over the durable run event stream."""
    workspace_id = _workspace_id(ctx)

    async def event_source():
        try:
            async for envelope in service.stream_run_events(workspace_id, run_id, cursor=cursor):
                yield f"data: {json.dumps(envelope.to_dict())}\n\n"
        except Exception as exc:  # noqa: BLE001 — narrowed by _map_service_error
            detail = _map_service_error(exc)
            yield f"event: error\ndata: {json.dumps({'code': detail.status_code})}\n\n"

    return StreamingResponse(event_source(), media_type="text/event-stream")


# --- command receipts & reconciliation (separate prefixes) --------------------


@commands_router.get("/{command_id}")
async def get_command(command_id: str, service: ServiceDep, ctx: AuthDep) -> dict[str, Any]:
    """Read one command receipt (lazy expiry on read)."""
    try:
        record = await service.get_command(_workspace_id(ctx), command_id)
    except Exception as exc:  # noqa: BLE001 — narrowed by _map_service_error
        raise _map_service_error(exc) from exc
    return {
        "command_id": record.command_id,
        "kind": record.kind.value,
        "state": record.state.value,
        "issued_at": record.issued_at,
        "issuer": record.issuer,
        "task_ref": record.task_ref.to_dict(),
        "run_ref": record.run_ref.to_dict() if record.run_ref is not None else None,
        "expires_at": record.expires_at,
        "expected_revision": record.expected_revision,
    }


@reconciliation_router.get("/pending")
async def pending_reconciliation(service: ServiceDep, ctx: AuthDep) -> dict[str, Any]:
    """Read-only view of unconverged facts in this workspace."""
    try:
        return await service.pending_reconciliation(_workspace_id(ctx))
    except Exception as exc:  # noqa: BLE001 — narrowed by _map_service_error
        raise _map_service_error(exc) from exc
