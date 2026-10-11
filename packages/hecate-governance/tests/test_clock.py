"""Trusted-time rules for approval expiry (task 3.5)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from hecate_governance.approval import (
    ActionRequest,
    ApprovalBinding,
    ApprovalGate,
    MemoryApprovalLedger,
)
from hecate_governance.clock import TimeAnchor, TrustedClock, validate_clock
from hecate_governance.principal import ActionPrincipal, PrincipalSource

NOW = datetime(2026, 10, 11, 12, 0, 0, tzinfo=UTC)


def _principal() -> ActionPrincipal:
    return ActionPrincipal.from_verified_claims(
        {"sub": "user-1"}, source=PrincipalSource.RUNNER_LOCAL, domains=("inventory",)
    )


def _request() -> ActionRequest:
    return ActionRequest(
        invocation_id="tool-1",
        tool_name="inventory.write",
        arguments_digest="d1",
        target_resources=("sku-42",),
        principal_fingerprint=_principal().fingerprint(),
    )


async def _binding(ledger, *, expires_at=None) -> str:
    await ledger.save(
        ApprovalBinding(
            approval_ref="appr-t",
            principal_fingerprint=_principal().fingerprint(),
            tool_name="inventory.write",
            arguments_digest="d1",
            target_resources=("sku-42",),
            issued_at=NOW - timedelta(minutes=5),
            expires_at=expires_at,
        )
    )
    await ledger.decide("appr-t", approved=True, reason="ok", decided_by="approver")
    return "appr-t"


class TestValidateClock:
    def test_normal_same_process_trusted(self) -> None:
        anchor = TimeAnchor(wall=NOW - timedelta(seconds=10), monotonic=100.0)
        reason = validate_clock(anchor, now_wall=NOW, now_monotonic=110.0)
        assert reason is None

    def test_rollback_detected_across_restart(self) -> None:
        # The wall anchor persists with the binding: rollback is detectable
        # even after the monotonic reading was lost to a restart.
        anchor = TimeAnchor(wall=NOW, monotonic=0.0)
        reason = validate_clock(anchor, now_wall=NOW - timedelta(minutes=10), now_monotonic=0.0)
        assert reason is not None and "backwards" in reason

    def test_restart_detected_by_monotonic_regression(self) -> None:
        anchor = TimeAnchor(wall=NOW - timedelta(seconds=10), monotonic=500.0)
        reason = validate_clock(anchor, now_wall=NOW, now_monotonic=100.0)
        assert reason is not None and "restarted" in reason

    def test_wall_monotonic_divergence_detected(self) -> None:
        anchor = TimeAnchor(wall=NOW - timedelta(seconds=10), monotonic=100.0)
        # Wall claims an hour passed; the monotonic clock says 10 seconds.
        reason = validate_clock(anchor, now_wall=NOW + timedelta(minutes=59, seconds=50), now_monotonic=110.0)
        assert reason is not None and "diverged" in reason


class TestGateUntrustedTime:
    async def test_untrusted_time_rejects_expiry_dependent_binding(self) -> None:
        ledger = MemoryApprovalLedger()
        await _binding(ledger, expires_at=NOW + timedelta(minutes=30))
        clock = TrustedClock(now_fn=lambda: NOW, monotonic_fn=lambda: 100.0)
        anchor = TimeAnchor(wall=NOW - timedelta(minutes=10), monotonic=500.0)
        gate = ApprovalGate(ledger)
        # Restart (monotonic regressed): expiry judgment unverifiable → reject.
        verdict = await gate.authorize(
            "appr-t",
            _request(),
            now=NOW,
            clock=clock,
            clock_anchor=anchor,
        )
        assert verdict.check.value == "expired"
        assert "time cannot be trusted" in (verdict.detail or "")

    async def test_untrusted_time_spares_expiry_free_binding(self) -> None:
        ledger = MemoryApprovalLedger()
        await _binding(ledger, expires_at=None)
        clock = TrustedClock(now_fn=lambda: NOW, monotonic_fn=lambda: 100.0)
        anchor = TimeAnchor(wall=NOW - timedelta(minutes=10), monotonic=500.0)
        gate = ApprovalGate(ledger)
        verdict = await gate.authorize("appr-t", _request(), now=NOW, clock=clock, clock_anchor=anchor)
        assert verdict.check.value == "ok"

    async def test_trusted_time_judges_expiry_normally(self) -> None:
        ledger = MemoryApprovalLedger()
        await _binding(ledger, expires_at=NOW + timedelta(minutes=30))
        clock = TrustedClock(now_fn=lambda: NOW, monotonic_fn=lambda: 110.0)
        anchor = TimeAnchor(wall=NOW - timedelta(seconds=10), monotonic=100.0)
        gate = ApprovalGate(ledger)
        verdict = await gate.authorize("appr-t", _request(), now=NOW, clock=clock, clock_anchor=anchor)
        assert verdict.check.value == "ok"

    async def test_no_clock_keeps_previous_semantics(self) -> None:
        ledger = MemoryApprovalLedger()
        await _binding(ledger, expires_at=NOW + timedelta(minutes=30))
        gate = ApprovalGate(ledger)
        verdict = await gate.authorize("appr-t", _request(), now=NOW)
        assert verdict.check.value == "ok"
