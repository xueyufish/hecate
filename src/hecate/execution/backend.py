"""AgentExecutionBackend - the platform-to-backend extension point.

This is the "platform calls the runtime" direction (the mirror image of
``RuntimePort``, which stays the engine-to-capability direction and is never
imported here). The interface is deliberately minimal: six methods. Optional
capabilities (provide_input, resolve_approval, pause, resume, export_context)
are expressed through the capability declaration, never through additional
interface methods.

Contract version: 0.x draft, unfrozen. The first publishable version freezes
only after the real heterogeneous-backend verification in plan step8 - never
on the strength of the stub alone.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from hecate.contracts.execution.capabilities import BackendCapabilities, CapabilityLevel
from hecate.contracts.execution.errors import BackendError, BackendErrorCode, Reconciliation
from hecate.contracts.execution.events import EventEnvelope
from hecate.contracts.execution.references import BackendRef, RefKind, require_kind
from hecate.contracts.execution.request import ExecutionRequest


class RunState(StrEnum):
    """Backend-observed state of one run (plan-level Task states belong to step6)."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class CancelRequestState(StrEnum):
    """Control-command receipt states: a request is not an applied cancel."""

    REQUESTED = "requested"
    ACKNOWLEDGED = "acknowledged"
    APPLIED = "applied"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class SubmitReceipt:
    """Receipt for one accepted (or reconciled) submission."""

    run_ref: BackendRef
    received_at: str
    detail_ns: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        require_kind(self.run_ref, RefKind.RUN)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"run_ref": self.run_ref.to_dict(), "received_at": self.received_at}
        if self.detail_ns:
            out["detail_ns"] = self.detail_ns
        out.update(self.extra)
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SubmitReceipt:
        return cls(
            run_ref=BackendRef.from_dict(data["run_ref"]),
            received_at=data["received_at"],
            detail_ns=data.get("detail_ns", {}),
            extra={k: v for k, v in data.items() if k not in {"run_ref", "received_at", "detail_ns"}},
        )


@dataclass(frozen=True)
class RunStatus:
    """Backend-observed status for one run."""

    run_ref: BackendRef
    state: RunState
    detail_ns: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        require_kind(self.run_ref, RefKind.RUN)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"run_ref": self.run_ref.to_dict(), "state": self.state.value}
        if self.detail_ns:
            out["detail_ns"] = self.detail_ns
        out.update(self.extra)
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RunStatus:
        return cls(
            run_ref=BackendRef.from_dict(data["run_ref"]),
            state=RunState(data["state"]),
            detail_ns=data.get("detail_ns", {}),
            extra={k: v for k, v in data.items() if k not in {"run_ref", "state", "detail_ns"}},
        )


@dataclass(frozen=True)
class EventPage:
    """One page of the event stream; cursors are opaque and resumable."""

    events: tuple[EventEnvelope, ...]
    next_cursor: str | None = None
    has_more: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"events": [env.to_dict() for env in self.events], "has_more": self.has_more}
        if self.next_cursor is not None:
            out["next_cursor"] = self.next_cursor
        out.update(self.extra)
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EventPage:
        return cls(
            events=tuple(EventEnvelope.from_dict(env) for env in data["events"]),
            next_cursor=data.get("next_cursor"),
            has_more=data["has_more"],
            extra={k: v for k, v in data.items() if k not in {"events", "next_cursor", "has_more"}},
        )


@dataclass(frozen=True)
class CancelReceipt:
    """Receipt for a cancel request - REQUESTED is not APPLIED."""

    run_ref: BackendRef
    state: CancelRequestState
    detail_ns: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        require_kind(self.run_ref, RefKind.RUN)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"run_ref": self.run_ref.to_dict(), "state": self.state.value}
        if self.detail_ns:
            out["detail_ns"] = self.detail_ns
        out.update(self.extra)
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CancelReceipt:
        return cls(
            run_ref=BackendRef.from_dict(data["run_ref"]),
            state=CancelRequestState(data["state"]),
            detail_ns=data.get("detail_ns", {}),
            extra={k: v for k, v in data.items() if k not in {"run_ref", "state", "detail_ns"}},
        )


class ExecutionBackendError(Exception):
    """In-process carrier for a contract :class:`BackendError`."""

    def __init__(self, error: BackendError) -> None:
        super().__init__(f"{error.code.value}: {error.message}")
        self.error = error


def backend_error(
    code: BackendErrorCode,
    request_ref: BackendRef,
    message: str,
    *,
    detail_ns: dict[str, Any] | None = None,
    reconciliation: Reconciliation | None = None,
) -> ExecutionBackendError:
    """Build an :class:`ExecutionBackendError` from contract fields."""

    return ExecutionBackendError(
        BackendError(
            code=code,
            request_ref=request_ref,
            message=message,
            detail_ns=detail_ns or {},
            reconciliation=reconciliation,
        )
    )


class UnsupportedCapabilityError(ExecutionBackendError):
    """Raised when an optional capability is requested at level ``unsupported``."""

    def __init__(self, capability: str, request_ref: BackendRef) -> None:
        super().__init__(
            BackendError(
                code=BackendErrorCode.UNSUPPORTED,
                request_ref=request_ref,
                message=f"capability {capability!r} is unsupported by this backend",
                detail_ns={"capability": capability},
            )
        )
        self.capability = capability


class AgentExecutionBackend(ABC):
    """Platform-to-backend execution contract (six methods, no more).

    Third parties implement this ABC against the authoritative schemas and
    standard samples only - never against Hecate ORM, composition, or Pregel
    internals. Backend-issued run references use the backend's own issuing
    domain; the platform Task/Run references inside the request remain
    platform-issued and the two are never interchangeable.
    """

    @abstractmethod
    def describe_capabilities(self) -> BackendCapabilities:
        """Declare ownership axes and per-capability levels with verification."""

    @abstractmethod
    def submit(self, request: ExecutionRequest) -> SubmitReceipt:
        """Submit one execution; idempotent on ``request.idempotency_key``.

        Replaying the same key with the same content returns the original
        receipt; replaying it with different content is a
        ``version_conflict``. A lost response yields ``outcome_unknown`` with
        a reconciliation hint - callers resubmit the same key, they never
        start a semantically new execution.
        """

    @abstractmethod
    def get_run(self, run_ref: BackendRef) -> RunStatus:
        """Return the backend-observed state of a backend-issued run reference."""

    @abstractmethod
    def read_events(self, run_ref: BackendRef, cursor: str | None = None) -> EventPage:
        """Read normalized events from an opaque cursor; gaps are explicit."""

    @abstractmethod
    def list_artifacts(self, run_ref: BackendRef) -> tuple[BackendRef, ...]:
        """List artifact references produced by the run."""

    @abstractmethod
    def request_cancel(self, run_ref: BackendRef) -> CancelReceipt:
        """Request cancellation; REQUESTED must never be reported as APPLIED."""

    # --- optional capabilities: declared, not added as sixth/seventh methods ---

    def _require_declared(self, capability: str, request_ref: BackendRef) -> None:
        level = self.describe_capabilities().level_of(capability)
        if level is CapabilityLevel.UNSUPPORTED:
            raise UnsupportedCapabilityError(capability, request_ref)

    def pause(self, run_ref: BackendRef) -> None:
        """Optional interaction; default refuses unsupported backends explicitly."""

        self._require_declared("pause", run_ref)
        raise NotImplementedError("pause interaction semantics are adapter-defined (0.x draft)")

    def resume(self, run_ref: BackendRef) -> None:
        self._require_declared("resume", run_ref)
        raise NotImplementedError("resume interaction semantics are adapter-defined (0.x draft)")

    def provide_input(self, run_ref: BackendRef, payload: dict[str, Any]) -> None:
        self._require_declared("provide_input", run_ref)
        raise NotImplementedError("provide_input interaction semantics are adapter-defined")

    def resolve_approval(self, run_ref: BackendRef, approval_ref: BackendRef, approved: bool) -> None:
        self._require_declared("resolve_approval", run_ref)
        raise NotImplementedError("resolve_approval interaction semantics are adapter-defined")

    def export_context(self, run_ref: BackendRef) -> dict[str, Any]:
        self._require_declared("export_context", run_ref)
        raise NotImplementedError("export_context interaction semantics are adapter-defined")
