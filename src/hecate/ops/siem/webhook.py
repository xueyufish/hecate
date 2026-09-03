"""WebhookSIEMExporter — HTTPS POST export to external SIEM endpoints.

Supports Splunk HEC format and generic JSON format.
"""

from __future__ import annotations

import asyncio
import json
import logging

import httpx

from hecate.core.config import settings
from hecate.ops.siem.event import SecurityEvent
from hecate.ops.siem.exporter import SIEMExporter

logger = logging.getLogger(__name__)


class WebhookSIEMExporter(SIEMExporter):
    """Export security events via HTTPS POST to a SIEM webhook endpoint.

    Args:
        url: Webhook endpoint URL. Defaults to SIEM_WEBHOOK_URL.
        token: Bearer token for auth. Defaults to SIEM_WEBHOOK_TOKEN.
        fmt: Output format: "json" or "splunk_hec". Defaults to SIEM_WEBHOOK_FORMAT.
        extra_headers: Additional headers as dict. Parsed from SIEM_WEBHOOK_HEADERS JSON.
    """

    def __init__(
        self,
        url: str | None = None,
        token: str | None = None,
        fmt: str | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self._url = url or settings.SIEM_WEBHOOK_URL
        self._token = token or settings.SIEM_WEBHOOK_TOKEN
        self._format = fmt or settings.SIEM_WEBHOOK_FORMAT
        self._extra_headers = extra_headers or self._parse_headers()

    def _parse_headers(self) -> dict[str, str]:
        """Parse extra headers from SIEM_WEBHOOK_HEADERS JSON config."""
        raw = settings.SIEM_WEBHOOK_HEADERS
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            logger.warning("Invalid SIEM_WEBHOOK_HEADERS JSON, ignoring: %s", raw)
            return {}

    @property
    def name(self) -> str:
        return f"webhook({self._format})"

    def _build_payload(self, events: list[SecurityEvent]) -> bytes:
        """Build HTTP payload based on configured format."""
        if self._format == "splunk_hec":
            # Splunk HEC: each event wrapped separately
            lines = []
            for event in events:
                payload = {
                    "time": event.timestamp.timestamp(),
                    "event": event.to_dict(),
                    "source": "hecate",
                    "sourcetype": "hecate:security",
                }
                lines.append(json.dumps(payload))
            return "\n".join(lines).encode("utf-8")
        # Generic JSON: all events in one body
        body = json.dumps({"events": [e.to_dict() for e in events]})
        return body.encode("utf-8")

    def _build_headers(self) -> dict[str, str]:
        """Build HTTP headers."""
        headers = {"Content-Type": "application/json"}
        if self._format == "splunk_hec" and self._token:
            headers["Authorization"] = f"Splunk {self._token}"
        elif self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        headers.update(self._extra_headers)
        return headers

    async def export(self, events: list[SecurityEvent]) -> None:
        """Send a batch of events via HTTP POST with retry."""
        if not self._url:
            logger.warning("WebhookSIEMExporter: no URL configured, skipping")
            return

        payload = self._build_payload(events)
        headers = self._build_headers()

        last_error = None
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.post(self._url, content=payload, headers=headers)

                if resp.status_code < 400:
                    logger.debug(
                        "WebhookSIEMExporter: sent %d events (HTTP %d)",
                        len(events),
                        resp.status_code,
                    )
                    return

                if resp.status_code < 500:
                    # Client error — no retry
                    logger.error(
                        "WebhookSIEMExporter: HTTP %d — dropping %d events (client error)",
                        resp.status_code,
                        len(events),
                    )
                    return

                # Server error — retry
                last_error = f"HTTP {resp.status_code}"
                logger.warning(
                    "WebhookSIEMExporter: %s, retrying (attempt %d/3)",
                    last_error,
                    attempt + 1,
                )
            except (httpx.ConnectError, httpx.TimeoutException) as e:
                last_error = str(e)
                logger.warning(
                    "WebhookSIEMExporter: %s, retrying (attempt %d/3)",
                    last_error,
                    attempt + 1,
                )

            if attempt < 2:
                await asyncio.sleep(2**attempt)  # 1s, 2s

        logger.error(
            "WebhookSIEMExporter: failed after 3 retries (%s), dropping %d events",
            last_error,
            len(events),
        )
