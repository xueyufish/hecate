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

import asyncio
import logging
import uuid
from dataclasses import dataclass
from typing import Any

from hecate_durable.contracts.durable import (
    ActionIntent,
    ActionOutcome,
    ActionOutcomeRecord,
    CommandState,
    ControlCommandKind,
    ControlCommandRecord,
    IdempotencyConflictError,
    IdempotencyKey,
    SubmissionAssociation,
    TaskLifecycleState,
    TaskStateRecord,
    canonical_request_digest,
)
from hecate_durable.contracts.references import BackendRef, RefKind
from hecate_durable.contracts.tools import ToolSideEffectClass
from hecate_durable.storage import SqlDurableStore
from hecate_runtime.action_ledger import (
    ActionClaimVerdict,
    ActionLedgerHook,
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


class RunnerLedgerHook(ActionLedgerHook):
    """Kernel hook over the persistent ledger for one run.

    One instance per run: it carries the run/task correlation that intent
    rows persist alongside the action key, so platform Actions map onto the
    runtime's TOOL_CALL/TOOL_RESULT identity without fabricating events.
    """

    def __init__(self, store: SqlDurableStore, *, task_ref: BackendRef, run_ref: BackendRef) -> None:
        self._store = store
        self._task_ref = task_ref
        self._run_ref = run_ref
        self._claim_tokens: dict[str, int] = {}
        self._failed_outcomes: set[str] = set()
        self._lock = asyncio.Lock()

    @property
    def has_failed_outcomes(self) -> bool:
        """Whether any outcome write failed (run stays pending reconciliation)."""

        return bool(self._failed_outcomes)

    async def resolve(self, *, session_id: str, execution_id: str) -> ToolExecutionResolution:
        try:
            return await asyncio.to_thread(self._resolve_sync, execution_id)
        except Exception:
            logger.warning("Ledger resolve failed for %s", execution_id, exc_info=True)
            return ToolExecutionResolution(state=ToolExecutionState.STORE_UNAVAILABLE)

    def _resolve_sync(self, execution_id: str) -> ToolExecutionResolution:
        recovery = self._store.recovery(execution_id)
        return self._to_resolution(execution_id, recovery)

    async def record_claim(
        self,
        *,
        session_id: str,
        execution_id: str,
        tool_call_id: str,
        tool_name: str,
        arguments: dict,
        arguments_digest: str,
        side_effect_class: str,
    ) -> ActionClaimVerdict:
        intent = ActionIntent(
            action_key=execution_id,
            action_name=tool_name,
            arguments_digest=arguments_digest,
            side_effect_class=ToolSideEffectClass(side_effect_class),
        )
        try:
            async with self._lock:
                await asyncio.to_thread(
                    self._store.record_intent_ex,
                    intent,
                    task_ref=self._task_ref,
                    run_ref=self._run_ref,
                    session_id=session_id,
                    execution_id=execution_id,
                    tool_call_id=tool_call_id,
                )
                receipt, token = await asyncio.to_thread(self._store.claim_ex, execution_id, holder=ISSUER)
        except Exception as exc:
            if isinstance(exc, IdempotencyConflictError):
                return ActionClaimVerdict(
                    claimed=False,
                    conflict=(
                        "[conflict] arguments differ from the recorded execution for this action; execution withheld"
                    ),
                )
            logger.warning("Ledger claim failed for %s", execution_id, exc_info=True)
            raise
        if not receipt.claimed:
            resolution = self._to_resolution(execution_id, receipt.recovery)
            return ActionClaimVerdict(claimed=False, resolution=resolution)
        if token is not None:
            self._claim_tokens[execution_id] = token
        return ActionClaimVerdict(claimed=True)

    async def record_outcome(
        self,
        *,
        session_id: str,
        execution_id: str,
        tool_call_id: str,
        tool_name: str,
        arguments_digest: str,
        status: str,
        result: Any = None,
        error: str | None = None,
    ) -> None:
        outcome = ActionOutcome.UNKNOWN if status == "unknown" else ActionOutcome(status)
        record = ActionOutcomeRecord(
            action_key=execution_id,
            outcome=outcome,
            result_digest=self._result_digest(result, error),
        )
        token = self._claim_tokens.get(execution_id)
        try:
            async with self._lock:
                await asyncio.to_thread(
                    self._store.record_outcome_ex,
                    record,
                    claim_token=token,
                    result_payload=self._result_payload(result, error),
                )
        except Exception:
            # The side effect already happened; keep the action claimed so
            # recovery fails closed instead of re-running it.
            self._failed_outcomes.add(execution_id)
            logger.warning(
                "Ledger outcome write failed for %s — stays claimed, pending reconciliation",
                execution_id,
                exc_info=True,
            )
            raise

    @staticmethod
    def _result_digest(result: Any, error: str | None) -> str | None:
        if error is not None:
            return tool_arguments_digest({"error": error[:500]})
        if result is None:
            return None
        return tool_arguments_digest(result)

    @staticmethod
    def _result_payload(result: Any, error: str | None) -> Any:
        if error is not None:
            return {"error": error[:500]}
        return result

    def _to_resolution(self, execution_id: str, recovery) -> ToolExecutionResolution:
        from hecate_durable.contracts.durable import ActionLedgerState

        state = recovery.state
        last = recovery.last_outcome
        if state is ActionLedgerState.NEVER_STARTED:
            return ToolExecutionResolution(state=ToolExecutionState.NEVER_STARTED)
        if state is ActionLedgerState.OUTCOME_UNKNOWN:
            return ToolExecutionResolution(
                state=ToolExecutionState.OUTCOME_UNKNOWN,
                arguments_digest=recovery.intent.arguments_digest if recovery.intent else None,
                tool_name=recovery.intent.action_name if recovery.intent else None,
            )
        if last is not None and last.outcome is ActionOutcome.SUCCEEDED:
            # The real recorded result content is the cross-restart backfill.
            content = None
            for action in self._store.list_run_actions(self._run_ref):
                if action.get("action_key") == execution_id and action.get("result_payload") is not None:
                    content = _stringify(action["result_payload"])
                    break
            return ToolExecutionResolution(
                state=ToolExecutionState.SUCCEEDED,
                arguments_digest=recovery.intent.arguments_digest if recovery.intent else None,
                result_digest=last.result_digest,
                tool_name=recovery.intent.action_name if recovery.intent else None,
                result_content=content,
            )
        if last is not None and last.outcome is ActionOutcome.FAILED:
            return ToolExecutionResolution(
                state=ToolExecutionState.FAILED,
                arguments_digest=recovery.intent.arguments_digest if recovery.intent else None,
                result_digest=last.result_digest,
                tool_name=recovery.intent.action_name if recovery.intent else None,
            )
        return ToolExecutionResolution(
            state=ToolExecutionState.CLAIMED,
            arguments_digest=recovery.intent.arguments_digest if recovery.intent else None,
            tool_name=recovery.intent.action_name if recovery.intent else None,
        )


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

    # -- submission / lifecycle ------------------------------------------------

    def submit(
        self,
        *,
        principal: str,
        run_input: dict,
        idempotency_key: str | None,
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
            input_payload=run_input,
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
    ) -> TaskStateRecord:
        """Converge a task; withheld actions land in ``reconciliation_required``."""

        if needs_reconciliation:
            return self.store.apply_task_state(task_ref, TaskLifecycleState.RECONCILIATION_REQUIRED)
        target = {
            "succeeded": TaskLifecycleState.SUCCEEDED,
            "failed": TaskLifecycleState.FAILED,
            "cancelled": TaskLifecycleState.CANCELLED,
            "unknown": TaskLifecycleState.RECONCILIATION_REQUIRED,
        }[outcome]
        return self.store.apply_task_state(task_ref, target)

    # -- control commands --------------------------------------------------------

    def record_cancel(self, *, command_id: str, issuer: str, task_ref: BackendRef, run_ref: BackendRef) -> None:
        self.store.record(
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

    def task_state(self, task_id: str):
        return self.store.get_task_state(BackendRef(kind=RefKind.TASK, issuer_domain=ISSUER, id=task_id))

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
