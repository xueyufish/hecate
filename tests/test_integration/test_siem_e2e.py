"""End-to-end integration tests for SIEM export pipeline."""

from __future__ import annotations

from hecate.services.security.siem.collector import (
    SecurityEventCollector,
    set_collector,
)
from hecate.services.security.siem.event import (
    EventSeverity,
    EventSource,
    EventType,
    SecurityEvent,
    from_security_finding,
    from_tool_decision,
)
from hecate.services.security.siem.exporter import SIEMExporter


class CaptureExporter(SIEMExporter):
    """Test exporter that captures events for assertions."""

    def __init__(self) -> None:
        self.exported: list[SecurityEvent] = []

    @property
    def name(self) -> str:
        return "capture"

    async def export(self, events: list[SecurityEvent]) -> None:
        self.exported.extend(events)


class TestToolDecisionToSIEM:
    """E2E: ToolDecisionEmitter → collector → exporter."""

    async def test_tool_decision_deny_reaches_exporter(self) -> None:
        exporter = CaptureExporter()
        collector = SecurityEventCollector()
        collector.register_exporter(exporter)
        set_collector(collector)

        event = from_tool_decision(
            agent_id="agent-1",
            workspace_id="ws-1",
            tool_name="bash",
            decision="DENY",
            reason="blocked",
        )
        from hecate.services.security.siem.collector import emit_to_siem

        emit_to_siem(event)
        await collector._flush_batch()

        assert len(exporter.exported) == 1
        assert exporter.exported[0].decision == "DENY"
        assert exporter.exported[0].severity == EventSeverity.HIGH
        await collector.stop()
        set_collector(None)

    async def test_tool_decision_allow_reaches_exporter(self) -> None:
        exporter = CaptureExporter()
        collector = SecurityEventCollector()
        collector.register_exporter(exporter)
        set_collector(collector)

        event = from_tool_decision(
            agent_id="agent-1",
            workspace_id="ws-1",
            tool_name="search",
            decision="ALLOW",
            reason="approved",
        )
        from hecate.services.security.siem.collector import emit_to_siem

        emit_to_siem(event)
        await collector._flush_batch()

        assert len(exporter.exported) == 1
        assert exporter.exported[0].severity == EventSeverity.INFO
        await collector.stop()
        set_collector(None)


class TestFindingToSIEM:
    """E2E: FindingEngine finding → SIEM export."""

    async def test_finding_reaches_exporter(self) -> None:
        exporter = CaptureExporter()
        collector = SecurityEventCollector()
        collector.register_exporter(exporter)
        set_collector(collector)

        event = from_security_finding(
            rule_name="bulk_delete_rule",
            severity="high",
            message="5 deletes in 1 minute",
            org_id="org-1",
            workspace_id="ws-1",
            user_id="user-1",
        )
        from hecate.services.security.siem.collector import emit_to_siem

        emit_to_siem(event)
        await collector._flush_batch()

        assert len(exporter.exported) == 1
        assert exporter.exported[0].event_type == EventType.ANOMALY
        assert exporter.exported[0].severity == EventSeverity.HIGH
        await collector.stop()
        set_collector(None)


class TestFilteringIntegration:
    """Integration tests for event filtering."""

    async def test_severity_filter_blocks_low_severity(self) -> None:
        exporter = CaptureExporter()
        collector = SecurityEventCollector(min_severity=EventSeverity.HIGH)
        collector.register_exporter(exporter)
        set_collector(collector)

        from hecate.services.security.siem.collector import emit_to_siem

        # INFO event should be filtered out
        info_event = SecurityEvent(
            event_type=EventType.API,
            severity=EventSeverity.INFO,
            source=EventSource.AUDIT_LOG,
        )
        emit_to_siem(info_event)

        # HIGH event should pass
        high_event = SecurityEvent(
            event_type=EventType.ANOMALY,
            severity=EventSeverity.HIGH,
            source=EventSource.SECURITY_FINDING,
        )
        emit_to_siem(high_event)

        await collector._flush_batch()

        assert len(exporter.exported) == 1
        assert exporter.exported[0].severity == EventSeverity.HIGH
        await collector.stop()
        set_collector(None)

    async def test_event_type_filter(self) -> None:
        exporter = CaptureExporter()
        collector = SecurityEventCollector(filter_event_types={"tool_policy", "anomaly"})
        collector.register_exporter(exporter)
        set_collector(collector)

        from hecate.services.security.siem.collector import emit_to_siem

        # API event should be filtered out
        api_event = SecurityEvent(
            event_type=EventType.API,
            severity=EventSeverity.CRITICAL,
            source=EventSource.AUDIT_LOG,
        )
        emit_to_siem(api_event)

        # tool_policy event should pass
        tool_event = SecurityEvent(
            event_type=EventType.TOOL_POLICY,
            severity=EventSeverity.HIGH,
            source=EventSource.TOOL_DECISION,
        )
        emit_to_siem(tool_event)

        await collector._flush_batch()

        assert len(exporter.exported) == 1
        assert exporter.exported[0].event_type == EventType.TOOL_POLICY
        await collector.stop()
        set_collector(None)


class TestGracefulShutdown:
    """Tests for buffer flush on shutdown."""

    async def test_buffer_flushed_on_stop(self) -> None:
        exporter = CaptureExporter()
        collector = SecurityEventCollector(batch_size=1000, flush_interval=999)
        collector.register_exporter(exporter)

        event = SecurityEvent(
            event_type=EventType.API,
            severity=EventSeverity.INFO,
            source=EventSource.AUDIT_LOG,
        )
        collector.emit(event)

        assert len(exporter.exported) == 0  # not yet flushed

        await collector.stop()  # stop triggers flush

        assert len(exporter.exported) == 1


class TestMultipleExporters:
    """Tests for multiple exporters receiving same events."""

    async def test_two_exporters_both_receive(self) -> None:
        exp1 = CaptureExporter()
        exp2 = CaptureExporter()
        collector = SecurityEventCollector()
        collector.register_exporter(exp1)
        collector.register_exporter(exp2)

        event = SecurityEvent(
            event_type=EventType.ANOMALY,
            severity=EventSeverity.CRITICAL,
            source=EventSource.SECURITY_FINDING,
        )
        collector.emit(event)
        await collector._flush_batch()

        assert len(exp1.exported) == 1
        assert len(exp2.exported) == 1
        await collector.stop()

    async def test_exporter_failure_does_not_block_others(self) -> None:
        class FailingExporter(SIEMExporter):
            @property
            def name(self) -> str:
                return "failing"

            async def export(self, events: list[SecurityEvent]) -> None:
                raise RuntimeError("export failed")

        good = CaptureExporter()
        bad = FailingExporter()
        collector = SecurityEventCollector()
        collector.register_exporter(bad)
        collector.register_exporter(good)

        event = SecurityEvent(
            event_type=EventType.API,
            severity=EventSeverity.INFO,
            source=EventSource.AUDIT_LOG,
        )
        collector.emit(event)
        await collector._flush_batch()

        # Good exporter should still receive despite bad one failing
        assert len(good.exported) == 1
        await collector.stop()


class TestDisabledSIEM:
    """Tests for SIEM disabled behavior."""

    def test_emit_to_siem_noop_without_collector(self) -> None:
        from hecate.services.security.siem.collector import emit_to_siem

        set_collector(None)
        event = SecurityEvent(
            event_type=EventType.API,
            severity=EventSeverity.INFO,
            source=EventSource.AUDIT_LOG,
        )
        emit_to_siem(event)  # no exception, no-op
