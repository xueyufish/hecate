"""Audit security policy engine for detecting suspicious activity.

Provides three built-in policies:

- :class:`BulkDeleteProtectionPolicy` — blocks bulk delete within a time window
- :class:`OffHoursSensitiveOpsPolicy` — flags sensitive ops outside business hours
- :class:`UnusualIPDetectionPolicy` — flags actions from unrecognized IPs

The :class:`PolicyEngine` evaluates all policies against each audit event.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from hecate.services.audit.store import AuditEvent

logger = logging.getLogger(__name__)


class PolicySeverity(StrEnum):
    """Severity level for policy violations."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class PolicyViolation:
    """A single policy violation detected from an audit event.

    Attributes:
        policy_name: Which policy triggered.
        severity: How serious the violation is.
        message: Human-readable description.
        event: The triggering audit event.
        metadata: Additional context (e.g., thresholds exceeded).
    """

    policy_name: str
    severity: PolicySeverity
    message: str
    event: AuditEvent
    metadata: dict[str, Any] = field(default_factory=dict)


class AuditSecurityPolicy(ABC):
    """Abstract base class for audit security policies."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Policy identifier."""

    @property
    @abstractmethod
    def description(self) -> str:
        """Human-readable policy description."""

    @abstractmethod
    async def evaluate(self, event: AuditEvent, context: PolicyContext) -> PolicyViolation | None:
        """Evaluate the policy against an event.

        Args:
            event: The audit event to evaluate.
            context: Historical context for cross-event analysis.

        Returns:
            A PolicyViolation if the policy is triggered, or None.
        """


@dataclass
class PolicyContext:
    """Context provided to policies for cross-event analysis.

    Attributes:
        recent_user_actions: Actions by this user in the last N minutes.
        user_known_ips: Set of IPs this user has previously used.
        now: Current timestamp (injectable for testing).
    """

    recent_user_actions: list[AuditEvent] = field(default_factory=list)
    user_known_ips: set[str] = field(default_factory=set)
    now: datetime | None = None

    def get_now(self) -> datetime:
        """Return the current time (or mock time for testing)."""
        return self.now or datetime.now()


class BulkDeleteProtectionPolicy(AuditSecurityPolicy):
    """Flag when a user performs more than N delete operations within a time window.

    Default: 5 deletes within 10 minutes triggers a MEDIUM violation.
    """

    def __init__(
        self,
        max_deletes: int = 5,
        window_minutes: int = 10,
        severity: PolicySeverity = PolicySeverity.MEDIUM,
    ) -> None:
        self._max_deletes = max_deletes
        self._window_minutes = window_minutes
        self._severity = severity

    @property
    def name(self) -> str:
        return "bulk_delete_protection"

    @property
    def description(self) -> str:
        return f"Flags users performing more than {self._max_deletes} deletes in {self._window_minutes} minutes"

    async def evaluate(self, event: AuditEvent, context: PolicyContext) -> PolicyViolation | None:
        if not event.action.endswith(".delete"):
            return None

        now = context.get_now()
        window_start = now - timedelta(minutes=self._window_minutes)
        recent_deletes = [
            a
            for a in context.recent_user_actions
            if a.action.endswith(".delete") and a.timestamp is not None and a.timestamp >= window_start
        ]

        if len(recent_deletes) + 1 > self._max_deletes:
            return PolicyViolation(
                policy_name=self.name,
                severity=self._severity,
                message=(
                    f"User performed {len(recent_deletes) + 1} delete operations within {self._window_minutes} minutes"
                ),
                event=event,
                metadata={"delete_count": len(recent_deletes) + 1, "window_minutes": self._window_minutes},
            )
        return None


class OffHoursSensitiveOpsPolicy(AuditSecurityPolicy):
    """Flag sensitive operations performed outside business hours.

    Business hours: Mon-Fri 09:00-18:00 (configurable).
    Sensitive actions: permission changes, API key operations, settings updates.
    """

    _SENSITIVE_PREFIXES = (
        "auth.permission.",
        "auth.api_key.",
        "system.settings.",
    )

    def __init__(
        self,
        business_start_hour: int = 9,
        business_end_hour: int = 18,
        severity: PolicySeverity = PolicySeverity.LOW,
    ) -> None:
        self._start_hour = business_start_hour
        self._end_hour = business_end_hour
        self._severity = severity

    @property
    def name(self) -> str:
        return "off_hours_sensitive_ops"

    @property
    def description(self) -> str:
        return f"Flags sensitive operations outside business hours ({self._start_hour}:00-{self._end_hour}:00)"

    async def evaluate(self, event: AuditEvent, context: PolicyContext) -> PolicyViolation | None:
        is_sensitive = any(event.action.startswith(prefix) for prefix in self._SENSITIVE_PREFIXES)
        if not is_sensitive:
            return None

        now = context.get_now()
        if now.weekday() >= 5:  # Saturday=5, Sunday=6
            return PolicyViolation(
                policy_name=self.name,
                severity=self._severity,
                message=f"Sensitive operation '{event.action}' performed on weekend",
                event=event,
                metadata={"day_of_week": now.strftime("%A"), "hour": now.hour},
            )

        if now.hour < self._start_hour or now.hour >= self._end_hour:
            return PolicyViolation(
                policy_name=self.name,
                severity=self._severity,
                message=f"Sensitive operation '{event.action}' performed outside business hours",
                event=event,
                metadata={"hour": now.hour, "business_hours": f"{self._start_hour}:00-{self._end_hour}:00"},
            )
        return None


class UnusualIPDetectionPolicy(AuditSecurityPolicy):
    """Flag when a user performs actions from an unrecognized IP address.

    An IP is "unrecognized" if it's not in the user's known IP set.
    """

    def __init__(self, severity: PolicySeverity = PolicySeverity.LOW) -> None:
        self._severity = severity

    @property
    def name(self) -> str:
        return "unusual_ip_detection"

    @property
    def description(self) -> str:
        return "Flags actions from IP addresses not previously seen for this user"

    async def evaluate(self, event: AuditEvent, context: PolicyContext) -> PolicyViolation | None:
        if event.ip_address is None:
            return None

        # First action ever, or IP is known — no violation
        if not context.user_known_ips or event.ip_address in context.user_known_ips:
            return None

        return PolicyViolation(
            policy_name=self.name,
            severity=self._severity,
            message=f"Action from unrecognized IP: {event.ip_address}",
            event=event,
            metadata={"ip_address": event.ip_address, "known_ips": list(context.user_known_ips)},
        )


class PolicyEngine:
    """Evaluate all registered security policies against audit events.

    Usage::

        engine = PolicyEngine()
        engine.register(BulkDeleteProtectionPolicy())
        engine.register(OffHoursSensitiveOpsPolicy())
        engine.register(UnusualIPDetectionPolicy())

        violations = await engine.evaluate(event, context)
    """

    def __init__(self) -> None:
        self._policies: list[AuditSecurityPolicy] = []

    def register(self, policy: AuditSecurityPolicy) -> None:
        """Register a security policy."""
        self._policies.append(policy)

    @property
    def policies(self) -> list[AuditSecurityPolicy]:
        """Return registered policies."""
        return list(self._policies)

    async def evaluate(self, event: AuditEvent, context: PolicyContext) -> list[PolicyViolation]:
        """Evaluate all policies against the given event.

        Returns all violations (an event can trigger multiple policies).
        """
        violations: list[PolicyViolation] = []
        for policy in self._policies:
            try:
                violation = await policy.evaluate(event, context)
                if violation is not None:
                    violations.append(violation)
                    logger.info(
                        "Policy violation: %s (severity=%s) for action=%s user=%s",
                        policy.name,
                        violation.severity,
                        event.action,
                        event.user_id,
                    )
            except Exception as e:
                logger.error("Policy %s evaluation failed: %s", policy.name, e)
        return violations
