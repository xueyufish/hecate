"""Tests for the policy profile interface and the safety floor (task 1.4)."""

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import pytest
from hecate_governance.action import ActionDecision
from hecate_governance.approval import (
    ApprovalBinding,
    ApprovalJudgment,
    MemoryApprovalLedger,
)
from hecate_governance.policy import (
    BudgetSignal,
    FloorViolation,
    GovernanceService,
    PolicyContext,
    PolicyOutcome,
    PolicyProfile,
    RevocationView,
)
from hecate_governance.principal import ActionPrincipal, PrincipalSource

NOW = datetime(2026, 10, 11, 12, 0, 0, tzinfo=UTC)


def _principal(actor: str = "user-1") -> ActionPrincipal:
    return ActionPrincipal.from_verified_claims(
        {"sub": actor},
        source=PrincipalSource.RUNNER_LOCAL,
        domains=("inventory",),
        deployment_id="dealer-app",
    )


def _context(**overrides: Any) -> PolicyContext:
    base: dict[str, Any] = {
        "principal": _principal(),
        "tool_name": "inventory.write",
        "arguments": {"path": "sku-42/stock.csv"},
        "arguments_digest": "digest-1",
        "invocation_id": "tool-abc",
        "target_resources": ("sku-42",),
        "task_ref": "task-1",
        "deployment_id": "dealer-app",
    }
    base.update(overrides)
    return PolicyContext(**base)


class StubProfile(PolicyProfile):
    """Scripted policy for floor tests."""

    def __init__(self, outcome: PolicyOutcome, version: str = "stub-1") -> None:
        self._outcome = outcome
        self._version = version

    @property
    def version(self) -> str:
        return self._version

    async def evaluate(self, context: PolicyContext) -> PolicyOutcome:
        return self._outcome


class RaisingProfile(PolicyProfile):
    @property
    def version(self) -> str:
        return "raising-1"

    async def evaluate(self, context: PolicyContext) -> PolicyOutcome:
        raise RuntimeError("policy engine exploded")


class StubRevocation(RevocationView):
    def __init__(self, revoked_actors: set[str]) -> None:
        self._revoked = revoked_actors

    async def is_revoked(self, principal: ActionPrincipal) -> bool:
        return principal.actor in self._revoked


async def _ledger_with_binding(**overrides: Any) -> MemoryApprovalLedger:
    from datetime import timedelta

    base: dict[str, Any] = {
        "approval_ref": "appr-1",
        "principal_fingerprint": _principal().fingerprint(),
        "tool_name": "inventory.write",
        "arguments_digest": "digest-1",
        "target_resources": ("sku-42",),
        "deployment_id": "dealer-app",
        "domains": ("inventory",),
        "task_scope": "task-1",
        "issued_at": NOW - timedelta(minutes=5),
    }
    base.update(overrides)
    ledger = MemoryApprovalLedger()
    binding = ApprovalBinding(**base)
    await ledger.save(binding)
    if binding.judgment is ApprovalJudgment.PENDING:
        await ledger.decide("appr-1", approved=True, reason="ok", decided_by="approver-1")
    return ledger


class TestSafetyFloor:
    async def test_identity_missing_denied(self) -> None:
        service = GovernanceService(StubProfile(_allow()))
        decision = await service.authorize(_context(principal=None), now=NOW)
        assert not decision.allowed
        assert decision.violation is FloorViolation.IDENTITY_MISSING

    async def test_identity_unverifiable_denied(self) -> None:
        service = GovernanceService(StubProfile(_allow()))
        # Direct construction: an envelope without a verified actor (e.g. a
        # hand-assembled one) must be rejected as unverifiable.
        unverifiable = ActionPrincipal(actor="", source=PrincipalSource.RUNNER_LOCAL, domains=("inventory",))
        decision = await service.authorize(_context(principal=unverifiable), now=NOW)
        assert decision.violation is FloorViolation.IDENTITY_MISSING

    async def test_evidence_unwritable_denied(self) -> None:
        service = GovernanceService(StubProfile(_allow()))
        decision = await service.authorize(_context(), now=NOW, evidence_writable=False)
        assert not decision.allowed
        assert decision.violation is FloorViolation.EVIDENCE_UNWRITABLE

    async def test_known_revocation_denied(self) -> None:
        service = GovernanceService(StubProfile(_allow()), revocation=StubRevocation({"user-1"}))
        decision = await service.authorize(_context(), now=NOW)
        assert not decision.allowed
        assert decision.violation is FloorViolation.PRINCIPAL_REVOKED

    async def test_policy_hard_deny_denied(self) -> None:
        service = GovernanceService(StubProfile(PolicyOutcome(ActionDecision.DENY, "stub-1", "forbidden tool")))
        decision = await service.authorize(_context(), now=NOW)
        assert not decision.allowed
        assert decision.violation is FloorViolation.POLICY_HARD_DENY

    async def test_policy_evaluation_failure_fails_closed(self) -> None:
        service = GovernanceService(RaisingProfile())
        decision = await service.authorize(_context(), now=NOW)
        assert not decision.allowed
        assert decision.violation is FloorViolation.POLICY_HARD_DENY
        assert "policy engine exploded" in decision.reason

    async def test_budget_exhausted_blocks_metered_only(self) -> None:
        service = GovernanceService(StubProfile(_allow()))
        denied = await service.authorize(_context(metered=True, budget=BudgetSignal.EXHAUSTED), now=NOW)
        assert not denied.allowed
        assert denied.violation is FloorViolation.BUDGET_EXHAUSTED
        unmetered = await service.authorize(_context(metered=False, budget=BudgetSignal.EXHAUSTED), now=NOW)
        assert unmetered.allowed

    async def test_reservation_failure_blocks_metered(self) -> None:
        service = GovernanceService(StubProfile(_allow()))
        decision = await service.authorize(_context(metered=True, budget=BudgetSignal.RESERVATION_FAILED), now=NOW)
        assert decision.violation is FloorViolation.BUDGET_EXHAUSTED


class TestApprovalBranchScoping:
    async def test_policy_allowed_without_approval_executes(self) -> None:
        # The floor must not widen the approval requirement: an allow decision
        # runs without any approval reference.
        service = GovernanceService(StubProfile(_allow()))
        decision = await service.authorize(_context(), now=NOW, approval_ref=None)
        assert decision.allowed
        assert decision.violation is None

    async def test_require_approval_without_binding_denied(self) -> None:
        service = GovernanceService(StubProfile(_require_approval()), approval_ledger=MemoryApprovalLedger())
        decision = await service.authorize(_context(), now=NOW, approval_ref=None)
        assert not decision.allowed
        assert decision.violation is FloorViolation.APPROVAL_BINDING_INVALID

    async def test_require_approval_with_valid_binding_allowed(self) -> None:
        ledger = await _ledger_with_binding()
        service = GovernanceService(StubProfile(_require_approval()), approval_ledger=ledger)
        decision = await service.authorize(_context(), now=NOW, approval_ref="appr-1")
        assert decision.allowed
        assert decision.binding is not None
        assert decision.binding.check.value == "ok"

    async def test_require_approval_with_mismatched_binding_denied(self) -> None:
        ledger = await _ledger_with_binding()
        service = GovernanceService(StubProfile(_require_approval()), approval_ledger=ledger)
        decision = await service.authorize(_context(arguments_digest="digest-2"), now=NOW, approval_ref="appr-1")
        assert not decision.allowed
        assert decision.violation is FloorViolation.APPROVAL_BINDING_INVALID
        assert decision.binding is not None

    async def test_argument_content_reaches_the_profile(self) -> None:
        # Parameter-conditional rules evaluate on validated content, not the
        # digest — the profile receives the actual arguments mapping.
        seen: dict[str, Mapping[str, Any]] = {}

        class CapturingProfile(PolicyProfile):
            @property
            def version(self) -> str:
                return "capture-1"

            async def evaluate(self, context: PolicyContext) -> PolicyOutcome:
                seen["args"] = context.arguments
                return _allow()

        service = GovernanceService(CapturingProfile())
        await service.authorize(_context(arguments={"path": ".env"}), now=NOW)
        assert seen["args"] == {"path": ".env"}


def _allow() -> PolicyOutcome:
    return PolicyOutcome(ActionDecision.ALLOW, "stub-1")


def _require_approval() -> PolicyOutcome:
    return PolicyOutcome(ActionDecision.REQUIRE_APPROVAL, "stub-1")


@pytest.mark.parametrize(
    ("violation", "kwargs"),
    [
        (FloorViolation.IDENTITY_MISSING, {"principal": None}),
        (FloorViolation.EVIDENCE_UNWRITABLE, {}),
    ],
)
async def test_floor_applies_regardless_of_profile(violation: FloorViolation, kwargs: dict[str, Any]) -> None:
    """Floor branches run even when the profile would allow."""
    service = GovernanceService(StubProfile(_allow()))
    if violation is FloorViolation.EVIDENCE_UNWRITABLE:
        decision = await service.authorize(_context(), now=NOW, evidence_writable=False)
    else:
        decision = await service.authorize(_context(**kwargs), now=NOW)
    assert decision.violation is violation
