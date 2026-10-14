"""Tests for the managed lease authorization profile (I-Cb slice)."""

from datetime import UTC, datetime, timedelta
from typing import Any

from hecate_governance.action import ActionDecision
from hecate_governance.clock import TimeAnchor, TrustedClock
from hecate_governance.managed import (
    ManagedLeaseFacts,
    ManagedLeasePolicyConfig,
    ManagedLeaseProfile,
    ManagedRevocationView,
    facts_from_claims,
)
from hecate_governance.policy import PolicyContext, PolicyOutcome, PolicyProfile
from hecate_governance.principal import ActionPrincipal, PrincipalSource

NOW = datetime(2026, 10, 13, 12, 0, 0, tzinfo=UTC)


class ScriptedRulesProfile(PolicyProfile):
    """Stands in for the runner's local profile: allow-all rules."""

    def __init__(self) -> None:
        self.calls: list[PolicyContext] = []

    @property
    def version(self) -> str:
        return "rules-stub-1"

    async def evaluate(self, context: PolicyContext) -> PolicyOutcome:
        self.calls.append(context)
        return PolicyOutcome(ActionDecision.ALLOW, self.version, "locally authorized")


def _principal() -> ActionPrincipal:
    return ActionPrincipal.from_verified_claims(
        {"sub": "host-alpha"},
        source=PrincipalSource.MANAGED_DELIVERY,
        domains=("inventory",),
        deployment_id="dealer-app",
    )


def _context(**overrides: Any) -> PolicyContext:
    base: dict[str, Any] = {
        "principal": _principal(),
        "tool_name": "inventory.write",
        "arguments": {"domain": "inventory"},
        "arguments_digest": "digest-1",
        "invocation_id": "tool-abc",
        "task_ref": "task-1",
        "deployment_id": "dealer-app",
        "tool_side_effect": "idempotent_write",
    }
    base.update(overrides)
    return PolicyContext(**base)


def _facts(**overrides: Any) -> ManagedLeaseFacts:
    base: dict[str, Any] = {
        "issuer": "managed-workspace-alpha",
        "audience": "dealer-app",
        "subject": "host-alpha",
        "tenant": "ws-1",
        "scope": ("inventory",),
        "policy_version": "mp-2026.10",
        "issued_at": NOW - timedelta(seconds=30),
        "expires_at": NOW + timedelta(seconds=90),
    }
    base.update(overrides)
    return ManagedLeaseFacts(**base)


class _Gate:
    """Mutable facts/anchor holders the profile re-reads per evaluation."""

    def __init__(self, facts: ManagedLeaseFacts | None, anchor: TimeAnchor | None) -> None:
        self.facts = facts
        self.anchor = anchor


def _profile(
    gate: _Gate, config: ManagedLeasePolicyConfig, *, clock: TrustedClock | None = None
) -> ManagedLeaseProfile:
    inner = ScriptedRulesProfile()
    if clock is None:
        # Deterministic clock for window tests: wall time is NOW and matches
        # the fixture anchors; tolerance is wide because these tests target
        # window arithmetic, not clock validation (covered by its own class
        # with tight-tolerance clocks).
        clock = TrustedClock(now_fn=lambda: NOW, monotonic_fn=lambda: 1000.0, tolerance_seconds=1e9)
    return ManagedLeaseProfile(
        inner,
        config,
        facts_provider=lambda: gate.facts,
        anchor_provider=lambda: gate.anchor,
        clock=clock,
    )


class TestManagedGate:
    async def test_readonly_bypasses_the_lease_gate(self) -> None:
        """Readonly dispatch flows through the inner rules even with no
        lease installed — the managed profile neither tightens nor loosens
        readonly semantics."""

        inner = ScriptedRulesProfile()
        profile = ManagedLeaseProfile(
            inner,
            ManagedLeasePolicyConfig(expected_policy_version="mp-2026.10"),
            facts_provider=lambda: None,
            anchor_provider=lambda: None,
        )
        outcome = await profile.evaluate(_context(tool_side_effect=None))
        assert outcome.decision is ActionDecision.ALLOW
        assert len(inner.calls) == 1

    async def test_protected_without_lease_denied(self) -> None:
        profile = _profile(
            _Gate(None, None),
            ManagedLeasePolicyConfig(expected_policy_version="mp-2026.10"),
        )
        outcome = await profile.evaluate(_context())
        assert outcome.decision is ActionDecision.DENY
        assert "no managed lease" in outcome.reason

    async def test_undeclared_version_keeps_the_facts_gate_only(self) -> None:
        """An undeclared expected version has nothing to be inconsistent
        with: the profile still requires installed lease facts and a valid
        window (disconnect protection), but does not hard-deny on version —
        step6 assemblies without a lease_policy block keep working."""

        gate = _Gate(_facts(), TimeAnchor(wall=NOW, monotonic=1000.0))
        profile = _profile(gate, ManagedLeasePolicyConfig(expected_policy_version=None))
        outcome = await profile.evaluate(_context())
        assert outcome.decision is ActionDecision.ALLOW
        # The disconnect protection is unchanged: no facts, no protected.
        empty = _Gate(None, None)
        denied = await _profile(empty, ManagedLeasePolicyConfig(expected_policy_version=None)).evaluate(_context())
        assert denied.decision is ActionDecision.DENY
        assert "no managed lease" in denied.reason

    async def test_policy_version_mismatch_denied(self) -> None:
        gate = _Gate(
            _facts(policy_version="mp-2026.09"),
            TimeAnchor(wall=NOW, monotonic=1000.0),
        )
        profile = _profile(gate, ManagedLeasePolicyConfig(expected_policy_version="mp-2026.10"))
        outcome = await profile.evaluate(_context())
        assert outcome.decision is ActionDecision.DENY
        assert "mp-2026.09" in outcome.reason

    async def test_legacy_lease_without_version_denied(self) -> None:
        gate = _Gate(
            _facts(policy_version=None, issued_at=None),
            TimeAnchor(wall=NOW, monotonic=1000.0),
        )
        profile = _profile(gate, ManagedLeasePolicyConfig(expected_policy_version="mp-2026.10"))
        outcome = await profile.evaluate(_context())
        assert outcome.decision is ActionDecision.DENY
        assert "none" in outcome.reason

    async def test_issuer_allowlist_cross_check(self) -> None:
        gate = _Gate(_facts(), TimeAnchor(wall=NOW, monotonic=1000.0))
        config = ManagedLeasePolicyConfig(
            expected_policy_version="mp-2026.10",
            allowed_issuers=("managed-workspace-other",),
        )
        outcome = await _profile(gate, config).evaluate(_context())
        assert outcome.decision is ActionDecision.DENY
        assert "allowlist" in outcome.reason

    async def test_matching_lease_reaches_inner_rules(self) -> None:
        gate = _Gate(_facts(), TimeAnchor(wall=NOW, monotonic=1000.0))
        profile = _profile(gate, ManagedLeasePolicyConfig(expected_policy_version="mp-2026.10"))
        outcome = await profile.evaluate(_context())
        assert outcome.decision is ActionDecision.ALLOW
        assert outcome.policy_version.startswith("managed-lease-1/")
        assert len(profile._inner.calls) == 1  # noqa: SLF001 - test introspection

    async def test_inner_deny_still_denies_protected(self) -> None:
        class DenyRules(ScriptedRulesProfile):
            async def evaluate(self, context: PolicyContext) -> PolicyOutcome:
                return PolicyOutcome(ActionDecision.DENY, self.version, "tool not locally authorized")

        profile = ManagedLeaseProfile(
            DenyRules(),
            ManagedLeasePolicyConfig(expected_policy_version="mp-2026.10"),
            facts_provider=lambda: _facts(),
            anchor_provider=lambda: TimeAnchor(wall=NOW, monotonic=1000.0),
            clock=TrustedClock(now_fn=lambda: NOW, monotonic_fn=lambda: 1000.0),
        )
        outcome = await profile.evaluate(_context())
        assert outcome.decision is ActionDecision.DENY
        assert "not locally authorized" in outcome.reason


class TestStalenessWindow:
    async def test_staleness_deadline_earlier_than_expiry_governs(self) -> None:
        """Offline validity = min(exp, iat + staleness): the lease is not yet
        expired but its staleness window has passed — protected stops."""

        issued = NOW - timedelta(seconds=100)
        facts = _facts(issued_at=issued, expires_at=NOW + timedelta(seconds=600))
        gate = _Gate(facts, TimeAnchor(wall=issued, monotonic=1000.0))
        config = ManagedLeasePolicyConfig(
            expected_policy_version="mp-2026.10",
            max_staleness_seconds=50.0,
        )
        outcome = await _profile(gate, config).evaluate(_context())
        assert outcome.decision is ActionDecision.DENY
        assert "window exhausted" in outcome.reason

    async def test_expiry_earlier_than_staleness_governs(self) -> None:
        issued = NOW - timedelta(seconds=10)
        facts = _facts(issued_at=issued, expires_at=NOW - timedelta(seconds=1))
        gate = _Gate(facts, TimeAnchor(wall=issued, monotonic=1000.0))
        config = ManagedLeasePolicyConfig(
            expected_policy_version="mp-2026.10",
            max_staleness_seconds=600.0,
        )
        outcome = await _profile(gate, config).evaluate(_context())
        assert outcome.decision is ActionDecision.DENY
        assert "window exhausted" in outcome.reason

    async def test_within_window_allowed(self) -> None:
        issued = NOW - timedelta(seconds=10)
        facts = _facts(issued_at=issued, expires_at=NOW + timedelta(seconds=20))
        gate = _Gate(facts, TimeAnchor(wall=issued, monotonic=1000.0))
        config = ManagedLeasePolicyConfig(
            expected_policy_version="mp-2026.10",
            max_staleness_seconds=30.0,
        )
        outcome = await _profile(gate, config).evaluate(_context())
        assert outcome.decision is ActionDecision.ALLOW

    async def test_no_staleness_configured_expiry_only(self) -> None:
        issued = NOW - timedelta(seconds=600)
        facts = _facts(issued_at=issued, expires_at=NOW + timedelta(seconds=20))
        gate = _Gate(facts, TimeAnchor(wall=issued, monotonic=1000.0))
        config = ManagedLeasePolicyConfig(expected_policy_version="mp-2026.10")
        outcome = await _profile(gate, config).evaluate(_context())
        assert outcome.decision is ActionDecision.ALLOW


class TestTrustedTimeGate:
    async def test_missing_anchor_denies(self) -> None:
        gate = _Gate(_facts(), None)
        profile = _profile(gate, ManagedLeasePolicyConfig(expected_policy_version="mp-2026.10"))
        outcome = await profile.evaluate(_context())
        assert outcome.decision is ActionDecision.DENY
        assert "anchor" in outcome.reason

    async def test_clock_rollback_denies(self) -> None:
        """A rolled-back wall clock must not extend the managed window: the
        judge refuses to evaluate deadlines against untrusted time."""

        issued = NOW - timedelta(seconds=30)
        gate = _Gate(_facts(issued_at=issued), TimeAnchor(wall=issued, monotonic=1000.0))
        clock = TrustedClock(now_fn=lambda: issued - timedelta(hours=2), monotonic_fn=lambda: 1100.0)
        config = ManagedLeasePolicyConfig(
            expected_policy_version="mp-2026.10",
            max_staleness_seconds=3600.0,
        )
        outcome = await _profile(gate, config, clock=clock).evaluate(_context())
        assert outcome.decision is ActionDecision.DENY
        assert "time cannot be trusted" in outcome.reason

    async def test_monotonic_regression_denies(self) -> None:
        gate = _Gate(_facts(), TimeAnchor(wall=NOW, monotonic=1000.0))
        clock = TrustedClock(now_fn=lambda: NOW, monotonic_fn=lambda: 900.0)
        profile = _profile(gate, ManagedLeasePolicyConfig(expected_policy_version="mp-2026.10"), clock=clock)
        outcome = await profile.evaluate(_context())
        assert outcome.decision is ActionDecision.DENY
        assert "time cannot be trusted" in outcome.reason


class TestFactsRefresh:
    async def test_newly_installed_lease_takes_effect(self) -> None:
        """Facts re-read per evaluation: the gate starts empty (protected
        denied), the first pull installs a lease and the same profile
        admits the next action."""

        gate = _Gate(None, None)
        profile = _profile(gate, ManagedLeasePolicyConfig(expected_policy_version="mp-2026.10"))
        denied = await profile.evaluate(_context())
        assert denied.decision is ActionDecision.DENY
        gate.facts = _facts()
        gate.anchor = TimeAnchor(wall=NOW, monotonic=1000.0)
        allowed = await profile.evaluate(_context())
        assert allowed.decision is ActionDecision.ALLOW


class TestRevocationMerge:
    async def test_local_revocation_detected(self) -> None:
        class LocalView:
            async def is_revoked(self, principal: ActionPrincipal) -> bool:
                return principal.actor == "host-alpha"

        view = ManagedRevocationView(LocalView())
        assert await view.is_revoked(_principal()) is True

    async def test_trust_root_revocation_detected(self) -> None:
        class LocalView:
            async def is_revoked(self, principal: ActionPrincipal) -> bool:
                return False

        view = ManagedRevocationView(LocalView(), trust_revoked_provider=lambda: True)
        assert await view.is_revoked(_principal()) is True

    async def test_neither_source_revoked(self) -> None:
        class LocalView:
            async def is_revoked(self, principal: ActionPrincipal) -> bool:
                return False

        view = ManagedRevocationView(LocalView(), trust_revoked_provider=lambda: False)
        assert await view.is_revoked(_principal()) is False


class TestFactsFromClaims:
    def test_duck_typed_claims_conversion(self) -> None:
        class ClaimsMirror:
            iss = "managed-workspace-alpha"
            aud = "dealer-app"
            sub = "host-alpha"
            tenant = "ws-1"
            scope = ["inventory", "pricing"]
            policy_version = "mp-2026.10"
            iat = (NOW - timedelta(seconds=30)).isoformat()
            exp = (NOW + timedelta(seconds=90)).isoformat()

        facts = facts_from_claims(ClaimsMirror())
        assert facts.issuer == "managed-workspace-alpha"
        assert facts.scope == ("inventory", "pricing")
        assert facts.policy_version == "mp-2026.10"
        assert facts.issued_at == NOW - timedelta(seconds=30)
        assert facts.expires_at == NOW + timedelta(seconds=90)

    def test_naive_timestamps_assumed_utc(self) -> None:
        class ClaimsMirror:
            iss = "i"
            aud = "a"
            sub = "s"
            tenant = None
            scope = None
            policy_version = None
            iat = "2026-10-13T11:59:30"
            exp = "2026-10-13T12:01:30"

        facts = facts_from_claims(ClaimsMirror())
        assert facts.issued_at is not None and facts.issued_at.tzinfo is UTC
        assert facts.expires_at is not None and facts.expires_at.tzinfo is UTC
