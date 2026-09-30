"""BackendError - machine-readable error with a closed six-code set.

Timeouts and connection loss map to ``outcome_unknown`` (reconcile later),
never to an automatic task failure. ``version_conflict`` covers contract-version
mismatch outside the support window and idempotency-key reuse with different
request content (mark ``detail_ns.reason = "idempotency_key_content_mismatch"``
and point at the original request via the request reference).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from hecate.contracts.execution.references import BackendRef


class BackendErrorCode(StrEnum):
    """Closed error taxonomy for the execution contract."""

    UNSUPPORTED = "unsupported"
    AUTHORIZATION_DENIED = "authorization_denied"
    BUDGET_EXHAUSTED = "budget_exhausted"
    VERSION_CONFLICT = "version_conflict"
    UNREACHABLE = "unreachable"
    OUTCOME_UNKNOWN = "outcome_unknown"


@dataclass(frozen=True)
class Reconciliation:
    """How to reconcile an outcome_unknown without blind retry."""

    strategy: str  # "query_by_idempotency_key" | "query_by_run_ref"
    key: str

    def to_dict(self) -> dict[str, str]:
        return {"strategy": self.strategy, "key": self.key}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Reconciliation:
        return cls(strategy=data["strategy"], key=data["key"])


@dataclass(frozen=True)
class BackendError:
    """Contract error DTO; every error carries a request reference."""

    code: BackendErrorCode
    request_ref: BackendRef
    message: str
    detail_ns: dict[str, Any] = field(default_factory=dict)
    reconciliation: Reconciliation | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.code, BackendErrorCode):
            raise ValueError(f"error code must be a BackendErrorCode, got {self.code!r}")
        if not self.message:
            raise ValueError("message must be a non-empty string")

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "code": self.code.value,
            "request_ref": self.request_ref.to_dict(),
            "message": self.message,
        }
        if self.detail_ns:
            out["detail_ns"] = self.detail_ns
        if self.reconciliation is not None:
            out["reconciliation"] = self.reconciliation.to_dict()
        out.update(self.extra)
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BackendError:
        known = {"code", "request_ref", "message", "detail_ns", "reconciliation"}
        extra = {key: value for key, value in data.items() if key not in known}
        return cls(
            code=BackendErrorCode(data["code"]),
            request_ref=BackendRef.from_dict(data["request_ref"]),
            message=data["message"],
            detail_ns=data.get("detail_ns", {}),
            reconciliation=(Reconciliation.from_dict(data["reconciliation"]) if "reconciliation" in data else None),
            extra=extra,
        )
