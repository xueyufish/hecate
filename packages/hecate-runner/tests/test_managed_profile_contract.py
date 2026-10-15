"""Shared contract tests: local vs managed profile rule parity (task 1.3).

The managed lease profile composes the runner's local rules profile; the
contract under test is that rule *decisions* do not drift between the two
assemblies — same input, same judgment — and that the GovernanceService
safety floor behaves identically regardless of which profile is mounted.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from hecate_governance.action import ActionDecision
from hecate_governance.clock import TimeAnchor, TrustedClock
from hecate_governance.managed import (
    ManagedLeaseFacts,
    ManagedLeasePolicyConfig,
    ManagedLeaseProfile,
)
from hecate_governance.policy import FloorViolation, GovernanceService, PolicyContext
from hecate_governance.principal import ActionPrincipal, PrincipalSource
from hecate_runner.governance import LocalPolicyProfile, LocalRevocationView

NOW = datetime(2026, 10, 13, 12, 0, 0, tzinfo=UTC)


class _GovernanceConfig:
    """Duck-typed stand-in for the profile's GovernanceLocalConfig.

    ``write_enablement`` is the experimental tier so write-class actions
    reach the rule match (the default ``disabled`` tier refuses every
    protected write before rules run — that posture is tested elsewhere).
    """

    def __init__(self, rules: tuple[dict, ...]) -> None:
        self.rules = rules
        self.write_enablement = "experimental"
        self.write_attestation = "business-api-enforces-final-authorization"
        self.revoked_principals: tuple[str, ...] = ()


def _rules() -> tuple[dict, ...]:
    return (
        {"tool": "query_inventory", "domains": {"domain_a"}, "approval_required": False},
        {"tool": "update_listing", "domains": {"domain_a"}, "approval_required": False},
        {"tool": "needs_signoff", "domains": {"domain_a"}, "approval_required": True},
    )


def _revoked_config() -> _GovernanceConfig:
    config = _GovernanceConfig(_rules())
    config.revoked_principals = ("app-writer",)
    return config


def _principal(source: PrincipalSource = PrincipalSource.RUNNER_LOCAL) -> ActionPrincipal:
    return ActionPrincipal.from_verified_claims(
        {"sub": "app-writer"},
        source=source,
        domains=("domain_a",),
        deployment_id="dealer-app",
    )


def _context(tool: str, side_effect: str | None, **kwargs: Any) -> PolicyContext:
    base: dict[str, Any] = {
        "principal": _principal(),
        "tool_name": tool,
        "arguments": {"domain": "domain_a"},
        "arguments_digest": "digest-1",
        "invocation_id": "tool-1",
        "deployment_id": "dealer-app",
        "tool_side_effect": side_effect,
    }
    base.update(kwargs)
    return PolicyContext(**base)


def _managed(inner: LocalPolicyProfile, *, facts: ManagedLeaseFacts | None) -> ManagedLeaseProfile:
    anchor = TimeAnchor(wall=NOW - timedelta(seconds=10), monotonic=1000.0) if facts else None
    return ManagedLeaseProfile(
        inner,
        ManagedLeasePolicyConfig(expected_policy_version="mp-2026.10", max_staleness_seconds=600.0),
        facts_provider=lambda: facts,
        anchor_provider=lambda: anchor,
        clock=TrustedClock(now_fn=lambda: NOW, monotonic_fn=lambda: 1000.0),
    )


def _facts() -> ManagedLeaseFacts:
    return ManagedLeaseFacts(
        issuer="managed-workspace-alpha",
        audience="dealer-app",
        subject="host-alpha",
        tenant="ws",
        scope=("domain_a",),
        policy_version="mp-2026.10",
        issued_at=NOW - timedelta(seconds=10),
        expires_at=NOW + timedelta(seconds=110),
    )


class TestRuleParity:
    @pytest.mark.parametrize(
        ("tool", "side_effect"),
        [
            ("query_inventory", None),
            ("query_inventory", "read_only"),
            ("update_listing", "idempotent_write"),
            ("needs_signoff", "non_idempotent_write"),
            ("unknown_tool", "idempotent_write"),
        ],
    )
    async def test_same_input_same_decision_with_valid_lease(self, tool: str, side_effect: str | None) -> None:
        """With a valid lease installed, the managed profile reaches the same
        decision the bare local profile would (gate admits, rules decide)."""

        local = LocalPolicyProfile(_GovernanceConfig(_rules()))
        managed = _managed(LocalPolicyProfile(_GovernanceConfig(_rules())), facts=_facts())
        context = _context(tool, side_effect)
        local_outcome = await local.evaluate(context)
        managed_outcome = await managed.evaluate(context)
        assert local_outcome.decision is managed_outcome.decision

    async def test_missing_lease_only_blocks_protected(self) -> None:
        """Without lease facts the managed profile refuses exactly the
        write-class actions the local profile would have allowed — never the
        readonly ones (no degradation into local self-authorization, no
        readonly tightening)."""

        local = LocalPolicyProfile(_GovernanceConfig(_rules()))
        managed = _managed(LocalPolicyProfile(_GovernanceConfig(_rules())), facts=None)
        readonly = _context("query_inventory", None)
        assert (await local.evaluate(readonly)).decision is ActionDecision.ALLOW
        assert (await managed.evaluate(readonly)).decision is ActionDecision.ALLOW
        write = _context("update_listing", "idempotent_write")
        assert (await local.evaluate(write)).decision is ActionDecision.ALLOW
        assert (await managed.evaluate(write)).decision is ActionDecision.DENY


class TestFloorParity:
    async def test_floor_branches_identical_across_profiles(self) -> None:
        """The safety floor belongs to GovernanceService, not the profile:
        identity-missing and revocation denials are the same floor branches
        whether the local or the managed profile is mounted."""

        for profile in (
            LocalPolicyProfile(_GovernanceConfig(_rules())),
            _managed(LocalPolicyProfile(_GovernanceConfig(_rules())), facts=_facts()),
        ):
            service = GovernanceService(profile)
            missing = await service.authorize(_context("update_listing", "idempotent_write", principal=None), now=NOW)
            assert missing.violation is FloorViolation.IDENTITY_MISSING
            revoked = await service.authorize(
                _context("update_listing", "idempotent_write", principal=_principal()),
                now=NOW,
            )
            assert revoked.allowed

    async def test_floor_revocation_overrides_profile_allow(self) -> None:
        """A revoked principal is refused under both profiles even where the
        rules would allow (floor is not profile-dependent). Revocation flows
        through the LocalRevocationView wired into GovernanceService."""

        for profile in (
            LocalPolicyProfile(_GovernanceConfig(_rules())),
            _managed(LocalPolicyProfile(_GovernanceConfig(_rules())), facts=_facts()),
        ):
            revocation = LocalRevocationView(_revoked_config())
            service = GovernanceService(profile, revocation=revocation)
            decision = await service.authorize(_context("query_inventory", None), now=NOW)
            assert not decision.allowed
            assert decision.violation is FloorViolation.PRINCIPAL_REVOKED
