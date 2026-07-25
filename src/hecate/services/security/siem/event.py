"""SecurityEvent — unified normalized event for SIEM export.

Normalizes events from AuditLog, ToolDecision, and SecurityFinding into
a single schema for export to external SIEM systems.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import IntEnum
from typing import Any


class EventSeverity(IntEnum):
    """Severity levels ordered by importance (lower = more severe for syslog)."""

    INFO = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4

    @classmethod
    def from_str(cls, value: str) -> EventSeverity:
        """Parse severity from string."""
        return {
            "info": cls.INFO,
            "low": cls.LOW,
            "medium": cls.MEDIUM,
            "high": cls.HIGH,
            "critical": cls.CRITICAL,
        }.get(value.lower(), cls.INFO)


class EventType:
    """Event type constants."""

    API = "api"
    TOOL_POLICY = "tool_policy"
    ANOMALY = "anomaly"


class EventSource:
    """Event source constants."""

    AUDIT_LOG = "audit_log"
    TOOL_DECISION = "tool_decision"
    SECURITY_FINDING = "security_finding"


@dataclass
class SecurityEvent:
    """Normalized security event for SIEM export.

    Attributes:
        event_type: One of EventType constants.
        severity: Event severity level.
        source: Which system produced this event.
        timestamp: When the event occurred.
        actor_user_id: User who triggered the event (if applicable).
        actor_agent_id: Agent that triggered the event (if applicable).
        action: What action was performed or evaluated.
        decision: Policy decision (ALLOW/DENY/SANDBOX for tool_policy events).
        resource: Resource affected (tool name, endpoint path, etc.).
        metadata: Extensible additional context.
    """

    event_type: str
    severity: EventSeverity
    source: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    actor_user_id: str | None = None
    actor_agent_id: str | None = None
    action: str = ""
    decision: str = ""
    resource: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary for JSON export."""
        return {
            "event_type": self.event_type,
            "severity": self.severity.name.lower(),
            "source": self.source,
            "timestamp": self.timestamp.isoformat(),
            "actor_user_id": self.actor_user_id,
            "actor_agent_id": self.actor_agent_id,
            "action": self.action,
            "decision": self.decision,
            "resource": self.resource,
            "metadata": self.metadata,
        }


# ---------------------------------------------------------------------------
# Normalization functions
# ---------------------------------------------------------------------------


def from_audit_log(
    *,
    action: str,
    success: bool,
    response_status: int | None,
    user_id: str | None,
    org_id: str | None,
    workspace_id: str | None,
    request_method: str | None,
    request_path: str | None,
    ip_address: str | None,
    timestamp: datetime | None = None,
) -> SecurityEvent:
    """Normalize an AuditLog event into a SecurityEvent.

    Severity mapping:
    - 2xx success → INFO
    - 4xx client error → LOW
    - 5xx server error → MEDIUM
    """
    if response_status is None or response_status < 400:
        severity = EventSeverity.INFO
    elif response_status < 500:
        severity = EventSeverity.LOW
    else:
        severity = EventSeverity.MEDIUM

    return SecurityEvent(
        event_type=EventType.API,
        severity=severity,
        source=EventSource.AUDIT_LOG,
        timestamp=timestamp or datetime.now(UTC),
        actor_user_id=str(user_id) if user_id else None,
        action=action,
        decision="success" if success else "failure",
        resource=request_path or "",
        metadata={
            "org_id": str(org_id) if org_id else None,
            "workspace_id": str(workspace_id) if workspace_id else None,
            "request_method": request_method,
            "response_status": response_status,
            "ip_address": ip_address,
        },
    )


def from_tool_decision(
    *,
    agent_id: str | None,
    workspace_id: str | None,
    tool_name: str,
    decision: str,
    reason: str,
    session_id: str | None = None,
    on_behalf_of_user: str | None = None,
    arguments_hash: str = "",
    policy_version: str = "",
    timestamp: datetime | None = None,
) -> SecurityEvent:
    """Normalize a ToolDecision event into a SecurityEvent.

    Severity mapping:
    - ALLOW → INFO
    - SANDBOX → MEDIUM
    - APPROVAL_REQUIRED → MEDIUM
    - DENY → HIGH
    """
    severity_map = {
        "allow": EventSeverity.INFO,
        "sandbox": EventSeverity.MEDIUM,
        "approval_required": EventSeverity.MEDIUM,
        "require_approval": EventSeverity.MEDIUM,
        "deny": EventSeverity.HIGH,
    }
    severity = severity_map.get(decision.lower(), EventSeverity.INFO)

    return SecurityEvent(
        event_type=EventType.TOOL_POLICY,
        severity=severity,
        source=EventSource.TOOL_DECISION,
        timestamp=timestamp or datetime.now(UTC),
        actor_agent_id=agent_id,
        actor_user_id=on_behalf_of_user,
        action=f"tool.{tool_name}",
        decision=decision,
        resource=tool_name,
        metadata={
            "workspace_id": workspace_id,
            "session_id": session_id,
            "reason": reason,
            "arguments_hash": arguments_hash,
            "policy_version": policy_version,
        },
    )


def from_security_finding(
    *,
    rule_name: str,
    severity: str,
    message: str,
    org_id: str | None,
    workspace_id: str | None,
    user_id: str | None,
    source_event: dict | None = None,
    finding_metadata: dict | None = None,
    timestamp: datetime | None = None,
) -> SecurityEvent:
    """Normalize a SecurityFinding into a SecurityEvent."""
    return SecurityEvent(
        event_type=EventType.ANOMALY,
        severity=EventSeverity.from_str(severity),
        source=EventSource.SECURITY_FINDING,
        timestamp=timestamp or datetime.now(UTC),
        actor_user_id=str(user_id) if user_id else None,
        action=f"finding.{rule_name}",
        decision="detected",
        resource=rule_name,
        metadata={
            "org_id": str(org_id) if org_id else None,
            "workspace_id": str(workspace_id) if workspace_id else None,
            "message": message,
            "source_event": source_event,
            "finding_metadata": finding_metadata or {},
        },
    )
