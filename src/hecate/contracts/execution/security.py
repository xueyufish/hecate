"""SecurityClaims - out-of-process identity and authorization claim set.

This mapping defines claim names and constraints only. Possessing a claim set
is not authentication: verification (signature, audience check, expiry) is the
receiving adapter's responsibility (plan step7). Identity never comes from the
request body - self-asserted role or admin fields in a request body are not
parsed into any authorization structure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from hecate.contracts.execution.references import BackendRef


@dataclass(frozen=True)
class SecurityClaims:
    """Claims binding one caller workload to one target backend service.

    ``aud`` binds the set to a single target backend service (no cross-service
    replay); ``sub`` is the caller's workload identity, never a human or
    platform-administrator principal; ``exp`` is RFC3339 UTC and the set is
    short-lived by binding-document constraint.
    """

    iss: str
    aud: str
    sub: str
    tenant: str
    exp: str
    delegation_ref: BackendRef | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("iss", "aud", "sub", "tenant", "exp"):
            if not getattr(self, name):
                raise ValueError(f"security claim {name!r} must be a non-empty string")
        if self.aud == self.iss:
            raise ValueError("aud must bind a distinct target service, not the issuer itself")

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "iss": self.iss,
            "aud": self.aud,
            "sub": self.sub,
            "tenant": self.tenant,
            "exp": self.exp,
        }
        if self.delegation_ref is not None:
            out["delegation_ref"] = self.delegation_ref.to_dict()
        out.update(self.extra)
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SecurityClaims:
        known = {"iss", "aud", "sub", "tenant", "exp", "delegation_ref"}
        extra = {key: value for key, value in data.items() if key not in known}
        return cls(
            iss=data["iss"],
            aud=data["aud"],
            sub=data["sub"],
            tenant=data["tenant"],
            exp=data["exp"],
            delegation_ref=(BackendRef.from_dict(data["delegation_ref"]) if "delegation_ref" in data else None),
            extra=extra,
        )
