"""Durable-execution seams - minimal interfaces for step6's parallel tracks.

``DurableTaskStore``, ``ControlCommandRecorder``, and ``ActionLedger`` are the
seams between contract semantics (``hecate.contracts.execution.durable``) and
their production implementations: the PostgreSQL/worker core
(``durable-execution-core``) and the platform/runner adapters
(``platform-task-control-api``). The InMemory doubles in
``hecate.execution.stub_durable`` are the second implementation required by
the runtime-pluggability rule and the parameterized contract suite's first
member; every production implementation must register into the same suite
before wiring.

Method signatures carry only contract dataclasses and builtins - never ORM
objects, SQL, or connection handles. Field ownership (who writes which
record) is a consumer concern; these seams fix only the semantics.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from .contracts.durable import (
    ActionIntent,
    ActionOutcomeRecord,
    ActionRecovery,
    ClaimReceipt,
    CommandState,
    ControlCommandRecord,
    IdempotencyKey,
    SubmissionAssociation,
    TaskLifecycleState,
    TaskStateRecord,
)
from .contracts.references import BackendRef


class DurableTaskStore(ABC):
    """Task lifecycle records plus idempotent submission association."""

    @abstractmethod
    def record_submission(
        self, key: IdempotencyKey, task_ref: BackendRef, run_ref: BackendRef
    ) -> SubmissionAssociation:
        """Register one submission under ``key``; idempotent on the key.

        Replaying the same key with the same digest returns the original
        association; replaying with a different digest raises
        :class:`IdempotencyConflictError` and never overwrites it.
        """

    @abstractmethod
    def apply_task_state(
        self,
        task_ref: BackendRef,
        target: TaskLifecycleState,
        *,
        expected_revision: int | None = None,
        reconciled: bool = False,
        recorded_at: str = "",
    ) -> TaskStateRecord:
        """Advance one task's lifecycle state after transition validation.

        Terminal states are absorbing and ``reconciliation_required``
        converges only with ``reconciled=True``. A stale
        ``expected_revision`` is rejected instead of silently overwritten.
        """

    @abstractmethod
    def get_task_state(self, task_ref: BackendRef) -> TaskStateRecord | None:
        """Latest lifecycle record for one task, or ``None`` when absent."""


class ControlCommandRecorder(ABC):
    """Independent receipt records for control commands."""

    @abstractmethod
    def record(self, command: ControlCommandRecord) -> ControlCommandRecord:
        """Persist one command record; idempotent on ``command_id``.

        Re-recording the same ``command_id`` returns the stored record; a
        different payload for the same id is a conflict.
        """

    @abstractmethod
    def transition(self, command_id: str, target: CommandState) -> ControlCommandRecord:
        """Advance one command's receipt state; terminals are absorbing."""

    @abstractmethod
    def get(self, command_id: str) -> ControlCommandRecord | None:
        """One command's record, or ``None`` when unknown."""


class ActionLedger(ABC):
    """Persistent action intent/claim/outcome ledger with safe recovery."""

    @abstractmethod
    def record_intent(self, intent: ActionIntent) -> None:
        """Persist intent before business dispatch; idempotent per action key.

        The same action key with a different arguments digest raises
        :class:`IdempotencyConflictError` and never executes.
        """

    @abstractmethod
    def claim(self, action_key: str) -> ClaimReceipt:
        """Atomically claim one action; at most one concurrent claimer wins.

        A ``never_started`` resolution is claimed by persisting intent first;
        a loser receives the current verdict without claiming rights.
        """

    @abstractmethod
    def record_outcome(self, outcome: ActionOutcomeRecord) -> None:
        """Record the real outcome (result reference or digest) for one action."""

    @abstractmethod
    def recovery(self, action_key: str) -> ActionRecovery:
        """Recovery query: four-state verdict plus the explicit marker.

        Returns the recorded result reference when known; unknown outcomes
        carry ``pending_reconciliation`` instead of placeholder results.
        """
