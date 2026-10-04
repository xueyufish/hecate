"""Durable execution contracts - task lifecycle, control commands, idempotency,
governance-event profile, and the persistent action ledger.

These types are the shared baseline for step6's two parallel deliveries
(``durable-execution-core`` persistence implementations and
``platform-task-control-api`` platform wiring): both consume this module and
must not define parallel vocabularies. Semantics pinned here:

- Task lifecycle states are a closed enum, distinct from the backend-observed
  ``RunState`` dimension; terminal states never roll back, and
  ``reconciliation_required`` converges only through an explicit
  reconciliation action.
- Control commands are independent receipt records; a transport-level success
  is never ``applied``, and ``expired`` is terminal.
- The idempotency key binds server-verified subject, workspace, and request
  digest; same key with a different digest is a conflict.
- The action ledger's four recovery states mirror the runtime
  ``ToolExecutionState`` verbatim; a drift test pins the two enums together.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from .references import BackendRef, RefKind, require_kind
from .tools import ToolSideEffectClass


class TaskLifecycleState(StrEnum):
    """Closed task lifecycle vocabulary (platform Task dimension)."""

    QUEUED = "queued"
    RUNNING = "running"
    WAITING_INPUT = "waiting_input"
    WAITING_APPROVAL = "waiting_approval"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    RECONCILIATION_REQUIRED = "reconciliation_required"


TERMINAL_TASK_STATES: frozenset[TaskLifecycleState] = frozenset(
    {
        TaskLifecycleState.SUCCEEDED,
        TaskLifecycleState.FAILED,
        TaskLifecycleState.CANCELLED,
    }
)


class InvalidTaskTransitionError(ValueError):
    """Raised when a lifecycle transition violates the contract."""


def validate_task_transition(
    current: TaskLifecycleState,
    target: TaskLifecycleState,
    *,
    reconciled: bool = False,
) -> None:
    """Validate one lifecycle transition; raise :class:`InvalidTaskTransitionError`.

    Terminal states are absorbing. ``reconciliation_required`` may only reach
    a terminal state through an explicit reconciliation action, never a
    silent rewrite into success or failure.
    """

    if current in TERMINAL_TASK_STATES:
        raise InvalidTaskTransitionError(f"terminal state {current.value} must not roll back")
    if current is TaskLifecycleState.RECONCILIATION_REQUIRED and target in TERMINAL_TASK_STATES and not reconciled:
        raise InvalidTaskTransitionError(
            "reconciliation_required converges only through an explicit reconciliation action"
        )


@dataclass(frozen=True)
class TaskStateRecord:
    """One task's lifecycle state as a durable record (with revision)."""

    task_ref: BackendRef
    lifecycle_state: TaskLifecycleState
    revision: int
    recorded_at: str
    writer_source: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        require_kind(self.task_ref, RefKind.TASK)
        if not isinstance(self.lifecycle_state, TaskLifecycleState):
            raise ValueError(f"lifecycle_state must be a TaskLifecycleState, got {self.lifecycle_state!r}")
        if self.revision < 0:
            raise ValueError("revision must be >= 0")
        if not self.recorded_at:
            raise ValueError("recorded_at must be a non-empty string")

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "task_ref": self.task_ref.to_dict(),
            "lifecycle_state": self.lifecycle_state.value,
            "revision": self.revision,
            "recorded_at": self.recorded_at,
        }
        if self.writer_source is not None:
            out["writer_source"] = self.writer_source
        out.update(self.extra)
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TaskStateRecord:
        # TaskLifecycleState(value) rejects unknown states instead of mapping.
        state = TaskLifecycleState(data["lifecycle_state"])
        return cls(
            task_ref=BackendRef.from_dict(data["task_ref"]),
            lifecycle_state=state,
            revision=data["revision"],
            recorded_at=data["recorded_at"],
            writer_source=data.get("writer_source"),
            extra={
                k: v
                for k, v in data.items()
                if k
                not in {
                    "task_ref",
                    "lifecycle_state",
                    "revision",
                    "recorded_at",
                    "writer_source",
                }
            },
        )


class ControlCommandKind(StrEnum):
    """Command vocabulary; ``provide_input`` payloads follow the envelope pattern."""

    CANCEL = "cancel"
    PAUSE = "pause"
    RESUME = "resume"
    PROVIDE_INPUT = "provide_input"


class CommandState(StrEnum):
    """Receipt states; ``applied`` comes only from an executor's actual effect."""

    REQUESTED = "requested"
    ACKNOWLEDGED = "acknowledged"
    APPLIED = "applied"
    REJECTED = "rejected"
    EXPIRED = "expired"


TERMINAL_COMMAND_STATES: frozenset[CommandState] = frozenset(
    {
        CommandState.APPLIED,
        CommandState.REJECTED,
        CommandState.EXPIRED,
    }
)


class InvalidCommandTransitionError(ValueError):
    """Raised when a command receipt transition violates the contract."""


def validate_command_transition(current: CommandState, target: CommandState) -> None:
    """Validate one receipt transition; ``expired`` and other terminals absorb."""

    if current in TERMINAL_COMMAND_STATES:
        raise InvalidCommandTransitionError(f"terminal command state {current.value} must not transition")


@dataclass(frozen=True)
class ControlCommandRecord:
    """One control command's independent receipt record."""

    command_id: str
    kind: ControlCommandKind
    issuer: str
    task_ref: BackendRef
    issued_at: str
    state: CommandState
    run_ref: BackendRef | None = None
    expires_at: str | None = None
    expected_revision: int | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    payload_schema_ref: str | None = None
    detail_ns: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        require_kind(self.task_ref, RefKind.TASK)
        if self.run_ref is not None:
            require_kind(self.run_ref, RefKind.RUN)
        if not self.command_id or not self.issuer or not self.issued_at:
            raise ValueError("command_id, issuer, and issued_at must be non-empty strings")
        if not isinstance(self.kind, ControlCommandKind):
            raise ValueError(f"kind must be a ControlCommandKind, got {self.kind!r}")
        if not isinstance(self.state, CommandState):
            raise ValueError(f"state must be a CommandState, got {self.state!r}")
        if self.payload and not self.payload_schema_ref:
            raise ValueError("a non-empty payload requires payload_schema_ref")

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "command_id": self.command_id,
            "kind": self.kind.value,
            "issuer": self.issuer,
            "task_ref": self.task_ref.to_dict(),
            "issued_at": self.issued_at,
            "state": self.state.value,
        }
        if self.run_ref is not None:
            out["run_ref"] = self.run_ref.to_dict()
        if self.expires_at is not None:
            out["expires_at"] = self.expires_at
        if self.expected_revision is not None:
            out["expected_revision"] = self.expected_revision
        if self.payload:
            out["payload"] = self.payload
        if self.payload_schema_ref is not None:
            out["payload_schema_ref"] = self.payload_schema_ref
        if self.detail_ns:
            out["detail_ns"] = self.detail_ns
        out.update(self.extra)
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ControlCommandRecord:
        return cls(
            command_id=data["command_id"],
            kind=ControlCommandKind(data["kind"]),
            issuer=data["issuer"],
            task_ref=BackendRef.from_dict(data["task_ref"]),
            issued_at=data["issued_at"],
            state=CommandState(data["state"]),
            run_ref=BackendRef.from_dict(data["run_ref"]) if "run_ref" in data else None,
            expires_at=data.get("expires_at"),
            expected_revision=data.get("expected_revision"),
            payload=data.get("payload", {}),
            payload_schema_ref=data.get("payload_schema_ref"),
            detail_ns=data.get("detail_ns", {}),
            extra={
                k: v
                for k, v in data.items()
                if k
                not in {
                    "command_id",
                    "kind",
                    "issuer",
                    "task_ref",
                    "issued_at",
                    "state",
                    "run_ref",
                    "expires_at",
                    "expected_revision",
                    "payload",
                    "payload_schema_ref",
                    "detail_ns",
                }
            },
        )


@dataclass(frozen=True)
class IdempotencyKey:
    """Submission key bound to server-verified subject, workspace, and digest."""

    key: str
    subject: str
    workspace: str
    request_digest: str

    def __post_init__(self) -> None:
        if not self.key or not self.subject or not self.workspace:
            raise ValueError("key, subject, and workspace must be non-empty strings")
        if not self.request_digest:
            raise ValueError("request_digest must be a non-empty string")

    def assert_matches_context(self, subject: str, workspace: str) -> None:
        """Reject a key whose bound scope differs from the server-verified context."""

        if (self.subject, self.workspace) != (subject, workspace):
            raise IdempotencyScopeError(self.key, self.subject, self.workspace)


class IdempotencyScopeError(ValueError):
    """Raised when a key's bound scope disagrees with the verified context."""

    def __init__(self, key: str, bound_subject: str, bound_workspace: str) -> None:
        super().__init__(
            f"idempotency key {key!r} is bound to subject {bound_subject!r} "
            f"and workspace {bound_workspace!r}; the verified context differs"
        )
        self.key = key
        self.bound_subject = bound_subject
        self.bound_workspace = bound_workspace


class IdempotencyConflictError(Exception):
    """Same key replayed with a different request digest."""

    def __init__(self, key: str, registered_digest: str) -> None:
        super().__init__(f"idempotency key {key!r} is already registered with a different request digest")
        self.key = key
        self.registered_digest = registered_digest


def canonical_request_digest(payload: dict[str, Any]) -> str:
    """Digest over canonical JSON so semantically identical bodies match."""

    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SubmissionAssociation:
    """Task/Run association registered under one idempotency key."""

    key: IdempotencyKey
    task_ref: BackendRef
    run_ref: BackendRef

    def __post_init__(self) -> None:
        require_kind(self.task_ref, RefKind.TASK)
        require_kind(self.run_ref, RefKind.RUN)


class ActionLedgerState(StrEnum):
    """Four recovery states, mirroring runtime ``ToolExecutionState`` verbatim.

    A drift test pins this enum against the runtime enum; changing one side
    without the other fails the suite.
    """

    NEVER_STARTED = "never_started"
    CLAIMED = "claimed"
    OUTCOME_UNKNOWN = "outcome_unknown"
    STORE_UNAVAILABLE = "store_unavailable"


REPLAYABLE_SIDE_EFFECTS: frozenset[ToolSideEffectClass] = frozenset(
    {
        ToolSideEffectClass.READONLY,
        ToolSideEffectClass.IDEMPOTENT_WRITE,
    }
)


def may_auto_replay(state: ActionLedgerState, side_effect_class: ToolSideEffectClass) -> bool:
    """Auto-replay policy mirroring runtime tool-recovery semantics.

    Only explicitly idempotent classes replay under ``claimed``; unknown
    outcomes never auto-replay; ``store_unavailable`` releases readonly only
    (a write action safely stops - store failure is never downgraded into
    never-started).
    """

    if state is ActionLedgerState.NEVER_STARTED:
        return True
    if state is ActionLedgerState.CLAIMED:
        return side_effect_class in REPLAYABLE_SIDE_EFFECTS
    if state is ActionLedgerState.STORE_UNAVAILABLE:
        return side_effect_class is ToolSideEffectClass.READONLY
    return False


@dataclass(frozen=True)
class ActionIntent:
    """Intent persisted before business dispatch."""

    action_key: str
    action_name: str
    arguments_digest: str
    side_effect_class: ToolSideEffectClass

    def __post_init__(self) -> None:
        if not self.action_key or not self.action_name or not self.arguments_digest:
            raise ValueError("action_key, action_name, and arguments_digest must be non-empty")
        if not isinstance(self.side_effect_class, ToolSideEffectClass):
            raise ValueError(f"side_effect_class must be a ToolSideEffectClass, got {self.side_effect_class!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_key": self.action_key,
            "action_name": self.action_name,
            "arguments_digest": self.arguments_digest,
            "side_effect_class": self.side_effect_class.value,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ActionIntent:
        return cls(
            action_key=data["action_key"],
            action_name=data["action_name"],
            arguments_digest=data["arguments_digest"],
            side_effect_class=ToolSideEffectClass(data["side_effect_class"]),
        )


class ActionOutcome(StrEnum):
    """Result statuses; ``unknown`` defers to reconciliation."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ActionOutcomeRecord:
    """One action's recorded outcome with a real result reference when known."""

    action_key: str
    outcome: ActionOutcome
    result_ref: BackendRef | None = None
    result_digest: str | None = None

    def __post_init__(self) -> None:
        if not self.action_key:
            raise ValueError("action_key must be a non-empty string")
        if not isinstance(self.outcome, ActionOutcome):
            raise ValueError(f"outcome must be an ActionOutcome, got {self.outcome!r}")
        if self.result_ref is not None:
            require_kind(self.result_ref, RefKind.ARTIFACT)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"action_key": self.action_key, "outcome": self.outcome.value}
        if self.result_ref is not None:
            out["result_ref"] = self.result_ref.to_dict()
        if self.result_digest is not None:
            out["result_digest"] = self.result_digest
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ActionOutcomeRecord:
        return cls(
            action_key=data["action_key"],
            outcome=ActionOutcome(data["outcome"]),
            result_ref=BackendRef.from_dict(data["result_ref"]) if "result_ref" in data else None,
            result_digest=data.get("result_digest"),
        )


@dataclass(frozen=True)
class ActionRecovery:
    """Recovery query result: state, intent, last outcome, reconciliation flag.

    ``pending_reconciliation`` is the explicit marker callers must surface;
    implementations never substitute placeholder results for it.
    """

    action_key: str
    state: ActionLedgerState
    intent: ActionIntent | None = None
    last_outcome: ActionOutcomeRecord | None = None
    pending_reconciliation: bool = False

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "action_key": self.action_key,
            "state": self.state.value,
            "pending_reconciliation": self.pending_reconciliation,
        }
        if self.intent is not None:
            out["intent"] = self.intent.to_dict()
        if self.last_outcome is not None:
            out["last_outcome"] = self.last_outcome.to_dict()
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ActionRecovery:
        return cls(
            action_key=data["action_key"],
            state=ActionLedgerState(data["state"]),
            intent=ActionIntent.from_dict(data["intent"]) if "intent" in data else None,
            last_outcome=(ActionOutcomeRecord.from_dict(data["last_outcome"]) if "last_outcome" in data else None),
            pending_reconciliation=data.get("pending_reconciliation", False),
        )


@dataclass(frozen=True)
class ClaimReceipt:
    """Claim arbitration: at most one concurrent claimer wins.

    ``claimed`` is the winner signal; losers receive the current recovery
    verdict without execution rights.
    """

    action_key: str
    claimed: bool
    recovery: ActionRecovery

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_key": self.action_key,
            "claimed": self.claimed,
            "recovery": self.recovery.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ClaimReceipt:
        return cls(
            action_key=data["action_key"],
            claimed=data["claimed"],
            recovery=ActionRecovery.from_dict(data["recovery"]),
        )
