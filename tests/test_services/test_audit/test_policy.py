"""Tests for audit security policies — bulk delete, off-hours, unusual IP."""

from __future__ import annotations

import uuid
from datetime import datetime

from hecate.services.audit.policy import (
    BulkDeleteProtectionPolicy,
    OffHoursSensitiveOpsPolicy,
    PolicyContext,
    PolicyEngine,
    PolicySeverity,
    UnusualIPDetectionPolicy,
)
from hecate.services.audit.store import AuditEvent


def _make_event(
    action: str = "api.agents.create",
    ip_address: str | None = None,
    user_id: uuid.UUID | None = None,
    timestamp: datetime | None = None,
) -> AuditEvent:
    return AuditEvent(
        org_id=uuid.UUID(int=1),
        user_id=user_id or uuid.UUID(int=1),
        action=action,
        ip_address=ip_address,
        timestamp=timestamp,
    )


class TestBulkDeleteProtectionPolicy:
    async def test_no_violation_under_threshold(self) -> None:
        policy = BulkDeleteProtectionPolicy(max_deletes=5)
        event = _make_event(action="api.agents.delete")
        ctx = PolicyContext()
        result = await policy.evaluate(event, ctx)
        assert result is None

    async def test_violation_over_threshold(self) -> None:
        policy = BulkDeleteProtectionPolicy(max_deletes=3)
        now = datetime.now()
        recent = [_make_event(action="api.agents.delete", timestamp=now) for _ in range(3)]
        ctx = PolicyContext(recent_user_actions=recent, now=now)
        event = _make_event(action="api.agents.delete", timestamp=now)
        result = await policy.evaluate(event, ctx)
        assert result is not None
        assert result.severity == PolicySeverity.MEDIUM
        assert "delete" in result.message.lower()

    async def test_non_delete_action_skipped(self) -> None:
        policy = BulkDeleteProtectionPolicy()
        event = _make_event(action="api.agents.create")
        ctx = PolicyContext()
        result = await policy.evaluate(event, ctx)
        assert result is None


class TestOffHoursSensitiveOpsPolicy:
    async def test_weekend_sensitive_op_flagged(self) -> None:
        policy = OffHoursSensitiveOpsPolicy()
        saturday = datetime(2025, 1, 4, 10, 0)
        event = _make_event(action="auth.permission.update")
        ctx = PolicyContext(now=saturday)
        result = await policy.evaluate(event, ctx)
        assert result is not None
        assert "weekend" in result.message.lower()

    async def test_business_hours_sensitive_op_ok(self) -> None:
        policy = OffHoursSensitiveOpsPolicy()
        tuesday = datetime(2025, 1, 7, 10, 0)
        event = _make_event(action="auth.permission.update")
        ctx = PolicyContext(now=tuesday)
        result = await policy.evaluate(event, ctx)
        assert result is None

    async def test_non_sensitive_action_skipped(self) -> None:
        policy = OffHoursSensitiveOpsPolicy()
        saturday = datetime(2025, 1, 4, 10, 0)
        event = _make_event(action="api.agents.create")
        ctx = PolicyContext(now=saturday)
        result = await policy.evaluate(event, ctx)
        assert result is None


class TestUnusualIPDetectionPolicy:
    async def test_known_ip_no_violation(self) -> None:
        policy = UnusualIPDetectionPolicy()
        event = _make_event(ip_address="192.168.1.1")
        ctx = PolicyContext(user_known_ips={"192.168.1.1"})
        result = await policy.evaluate(event, ctx)
        assert result is None

    async def test_unknown_ip_flagged(self) -> None:
        policy = UnusualIPDetectionPolicy()
        event = _make_event(ip_address="10.0.0.1")
        ctx = PolicyContext(user_known_ips={"192.168.1.1"})
        result = await policy.evaluate(event, ctx)
        assert result is not None
        assert "unrecognized" in result.message.lower()

    async def test_no_ip_skipped(self) -> None:
        policy = UnusualIPDetectionPolicy()
        event = _make_event(ip_address=None)
        ctx = PolicyContext(user_known_ips={"192.168.1.1"})
        result = await policy.evaluate(event, ctx)
        assert result is None

    async def test_first_action_no_violation(self) -> None:
        policy = UnusualIPDetectionPolicy()
        event = _make_event(ip_address="10.0.0.1")
        ctx = PolicyContext(user_known_ips=set())
        result = await policy.evaluate(event, ctx)
        assert result is None


class TestPolicyEngine:
    async def test_evaluate_all_policies(self) -> None:
        engine = PolicyEngine()
        engine.register(BulkDeleteProtectionPolicy())
        engine.register(OffHoursSensitiveOpsPolicy())
        engine.register(UnusualIPDetectionPolicy())

        event = _make_event(action="api.agents.create")
        ctx = PolicyContext()
        violations = await engine.evaluate(event, ctx)
        assert isinstance(violations, list)

    async def test_multiple_violations_possible(self) -> None:
        engine = PolicyEngine()
        engine.register(UnusualIPDetectionPolicy())
        engine.register(OffHoursSensitiveOpsPolicy())

        saturday = datetime(2025, 1, 4, 10, 0)
        event = _make_event(action="auth.api_key.create", ip_address="10.0.0.1")
        ctx = PolicyContext(user_known_ips={"192.168.1.1"}, now=saturday)
        violations = await engine.evaluate(event, ctx)
        assert len(violations) == 2

    async def test_policy_error_does_not_crash_engine(self) -> None:
        """A failing policy should not prevent other policies from running."""

        class BrokenPolicy(UnusualIPDetectionPolicy):
            @property
            def name(self) -> str:
                return "broken"

            async def evaluate(self, event: AuditEvent, context: PolicyContext) -> object:
                raise RuntimeError("intentional error")

        engine = PolicyEngine()
        engine.register(BrokenPolicy())
        engine.register(UnusualIPDetectionPolicy())

        event = _make_event(ip_address="10.0.0.1")
        ctx = PolicyContext(user_known_ips={"192.168.1.1"})
        violations = await engine.evaluate(event, ctx)
        assert len(violations) == 1
