"""Tests for the invocation identity contract (task 1.1)."""

import pytest
from hecate_governance.action import tool_arguments_digest
from hecate_governance.invocation import (
    INVOCATION_NAMESPACE_MODEL,
    INVOCATION_NAMESPACE_TOOL,
    InvocationConflictError,
    InvocationConflictField,
    InvocationIdentity,
    ensure_invocation_id,
    new_invocation_id,
)
from hecate_governance.principal import ActionPrincipal, PrincipalSource


def _principal(actor: str = "user-1") -> ActionPrincipal:
    return ActionPrincipal.from_verified_claims(
        {"sub": actor},
        source=PrincipalSource.RUNNER_LOCAL,
        domains=("inventory",),
    )


class TestNewInvocationId:
    def test_namespaced_and_unique(self) -> None:
        first = new_invocation_id(INVOCATION_NAMESPACE_TOOL)
        second = new_invocation_id(INVOCATION_NAMESPACE_TOOL)
        assert first.startswith("tool-")
        assert first != second

    def test_unknown_namespace_rejected(self) -> None:
        with pytest.raises(ValueError, match="namespace"):
            new_invocation_id("agent")

    def test_model_namespace_separate_from_tool(self) -> None:
        model_id = new_invocation_id(INVOCATION_NAMESPACE_MODEL)
        assert model_id.startswith("model-")
        assert not model_id.startswith("tool-")


class TestEnsureInvocationId:
    def test_fresh_call_generates(self) -> None:
        assert ensure_invocation_id(None, INVOCATION_NAMESPACE_TOOL).startswith("tool-")

    def test_recovery_reuses_persisted_id(self) -> None:
        # Same logical call across attempts/restarts keeps the same id: the
        # recovering path reuses the persisted id instead of re-signing.
        original = new_invocation_id(INVOCATION_NAMESPACE_TOOL)
        assert ensure_invocation_id(original, INVOCATION_NAMESPACE_TOOL) == original

    def test_foreign_namespace_rejected(self) -> None:
        model_id = new_invocation_id(INVOCATION_NAMESPACE_MODEL)
        with pytest.raises(InvocationConflictError) as exc_info:
            ensure_invocation_id(model_id, INVOCATION_NAMESPACE_TOOL)
        assert exc_info.value.field == InvocationConflictField.NAMESPACE


class TestConflict:
    def _identity(
        self,
        invocation_id: str,
        arguments: dict | None = None,
        actor: str = "user-1",
    ) -> InvocationIdentity:
        return InvocationIdentity(
            invocation_id=invocation_id,
            arguments_digest=tool_arguments_digest(arguments or {"path": "a.txt"}),
            principal_fingerprint=_principal(actor).fingerprint(),
        )

    def test_same_identity_no_conflict(self) -> None:
        stored = self._identity("tool-1")
        stored.check_conflict(self._identity("tool-1"))

    def test_same_id_different_arguments_conflict(self) -> None:
        stored = self._identity("tool-1", {"path": "a.txt"})
        with pytest.raises(InvocationConflictError) as exc_info:
            stored.check_conflict(self._identity("tool-1", {"path": "b.txt"}))
        assert exc_info.value.field == InvocationConflictField.ARGUMENTS_DIGEST

    def test_same_id_different_principal_conflict(self) -> None:
        stored = self._identity("tool-1", actor="user-1")
        with pytest.raises(InvocationConflictError) as exc_info:
            stored.check_conflict(self._identity("tool-1", actor="user-2"))
        assert exc_info.value.field == InvocationConflictField.PRINCIPAL_FINGERPRINT

    def test_arguments_digest_canonical(self) -> None:
        # Key order must not change the digest (approval bindings compare it).
        assert tool_arguments_digest({"a": 1, "b": 2}) == tool_arguments_digest({"b": 2, "a": 1})
