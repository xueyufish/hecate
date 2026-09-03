"""Tests for WebhookSIEMExporter."""

from __future__ import annotations

import json

import httpx

from hecate.ops.siem.event import (
    EventSeverity,
    EventSource,
    EventType,
    SecurityEvent,
)
from hecate.ops.siem.webhook import WebhookSIEMExporter


def _make_event(**kwargs) -> SecurityEvent:
    defaults = {
        "event_type": EventType.API,
        "severity": EventSeverity.INFO,
        "source": EventSource.AUDIT_LOG,
        "action": "agent.create",
        "decision": "success",
    }
    defaults.update(kwargs)
    return SecurityEvent(**defaults)


class TestWebhookPayload:
    """Tests for payload construction."""

    def test_json_format(self) -> None:
        exporter = WebhookSIEMExporter(url="http://test", fmt="json")
        events = [_make_event(), _make_event(severity=EventSeverity.HIGH)]
        payload = exporter._build_payload(events)
        data = json.loads(payload)
        assert "events" in data
        assert len(data["events"]) == 2

    def test_splunk_hec_format(self) -> None:
        exporter = WebhookSIEMExporter(url="http://test", fmt="splunk_hec")
        events = [_make_event()]
        payload = exporter._build_payload(events)
        lines = payload.decode("utf-8").strip().split("\n")
        assert len(lines) == 1
        line = json.loads(lines[0])
        assert "event" in line
        assert "time" in line
        assert line["sourcetype"] == "hecate:security"

    def test_headers_no_token(self) -> None:
        exporter = WebhookSIEMExporter(url="http://test", token="")
        headers = exporter._build_headers()
        assert "Authorization" not in headers
        assert headers["Content-Type"] == "application/json"

    def test_headers_bearer_token(self) -> None:
        exporter = WebhookSIEMExporter(url="http://test", token="secret123")  # noqa: S106
        headers = exporter._build_headers()
        assert headers["Authorization"] == "Bearer secret123"

    def test_headers_splunk_token(self) -> None:
        exporter = WebhookSIEMExporter(url="http://test", token="splunk-token", fmt="splunk_hec")  # noqa: S106
        headers = exporter._build_headers()
        assert headers["Authorization"] == "Splunk splunk-token"

    def test_headers_extra(self) -> None:
        exporter = WebhookSIEMExporter(
            url="http://test",
            extra_headers={"X-Custom": "value"},
        )
        headers = exporter._build_headers()
        assert headers["X-Custom"] == "value"

    @property
    def name(self) -> str:
        return "test"


class TestWebhookExport:
    """Tests for export behavior with mock HTTP."""

    async def test_export_success(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        exporter = WebhookSIEMExporter(url="http://test.local", fmt="json")

        class MockResp:
            status_code = 200

        async def mock_post(*args, **kwargs):
            return MockResp()

        monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)
        events = [_make_event()]
        await exporter.export(events)  # no exception

    async def test_export_no_url(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        exporter = WebhookSIEMExporter(url="", fmt="json")
        events = [_make_event()]
        await exporter.export(events)  # no exception, skipped

    async def test_export_client_error_no_retry(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        exporter = WebhookSIEMExporter(url="http://test.local", fmt="json")

        call_count = 0

        class MockResp:
            status_code = 401

        async def mock_post(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return MockResp()

        monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)
        events = [_make_event()]
        await exporter.export(events)
        assert call_count == 1  # no retry on 4xx

    async def test_export_server_error_retries(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        import asyncio

        original_sleep = asyncio.sleep

        async def fast_sleep(seconds):
            pass

        monkeypatch.setattr(asyncio, "sleep", fast_sleep)

        exporter = WebhookSIEMExporter(url="http://test.local", fmt="json")

        call_count = 0

        class MockResp:
            status_code = 503

        async def mock_post(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return MockResp()

        monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)
        events = [_make_event()]
        await exporter.export(events)
        assert call_count == 3  # 3 retries on 5xx

        monkeypatch.setattr(asyncio, "sleep", original_sleep)
