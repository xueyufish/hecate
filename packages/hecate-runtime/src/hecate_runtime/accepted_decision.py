"""Accepted Agent decision records and the runtime-owned persistence seam.

Only accepted output and restorable action intent belong here, never a vendor's
hidden reasoning payload. Hosts inject persistence and authoritative ownership;
the Runtime imports no durable storage or platform modules.
"""

from __future__ import annotations

import abc
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from hecate_runtime.tool_side_effects import SideEffectClass


class DecisionValidationError(ValueError):
    """A decision or its identity is invalid and cannot become accepted."""


ACTION_DECISION_SCHEMA_VERSION = 1
FINAL_ANSWER_SCHEMA_VERSION = 2
SUPPORTED_SCHEMA_VERSIONS = (ACTION_DECISION_SCHEMA_VERSION, FINAL_ANSWER_SCHEMA_VERSION)


class DecisionConflictError(ValueError):
    """Different content or input versions were presented for an accepted step."""

    def __init__(self, decision_step_id: str, existing_digest: str, attempted_digest: str) -> None:
        super().__init__(
            f"decision step {decision_step_id!r} conflicts "
            f"(existing_digest={existing_digest}, attempted_digest={attempted_digest})"
        )
        self.decision_step_id = decision_step_id
        self.existing_digest = existing_digest
        self.attempted_digest = attempted_digest


class DecisionStoreUnavailableError(RuntimeError):
    """Storage failed or a persisted record is corrupt; never means a miss."""


class StaleDecisionFenceError(RuntimeError):
    """Ownership is absent, expired, mismatched or revoked by a newer owner."""

    def __init__(self, decision_step_id: str, presented_token: int, current_token: int) -> None:
        super().__init__(
            f"invalid ownership for decision step {decision_step_id!r}: "
            f"presented token {presented_token}, current {current_token}"
        )
        self.decision_step_id = decision_step_id
        self.presented_token = presented_token
        self.current_token = current_token


def _identity(value: Any, label: str) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise DecisionValidationError(f"{label} must be a nonblank string of at most 256 characters")


def canonical_json(value: Any) -> str:
    """Serialize strict JSON, rejecting nonfinite numbers and lossy key coercion."""

    def validate(item: Any) -> None:
        if isinstance(item, dict):
            if any(not isinstance(key, str) for key in item):
                raise DecisionValidationError("JSON object keys must be strings")
            for child in item.values():
                validate(child)
        elif isinstance(item, list):
            for child in item:
                validate(child)
        elif item is not None and not isinstance(item, (str, int, float, bool)):
            raise DecisionValidationError("decision content must contain only JSON values")

    try:
        validate(value)
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError, RecursionError) as exc:
        raise DecisionValidationError("decision content must be finite, acyclic JSON") from exc


def _copy(value: Any) -> Any:
    return json.loads(canonical_json(value))


@dataclass(frozen=True)
class RunRef:
    """Opaque run identity in its issuing deployment's namespace."""

    issuer_domain: str
    run_id: str

    def __post_init__(self) -> None:
        _identity(self.issuer_domain, "issuer_domain")
        _identity(self.run_id, "run_id")

    def to_dict(self) -> dict[str, str]:
        """Return the neutral run identity without a storage-specific object."""
        return {"issuer_domain": self.issuer_domain, "run_id": self.run_id}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RunRef:
        """Decode an identity without coercing malformed values into strings."""
        return cls(issuer_domain=data["issuer_domain"], run_id=data["run_id"])


@dataclass(frozen=True)
class AcceptedAction:
    """Restorable action intent; execution outcomes remain in the Action Ledger."""

    action_key: str
    tool: str
    arguments: dict[str, Any]
    side_effect_class: str = "unknown"

    def to_dict(self) -> dict[str, Any]:
        """Return a detached JSON representation."""
        return _copy(
            {
                "action_key": self.action_key,
                "tool": self.tool,
                "arguments": self.arguments,
                "side_effect_class": self.side_effect_class,
            }
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AcceptedAction:
        """Restore action intent without silently repairing malformed data."""
        return cls(data["action_key"], data["tool"], _copy(data["arguments"]), data.get("side_effect_class", "unknown"))


@dataclass(frozen=True)
class AcceptedDecisionRecord:
    """Versioned accepted output and its ordered, restorable action set.

    ``schema_version`` discriminates the record kind: version 1 is an action
    decision with a nonempty action set; version 2 is a final-answer decision
    whose action set is empty and whose accepted output carries the nonblank
    answer text. The version participates in the content digest, so records
    of different kinds never collide on the same identity.
    """

    run_ref: RunRef
    decision_step_id: str
    input_refs: dict[str, Any] = field(default_factory=dict)
    accepted_output: dict[str, Any] = field(default_factory=dict)
    actions: tuple[AcceptedAction, ...] = ()
    accepted_at: str | None = None
    content_digest: str | None = None
    schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        """Return a detached, JSON-serializable record."""
        return _copy(
            {
                "schema_version": self.schema_version,
                "run_ref": self.run_ref.to_dict(),
                "decision_step_id": self.decision_step_id,
                "input_refs": self.input_refs,
                "accepted_output": self.accepted_output,
                "actions": [action.to_dict() for action in self.actions],
                "accepted_at": self.accepted_at,
                "content_digest": self.content_digest,
            }
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AcceptedDecisionRecord:
        """Restore the known record version; unknown versions fail closed."""
        record = cls(
            run_ref=RunRef.from_dict(data["run_ref"]),
            decision_step_id=data["decision_step_id"],
            input_refs=_copy(data["input_refs"]),
            accepted_output=_copy(data["accepted_output"]),
            actions=tuple(AcceptedAction.from_dict(action) for action in data["actions"]),
            accepted_at=data.get("accepted_at"),
            content_digest=data.get("content_digest"),
            schema_version=data.get("schema_version", 1),
        )
        validate_decision(record)
        return record


def validate_decision(record: AcceptedDecisionRecord) -> None:
    """Validate both direct store writes and model proposals before acceptance.

    The record version selects the shape contract: action decisions keep the
    original nonempty-action rules; final-answer decisions carry no actions
    and must hold nonblank accepted text. Unknown versions fail closed.
    """
    if not isinstance(record.run_ref, RunRef):
        raise DecisionValidationError("run_ref must be a RunRef")
    _identity(record.decision_step_id, "decision_step_id")
    if type(record.schema_version) is not int or record.schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        raise DecisionValidationError("unsupported accepted-decision schema version")
    if not isinstance(record.input_refs, dict) or not isinstance(record.accepted_output, dict):
        raise DecisionValidationError("input_refs and accepted_output must be JSON objects")
    if record.schema_version == FINAL_ANSWER_SCHEMA_VERSION:
        if not isinstance(record.actions, tuple) or record.actions:
            raise DecisionValidationError("a final-answer decision must not carry actions")
        text = record.accepted_output.get("text")
        if not isinstance(text, str) or not text.strip():
            raise DecisionValidationError("a final-answer decision requires nonblank accepted text")
    else:
        if not isinstance(record.actions, tuple) or not record.actions:
            raise DecisionValidationError("this decision slice requires a nonempty action tuple")
        keys = set()
        for action in record.actions:
            if not isinstance(action, AcceptedAction):
                raise DecisionValidationError("actions must be AcceptedAction records")
            _identity(action.action_key, "action_key")
            _identity(action.tool, "tool")
            if action.action_key in keys:
                raise DecisionValidationError("duplicate action key")
            keys.add(action.action_key)
            if not isinstance(action.arguments, dict):
                raise DecisionValidationError("action arguments must be a JSON object")
            if action.side_effect_class not in {item.value for item in SideEffectClass}:
                raise DecisionValidationError("unknown side-effect classification")
    canonical_decision_digest(record)


def canonical_decision_digest(record: AcceptedDecisionRecord) -> str:
    """Hash meaning, including identity and input/config versions, never timestamps."""
    payload = {
        "schema_version": record.schema_version,
        "run_ref": record.run_ref.to_dict(),
        "decision_step_id": record.decision_step_id,
        "accepted_output": record.accepted_output,
        "actions": [action.to_dict() for action in record.actions],
        "input_refs": record.input_refs,
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def snapshot_decision(record: AcceptedDecisionRecord) -> AcceptedDecisionRecord:
    """Detach mutable JSON fields before crossing a thread or persistence boundary."""
    validate_decision(record)
    return AcceptedDecisionRecord.from_dict(record.to_dict())


@dataclass(frozen=True)
class DecisionOwnership:
    """Host-issued lease context; SQL checks holder, exact token and expiry in DB."""

    lease_key: str
    fence_token: int
    holder: str


class AcceptedDecisionStore(abc.ABC):
    """Atomic immutable decisions with authoritative ownership and explicit errors."""

    @abc.abstractmethod
    async def lookup(self, run_ref: RunRef, decision_step_id: str) -> AcceptedDecisionRecord | None:
        """Read an accepted record; unavailable or corrupt data must never be a miss."""

    @abc.abstractmethod
    async def commit(
        self, record: AcceptedDecisionRecord, ownership: DecisionOwnership | None = None
    ) -> AcceptedDecisionRecord:
        """Accept only under a live owning Task dispatch lease, including idempotent writes."""
