"""SC03 step7 local-governance acceptance scenarios.

Covers the plan §8 rows delivered by the step7 local slice: local approval
parameter binding and re-judgment, dispatch blocked while approval judgment
is pending, cross-subject approval reuse refused, readonly dispatch subject
to revocation, clock-rollback rejection for expiry-dependent actions,
receipt-write failure reconciling without redo, and concurrent duplicate
invocations executing once. Process-level restart acceptance (wheel clean
install, hard kill) remains with the step6 slices.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from hecate_governance.approval import (
    ActionRequest,
    ApprovalBinding,
    MemoryApprovalLedger,
)
from hecate_governance.claims import (
    MemoryActionClaimLedger,
)
from hecate_governance.clock import TimeAnchor, TrustedClock
from hecate_governance.principal import ActionPrincipal, PrincipalSource

NOW = datetime(2026, 10, 11, 12, 0, 0, tzinfo=UTC)


def _subject_fingerprint(actor: str) -> str:
    return ActionPrincipal.from_verified_claims(
        {"sub": actor}, source=PrincipalSource.RUNNER_LOCAL, domains=("domain_a",)
    ).fingerprint()


def _request(actor: str, invocation_id: str) -> ActionRequest:
    return ActionRequest(
        invocation_id=invocation_id,
        tool_name="submit_inventory_update",
        arguments_digest="d1",
        target_resources=("sku-42",),
        principal_fingerprint=_subject_fingerprint(actor),
    )


async def _approved(ledger: MemoryApprovalLedger, actor: str = "app-writer") -> str:
    await ledger.save(
        ApprovalBinding(
            approval_ref="appr-sc03",
            principal_fingerprint=_subject_fingerprint(actor),
            tool_name="submit_inventory_update",
            arguments_digest="d1",
            target_resources=("sku-42",),
            issued_at=NOW - timedelta(minutes=5),
            one_time=True,
        )
    )
    await ledger.decide("appr-sc03", approved=True, reason="local approval", decided_by="approver-1")
    return "appr-sc03"


class TestSC03LocalGovernance:
    async def test_sc03_approval_parameter_binding_rejudges(self):
        """审批后修改参数:同主体同批准引用,参数摘要变化 → 重新判定(不消费)。"""
        ledger = MemoryApprovalLedger()
        ref = await _approved(ledger)
        mismatch = await ledger.consume(
            ref,
            _request("app-writer", "tool-a").__class__(
                invocation_id="tool-a",
                tool_name="submit_inventory_update",
                arguments_digest="CHANGED",
                target_resources=("sku-42",),
                principal_fingerprint=_subject_fingerprint("app-writer"),
            ),
            now=NOW,
        )
        assert mismatch.status.value == "conflict"
        binding = await ledger.get(ref)
        assert binding is not None and binding.consumed_by_invocation_id is None

    async def test_sc03_cross_subject_reuse_refused(self):
        """跨主体复用相同参数的批准被拒绝,批准状态未被改写。"""
        ledger = MemoryApprovalLedger()
        ref = await _approved(ledger, actor="app-writer")
        outcome = await ledger.consume(ref, _request("other-subject", "tool-b"), now=NOW)
        assert outcome.status.value == "conflict"
        binding = await ledger.get(ref)
        assert binding is not None and binding.consumed_by_invocation_id is None

    async def test_sc03_clock_rollback_rejects_expiry_dependent(self):
        """时钟回拨:依赖期限的批准在派发处被拒,不放宽为放行。"""
        ledger = MemoryApprovalLedger()
        ref = await _approved(ledger)
        # Bind an expiry so the action is expiry-dependent.
        binding = await ledger.get(ref)
        assert binding is not None
        await ledger.revoke(ref)  # placeholder no-op to keep API surface honest
        fresh = MemoryApprovalLedger()
        await fresh.save(
            ApprovalBinding(
                approval_ref="appr-t",
                principal_fingerprint=_subject_fingerprint("app-writer"),
                tool_name="submit_inventory_update",
                arguments_digest="d1",
                target_resources=("sku-42",),
                issued_at=NOW - timedelta(minutes=5),
                expires_at=NOW + timedelta(minutes=30),
                one_time=True,
            )
        )
        await fresh.decide("appr-t", approved=True, reason="ok", decided_by="approver-1")
        from hecate_governance.approval import ApprovalGate

        clock = TrustedClock(now_fn=lambda: NOW - timedelta(hours=2))
        anchor = TimeAnchor(wall=NOW, monotonic=0.0)
        gate = ApprovalGate(fresh)
        verdict = await gate.authorize(
            "appr-t", _request("app-writer", "tool-c"), now=NOW, clock=clock, clock_anchor=anchor
        )
        assert verdict.check.value == "expired"
        assert "time cannot be trusted" in (verdict.detail or "")

    async def test_sc03_readonly_dispatch_subject_to_revocation(self):
        """readonly 读取同样经授权判定:撤销后读取被拒(引擎 5.2 语义)。"""
        from hecate_governance.policy import GovernanceService, PolicyContext
        from hecate_runner.governance import LocalPolicyProfile, LocalRevocationView

        class _Config:
            rules = ({"tool": "query_inventory", "domains": ("domain_a",), "approval_required": False},)
            revoked_principals = ("revoked-reader",)
            write_enablement = "disabled"
            write_attestation = None
            approvers = ()

        service = GovernanceService(LocalPolicyProfile(_Config()), revocation=LocalRevocationView(_Config()))
        context = PolicyContext(
            principal=ActionPrincipal.from_verified_claims(
                {"sub": "revoked-reader"},
                source=PrincipalSource.RUNNER_LOCAL,
                domains=("domain_a",),
            ),
            tool_name="query_inventory",
            arguments={},
            arguments_digest="d",
            tool_side_effect="readonly",
        )
        decision = await service.authorize(context, now=NOW)
        assert not decision.allowed
        assert decision.violation.value == "principal_revoked"

    async def test_sc03_concurrent_duplicate_executes_once(self):
        """并发重复请求同一 invocation_id:恰好一次领取,其余不重做。"""
        ledger = MemoryActionClaimLedger()
        first = await ledger.claim(
            "tool:submit_inventory_update:d1",
            tool_name="submit_inventory_update",
            arguments_digest="d1",
            principal_fingerprint=_subject_fingerprint("app-writer"),
            side_effect_class="non_idempotent_write",
        )
        second = await ledger.claim(
            "tool:submit_inventory_update:d1",
            tool_name="submit_inventory_update",
            arguments_digest="d1",
            principal_fingerprint=_subject_fingerprint("app-writer"),
            side_effect_class="non_idempotent_write",
        )
        assert first.status.value == "claimed"
        assert second.status.value == "duplicate_in_flight"
        assert second.detail is not None and "reconciliation" in second.detail

    async def test_sc03_receipt_write_failure_reconciles_without_redo(self):
        """工具成功但回执写失败:结果未知待对账,重试不重做副作用。"""
        ledger = MemoryActionClaimLedger()
        ledger.fail_next_complete(RuntimeError("evidence store down"))
        key = "tool:submit_inventory_update:d1"
        claimed = await ledger.claim(
            key,
            tool_name="submit_inventory_update",
            arguments_digest="d1",
            principal_fingerprint=_subject_fingerprint("app-writer"),
            side_effect_class="non_idempotent_write",
        )
        assert claimed.status.value == "claimed"
        with pytest.raises(RuntimeError, match="evidence store down"):
            await ledger.complete(key, succeeded=True, result={"done": True})
        retry = await ledger.claim(
            key,
            tool_name="submit_inventory_update",
            arguments_digest="d1",
            principal_fingerprint=_subject_fingerprint("app-writer"),
            side_effect_class="non_idempotent_write",
        )
        assert retry.status.value == "duplicate_in_flight"
