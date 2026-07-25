"""Tests for OCSFFormatter OCSF v1.5 schema mapping."""

from __future__ import annotations

from hecate.services.security.siem.event import (
    EventSeverity,
    EventSource,
    EventType,
    SecurityEvent,
)
from hecate.services.security.siem.exporter import SIEMExporter
from hecate.services.security.siem.ocsf import OCSFFormatter


class CountingExporter(SIEMExporter):
    """Test double that captures events for inspection."""

    def __init__(self) -> None:
        self.events: list[SecurityEvent] = []

    @property
    def name(self) -> str:
        return "counting"

    async def export(self, events: list[SecurityEvent]) -> None:
        self.events.extend(events)


class TestOCSFActivity:
    """Tests for OCSF Activity class (4001) mapping — API events."""

    def test_activity_class_uid(self) -> None:
        wrapped = CountingExporter()
        formatter = OCSFFormatter(wrapped)
        event = SecurityEvent(
            event_type=EventType.API,
            severity=EventSeverity.INFO,
            source=EventSource.AUDIT_LOG,
            action="agent.create",
            decision="success",
        )
        result = formatter._transform(event)
        assert result["class_uid"] == 4001
        assert result["class_name"] == "Activity Audit"

    def test_activity_actor_user(self) -> None:
        wrapped = CountingExporter()
        formatter = OCSFFormatter(wrapped)
        event = SecurityEvent(
            event_type=EventType.API,
            severity=EventSeverity.INFO,
            source=EventSource.AUDIT_LOG,
            actor_user_id="user-123",
            action="agent.delete",
            decision="success",
        )
        result = formatter._transform(event)
        assert result["actor"]["user"]["uid"] == "user-123"

    def test_activity_severity_id(self) -> None:
        wrapped = CountingExporter()
        formatter = OCSFFormatter(wrapped)
        event = SecurityEvent(
            event_type=EventType.API,
            severity=EventSeverity.MEDIUM,
            source=EventSource.AUDIT_LOG,
        )
        result = formatter._transform(event)
        assert result["severity_id"] == 3


class TestOCSFAuthorization:
    """Tests for OCSF Authorization class (2201) — tool decision events."""

    def test_auth_class_uid(self) -> None:
        wrapped = CountingExporter()
        formatter = OCSFFormatter(wrapped)
        event = SecurityEvent(
            event_type=EventType.TOOL_POLICY,
            severity=EventSeverity.HIGH,
            source=EventSource.TOOL_DECISION,
            decision="DENY",
            resource="bash",
        )
        result = formatter._transform(event)
        assert result["class_uid"] == 2201
        assert result["class_name"] == "Authorization"

    def test_auth_decision_deny(self) -> None:
        wrapped = CountingExporter()
        formatter = OCSFFormatter(wrapped)
        event = SecurityEvent(
            event_type=EventType.TOOL_POLICY,
            severity=EventSeverity.HIGH,
            source=EventSource.TOOL_DECISION,
            decision="DENY",
        )
        result = formatter._transform(event)
        assert result["decision"] == "DENY"
        assert result["decision_id"] == 2

    def test_auth_decision_allow(self) -> None:
        wrapped = CountingExporter()
        formatter = OCSFFormatter(wrapped)
        event = SecurityEvent(
            event_type=EventType.TOOL_POLICY,
            severity=EventSeverity.INFO,
            source=EventSource.TOOL_DECISION,
            decision="ALLOW",
        )
        result = formatter._transform(event)
        assert result["decision_id"] == 1

    def test_auth_actor_agent(self) -> None:
        wrapped = CountingExporter()
        formatter = OCSFFormatter(wrapped)
        event = SecurityEvent(
            event_type=EventType.TOOL_POLICY,
            severity=EventSeverity.INFO,
            source=EventSource.TOOL_DECISION,
            actor_agent_id="agent-1",
            decision="ALLOW",
        )
        result = formatter._transform(event)
        assert result["actor"]["agent"]["uid"] == "agent-1"

    def test_auth_resource_tool(self) -> None:
        wrapped = CountingExporter()
        formatter = OCSFFormatter(wrapped)
        event = SecurityEvent(
            event_type=EventType.TOOL_POLICY,
            severity=EventSeverity.INFO,
            source=EventSource.TOOL_DECISION,
            resource="python",
            decision="SANDBOX",
        )
        result = formatter._transform(event)
        assert result["resources"][0]["type"] == "tool"
        assert result["resources"][0]["name"] == "python"


class TestOCSFFinding:
    """Tests for OCSF Security Finding class (2001) — anomaly events."""

    def test_finding_class_uid(self) -> None:
        wrapped = CountingExporter()
        formatter = OCSFFormatter(wrapped)
        event = SecurityEvent(
            event_type=EventType.ANOMALY,
            severity=EventSeverity.HIGH,
            source=EventSource.SECURITY_FINDING,
            action="finding.bulk_delete_rule",
            resource="bulk_delete_rule",
            metadata={"message": "5 deletes in 1 min"},
        )
        result = formatter._transform(event)
        assert result["class_uid"] == 2001
        assert result["class_name"] == "Security Finding"

    def test_finding_info(self) -> None:
        wrapped = CountingExporter()
        formatter = OCSFFormatter(wrapped)
        event = SecurityEvent(
            event_type=EventType.ANOMALY,
            severity=EventSeverity.CRITICAL,
            source=EventSource.SECURITY_FINDING,
            action="finding.injection_detected",
            resource="injection_rule",
            metadata={"message": "SQL injection attempt detected"},
        )
        result = formatter._transform(event)
        assert result["finding_info"]["title"] == "SQL injection attempt detected"
        assert result["finding_info"]["uid"] == "injection_rule"

    def test_finding_severity_critical(self) -> None:
        wrapped = CountingExporter()
        formatter = OCSFFormatter(wrapped)
        event = SecurityEvent(
            event_type=EventType.ANOMALY,
            severity=EventSeverity.CRITICAL,
            source=EventSource.SECURITY_FINDING,
            metadata={"message": "test"},
        )
        result = formatter._transform(event)
        assert result["severity_id"] == 5


class TestOCSFDecoratorExport:
    """Tests for OCSF decorator export delegation."""

    async def test_export_delegates_to_wrapped(self) -> None:
        wrapped = CountingExporter()
        formatter = OCSFFormatter(wrapped)
        events = [
            SecurityEvent(
                event_type=EventType.API,
                severity=EventSeverity.INFO,
                source=EventSource.AUDIT_LOG,
                action="test",
                decision="success",
            ),
            SecurityEvent(
                event_type=EventType.TOOL_POLICY,
                severity=EventSeverity.HIGH,
                source=EventSource.TOOL_DECISION,
                decision="DENY",
            ),
        ]
        await formatter.export(events)
        assert len(wrapped.events) == 2
        assert wrapped.events[0].metadata["ocsf"]["class_uid"] == 4001
        assert wrapped.events[1].metadata["ocsf"]["class_uid"] == 2201

    def test_name_includes_wrapped(self) -> None:
        wrapped = CountingExporter()
        formatter = OCSFFormatter(wrapped)
        assert formatter.name == "ocsf+counting"
