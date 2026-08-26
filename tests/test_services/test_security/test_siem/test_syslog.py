"""Tests for SyslogSIEMExporter RFC 5424 message construction."""

from __future__ import annotations

from datetime import UTC, datetime

from hecate.services.security.siem.event import (
    EventSeverity,
    EventSource,
    EventType,
    SecurityEvent,
)
from hecate.services.security.siem.syslog import SyslogSIEMExporter


def _make_event(severity: EventSeverity = EventSeverity.INFO) -> SecurityEvent:
    return SecurityEvent(
        event_type=EventType.API,
        severity=severity,
        source=EventSource.AUDIT_LOG,
        timestamp=datetime(2026, 7, 25, 12, 0, 0, tzinfo=UTC),
        actor_user_id="user-1",
        action="agent.create",
        decision="success",
        resource="/api/agents",
    )


class TestSyslogMessage:
    """Tests for RFC 5424 message construction."""

    def test_pri_calculation_info(self) -> None:
        exporter = SyslogSIEMExporter(facility=4)
        msg = exporter._build_message(_make_event(EventSeverity.INFO))
        decoded = msg.decode("utf-8")
        # facility=4, syslog severity for INFO=6 → PRI = 4*8+6 = 38
        assert "<38>1" in decoded

    def test_pri_calculation_critical(self) -> None:
        exporter = SyslogSIEMExporter(facility=4)
        msg = exporter._build_message(_make_event(EventSeverity.CRITICAL))
        decoded = msg.decode("utf-8")
        # facility=4, syslog severity for CRITICAL=0 → PRI = 4*8+0 = 32
        assert "<32>1" in decoded

    def test_pri_calculation_high(self) -> None:
        exporter = SyslogSIEMExporter(facility=4)
        msg = exporter._build_message(_make_event(EventSeverity.HIGH))
        decoded = msg.decode("utf-8")
        # facility=4, syslog severity for HIGH=1 → PRI = 4*8+1 = 33
        assert "<33>1" in decoded

    def test_message_contains_timestamp(self) -> None:
        exporter = SyslogSIEMExporter()
        msg = exporter._build_message(_make_event())
        decoded = msg.decode("utf-8")
        assert "2026-07-25T12:00:00" in decoded

    def test_message_contains_appname(self) -> None:
        exporter = SyslogSIEMExporter()
        msg = exporter._build_message(_make_event())
        decoded = msg.decode("utf-8")
        assert "hecate" in decoded

    def test_message_contains_msgid(self) -> None:
        exporter = SyslogSIEMExporter()
        msg = exporter._build_message(_make_event())
        decoded = msg.decode("utf-8")
        assert "hecate.api" in decoded

    def test_message_contains_structured_data(self) -> None:
        exporter = SyslogSIEMExporter()
        msg = exporter._build_message(_make_event())
        decoded = msg.decode("utf-8")
        assert "[hecate" in decoded
        assert 'hecateEvent="api"' in decoded

    def test_message_ends_with_newline(self) -> None:
        exporter = SyslogSIEMExporter()
        msg = exporter._build_message(_make_event())
        assert msg.endswith(b"\n")

    def test_name_property(self) -> None:
        exporter = SyslogSIEMExporter(host="siem.example.com", port=601, protocol="tcp")
        assert exporter.name == "syslog(tcp://siem.example.com:601)"

    async def test_export_udp_no_exception(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        """UDP export should not raise even without a real server."""
        exporter = SyslogSIEMExporter(host="127.0.0.1", port=9999, protocol="udp")

        # Mock the datagram endpoint to avoid actual network calls
        import asyncio

        class MockTransport:
            def sendto(self, data: bytes) -> None:
                pass

            def close(self) -> None:
                pass

        async def mock_create_datagram(*args, **kwargs):
            return MockTransport(), None

        monkeypatch.setattr(
            asyncio.get_running_loop(),
            "create_datagram_endpoint",
            mock_create_datagram,
        )
        events = [_make_event()]
        await exporter.export(events)  # no exception
