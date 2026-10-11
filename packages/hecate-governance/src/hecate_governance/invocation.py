"""Invocation identity contract for controlled actions.

``invocation_id`` is the logical-call identity of one controlled action or one
metered model call. The dispatch boundary that wraps the action generates it;
the id is logical-call scoped — the same logical call keeps the same id across
attempts, restarts and recovery (the recovering path reuses the persisted id
via :func:`ensure_invocation_id` instead of re-signing), while distinct logical
calls always receive distinct ids.

Tool actions and model calls use separate namespaces so the two ledgers never
collide. An id is persisted together with its arguments digest and principal
fingerprint; a repeat submission carrying the same id but a different digest or
principal is a conflict and must be rejected (never executed).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import StrEnum

INVOCATION_NAMESPACE_TOOL = "tool"
INVOCATION_NAMESPACE_MODEL = "model"
_NAMESPACES = (INVOCATION_NAMESPACE_TOOL, INVOCATION_NAMESPACE_MODEL)


class InvocationConflictField(StrEnum):
    """Which identity component disagreed on a repeated invocation id."""

    ARGUMENTS_DIGEST = "arguments_digest"
    PRINCIPAL_FINGERPRINT = "principal_fingerprint"
    NAMESPACE = "namespace"


class InvocationConflictError(Exception):
    """Same invocation id submitted with different identity components."""

    def __init__(
        self,
        invocation_id: str,
        field: InvocationConflictField,
        expected: str,
        actual: str,
    ) -> None:
        self.invocation_id = invocation_id
        self.field = field
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"invocation {invocation_id!r} conflict on {field.value}: expected {expected!r}, got {actual!r}"
        )


def new_invocation_id(namespace: str) -> str:
    """Generate a fresh invocation id at the dispatch boundary."""
    if namespace not in _NAMESPACES:
        raise ValueError(f"unknown invocation namespace {namespace!r}")
    return f"{namespace}-{uuid.uuid4().hex}"


def ensure_invocation_id(existing: str | None, namespace: str) -> str:
    """Reuse the persisted id on recovery; generate one for a fresh call.

    The recovering path must never mint a new id for an existing logical call —
    that would create a second same-purpose action. The namespace prefix is
    validated so a foreign-namespace id cannot be silently adopted.
    """
    if existing is None:
        return new_invocation_id(namespace)
    if not existing.startswith(f"{namespace}-"):
        raise InvocationConflictError(
            existing,
            InvocationConflictField.NAMESPACE,
            namespace,
            existing.split("-", 1)[0] if "-" in existing else "",
        )
    return existing


@dataclass(frozen=True)
class InvocationIdentity:
    """Identity components persisted with one invocation id."""

    invocation_id: str
    arguments_digest: str
    principal_fingerprint: str

    def check_conflict(self, incoming: InvocationIdentity) -> None:
        """Raise :class:`InvocationConflictError` on identity disagreement.

        Callers invoke this when a request arrives with an id that already has
        a persisted identity; a mismatch means the caller is trying to reuse
        someone else's id (or reuse an id for different arguments) and the
        dispatch must be rejected without side effects.
        """
        if self.arguments_digest != incoming.arguments_digest:
            raise InvocationConflictError(
                self.invocation_id,
                InvocationConflictField.ARGUMENTS_DIGEST,
                self.arguments_digest,
                incoming.arguments_digest,
            )
        if self.principal_fingerprint != incoming.principal_fingerprint:
            raise InvocationConflictError(
                self.invocation_id,
                InvocationConflictField.PRINCIPAL_FINGERPRINT,
                self.principal_fingerprint,
                incoming.principal_fingerprint,
            )
