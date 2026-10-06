"""Durable execution wiring for the standalone host (step6 local half).

The host consumes ``hecate-durable`` directly: submissions become persistent
tasks under idempotency keys bound to the server-verified identity, every
tool dispatch flows through the persistent action ledger (intent → atomic
claim → real outcome), cancel lands as an independent command receipt, and
the governance event log serves cursor reads that survive restarts.

Recovery is replay-based: after a restart, non-terminal tasks are re-driven
through the fixed graph while the ledger gates every action — a succeeded
action backfills its recorded real result (no duplicate business write), a
claimed write safely stops and the task converges to
``reconciliation_required``. That is the SC03 persistence/restart half; the
approval-binding half stays with step7.

``RunnerLedgerHook`` implements the kernel's ``ActionLedgerHook`` so the
runner path and the platform path share one decision table
(``recovery_decision``). The sync SQL store is offloaded via
``asyncio.to_thread`` so the host loop never blocks on the database.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from hecate_durable.contracts.durable import (
    CommandState,
    ControlCommandKind,
    ControlCommandRecord,
    IdempotencyKey,
    SubmissionAssociation,
    TaskLifecycleState,
    TaskStateRecord,
    canonical_request_digest,
)
from hecate_durable.contracts.events import EventEnvelope, EventSource
from hecate_durable.contracts.references import BackendRef, RefKind
from hecate_durable.contracts.tools import ToolSideEffectClass
from hecate_durable.runtime_hook import SqlActionLedgerHook as RunnerLedgerHook
from hecate_durable.storage import SqlDurableStore
from hecate_durable.storage.eventlog import EventPage, SqlEventLog, build_envelope
from hecate_runtime.action_ledger import (
    ToolExecutionResolution,
    ToolExecutionState,
    recovery_decision,
    tool_arguments_digest,
)
from hecate_runtime.tool_side_effects import SideEffectClass

logger = logging.getLogger(__name__)

ISSUER = "standalone-host"

# Lifecycle states that still carry work after a restart.
_NON_TERMINAL = (
    TaskLifecycleState.QUEUED,
    TaskLifecycleState.RUNNING,
    TaskLifecycleState.WAITING_INPUT,
    TaskLifecycleState.WAITING_APPROVAL,
    TaskLifecycleState.RECONCILIATION_REQUIRED,
)


def action_key_for(run_id: str, tool_name: str) -> str:
    """Stable action key across restarts (run identity + tool identity)."""

    return f"{run_id}:{tool_name}"


@dataclass(frozen=True)
class DurableSubmission:
    """What ``DurableRuntime.submit`` hands back to the engine/server."""

    association: SubmissionAssociation
    replayed: bool  # same idempotency key already registered before


def _stringify(payload: Any) -> str:
    import json

    if isinstance(payload, str):
        return payload
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


class DurableRuntime:
    """The host's durable execution facade over :class:`SqlDurableStore`."""

    def __init__(self, store: SqlDurableStore, *, workspace: str) -> None:
        self.store = store
        self.workspace = workspace
        self.execution_events = SqlEventLog(
            store.session_factory,
            source=EventSource.EXECUTION_BACKEND.value,
            actor_id="hecate-runner",
            clock=lambda: datetime.now(tz=UTC).isoformat(),
        )

    # -- submission / lifecycle ------------------------------------------------

    def submit(
        self,
        *,
        principal: str,
        run_input: dict,
        idempotency_key: str | None,
        domains: tuple[str, ...] | None = None,
    ) -> DurableSubmission:
        """Register a submission; same key + body returns the original task."""

        task_id = f"task-{uuid.uuid4()}"
        run_id = f"run-{uuid.uuid4()}"
        key = IdempotencyKey(
            key=idempotency_key or f"auto-{uuid.uuid4()}",
            subject=principal,
            workspace=self.workspace,
            request_digest=canonical_request_digest(run_input),
        )
        association = self.store.submit_task(
            key=key,
            task_ref=BackendRef(kind=RefKind.TASK, issuer_domain=ISSUER, id=task_id),
            run_ref=BackendRef(kind=RefKind.RUN, issuer_domain=ISSUER, id=run_id),
            input_payload={
                **run_input,
                "_host_identity": {"principal": principal, "domains": list(domains) if domains is not None else None},
            },
        )
        replayed = association.run_ref.id != run_id
        return DurableSubmission(association=association, replayed=replayed)

    def begin_run(self, task_ref: BackendRef) -> None:
        self.store.apply_task_state(task_ref, TaskLifecycleState.RUNNING)

    def finish_run(
        self,
        task_ref: BackendRef,
        outcome: str,
        *,
        needs_reconciliation: bool = False,
        error: str | None = None,
    ) -> TaskStateRecord:
        """Converge a task; withheld actions land in ``reconciliation_required``.

        Non-reconciliation targets commit a ``run_terminal`` governance event
        in the same transaction as the state change, carrying the honest
        outcome (status + error); that event is what the platform projection
        folds into the managed Run's terminal state.
        """

        if needs_reconciliation:
            return self.store.apply_task_state(task_ref, TaskLifecycleState.RECONCILIATION_REQUIRED)
        target = {
            "succeeded": TaskLifecycleState.SUCCEEDED,
            "failed": TaskLifecycleState.FAILED,
            "cancelled": TaskLifecycleState.CANCELLED,
            "unknown": TaskLifecycleState.RECONCILIATION_REQUIRED,
        }[outcome]
        terminal_payload = (
            {"status": outcome, "error": error} if target is not TaskLifecycleState.RECONCILIATION_REQUIRED else None
        )
        return self.store.apply_task_state(task_ref, target, terminal_payload=terminal_payload)

    # -- persistent waiting (step6c) ----------------------------------------------

    def park_wait(
        self,
        task_ref: BackendRef,
        run_ref: BackendRef,
        *,
        wake_kind: str,
        contract_ref: dict[str, Any],
        expires_at: str | None = None,
    ) -> str:
        """Park a running task into a persistent wait; returns the wake token.

        The wait record rides the state transition's transaction (the same
        shape the platform dispatcher parks): wake kind, contract reference,
        one-time token, deadline, and the unconsumed flag. The task's run
        linkage stays on the waiting attempt until a wake rebinds a new one.
        """

        token = uuid.uuid4().hex
        target = (
            TaskLifecycleState.WAITING_INPUT
            if wake_kind == ControlCommandKind.PROVIDE_INPUT.value
            else TaskLifecycleState.WAITING_APPROVAL
        )
        self.store.apply_task_state(
            task_ref,
            target,
            event_run_ref=run_ref,
            extra_update={
                "wait": {
                    "wake_kind": wake_kind,
                    "contract_ref": contract_ref,
                    "wait_token": token,
                    "wait_expires_at": expires_at,
                    "consumed": False,
                }
            },
        )
        return token

    def apply_wake(
        self,
        *,
        command_id: str,
        kind: str,
        issuer: str,
        task_ref: BackendRef,
        wait_token: str,
        input_payload: dict[str, Any] | None = None,
        expires_at: str | None = None,
    ) -> tuple[ControlCommandRecord, str | None]:
        """Record and atomically apply one wake command (provide_input/resume).

        Returns ``(receipt, rejection_reason)`` — the reason is ``None`` for
        applied and idempotent replays. Idempotent by ``command_id``: a
        terminal receipt replays as-is; a recorded-but-unapplied command
        resumes its application. Validation failures move the receipt to
        ``rejected`` with the reason on the caller — the wait record is
        untouched and nothing dispatches. A valid application consumes the
        wait token, merges the provided input into the task's stored input,
        requeues the task on a NEW attempt run, and flips the command to
        ``applied`` — all in the store's single transaction.
        """

        wake_kind = (
            ControlCommandKind.PROVIDE_INPUT
            if kind == ControlCommandKind.PROVIDE_INPUT.value
            else ControlCommandKind.RESUME
        )
        existing = self.store.get(command_id)
        if existing is not None:
            if (existing.kind, existing.task_ref) != (wake_kind, task_ref):
                raise ValueError("command ID is already bound to another command")
            if existing.state is CommandState.APPLIED:
                return existing, None
            if existing.state is CommandState.REJECTED:
                return existing, "previously rejected"
        else:
            has_payload = input_payload is not None
            self.store.record(
                ControlCommandRecord(
                    command_id=command_id,
                    kind=wake_kind,
                    issuer=issuer,
                    task_ref=task_ref,
                    issued_at=_now_iso(),
                    state=CommandState.REQUESTED,
                    expires_at=expires_at,
                    payload={"input": input_payload} if has_payload else {},
                    payload_schema_ref=("urn:hecate:runner:wake-input/0" if has_payload else None),
                )
            )

        def _reject(reason: str) -> tuple[ControlCommandRecord, str | None]:
            logger.info("wake command %s rejected: %s", command_id, reason)
            return self.store.transition(command_id, CommandState.REJECTED), reason

        record = self.store.get_task_state(task_ref)
        if record is None or record.lifecycle_state not in (
            TaskLifecycleState.WAITING_INPUT,
            TaskLifecycleState.WAITING_APPROVAL,
        ):
            return _reject("task is not in a persistent wait")
        expected_state = (
            TaskLifecycleState.WAITING_INPUT
            if wake_kind is ControlCommandKind.PROVIDE_INPUT
            else TaskLifecycleState.WAITING_APPROVAL
        )
        if record.lifecycle_state is not expected_state:
            return _reject(f"task waits for {record.lifecycle_state.value}, not {wake_kind.value}")
        wait = (record.extra or {}).get("wait") or {}
        if wait.get("consumed"):
            return _reject("wait token already consumed")
        if wait.get("wait_token") != wait_token:
            return _reject("wait token mismatch")
        wait_deadline = wait.get("wait_expires_at")
        if wait_deadline is not None:
            from datetime import datetime as _dt

            deadline = _dt.fromisoformat(wait_deadline)
            if deadline.tzinfo is None:
                deadline = deadline.replace(tzinfo=__import__("datetime").UTC)
            if deadline <= _dt.now(__import__("datetime").UTC):
                return _reject("wait expired")

        persisted = self.store.get_task_input(task_ref) or {}
        wait = (record.extra or {}).get("wait") or {}
        contract_ref = wait.get("contract_ref") or {}
        merged = {**persisted, "provided": input_payload} if input_payload is not None else dict(persisted)
        # One-shot dispatch grant for the parked tool: the wake decision is
        # the recorded approval; the new attempt's dispatch boundary
        # consumes it instead of re-parking (the ledger claim for the old
        # attempt stays claimed; the new attempt claims its own key).
        merged["_wake_grant"] = {
            "tool": contract_ref.get("tool"),
            "arguments_digest": contract_ref.get("arguments_digest"),
        }
        new_run = BackendRef(RefKind.RUN, ISSUER, f"run-{uuid.uuid4()}")
        try:
            # One transaction: requeue on the new attempt run, merge the
            # provided input, consume the wait token, and flip the command
            # to applied (the store validates the command's expiry and task
            # binding inside the same commit).
            self.store.apply_task_state(
                task_ref,
                TaskLifecycleState.QUEUED,
                input_payload=merged,
                event_run_ref=new_run,
                applied_command_id=command_id,
                extra_update={"wait": {**wait, "consumed": True}},
            )
        except Exception as exc:  # expired command / stale revision in the store
            return _reject(str(exc))
        receipt = self.store.get(command_id)
        if receipt is None:  # pragma: no cover - recorded above in this call
            raise ValueError(f"wake command {command_id} vanished after apply")
        return receipt, None

    def wait_of(self, task_ref: BackendRef) -> dict[str, Any] | None:
        """The authorized view of a task's wait record (None when not waiting)."""

        record = self.store.get_task_state(task_ref)
        if record is None:
            return None
        wait = (record.extra or {}).get("wait")
        return dict(wait) if wait else None

    # -- control commands --------------------------------------------------------

    def record_cancel(
        self, *, command_id: str, issuer: str, task_ref: BackendRef, run_ref: BackendRef
    ) -> ControlCommandRecord:
        """Record one cancellation intent; retries preserve the original receipt."""

        existing = self.store.get(command_id)
        if existing is not None:
            if (existing.kind, existing.issuer, existing.task_ref, existing.run_ref) != (
                ControlCommandKind.CANCEL,
                issuer,
                task_ref,
                run_ref,
            ):
                raise ValueError("command ID is already bound to another cancellation")
            return existing
        return self.store.record(
            ControlCommandRecord(
                command_id=command_id,
                kind=ControlCommandKind.CANCEL,
                issuer=issuer,
                task_ref=task_ref,
                issued_at=_now_iso(),
                state=CommandState.REQUESTED,
                run_ref=run_ref,
            )
        )

    def cancel_applied(self, command_id: str) -> None:
        self.store.transition(command_id, CommandState.APPLIED)

    def cancel_rejected(self, command_id: str) -> None:
        self.store.transition(command_id, CommandState.REJECTED)

    # -- recovery ------------------------------------------------------------------

    def pending_tasks(self) -> list[tuple[Any, dict[str, Any] | None]]:
        """Non-terminal tasks with their replay input, oldest first."""

        records = self.store.list_tasks()
        pending = [r for r in records if r.lifecycle_state in _NON_TERMINAL]
        return [(r, self.store.get_task_input(r.task_ref)) for r in pending]

    def run_actions(self, run_ref: BackendRef) -> list[dict[str, Any]]:
        return self.store.list_run_actions(run_ref)

    def read_events(self, run_ref: BackendRef, *, cursor: int = 0, limit: int = 100):
        return self.store.read_events(run_ref, cursor=cursor, limit=limit)

    def emit_execution_event(
        self,
        *,
        task_ref: BackendRef,
        run_ref: BackendRef,
        source_sequence: int,
        event_type: str,
        payload: dict[str, Any],
    ) -> EventEnvelope:
        envelope = build_envelope(
            task_ref=task_ref,
            run_ref=run_ref,
            source=EventSource.EXECUTION_BACKEND,
            source_sequence=source_sequence,
            event_type=event_type,
            payload=payload,
            occurred_at=datetime.now(tz=UTC).isoformat(),
            actor_id="hecate-runner",
            event_id=f"execution-{run_ref.id}-{source_sequence}",
        )
        self.execution_events.append(envelope)
        return envelope

    def read_execution_events(self, run_ref: BackendRef, *, cursor: int = 0, limit: int = 100) -> EventPage:
        return self.execution_events.read(run_ref, cursor=cursor, limit=limit)

    def task_state(self, task_id: str):
        return self.store.get_task_state(BackendRef(kind=RefKind.TASK, issuer_domain=ISSUER, id=task_id))

    def association_for_run(self, run_ref: BackendRef) -> SubmissionAssociation | None:
        return self.store.submission_for_run(run_ref)

    def hook_for(self, task_ref: BackendRef, run_ref: BackendRef) -> RunnerLedgerHook:
        return RunnerLedgerHook(self.store, task_ref=task_ref, run_ref=run_ref)


def _now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


async def gate_dispatch_async(
    hook: RunnerLedgerHook,
    *,
    run_id: str,
    tool_name: str,
    arguments: dict,
    side_effect_class: ToolSideEffectClass,
) -> tuple[dict | None, dict | None]:
    """Ledger gate for one dispatch: (pre-decision, claim verdict outcome).

    Returns ``(withheld, claimed)``:

    - ``withheld`` is a terminal outcome dict when recovery blocks dispatch
      (backfilled real result, review marker, conflict) — the business API
      is not called;
    - when ``withheld`` is ``None``, ``claimed`` is ``None`` on success or a
      withheld outcome when the atomic claim was lost or its ledger write
      failed for a side-effecting class.
    """

    execution_id = action_key_for(run_id, tool_name)
    digest = tool_arguments_digest(arguments)
    resolution = await hook.resolve(session_id=run_id, execution_id=execution_id)
    decision = recovery_decision(
        resolution,
        tc_id=execution_id,
        tool_name=tool_name,
        arguments_digest=digest,
        classification=_kernel_class(side_effect_class),
        messages=[],
        recorded_content=resolution.result_content,
    )
    if decision is not None:
        return _outcome_from_decision(decision), None
    try:
        verdict = await hook.record_claim(
            session_id=run_id,
            execution_id=execution_id,
            tool_call_id=execution_id,
            tool_name=tool_name,
            arguments=arguments,
            arguments_digest=digest,
            side_effect_class=side_effect_class.value,
        )
    except Exception:
        if side_effect_class is ToolSideEffectClass.READONLY:
            return None, None
        detail = "[withheld] action ledger unavailable; side-effecting execution withheld"
        return None, {"status": "store_unavailable", "detail": detail}
    if verdict.conflict is not None:
        return None, {"status": "conflict", "detail": verdict.conflict}
    if verdict.claimed:
        return None, None
    loser = recovery_decision(
        verdict.resolution or ToolExecutionResolution(state=ToolExecutionState.NEVER_STARTED),
        tc_id=execution_id,
        tool_name=tool_name,
        arguments_digest=digest,
        classification=_kernel_class(side_effect_class),
        messages=[],
    )
    if loser is None:
        return None, None
    return None, _outcome_from_decision(loser)


def _kernel_class(effect: ToolSideEffectClass) -> SideEffectClass:
    return SideEffectClass(effect.value)


def _outcome_from_decision(decision: dict) -> dict:
    content = decision.get("content", "")
    status = "withheld"
    if content.startswith("[conflict]"):
        status = "conflict"
    elif "[reconciliation required]" in content:
        status = "reconciliation_required"
    elif "needs review" in content or "withheld" in content:
        status = "needs_review"
    elif not decision.get("is_error", False):
        status = "ok"
        return {"status": "ok", "result": content}
    return {"status": status, "detail": content}


async def record_outcome_async(
    hook: RunnerLedgerHook,
    *,
    run_id: str,
    tool_name: str,
    arguments: dict,
    outcome: dict,
) -> None:
    """Mirror one dispatch outcome into the ledger (status → outcome class)."""

    execution_id = action_key_for(run_id, tool_name)
    digest = tool_arguments_digest(arguments)
    status = outcome.get("status")
    if status == "ok":
        try:
            await hook.record_outcome(
                session_id=run_id,
                execution_id=execution_id,
                tool_call_id=execution_id,
                tool_name=tool_name,
                arguments_digest=digest,
                status="succeeded",
                result=outcome.get("result", outcome),
            )
        except Exception:
            # The side effect already happened; the hook flagged the failed
            # write and the action stays claimed — recovery fails closed.
            logger.debug("Ledger outcome mirror failed for %s", execution_id, exc_info=True)
        return
    ledger_status = {
        "unknown": "unknown",
        "store_unavailable": "unknown",
        "conflict": "failed",
        "needs_review": "failed",
        "reconciliation_required": "unknown",
    }.get(str(status), "failed")
    detail = outcome.get("detail") or outcome.get("result")
    try:
        await hook.record_outcome(
            session_id=run_id,
            execution_id=execution_id,
            tool_call_id=execution_id,
            tool_name=tool_name,
            arguments_digest=digest,
            status=ledger_status,
            result=None,
            error=_stringify(detail) if detail is not None else "outcome not ok",
        )
    except Exception:
        # record_outcome already flagged the failure; recovery stays safe.
        return
