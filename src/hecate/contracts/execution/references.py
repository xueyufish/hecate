"""Logical references for the execution contract.

A reference is an opaque identifier with an issuing domain. Receivers resolve
references from the request itself and trusted local registration, never by
querying a platform ORM. Platform-side kinds (task/run/deployment/authorization)
and vendor-side kinds (session/turn) are distinct: a reference of one kind MUST
NOT be passed where another kind is required.

Encoding: IDs are opaque non-empty strings; kinds are lowercase enum strings.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class RefKind(StrEnum):
    """Discriminant separating platform-side from vendor-side references."""

    TASK = "task"
    RUN = "run"
    DEPLOYMENT = "deployment"
    SESSION = "session"
    TURN = "turn"
    ARTIFACT = "artifact"
    EVIDENCE = "evidence"
    APPROVAL = "approval"
    AUTHORIZATION = "authorization"


@dataclass(frozen=True)
class BackendRef:
    """Opaque logical reference with an issuing domain."""

    kind: RefKind
    issuer_domain: str
    id: str

    def __post_init__(self) -> None:
        if not isinstance(self.kind, RefKind):
            raise ValueError(f"reference kind must be a RefKind, got {self.kind!r}")
        if not self.issuer_domain or not isinstance(self.issuer_domain, str):
            raise ValueError("issuer_domain must be a non-empty string")
        if not self.id or not isinstance(self.id, str):
            raise ValueError("id must be a non-empty string")

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind.value, "issuer_domain": self.issuer_domain, "id": self.id}

    @classmethod
    def from_dict(cls, data: dict[str, str]) -> BackendRef:
        return cls(
            kind=RefKind(data["kind"]),
            issuer_domain=data["issuer_domain"],
            id=data["id"],
        )


def require_kind(ref: BackendRef, kind: RefKind) -> BackendRef:
    """Validate that ``ref`` carries exactly ``kind``; raise otherwise."""

    if ref.kind is not kind:
        raise ValueError(f"reference kind mismatch: expected {kind.value}, got {ref.kind.value}")
    return ref


def task_ref(issuer_domain: str, id: str) -> BackendRef:
    return BackendRef(RefKind.TASK, issuer_domain, id)


def run_ref(issuer_domain: str, id: str) -> BackendRef:
    return BackendRef(RefKind.RUN, issuer_domain, id)


def deployment_ref(issuer_domain: str, id: str) -> BackendRef:
    return BackendRef(RefKind.DEPLOYMENT, issuer_domain, id)


def session_ref(issuer_domain: str, id: str) -> BackendRef:
    return BackendRef(RefKind.SESSION, issuer_domain, id)


def turn_ref(issuer_domain: str, id: str) -> BackendRef:
    return BackendRef(RefKind.TURN, issuer_domain, id)


def artifact_ref(issuer_domain: str, id: str) -> BackendRef:
    return BackendRef(RefKind.ARTIFACT, issuer_domain, id)


def evidence_ref(issuer_domain: str, id: str) -> BackendRef:
    return BackendRef(RefKind.EVIDENCE, issuer_domain, id)


def approval_ref(issuer_domain: str, id: str) -> BackendRef:
    return BackendRef(RefKind.APPROVAL, issuer_domain, id)


def authorization_ref(issuer_domain: str, id: str) -> BackendRef:
    return BackendRef(RefKind.AUTHORIZATION, issuer_domain, id)
