"""IdentityChain - the four-slot identity vocabulary for one execution.

Distinguishes the plan's four identity types (step4): the human initiator,
the agent principal, the execution workload identity, and the optional
on-behalf-of delegation. The chain is a data contract only - possessing one
is not authentication, and workload identity never inherits human or
platform-administrator standing (plan step4/step7). Platform-side actor ids
(``initiator``, ``principal_id``) are opaque platform-issued identifiers;
references to contract-kind entities use :class:`BackendRef`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hecate.contracts.execution.references import BackendRef, RefKind, require_kind


@dataclass(frozen=True)
class WorkloadIdentity:
    """The execution workload behind a run.

    ``deployment`` pins the deployment instance that proves the workload;
    ``workload_id`` is the workload's own identity string and aligns with the
    ``SecurityClaims.sub`` semantics (never a human or platform-administrator
    principal).
    """

    deployment: BackendRef
    workload_id: str

    def __post_init__(self) -> None:
        require_kind(self.deployment, RefKind.DEPLOYMENT)
        if not isinstance(self.workload_id, str) or not self.workload_id.strip():
            raise ValueError("workload_id must be a non-empty string")

    def to_dict(self) -> dict[str, Any]:
        return {"deployment": self.deployment.to_dict(), "workload_id": self.workload_id}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkloadIdentity:
        return cls(deployment=BackendRef.from_dict(data["deployment"]), workload_id=data["workload_id"])


@dataclass(frozen=True)
class IdentityChain:
    """One execution's identity chain across the four identity types.

    ``initiator`` is the platform id of the human initiator; ``None`` records
    system/automation initiation explicitly rather than fabricating a human.
    ``principal_id`` is the agent principal (an enterprise responsibility
    subject, never a persona string). ``on_behalf_of`` carries the delegation
    reference when the workload acts on behalf of another principal.
    ``audience`` fixes the intended target; legacy chains may omit it, but
    new platform runs require it. The chain never authenticates its claims.
    """

    initiator: str | None
    principal_id: str
    workload: WorkloadIdentity
    on_behalf_of: BackendRef | None = None
    audience: str | None = None

    def __post_init__(self) -> None:
        if self.initiator is not None and (not isinstance(self.initiator, str) or not self.initiator.strip()):
            raise ValueError("initiator must be a non-empty string or None (system-initiated)")
        if not isinstance(self.principal_id, str) or not self.principal_id.strip():
            raise ValueError("principal_id must be a non-empty string")
        if self.on_behalf_of is not None and not isinstance(self.on_behalf_of, BackendRef):
            raise ValueError("on_behalf_of must be a BackendRef when present")
        if not isinstance(self.workload, WorkloadIdentity):
            raise ValueError("workload must be a WorkloadIdentity")
        if self.on_behalf_of is not None:
            require_kind(self.on_behalf_of, RefKind.AUTHORIZATION)
        if self.audience is not None and (not isinstance(self.audience, str) or not self.audience.strip()):
            raise ValueError("audience must be a non-empty string when present")

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "initiator": self.initiator,
            "principal_id": self.principal_id,
            "workload": self.workload.to_dict(),
        }
        if self.on_behalf_of is not None:
            out["on_behalf_of"] = self.on_behalf_of.to_dict()
        if self.audience is not None:
            out["audience"] = self.audience
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> IdentityChain:
        return cls(
            initiator=data.get("initiator"),
            principal_id=data["principal_id"],
            workload=WorkloadIdentity.from_dict(data["workload"]),
            on_behalf_of=(BackendRef.from_dict(data["on_behalf_of"]) if "on_behalf_of" in data else None),
            audience=data.get("audience"),
        )
