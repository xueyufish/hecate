"""EventEnvelope - normalized cross-process event mapping.

kind=event carries a payload declared by ``payload_schema_ref``; kind=gap is an
explicit marker for a missing sequence range. ``source_sequence`` is per-run
and monotonically increasing as emitted by the backend; receivers never rebuild
a global order from it. Unknown fields are tolerated and preserved.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from hecate.contracts.execution.references import BackendRef, RefKind, require_kind


class EventKind(StrEnum):
    """Envelope discriminant: real event or explicit gap marker."""

    EVENT = "event"
    GAP = "gap"


class ActorKind(StrEnum):
    """Who performed the action a governance event records."""

    HUMAN = "human"
    AGENT_PRINCIPAL = "agent_principal"
    SERVICE = "service"
    PLATFORM = "platform"


class EventSource(StrEnum):
    """Authoritative writer of an event, grading observed vs reported evidence."""

    PLATFORM = "platform"
    STANDALONE_HOST = "standalone_host"
    EXECUTION_BACKEND = "execution_backend"


@dataclass(frozen=True)
class ActorRef:
    """Acting principal reference (governance-event profile requires it)."""

    kind: ActorKind
    id: str
    on_behalf_of: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ActorKind):
            raise ValueError(f"actor kind must be an ActorKind, got {self.kind!r}")
        if not self.id:
            raise ValueError("actor id must be a non-empty string")

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"kind": self.kind.value, "id": self.id}
        if self.on_behalf_of is not None:
            out["on_behalf_of"] = self.on_behalf_of
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ActorRef:
        return cls(
            kind=ActorKind(data["kind"]),
            id=data["id"],
            on_behalf_of=data.get("on_behalf_of"),
        )


@dataclass(frozen=True)
class GapRange:
    """Explicitly missing sequence range (never silently skipped)."""

    from_sequence: int
    to_sequence: int

    def to_dict(self) -> dict[str, int]:
        return {"from_sequence": self.from_sequence, "to_sequence": self.to_sequence}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GapRange:
        return cls(from_sequence=data["from_sequence"], to_sequence=data["to_sequence"])


@dataclass(frozen=True)
class EventEnvelope:
    """One normalized event (or gap marker) on a run's event stream."""

    contract_version: str
    kind: EventKind
    event_id: str
    task_ref: BackendRef
    run_ref: BackendRef
    source_sequence: int
    occurred_at: str
    received_at: str
    payload_schema_ref: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    evidence_ref: BackendRef | None = None
    correlation_id: str | None = None
    causation_id: str | None = None
    actor: ActorRef | None = None
    source: EventSource | None = None
    gap: GapRange | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        require_kind(self.task_ref, RefKind.TASK)
        require_kind(self.run_ref, RefKind.RUN)
        if not self.event_id:
            raise ValueError("event_id must be a non-empty string")
        if self.source_sequence < 0:
            raise ValueError("source_sequence must be >= 0")
        if self.kind is EventKind.EVENT:
            if not self.payload_schema_ref:
                raise ValueError("payload_schema_ref is required for kind=event")
            if self.gap is not None:
                raise ValueError("gap range is not allowed on kind=event")
        elif self.kind is EventKind.GAP:
            if self.gap is None:
                raise ValueError("gap range is required for kind=gap")
            if self.gap.to_sequence < self.gap.from_sequence:
                raise ValueError("gap range is inverted")

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "contract_version": self.contract_version,
            "kind": self.kind.value,
            "event_id": self.event_id,
            "task_ref": self.task_ref.to_dict(),
            "run_ref": self.run_ref.to_dict(),
            "source_sequence": self.source_sequence,
            "occurred_at": self.occurred_at,
            "received_at": self.received_at,
        }
        if self.payload_schema_ref is not None:
            out["payload_schema_ref"] = self.payload_schema_ref
        if self.payload:
            out["payload"] = self.payload
        if self.evidence_ref is not None:
            out["evidence_ref"] = self.evidence_ref.to_dict()
        if self.correlation_id is not None:
            out["correlation_id"] = self.correlation_id
        if self.causation_id is not None:
            out["causation_id"] = self.causation_id
        if self.actor is not None:
            out["actor"] = self.actor.to_dict()
        if self.source is not None:
            out["source"] = self.source.value
        if self.gap is not None:
            out["gap"] = self.gap.to_dict()
        out.update(self.extra)
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EventEnvelope:
        known = {
            "contract_version",
            "kind",
            "event_id",
            "task_ref",
            "run_ref",
            "source_sequence",
            "occurred_at",
            "received_at",
            "payload_schema_ref",
            "payload",
            "evidence_ref",
            "correlation_id",
            "causation_id",
            "actor",
            "source",
            "gap",
        }
        extra = {key: value for key, value in data.items() if key not in known}
        return cls(
            contract_version=data["contract_version"],
            kind=EventKind(data["kind"]),
            event_id=data["event_id"],
            task_ref=BackendRef.from_dict(data["task_ref"]),
            run_ref=BackendRef.from_dict(data["run_ref"]),
            source_sequence=data["source_sequence"],
            occurred_at=data["occurred_at"],
            received_at=data["received_at"],
            payload_schema_ref=data.get("payload_schema_ref"),
            payload=data.get("payload", {}),
            evidence_ref=(BackendRef.from_dict(data["evidence_ref"]) if "evidence_ref" in data else None),
            correlation_id=data.get("correlation_id"),
            causation_id=data.get("causation_id"),
            actor=ActorRef.from_dict(data["actor"]) if "actor" in data else None,
            source=EventSource(data["source"]) if "source" in data else None,
            gap=GapRange.from_dict(data["gap"]) if "gap" in data else None,
            extra=extra,
        )


def validate_governance_event(envelope: EventEnvelope) -> None:
    """Governance-event profile: ``actor`` and ``source`` are both required.

    Governance events must stay attributable to an acting principal and
    gradable by their authoritative writer; an envelope missing either is
    rejected rather than defaulted.
    """

    missing = [name for name, value in (("actor", envelope.actor), ("source", envelope.source)) if value is None]
    if missing:
        raise ValueError(
            f"governance event {envelope.event_id!r} requires {' and '.join(missing)}; refusing to accept the event"
        )
