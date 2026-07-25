"""SyslogSIEMExporter — RFC 5424 syslog export over TCP/UDP with optional TLS.

Sends security events as syslog messages to external SIEM systems
(QRadar, ArcSight, rsyslog, etc.).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import socket
import ssl

from hecate.core.config import settings
from hecate.services.security.siem.event import EventSeverity, SecurityEvent
from hecate.services.security.siem.exporter import SIEMExporter

logger = logging.getLogger(__name__)

# Syslog severity mapping (RFC 5424 Section 6.2.1)
# OCSF/SecurityEvent severity → syslog severity (lower = more severe)
_SEVERITY_TO_SYSLOG = {
    EventSeverity.CRITICAL: 0,  # Emergency
    EventSeverity.HIGH: 1,  # Alert
    EventSeverity.MEDIUM: 3,  # Error
    EventSeverity.LOW: 4,  # Warning
    EventSeverity.INFO: 6,  # Informational
}


class SyslogSIEMExporter(SIEMExporter):
    """Export security events via RFC 5424 syslog.

    Args:
        host: Syslog server hostname. Defaults to SIEM_SYSLOG_HOST.
        port: Syslog server port. Defaults to SIEM_SYSLOG_PORT.
        protocol: Transport protocol: "tcp" or "udp". Defaults to SIEM_SYSLOG_PROTOCOL.
        use_tls: Wrap TCP connection in TLS. Defaults to SIEM_SYSLOG_TLS.
        facility: Syslog facility code. Defaults to SIEM_SYSLOG_FACILITY (4 = security).
    """

    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        protocol: str | None = None,
        use_tls: bool | None = None,
        facility: int | None = None,
    ) -> None:
        self._host = host or settings.SIEM_SYSLOG_HOST
        self._port = port or settings.SIEM_SYSLOG_PORT
        self._protocol = (protocol or settings.SIEM_SYSLOG_PROTOCOL).lower()
        self._use_tls = use_tls if use_tls is not None else settings.SIEM_SYSLOG_TLS
        self._facility = facility if facility is not None else settings.SIEM_SYSLOG_FACILITY
        self._tcp_writer: asyncio.StreamWriter | None = None

    @property
    def name(self) -> str:
        return f"syslog({self._protocol}://{self._host}:{self._port})"

    def _build_message(self, event: SecurityEvent) -> bytes:
        """Build an RFC 5424 compliant syslog message.

        Format: <PRI>VERSION TIMESTAMP HOSTNAME APPNAME PROCID MSGID STRUCTURED-DATA MSG
        """
        syslog_severity = _SEVERITY_TO_SYSLOG.get(event.severity, 6)
        pri = self._facility * 8 + syslog_severity

        timestamp = event.timestamp.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        hostname = socket.gethostname()
        appname = "hecate"
        procid = "-"
        msgid = f"hecate.{event.event_type}"

        # Structured data with key fields
        sd_parts = [
            f'hecateEvent="{event.event_type}"',
            f'hecateSeverity="{event.severity.name.lower()}"',
            f'hecateSource="{event.source}"',
            f'hecateAction="{event.action}"',
            f'hecateDecision="{event.decision}"',
        ]
        structured_data = f"[hecate {' '.join(sd_parts)}]"

        # Message body
        msg = event.to_dict()
        import json

        msg_str = json.dumps(msg, default=str)

        message = f"<{pri}>1 {timestamp} {hostname} {appname} {procid} {msgid} {structured_data} {msg_str}"
        return (message + "\n").encode("utf-8")

    async def _ensure_tcp_connection(self) -> asyncio.StreamWriter | None:
        """Ensure a TCP connection exists, reconnecting if needed."""
        if self._tcp_writer is not None and not self._tcp_writer.is_closing():
            return self._tcp_writer

        try:
            if self._use_tls:
                ctx = ssl.create_default_context()
                reader, writer = await asyncio.open_connection(self._host, self._port, ssl=ctx)
            else:
                reader, writer = await asyncio.open_connection(self._host, self._port)
            self._tcp_writer = writer
            return writer
        except (TimeoutError, OSError) as e:
            logger.error("SyslogSIEMExporter: failed to connect to %s:%d: %s", self._host, self._port, e)
            self._tcp_writer = None
            return None

    async def _send_tcp(self, data: bytes) -> None:
        """Send data over TCP with reconnection."""
        writer = await self._ensure_tcp_connection()
        if writer is None:
            logger.error("SyslogSIEMExporter: no TCP connection, dropping batch")
            return

        try:
            writer.write(data)
            await writer.drain()
        except (TimeoutError, OSError) as e:
            logger.warning("SyslogSIEMExporter: TCP send failed (%s), will reconnect", e)
            self._tcp_writer = None
            # Try one reconnect
            writer = await self._ensure_tcp_connection()
            if writer is not None:
                try:
                    writer.write(data)
                    await writer.drain()
                except (TimeoutError, OSError):
                    logger.error("SyslogSIEMExporter: reconnect failed, dropping batch")
                    self._tcp_writer = None

    async def _send_udp(self, data: bytes) -> None:
        """Send data over UDP (fire-and-forget)."""
        loop = asyncio.get_event_loop()
        try:
            transport = await loop.create_datagram_endpoint(
                asyncio.DatagramProtocol,
                remote_addr=(self._host, self._port),
            )
            sock = transport[0]
            sock.sendto(data)
            sock.close()
        except OSError as e:
            logger.error("SyslogSIEMExporter: UDP send failed: %s", e)

    async def export(self, events: list[SecurityEvent]) -> None:
        """Send a batch of events via syslog."""
        for event in events:
            data = self._build_message(event)
            if self._protocol == "tcp":
                await self._send_tcp(data)
            else:
                await self._send_udp(data)

        logger.debug("SyslogSIEMExporter: sent %d events", len(events))

    async def close(self) -> None:
        """Close TCP connection if open."""
        if self._tcp_writer is not None:
            self._tcp_writer.close()
            with contextlib.suppress(TimeoutError, OSError):
                await self._tcp_writer.wait_closed()
            self._tcp_writer = None
