"""ExecutionRequest - the platform-to-backend execution request mapping.

Unknown top-level fields are tolerated and preserved through round-trips
(forward compatibility); required fields are validated. The contract version
the sender speaks travels in ``contract_version``; receivers return
``version_conflict`` when it falls outside their support window.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from hecate.contracts.execution.references import (
    BackendRef,
    RefKind,
    require_kind,
)


@dataclass(frozen=True)
class TraceCorrelation:
    """Distributed-trace linkage for one execution request."""

    trace_id: str
    parent_span_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"trace_id": self.trace_id}
        if self.parent_span_id is not None:
            out["parent_span_id"] = self.parent_span_id
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TraceCorrelation:
        return cls(trace_id=data["trace_id"], parent_span_id=data.get("parent_span_id"))


@dataclass(frozen=True)
class Budget:
    """Optional resource bounds for one execution."""

    max_tokens: int | None = None
    max_cost_units: float | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.max_tokens is not None:
            out["max_tokens"] = self.max_tokens
        if self.max_cost_units is not None:
            out["max_cost_units"] = self.max_cost_units
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Budget:
        return cls(max_tokens=data.get("max_tokens"), max_cost_units=data.get("max_cost_units"))


@dataclass(frozen=True)
class ExecutionRequest:
    """One execution the platform asks a backend to run.

    Identifiers are logical references carrying their issuing domain; the
    receiver resolves them without querying a platform ORM. ``extra`` captures
    unknown fields so round-trips do not silently drop forward-compatible
    extensions.
    """

    contract_version: str
    task_ref: BackendRef
    run_ref: BackendRef
    deployment_ref: BackendRef
    authorization_ref: BackendRef
    input: dict[str, Any]
    idempotency_key: str
    trace_correlation: TraceCorrelation
    input_artifact_refs: tuple[BackendRef, ...] = ()
    deadline_at: str | None = None
    budget: Budget | None = None
    backend_config_ns: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        require_kind(self.task_ref, RefKind.TASK)
        require_kind(self.run_ref, RefKind.RUN)
        require_kind(self.deployment_ref, RefKind.DEPLOYMENT)
        require_kind(self.authorization_ref, RefKind.AUTHORIZATION)
        if not self.contract_version:
            raise ValueError("contract_version must be a non-empty string")
        if not self.idempotency_key:
            raise ValueError("idempotency_key must be a non-empty string")
        for ref in self.input_artifact_refs:
            require_kind(ref, RefKind.ARTIFACT)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "contract_version": self.contract_version,
            "task_ref": self.task_ref.to_dict(),
            "run_ref": self.run_ref.to_dict(),
            "deployment_ref": self.deployment_ref.to_dict(),
            "authorization_ref": self.authorization_ref.to_dict(),
            "input": self.input,
            "idempotency_key": self.idempotency_key,
            "trace_correlation": self.trace_correlation.to_dict(),
        }
        if self.input_artifact_refs:
            out["input_artifact_refs"] = [ref.to_dict() for ref in self.input_artifact_refs]
        if self.deadline_at is not None:
            out["deadline_at"] = self.deadline_at
        if self.budget is not None:
            out["budget"] = self.budget.to_dict()
        if self.backend_config_ns:
            out["backend_config_ns"] = self.backend_config_ns
        out.update(self.extra)
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExecutionRequest:
        known = {
            "contract_version",
            "task_ref",
            "run_ref",
            "deployment_ref",
            "authorization_ref",
            "input",
            "idempotency_key",
            "trace_correlation",
            "input_artifact_refs",
            "deadline_at",
            "budget",
            "backend_config_ns",
        }
        extra = {key: value for key, value in data.items() if key not in known}
        return cls(
            contract_version=data["contract_version"],
            task_ref=BackendRef.from_dict(data["task_ref"]),
            run_ref=BackendRef.from_dict(data["run_ref"]),
            deployment_ref=BackendRef.from_dict(data["deployment_ref"]),
            authorization_ref=BackendRef.from_dict(data["authorization_ref"]),
            input=data["input"],
            idempotency_key=data["idempotency_key"],
            trace_correlation=TraceCorrelation.from_dict(data["trace_correlation"]),
            input_artifact_refs=tuple(BackendRef.from_dict(ref) for ref in data.get("input_artifact_refs", [])),
            deadline_at=data.get("deadline_at"),
            budget=Budget.from_dict(data["budget"]) if "budget" in data else None,
            backend_config_ns=data.get("backend_config_ns", {}),
            extra=extra,
        )
