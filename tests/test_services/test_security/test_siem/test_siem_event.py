"""Tests for SecurityEvent normalization and SIEM collector."""

from __future__ import annotations

from hecate.services.security.siem.collector import SecurityEventCollector
from hecate.services.security.siem.event import (
    EventSeverity,
    EventSource,
    EventType,
    SecurityEvent,
    from_audit_log,
    from_security_finding,
    from_tool_decision,
)
from hecate.services.security.siem.exporter import NullSIEMExporter, SIEMExporter


class TestSecurityEventNormalization:
    """Tests for SecurityEvent normalization from audit sources."""

    def test_from_audit_log_success(self) -> None:
        event = from_audit_log(
            action="agent.create",
            success=True,
            response_status=201,
            user_id="user-1",
            org_id="org-1",
            workspace_id="ws-1",
            request_method="POST",
            request_path="/api/agents",
            ip_address="10.0.0.1",
        )
        assert event.event_type == EventType.API
        assert event.severity == EventSeverity.INFO
        assert event.source == EventSource.AUDIT_LOG
        assert event.action == "agent.create"
        assert event.decision == "success"

    def test_from_audit_log_client_error(self) -> None:
        event = from_audit_log(
            action="agent.delete",
            success=False,
            response_status=404,
            user_id="user-1",
            org_id="org-1",
            workspace_id=None,
            request_method="DELETE",
            request_path="/api/agents/123",
            ip_address=None,
        )
        assert event.severity == EventSeverity.LOW

    def test_from_audit_log_server_error(self) -> None:
        event = from_audit_log(
            action="agent.create",
            success=False,
            response_status=500,
            user_id="user-1",
            org_id="org-1",
            workspace_id=None,
            request_method="POST",
            request_path="/api/agents",
            ip_address=None,
        )
        assert event.severity == EventSeverity.MEDIUM

    def test_from_tool_decision_allow(self) -> None:
        event = from_tool_decision(
            agent_id="agent-1",
            workspace_id="ws-1",
            tool_name="search",
            decision="ALLOW",
            reason="approved",
        )
        assert event.event_type == EventType.TOOL_POLICY
        assert event.severity == EventSeverity.INFO
        assert event.decision == "ALLOW"

    def test_from_tool_decision_deny(self) -> None:
        event = from_tool_decision(
            agent_id="agent-1",
            workspace_id="ws-1",
            tool_name="bash",
            decision="DENY",
            reason="blocked: rm -rf",
        )
        assert event.severity == EventSeverity.HIGH

    def test_from_tool_decision_sandbox(self) -> None:
        event = from_tool_decision(
            agent_id="agent-1",
            workspace_id="ws-1",
            tool_name="python",
            decision="SANDBOX",
            reason="untrusted tool",
        )
        assert event.severity == EventSeverity.MEDIUM

    def test_from_security_finding(self) -> None:
        event = from_security_finding(
            rule_name="bulk_delete_rule",
            severity="high",
            message="User performed 5 deletes in 1 minute",
            org_id="org-1",
            workspace_id="ws-1",
            user_id="user-1",
        )
        assert event.event_type == EventType.ANOMALY
        assert event.severity == EventSeverity.HIGH
        assert event.action == "finding.bulk_delete_rule"


class TestEventSeverity:
    """Tests for EventSeverity enum."""

    def test_from_str_info(self) -> None:
        assert EventSeverity.from_str("info") == EventSeverity.INFO

    def test_from_str_critical(self) -> None:
        assert EventSeverity.from_str("critical") == EventSeverity.CRITICAL

    def test_from_str_invalid(self) -> None:
        assert EventSeverity.from_str("unknown") == EventSeverity.INFO

    def test_severity_ordering(self) -> None:
        assert EventSeverity.INFO < EventSeverity.LOW
        assert EventSeverity.LOW < EventSeverity.MEDIUM
        assert EventSeverity.MEDIUM < EventSeverity.HIGH
        assert EventSeverity.HIGH < EventSeverity.CRITICAL


class TestSecurityEventDict:
    """Tests for SecurityEvent.to_dict()."""

    def test_to_dict(self) -> None:
        event = SecurityEvent(
            event_type=EventType.TOOL_POLICY,
            severity=EventSeverity.HIGH,
            source=EventSource.TOOL_DECISION,
            actor_agent_id="agent-1",
            action="tool.bash",
            decision="DENY",
            resource="bash",
            metadata={"reason": "blocked"},
        )
        d = event.to_dict()
        assert d["event_type"] == "tool_policy"
        assert d["severity"] == "high"
        assert d["source"] == "tool_decision"
        assert d["decision"] == "DENY"
        assert d["metadata"]["reason"] == "blocked"


class TestSecurityEventCollector:
    """Tests for SecurityEventCollector filtering and batching."""

    def test_emit_filters_by_event_type(self) -> None:
        collector = SecurityEventCollector(filter_event_types={"tool_policy"})

        # tool_policy event should be buffered
        tool_event = SecurityEvent(
            event_type=EventType.TOOL_POLICY,
            severity=EventSeverity.INFO,
            source=EventSource.TOOL_DECISION,
        )
        collector.emit(tool_event)
        assert collector._queue.qsize() == 1

        # api event should be filtered out
        api_event = SecurityEvent(
            event_type=EventType.API,
            severity=EventSeverity.INFO,
            source=EventSource.AUDIT_LOG,
        )
        collector.emit(api_event)
        assert collector._queue.qsize() == 1  # still 1, api event filtered

    def test_emit_filters_by_severity(self) -> None:
        collector = SecurityEventCollector(min_severity=EventSeverity.HIGH)

        # HIGH severity should pass
        high_event = SecurityEvent(
            event_type=EventType.ANOMALY,
            severity=EventSeverity.HIGH,
            source=EventSource.SECURITY_FINDING,
        )
        collector.emit(high_event)
        assert collector._queue.qsize() == 1

        # INFO severity should be filtered
        info_event = SecurityEvent(
            event_type=EventType.API,
            severity=EventSeverity.INFO,
            source=EventSource.AUDIT_LOG,
        )
        collector.emit(info_event)
        assert collector._queue.qsize() == 1  # still 1, info filtered

    async def test_collector_flushes_to_exporters(self) -> None:
        class CountingExporter(SIEMExporter):
            def __init__(self) -> None:
                self.exported: list[SecurityEvent] = []

            @property
            def name(self) -> str:
                return "counting"

            async def export(self, events: list[SecurityEvent]) -> None:
                self.exported.extend(events)

        collector = SecurityEventCollector(batch_size=100, flush_interval=999)
        exporter = CountingExporter()
        collector.register_exporter(exporter)

        event = SecurityEvent(
            event_type=EventType.API,
            severity=EventSeverity.INFO,
            source=EventSource.AUDIT_LOG,
        )
        collector.emit(event)

        await collector._flush_batch()
        assert len(exporter.exported) == 1
        await collector.stop()

    def test_emit_to_siem_noop_when_disabled(self) -> None:
        from hecate.services.security.siem.collector import emit_to_siem

        # Should not raise when collector is None
        event = SecurityEvent(
            event_type=EventType.API,
            severity=EventSeverity.INFO,
            source=EventSource.AUDIT_LOG,
        )
        emit_to_siem(event)  # no exception


class TestNullSIEMExporter:
    """Tests for NullSIEMExporter."""

    async def test_null_exporter_discards(self) -> None:
        exporter = NullSIEMExporter()
        events = [
            SecurityEvent(
                event_type=EventType.API,
                severity=EventSeverity.INFO,
                source=EventSource.AUDIT_LOG,
            )
        ]
        await exporter.export(events)  # no exception
        assert exporter.name == "null"
