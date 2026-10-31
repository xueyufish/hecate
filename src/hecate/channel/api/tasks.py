"""Platform task control API (step6 platform track).

Task lifecycle surface over :class:`TaskControlService`:

- ``POST /api/tasks`` — idempotent submission; returns the persistent
  task/run references. ``wait=true`` is the synchronous wait view on the
  same Task/Run; otherwise the execution proceeds in-process (dev
  dispatch) and the client follows progress via the event views.
- ``GET /api/tasks`` / ``GET /api/tasks/{id}`` — workspace-scoped queries
  with the lifecycle projection.
- ``GET /api/runs/{run_id}/actions`` — scoped action-ledger summaries.
- ``GET /api/runs/{run_id}/action-evidence`` — one action's evidence
  receipt: the recorded facts, the digest that was checked and what the
  re-check found. Read-only; a matching digest proves content equality
  only, never that the business goal passed.
- ``GET /api/runs/{run_id}/events`` — cursor-paginated event reads;
  ``GET /api/runs/{run_id}/events/stream`` — the SSE subscribe view on
  the same stream (resume by cursor after a disconnect). An established
  stream keeps re-verifying the caller's identity, VIEWER floor, and
  run/task visibility on a bounded interval (idle streams included) and
  ends with a code-only ``event: error`` frame once revoked.
- ``POST /api/tasks/{id}/commands`` / ``POST /api/tasks/{id}/cancel`` /
  ``GET /api/commands/{command_id}`` — control-command receipts. An HTTP
  success here records a request; it never claims the command was
  applied.
- ``POST /api/tasks/{id}/workflow-callback`` — the verified workflow
  child-outcome callback. Same two-level write gate as the command
  entries (parent visibility, then the server-side EDITOR floor) ahead
  of any replay, verification or receipt write; trusted internal
  callers consume the service directly without this REST role gate.
- ``GET /api/tasks/{id}/commands`` — read-only paged receipts of one
  task's commands, recorded states verbatim (no lazy expiry here).
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
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.contracts.execution.durable import ControlCommandKind
from hecate.core.auth_context import AuthContext
from hecate.core.composition.durable_platform import get_durable_suite
from hecate.core.deps import get_db
from hecate.core.deps_workspace import (
    WorkspaceRole,
    WorkspaceRoleRequiredError,
    ensure_workspace_role,
    get_auth_context,
    security_scheme,
)

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
    command_id: str | None = Field(default=None, min_length=1, max_length=256)
    payload: dict[str, Any] | None = None
    payload_schema_ref: str | None = None
    expected_revision: int | None = None
    expires_at: str | None = None
    detail_ns: dict[str, Any] = Field(default_factory=dict)


class ApprovalDecisionRequest(BaseModel):
    """One human approval decision bound to the entire observed wait."""

    operation_id: str = Field(min_length=1, max_length=200)
    approval_ref: str = Field(min_length=1, max_length=128)
    decision: Literal["approve", "reject"]
    reason: str = Field(default="", max_length=2000)
    wait_token: str = Field(min_length=1, max_length=256)
    expected_revision: int = Field(ge=0)


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


def unbind_worker_state(state: Any) -> None:
    """Unbind a stopped application's worker without disturbing another app."""
    global _worker_state
    if _worker_state is state:
        _worker_state = None


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
    # Write gate before any submission work: workspace requirement (403
    # NO_WORKSPACE, unchanged), then the server-side EDITOR floor —
    # ahead of business validation, idempotent replay, registration and
    # dispatch. Request bodies carry no role; the identity comes from
    # the AuthContext only, so an existing idempotency key cannot
    # bypass the check after a downgrade.
    workspace_id = _workspace_id(ctx)
    try:
        ensure_workspace_role(ctx, WorkspaceRole.EDITOR)
    except WorkspaceRoleRequiredError as exc:
        raise _error("FORBIDDEN", exc.message, status.HTTP_403_FORBIDDEN) from exc
    try:
        result = await service.submit(
            workspace_id=workspace_id,
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


async def _task_read_gate(
    ctx: AuthContext,
    service: TaskControlService,
    task_id: uuid.UUID | None,
) -> uuid.UUID:
    """Read gate shared by the task observation entries.

    Workspace requirement first (403 NO_WORKSPACE, unchanged), then — for
    the task-scoped entries — visibility (foreign, missing and
    soft-deleted tasks are indistinguishable) before the server-side
    VIEWER floor. Everything runs before any list/detail business query
    or durable-state read.
    """
    workspace_id = _workspace_id(ctx)
    if task_id is not None:
        try:
            await service.assert_task_visible(workspace_id=workspace_id, task_id=task_id)
        except Exception as exc:  # noqa: BLE001 — narrowed by _map_service_error
            raise _map_service_error(exc) from exc
    try:
        ensure_workspace_role(ctx, WorkspaceRole.VIEWER)
    except WorkspaceRoleRequiredError as exc:
        raise _error("FORBIDDEN", exc.message, status.HTTP_403_FORBIDDEN) from exc
    return workspace_id


@router.get("")
async def list_tasks(
    service: ServiceDep,
    ctx: AuthDep,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    lifecycle_state: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    """Workspace-scoped task list with lifecycle projection."""
    workspace_id = await _task_read_gate(ctx, service, None)
    try:
        items, total = await service.list_tasks(workspace_id, state=lifecycle_state, page=page, page_size=page_size)
    except Exception as exc:  # noqa: BLE001 — narrowed by _map_service_error
        raise _map_service_error(exc) from exc
    return {"items": items, "total": total}


@router.get("/{task_id}")
async def get_task(task_id: uuid.UUID, service: ServiceDep, ctx: AuthDep) -> dict[str, Any]:
    """Task detail: responsibility record, lifecycle projection, runs."""
    workspace_id = await _task_read_gate(ctx, service, task_id)
    try:
        return await service.get_task_detail(workspace_id, task_id)
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


@router.get("/{task_id}/approval")
async def get_task_approval(task_id: uuid.UUID, service: ServiceDep, ctx: AuthDep) -> dict[str, Any]:
    """The whitelisted approval view: wait summary, decision facts, resume receipt.

    Observation only — reading never issues a resume. Visibility follows the
    shared task read gate (foreign, missing and soft-deleted tasks are
    indistinguishable).
    """
    workspace_id = await _task_read_gate(ctx, service, task_id)
    from hecate.execution.approval_decision import ApprovalDecisionService

    return await ApprovalDecisionService.of(service).get_approval_view(
        workspace_id,
        task_id,
        decided_by=str(ctx.user_id),
        can_admin=ctx.role is WorkspaceRole.ADMIN and not ctx.is_system_scope,
    )


@router.post("/{task_id}/approval")
async def submit_task_approval(
    task_id: uuid.UUID, body: ApprovalDecisionRequest, service: ServiceDep, ctx: AuthDep
) -> dict[str, Any]:
    """Record one human approval decision and drive the resume phase.

    Decision authority requires the server-side ADMIN role — an EDITOR's
    resume permission never manufactures an approval fact — and the
    decider identity comes from the AuthContext, never the request body.
    The self-approval ban and the approval-reference binding are enforced
    in the service, before any authority query.
    """
    from hecate.execution.approval_decision import ApprovalDecisionError, ApprovalDecisionService

    workspace_id = _workspace_id(ctx)
    try:
        await service.assert_task_visible(workspace_id=workspace_id, task_id=task_id)
    except Exception as exc:  # noqa: BLE001 — narrowed by _map_service_error
        raise _map_service_error(exc) from exc
    try:
        if ctx.is_system_scope or ctx.role is not WorkspaceRole.ADMIN:
            raise _error("FORBIDDEN", "a workspace ADMIN human identity is required", status.HTTP_403_FORBIDDEN)
        ensure_workspace_role(ctx, WorkspaceRole.ADMIN)
    except WorkspaceRoleRequiredError as exc:
        raise _error("FORBIDDEN", exc.message, status.HTTP_403_FORBIDDEN) from exc
    decided_by = str(ctx.user_id) if ctx.user_id is not None else ""
    if not decided_by:
        raise _error(
            "IDENTITY_REQUIRED",
            "approval decisions require an authenticated identity",
            status.HTTP_403_FORBIDDEN,
        )
    try:
        return await ApprovalDecisionService.of(service).submit_decision(
            workspace_id,
            task_id,
            operation_id=body.operation_id,
            approval_ref=body.approval_ref,
            decision=body.decision,
            reason=body.reason,
            wait_token=body.wait_token,
            expected_revision=body.expected_revision,
            decided_by=decided_by,
        )
    except ApprovalDecisionError as exc:
        http_status = {
            "SELF_APPROVAL": status.HTTP_403_FORBIDDEN,
            "REVISION_CONFLICT": status.HTTP_409_CONFLICT,
            "WAIT_EXPIRED": status.HTTP_409_CONFLICT,
            "NOT_WAITING_APPROVAL": status.HTTP_409_CONFLICT,
            "ALREADY_DECIDED": status.HTTP_409_CONFLICT,
            "OPERATION_CONFLICT": status.HTTP_409_CONFLICT,
            "OPERATION_BOUND_ELSEWHERE": status.HTTP_409_CONFLICT,
            "OPERATION_RACE": status.HTTP_409_CONFLICT,
            "INITIATOR_UNPARSEABLE": status.HTTP_409_CONFLICT,
        }.get(exc.code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        raise _error(exc.code, exc.message, http_status) from exc
    except Exception as exc:  # noqa: BLE001 — narrowed by _map_service_error
        raise _map_service_error(exc) from exc


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
    # Write gate before any callback replay, verification or receipt write:
    # workspace requirement, then parent visibility (foreign, missing and
    # soft-deleted tasks are indistinguishable), then the server-side EDITOR
    # floor. The wait token and a known command_id are facts about the task,
    # never substitutes for write permission; trusted internal callers reach
    # the service directly and do not pass through this role gate.
    workspace_id = _workspace_id(ctx)
    try:
        await service.assert_task_visible(workspace_id=workspace_id, task_id=task_id)
    except Exception as exc:  # noqa: BLE001 — narrowed by _map_service_error
        raise _map_service_error(exc) from exc
    try:
        ensure_workspace_role(ctx, WorkspaceRole.EDITOR)
    except WorkspaceRoleRequiredError as exc:
        raise _error("FORBIDDEN", exc.message, status.HTTP_403_FORBIDDEN) from exc
    try:
        issued = await service.submit_workflow_callback(
            workspace_id=workspace_id,
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


def _command_summary(record: Any) -> dict[str, Any]:
    """Use the shared whitelist for the existing single-command response."""
    from hecate.execution.task_control import command_receipt_summary

    return command_receipt_summary(record)


@router.get("/{task_id}/commands")
async def list_task_commands(
    task_id: uuid.UUID,
    service: ServiceDep,
    ctx: AuthDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    """Read-only page of one task's command receipts, newest first.

    Recorded states verbatim — the list never advances a command, never
    triggers execution and never performs the single-read lazy expiry.
    The shared task read gate (visibility, then VIEWER) runs first.
    """
    workspace_id = await _task_read_gate(ctx, service, task_id)
    try:
        return await service.list_task_commands(workspace_id, task_id, limit=limit, offset=offset)
    except Exception as exc:  # noqa: BLE001 — narrowed by _map_service_error
        raise _map_service_error(exc) from exc


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
    workspace_id = _workspace_id(ctx)
    # Write gate before any command storage access or idempotent replay:
    # visibility first (foreign, missing and soft-deleted tasks are
    # indistinguishable), then the server-side EDITOR role floor. Request
    # bodies carry no role — the identity comes from the AuthContext only.
    try:
        await service.assert_task_visible(workspace_id=workspace_id, task_id=task_id)
    except Exception as exc:  # noqa: BLE001 — narrowed by _map_service_error
        raise _map_service_error(exc) from exc
    try:
        ensure_workspace_role(ctx, WorkspaceRole.EDITOR)
    except WorkspaceRoleRequiredError as exc:
        raise _error("FORBIDDEN", exc.message, status.HTTP_403_FORBIDDEN) from exc
    from hecate.execution.approval_decision import APPROVAL_DECISION_RESUME_PREFIX

    if body.command_id and body.command_id.startswith(APPROVAL_DECISION_RESUME_PREFIX):
        raise _error(
            "RESERVED_COMMAND_ID", "approval resume ids are server-owned", status.HTTP_422_UNPROCESSABLE_ENTITY
        )
    try:
        issued = await service.issue_command(
            workspace_id=workspace_id,
            task_id=task_id,
            kind=kind,
            command_id=body.command_id,
            issuer=str(ctx.user_id) if ctx.user_id is not None else "anonymous",
            payload=body.payload,
            payload_schema_ref=body.payload_schema_ref,
            expected_revision=body.expected_revision,
            expires_at=body.expires_at,
            detail_ns=body.detail_ns,
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
    workspace_id = await _task_read_gate(ctx, service, task_id)
    try:
        detail = await service.get_task_detail(workspace_id, task_id)
    except Exception as exc:  # noqa: BLE001 — narrowed by _map_service_error
        raise _map_service_error(exc) from exc
    return {"items": detail["runs"]}


async def _run_read_gate(ctx: AuthContext, service: TaskControlService, run_id: uuid.UUID) -> uuid.UUID:
    """Read gate shared by the four run observation entries.

    Visibility first (foreign, missing and soft-deleted runs — and their
    parent task — are indistinguishable), then the server-side VIEWER
    floor. Everything runs before any ledger/event access, relay
    projection or stream creation.
    """
    workspace_id = _workspace_id(ctx)
    try:
        await service.assert_run_observable(workspace_id=workspace_id, run_id=run_id)
    except Exception as exc:  # noqa: BLE001 — narrowed by _map_service_error
        raise _map_service_error(exc) from exc
    try:
        ensure_workspace_role(ctx, WorkspaceRole.VIEWER)
    except WorkspaceRoleRequiredError as exc:
        raise _error("FORBIDDEN", exc.message, status.HTTP_403_FORBIDDEN) from exc
    return workspace_id


@runs_router.get("/{run_id}")
async def get_run(run_id: uuid.UUID, service: ServiceDep, ctx: AuthDep) -> dict[str, Any]:
    """Run detail with the platform-side projection."""
    workspace_id = await _run_read_gate(ctx, service, run_id)
    try:
        return await service.get_run_detail(workspace_id, run_id)
    except Exception as exc:  # noqa: BLE001 — narrowed by _map_service_error
        raise _map_service_error(exc) from exc


@runs_router.get("/{run_id}/actions")
async def list_run_actions(run_id: uuid.UUID, service: ServiceDep, ctx: AuthDep) -> dict[str, Any]:
    """Whitelisted action-ledger records for one run (read-only, any attempt)."""
    workspace_id = await _run_read_gate(ctx, service, run_id)
    try:
        return await service.list_run_actions(workspace_id, run_id)
    except Exception as exc:  # noqa: BLE001 — narrowed by _map_service_error
        raise _map_service_error(exc) from exc


@runs_router.get("/{run_id}/action-evidence")
async def get_run_action_evidence(
    run_id: uuid.UUID,
    service: ServiceDep,
    ctx: AuthDep,
    action_key: Annotated[str, Query(min_length=1, max_length=512)],
) -> dict[str, Any]:
    """One action's evidence receipt (read-only).

    The action key travels as a query parameter: it is a caller-defined
    ledger key that may contain characters a path segment cannot carry.
    The receipt reports recorded facts and what can be re-verified now; a
    matching digest proves content equality only, never that the business
    goal passed.
    """
    workspace_id = await _run_read_gate(ctx, service, run_id)
    try:
        return await service.get_run_action_evidence(workspace_id, run_id, action_key)
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
    workspace_id = await _run_read_gate(ctx, service, run_id)
    try:
        page = await service.read_run_events(workspace_id, run_id, cursor=cursor, limit=limit)
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
    db: Annotated[AsyncSession, Depends(get_db)],
    cursor: Annotated[str | None, Query()] = None,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security_scheme)] = None,
) -> StreamingResponse:
    """SSE subscribe view over the durable run event stream.

    The gate completes before the stream is created: authorization
    failures surface as normal HTTP 403/404, never as 200 + error frames.
    Once established, the stream keeps honoring the caller's current
    access facts: the original Bearer credential is replayed through the
    shared auth chain and the run/task visibility is re-derived on a
    short-lived session at least once per recheck interval — including on
    idle streams — before each event read and relay projection, and
    before relaying a batch the loop already obtained. A revocation
    (expired or revoked credential, removed membership, role below
    viewer, deleted or invisible run/task) ends the stream with the
    ``event: error`` frame carrying only the mapped status code
    (401/403/404; 500 when the check itself fails); already buffered
    envelopes are dropped instead of relayed. Closing the observation
    never cancels or wakes the executing run.
    """
    workspace_id = await _run_read_gate(ctx, service, run_id)
    # API-key authentication flushes last_used_at. Release that request
    # transaction before a fresh recheck tries to touch the same key;
    # otherwise the long-lived stream holds its own verifier's row lock.
    await db.commit()
    token = credentials.credentials if credentials is not None else None

    async def event_source():
        from hecate.execution.run_stream_guard import (
            RECHECK_INTERVAL_SECONDS,
            PeriodicAccessGuard,
            RunStreamAccessError,
            recheck_stream_access,
        )

        async def recheck() -> None:
            await recheck_stream_access(token=token, bound_ctx=ctx, workspace_id=workspace_id, run_id=run_id)

        guard = PeriodicAccessGuard(recheck, RECHECK_INTERVAL_SECONDS)
        stream = service.stream_run_events(workspace_id, run_id, cursor=cursor, access_guard=guard)
        try:
            async for envelope in stream:
                yield f"data: {json.dumps(envelope.to_dict())}\n\n"
        except RunStreamAccessError as exc:
            # Revocation: a code-only frame; never the reason or any
            # buffered-but-unrelayed content.
            yield f"event: error\ndata: {json.dumps({'code': exc.status_code})}\n\n"
        except Exception as exc:  # noqa: BLE001 — narrowed by _map_service_error
            detail = _map_service_error(exc)
            yield f"event: error\ndata: {json.dumps({'code': detail.status_code})}\n\n"
        finally:
            # Client disconnect or terminal end: close the upstream
            # generator so nothing keeps polling. Closing the observation
            # never touches the executing run itself.
            await stream.aclose()

    return StreamingResponse(event_source(), media_type="text/event-stream")


# --- command receipts & reconciliation (separate prefixes) --------------------


@commands_router.get("/{command_id}")
async def get_command(command_id: str, service: ServiceDep, ctx: AuthDep) -> dict[str, Any]:
    """Read one command receipt (lazy expiry on read).

    Visibility precedes the role floor via a non-advancing lookup, so a
    rejected caller neither distinguishes missing from foreign commands
    nor triggers the lazy expiry; authorized reads keep it verbatim.
    """
    workspace_id = _workspace_id(ctx)
    try:
        await service.assert_command_visible(workspace_id=workspace_id, command_id=command_id)
    except Exception as exc:  # noqa: BLE001 — narrowed by _map_service_error
        raise _map_service_error(exc) from exc
    try:
        ensure_workspace_role(ctx, WorkspaceRole.VIEWER)
    except WorkspaceRoleRequiredError as exc:
        raise _error("FORBIDDEN", exc.message, status.HTTP_403_FORBIDDEN) from exc
    try:
        record = await service.get_command(workspace_id, command_id)
    except Exception as exc:  # noqa: BLE001 — narrowed by _map_service_error
        raise _map_service_error(exc) from exc
    return _command_summary(record)


@reconciliation_router.get("/pending")
async def pending_reconciliation(service: ServiceDep, ctx: AuthDep) -> dict[str, Any]:
    """Read-only view of unconverged facts in this workspace.

    The VIEWER floor runs before the service query, so rejected callers
    never trigger the lazy expiry sweep or the relay status read.
    """
    workspace_id = _workspace_id(ctx)
    try:
        ensure_workspace_role(ctx, WorkspaceRole.VIEWER)
    except WorkspaceRoleRequiredError as exc:
        raise _error("FORBIDDEN", exc.message, status.HTTP_403_FORBIDDEN) from exc
    try:
        return await service.pending_reconciliation(workspace_id)
    except Exception as exc:  # noqa: BLE001 — narrowed by _map_service_error
        raise _map_service_error(exc) from exc
