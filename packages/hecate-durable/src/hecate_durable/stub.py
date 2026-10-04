"""InMemory doubles for the durable-execution seams.

Second implementation required by the runtime-pluggability rule and the first
member of the parameterized contract suite. Suitable for tests and local
previews only - a process restart drops everything, so these doubles carry no
durability guarantee and must never back a production profile.
"""

from __future__ import annotations

import threading

from hecate_durable.seams import ActionLedger, ControlCommandRecorder, DurableTaskStore

from .contracts.durable import (
    ActionIntent,
    ActionLedgerState,
    ActionOutcome,
    ActionOutcomeRecord,
    ActionRecovery,
    ClaimReceipt,
    CommandState,
    ControlCommandRecord,
    IdempotencyConflictError,
    IdempotencyKey,
    SubmissionAssociation,
    TaskLifecycleState,
    TaskStateRecord,
    may_auto_replay,
    validate_command_transition,
    validate_task_transition,
)
from .contracts.references import BackendRef


class InMemoryDurableTaskStore(DurableTaskStore):
    """Dict-backed task lifecycle records and idempotency associations."""

    def __init__(self) -> None:
        self._states: dict[str, TaskStateRecord] = {}
        self._submissions: dict[str, SubmissionAssociation] = {}
        self._lock = threading.Lock()

    def record_submission(
        self, key: IdempotencyKey, task_ref: BackendRef, run_ref: BackendRef
    ) -> SubmissionAssociation:
        with self._lock:
            existing = self._submissions.get(key.key)
            if existing is not None:
                if existing.key.request_digest != key.request_digest:
                    raise IdempotencyConflictError(key.key, existing.key.request_digest)
                return existing
            association = SubmissionAssociation(key=key, task_ref=task_ref, run_ref=run_ref)
            self._submissions[key.key] = association
            return association

    def apply_task_state(
        self,
        task_ref: BackendRef,
        target: TaskLifecycleState,
        *,
        expected_revision: int | None = None,
        reconciled: bool = False,
        recorded_at: str = "",
    ) -> TaskStateRecord:
        with self._lock:
            current = self._states.get(task_ref.id)
            if current is not None:
                validate_task_transition(current.lifecycle_state, target, reconciled=reconciled)
                if expected_revision is not None and expected_revision != current.revision:
                    raise ValueError(f"stale revision: expected {expected_revision}, current is {current.revision}")
                revision = current.revision + 1
            else:
                if target is not TaskLifecycleState.QUEUED:
                    raise ValueError(f"first recorded state must be queued, got {target.value}")
                if expected_revision is not None and expected_revision != 0:
                    raise ValueError(f"stale revision: expected {expected_revision}, current is 0")
                revision = 0
            record = TaskStateRecord(
                task_ref=task_ref,
                lifecycle_state=target,
                revision=revision,
                recorded_at=recorded_at or "1970-01-01T00:00:00Z",
            )
            self._states[task_ref.id] = record
            return record

    def get_task_state(self, task_ref: BackendRef) -> TaskStateRecord | None:
        with self._lock:
            return self._states.get(task_ref.id)


class InMemoryControlCommandRecorder(ControlCommandRecorder):
    """Dict-backed command receipt records with terminal absorption."""

    def __init__(self) -> None:
        self._commands: dict[str, ControlCommandRecord] = {}
        self._lock = threading.Lock()

    def record(self, command: ControlCommandRecord) -> ControlCommandRecord:
        with self._lock:
            existing = self._commands.get(command.command_id)
            if existing is not None:
                if existing.to_dict() != command.to_dict():
                    raise ValueError(f"command {command.command_id!r} already recorded with different content")
                return existing
            self._commands[command.command_id] = command
            return command

    def transition(self, command_id: str, target: CommandState) -> ControlCommandRecord:
        with self._lock:
            current = self._commands.get(command_id)
            if current is None:
                raise KeyError(f"unknown command {command_id!r}")
            validate_command_transition(current.state, target)
            updated = ControlCommandRecord(
                command_id=current.command_id,
                kind=current.kind,
                issuer=current.issuer,
                task_ref=current.task_ref,
                issued_at=current.issued_at,
                state=target,
                run_ref=current.run_ref,
                expires_at=current.expires_at,
                expected_revision=current.expected_revision,
                payload=current.payload,
                payload_schema_ref=current.payload_schema_ref,
                detail_ns=current.detail_ns,
            )
            self._commands[command_id] = updated
            return updated

    def get(self, command_id: str) -> ControlCommandRecord | None:
        with self._lock:
            return self._commands.get(command_id)


class InMemoryActionLedger(ActionLedger):
    """Dict-backed action ledger with atomic claim arbitration under a lock."""

    def __init__(self) -> None:
        self._intents: dict[str, ActionIntent] = {}
        self._outcomes: dict[str, ActionOutcomeRecord] = {}
        self._active_claims: set[str] = set()
        self._lock = threading.Lock()

    def record_intent(self, intent: ActionIntent) -> None:
        with self._lock:
            existing = self._intents.get(intent.action_key)
            if existing is not None:
                if existing.arguments_digest != intent.arguments_digest:
                    raise IdempotencyConflictError(intent.action_key, existing.arguments_digest)
                return
            self._intents[intent.action_key] = intent

    def claim(self, action_key: str) -> ClaimReceipt:
        with self._lock:
            recovery = self._recovery_locked(action_key)
            if recovery.state is ActionLedgerState.NEVER_STARTED:
                raise KeyError(f"action {action_key!r} has no recorded intent; record intent before claiming")
            claimable = self._claimable_locked(action_key, recovery)
            if not claimable or action_key in self._active_claims:
                return ClaimReceipt(action_key=action_key, claimed=False, recovery=recovery)
            self._active_claims.add(action_key)
            return ClaimReceipt(action_key=action_key, claimed=True, recovery=recovery)

    def record_outcome(self, outcome: ActionOutcomeRecord) -> None:
        with self._lock:
            if outcome.action_key not in self._intents:
                raise KeyError(f"action {outcome.action_key!r} has no recorded intent")
            self._outcomes[outcome.action_key] = outcome
            self._active_claims.discard(outcome.action_key)

    def recovery(self, action_key: str) -> ActionRecovery:
        with self._lock:
            return self._recovery_locked(action_key)

    def _claimable_locked(self, action_key: str, recovery: ActionRecovery) -> bool:
        intent = self._intents[action_key]
        outcome = self._outcomes.get(action_key)
        if outcome is None:
            return True
        if outcome.outcome in (ActionOutcome.SUCCEEDED, ActionOutcome.UNKNOWN):
            return False
        return may_auto_replay(ActionLedgerState.CLAIMED, intent.side_effect_class)

    def _recovery_locked(self, action_key: str) -> ActionRecovery:
        intent = self._intents.get(action_key)
        if intent is None:
            return ActionRecovery(action_key=action_key, state=ActionLedgerState.NEVER_STARTED)
        outcome = self._outcomes.get(action_key)
        if outcome is not None and outcome.outcome is ActionOutcome.UNKNOWN:
            return ActionRecovery(
                action_key=action_key,
                state=ActionLedgerState.OUTCOME_UNKNOWN,
                intent=intent,
                last_outcome=outcome,
                pending_reconciliation=True,
            )
        if outcome is not None:
            return ActionRecovery(
                action_key=action_key,
                state=ActionLedgerState.CLAIMED,
                intent=intent,
                last_outcome=outcome,
                pending_reconciliation=(
                    outcome.outcome is ActionOutcome.FAILED
                    and not may_auto_replay(ActionLedgerState.CLAIMED, intent.side_effect_class)
                ),
            )
        return ActionRecovery(
            action_key=action_key,
            state=ActionLedgerState.CLAIMED,
            intent=intent,
            pending_reconciliation=not may_auto_replay(ActionLedgerState.CLAIMED, intent.side_effect_class),
        )
