"""Tests for the verified identity envelope (task 1.1)."""

import pytest
from hecate_governance.principal import ActionPrincipal, PrincipalSource
from pydantic import ValidationError


class TestFromVerifiedClaims:
    def test_verified_subject_becomes_actor(self) -> None:
        principal = ActionPrincipal.from_verified_claims(
            {"sub": "user-1"},
            source=PrincipalSource.PLATFORM_ENTRY,
            domains=("inventory",),
        )
        assert principal.actor == "user-1"
        assert principal.domains == ("inventory",)

    def test_self_asserted_fields_are_ignored(self) -> None:
        # Roles, domains and principal assertions inside the claims mapping are
        # authorization decisions: they must come from server-side derivation,
        # never from the request.
        principal = ActionPrincipal.from_verified_claims(
            {
                "sub": "user-1",
                "role": "admin",
                "agent_principal": "agent-9",
                "domains": ("finance", "hr"),
                "delegation_id": "dele-1",
            },
            source=PrincipalSource.PLATFORM_ENTRY,
            domains=("inventory",),
        )
        assert principal.actor == "user-1"
        assert principal.agent_principal is None
        assert principal.delegation_id is None
        assert principal.domains == ("inventory",)

    def test_missing_subject_rejected(self) -> None:
        with pytest.raises(ValueError, match="sub"):
            ActionPrincipal.from_verified_claims(
                {"user": "user-1"},
                source=PrincipalSource.PLATFORM_ENTRY,
                domains=(),
            )

    def test_empty_subject_rejected(self) -> None:
        with pytest.raises(ValueError, match="sub"):
            ActionPrincipal.from_verified_claims(
                {"sub": ""},
                source=PrincipalSource.PLATFORM_ENTRY,
                domains=(),
            )


class TestEnvelope:
    def test_envelope_is_immutable(self) -> None:
        principal = ActionPrincipal.from_verified_claims(
            {"sub": "user-1"},
            source=PrincipalSource.RUNNER_LOCAL,
            domains=("inventory",),
        )
        with pytest.raises(ValidationError):
            principal.actor = "user-2"

    def test_full_chain_fields(self) -> None:
        principal = ActionPrincipal.from_verified_claims(
            {"sub": "svc-runner"},
            source=PrincipalSource.MANAGED_DELIVERY,
            domains=("inventory",),
            deployment_id="dealer-app",
            agent_principal="agent-7",
            on_behalf_of="user-2",
            delegation_id="dele-3",
        )
        assert principal.deployment_id == "dealer-app"
        assert principal.agent_principal == "agent-7"
        assert principal.on_behalf_of == "user-2"
        assert principal.delegation_id == "dele-3"

    def test_fingerprint_stable_and_discriminating(self) -> None:
        base = ActionPrincipal.from_verified_claims(
            {"sub": "user-1"},
            source=PrincipalSource.PLATFORM_ENTRY,
            domains=("inventory",),
        )
        same = ActionPrincipal.from_verified_claims(
            {"sub": "user-1"},
            source=PrincipalSource.PLATFORM_ENTRY,
            domains=("inventory",),
        )
        other_domains = ActionPrincipal.from_verified_claims(
            {"sub": "user-1"},
            source=PrincipalSource.PLATFORM_ENTRY,
            domains=("finance",),
        )
        other_actor = ActionPrincipal.from_verified_claims(
            {"sub": "user-2"},
            source=PrincipalSource.PLATFORM_ENTRY,
            domains=("inventory",),
        )
        assert base.fingerprint() == same.fingerprint()
        assert base.fingerprint() != other_domains.fingerprint()
        assert base.fingerprint() != other_actor.fingerprint()
