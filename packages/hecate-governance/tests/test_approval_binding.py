"""Tests for approval binding validation and the consume protocol (task 1.2)."""

from datetime import UTC, datetime, timedelta

from hecate_governance.approval import (
    ActionRequest,
    ApprovalBinding,
    ApprovalGate,
    ApprovalJudgment,
    BindingCheck,
    BindingMismatch,
    ConsumeStatus,
    MemoryApprovalLedger,
    check_binding,
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


def _request(**overrides: object) -> ActionRequest:
    base: dict[str, object] = {
        "invocation_id": "tool-abc",
        "tool_name": "inventory.write",
        "arguments_digest": "digest-1",
        "target_resources": ("sku-42",),
        "principal_fingerprint": _principal().fingerprint(),
        "deployment_id": "dealer-app",
        "domains": ("inventory",),
        "task_scope": "task-1",
    }
    base.update(overrides)
    return ActionRequest(**base)


async def _approved_binding(ledger: MemoryApprovalLedger, **overrides: object) -> ApprovalBinding:
    base: dict[str, object] = {
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
    binding = ApprovalBinding(**base)
    await ledger.save(binding)
    ref = binding.approval_ref
    if binding.judgment is ApprovalJudgment.PENDING:
        await ledger.decide(ref, approved=True, reason="ok", decided_by="approver-1")
    stored = await ledger.get(ref)
    assert stored is not None
    return stored


class TestBindingCheck:
    async def test_full_binding_passes(self) -> None:
        ledger = MemoryApprovalLedger()
        binding = await _approved_binding(ledger)
        assert check_binding(binding, _request(), now=NOW).check is BindingCheck.OK

    async def test_parameter_change_requires_rejudgment(self) -> None:
        ledger = MemoryApprovalLedger()
        binding = await _approved_binding(ledger)
        verdict = check_binding(binding, _request(arguments_digest="digest-2"), now=NOW)
        assert verdict.check is BindingCheck.MISMATCH
        assert verdict.mismatch is BindingMismatch.ARGUMENTS

    async def test_resource_mismatch_rejected(self) -> None:
        ledger = MemoryApprovalLedger()
        binding = await _approved_binding(ledger)
        verdict = check_binding(binding, _request(target_resources=("sku-99",)), now=NOW)
        assert verdict.check is BindingCheck.MISMATCH
        assert verdict.mismatch is BindingMismatch.RESOURCES

    async def test_cross_principal_reuse_rejected(self) -> None:
        ledger = MemoryApprovalLedger()
        binding = await _approved_binding(ledger)
        other = _request(principal_fingerprint=_principal("user-2").fingerprint())
        verdict = check_binding(binding, other, now=NOW)
        assert verdict.check is BindingCheck.MISMATCH
        assert verdict.mismatch is BindingMismatch.PRINCIPAL

    async def test_cross_deployment_reuse_rejected(self) -> None:
        ledger = MemoryApprovalLedger()
        binding = await _approved_binding(ledger)
        verdict = check_binding(binding, _request(deployment_id="other-app"), now=NOW)
        assert verdict.check is BindingCheck.MISMATCH
        assert verdict.mismatch is BindingMismatch.DEPLOYMENT

    async def test_cross_domain_reuse_rejected(self) -> None:
        ledger = MemoryApprovalLedger()
        binding = await _approved_binding(ledger)
        verdict = check_binding(binding, _request(domains=("finance",)), now=NOW)
        assert verdict.check is BindingCheck.MISMATCH
        assert verdict.mismatch is BindingMismatch.DOMAINS

    async def test_expired_rejected(self) -> None:
        ledger = MemoryApprovalLedger()
        binding = await _approved_binding(ledger, expires_at=NOW - timedelta(seconds=1))
        assert check_binding(binding, _request(), now=NOW).check is BindingCheck.EXPIRED

    async def test_revoked_rejected(self) -> None:
        ledger = MemoryApprovalLedger()
        await _approved_binding(ledger)
        await ledger.revoke("appr-1")
        binding = await ledger.get("appr-1")
        assert binding is not None
        assert check_binding(binding, _request(), now=NOW).check is BindingCheck.REVOKED

    async def test_pending_not_authorized(self) -> None:
        ledger = MemoryApprovalLedger()
        await _approved_binding(ledger)
        raw = ApprovalBinding(
            approval_ref="appr-2",
            principal_fingerprint=_principal().fingerprint(),
            tool_name="inventory.write",
            arguments_digest="digest-1",
            issued_at=NOW - timedelta(minutes=1),
        )
        await ledger.save(raw)
        pending = await ledger.get("appr-2")
        assert pending is not None
        assert check_binding(pending, _request(), now=NOW).check is BindingCheck.NOT_APPROVED


class TestConsumeProtocol:
    async def test_pending_does_not_consume(self) -> None:
        ledger = MemoryApprovalLedger()
        await _approved_binding(ledger)
        await ledger.save(
            ApprovalBinding(
                approval_ref="appr-pending",
                principal_fingerprint=_principal().fingerprint(),
                tool_name="inventory.write",
                arguments_digest="digest-1",
                issued_at=NOW - timedelta(minutes=1),
            )
        )
        outcome = await ledger.consume("appr-pending", _request(), now=NOW)
        assert outcome.status is ConsumeStatus.NOT_APPROVED
        assert outcome.binding is not None
        assert outcome.binding.judgment is ApprovalJudgment.PENDING
        assert outcome.binding.consumed_by_invocation_id is None

    async def test_expired_does_not_consume(self) -> None:
        ledger = MemoryApprovalLedger()
        await _approved_binding(ledger, expires_at=NOW - timedelta(seconds=1))
        outcome = await ledger.consume("appr-1", _request(), now=NOW)
        assert outcome.status is ConsumeStatus.EXPIRED

    async def test_different_actions_contend_only_one_wins(self) -> None:
        ledger = MemoryApprovalLedger()
        await _approved_binding(ledger)
        first = await ledger.consume("appr-1", _request(), now=NOW)
        assert first.status is ConsumeStatus.CONSUMED
        # A different invocation (another logical action) loses the race with
        # an explicit conflict — no second side effect from one approval.
        second = await ledger.consume("appr-1", _request(invocation_id="tool-other"), now=NOW)
        assert second.status is ConsumeStatus.CONFLICT

    async def test_same_action_recovering_rebinds(self) -> None:
        # Crash between consumption and claim: the same invocation_id recovers
        # on its existing consumption association without a new approval.
        ledger = MemoryApprovalLedger()
        await _approved_binding(ledger)
        await ledger.consume("appr-1", _request(), now=NOW)
        outcome = await ledger.consume("appr-1", _request(), now=NOW)
        assert outcome.status is ConsumeStatus.SAME_ACTION_REBOUND

    async def test_binding_mismatch_never_consumes(self) -> None:
        ledger = MemoryApprovalLedger()
        await _approved_binding(ledger)
        outcome = await ledger.consume("appr-1", _request(arguments_digest="digest-2"), now=NOW)
        assert outcome.status is ConsumeStatus.CONFLICT
        binding = await ledger.get("appr-1")
        assert binding is not None
        assert binding.consumed_by_invocation_id is None

    async def test_non_one_time_allows_second_action(self) -> None:
        ledger = MemoryApprovalLedger()
        await _approved_binding(ledger, one_time=False)
        first = await ledger.consume("appr-1", _request(), now=NOW)
        second = await ledger.consume("appr-1", _request(invocation_id="tool-other"), now=NOW)
        assert first.status is ConsumeStatus.CONSUMED
        assert second.status is ConsumeStatus.CONSUMED


class TestGateFailClosed:
    async def test_unknown_reference_denied(self) -> None:
        gate = ApprovalGate(MemoryApprovalLedger())
        verdict = await gate.authorize("nope", _request(), now=NOW)
        assert verdict.check is BindingCheck.NOT_APPROVED

    async def test_storage_failure_denies_authorize(self) -> None:
        ledger = MemoryApprovalLedger()
        await _approved_binding(ledger)
        gate = ApprovalGate(ledger)
        ledger.fail_next_access(RuntimeError("disk on fire"))
        verdict = await gate.authorize("appr-1", _request(), now=NOW)
        assert verdict.check is BindingCheck.NOT_APPROVED
        assert "approval evaluation failed" in verdict.detail

    async def test_storage_failure_denies_consume(self) -> None:
        ledger = MemoryApprovalLedger()
        await _approved_binding(ledger)
        gate = ApprovalGate(ledger)
        ledger.fail_next_access(RuntimeError("disk on fire"))
        outcome = await gate.consume("appr-1", _request(), now=NOW)
        assert outcome.status is ConsumeStatus.CONFLICT
        binding = await ledger.get("appr-1")
        assert binding is not None
        assert binding.consumed_by_invocation_id is None
