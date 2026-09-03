"""SecurityEventCollector — subscribes to all security event sources.

Normalizes events from AuditLog, ToolDecision, and SecurityFinding into
SecurityEvent, applies configurable filtering (event type + severity),
and routes to registered SIEMExporters via async batch flushing.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from hecate.core.config import settings
from hecate.ops.siem.event import EventSeverity, SecurityEvent
from hecate.ops.siem.exporter import SIEMExporter

logger = logging.getLogger(__name__)


class SecurityEventCollector:
    """Collects, normalizes, filters, and exports security events.

    Usage::

        collector = SecurityEventCollector()
        collector.register_exporter(WebhookSIEMExporter(...))
        await collector.start()

        # Emit from anywhere:
        collector.emit(event)

        # Shutdown:
        await collector.stop()
    """

    def __init__(
        self,
        batch_size: int | None = None,
        flush_interval: float | None = None,
        filter_event_types: set[str] | None = None,
        min_severity: EventSeverity | None = None,
    ) -> None:
        self._batch_size = batch_size or settings.SIEM_BATCH_SIZE
        self._flush_interval = flush_interval or settings.SIEM_FLUSH_INTERVAL
        self._filter_event_types = filter_event_types or self._parse_filter_types()
        self._min_severity = min_severity or self._parse_min_severity()
        self._exporters: list[SIEMExporter] = []
        self._queue: asyncio.Queue[SecurityEvent] = asyncio.Queue(maxsize=10000)
        self._drain_task: asyncio.Task[None] | None = None

    def _parse_filter_types(self) -> set[str]:
        """Parse SIEM_FILTER_EVENT_TYPES config into a set."""
        raw = settings.SIEM_FILTER_EVENT_TYPES
        if not raw:
            return set()  # empty = all types
        return {t.strip() for t in raw.split(",") if t.strip()}

    def _parse_min_severity(self) -> EventSeverity:
        """Parse SIEM_MIN_SEVERITY config."""
        return EventSeverity.from_str(settings.SIEM_MIN_SEVERITY)

    def register_exporter(self, exporter: SIEMExporter) -> None:
        """Register a SIEM exporter."""
        self._exporters.append(exporter)
        logger.info("Registered SIEM exporter: %s", exporter.name)

    def emit(self, event: SecurityEvent) -> None:
        """Accept a security event for export.

        Non-blocking: buffers the event for async batch flush.
        Applies filtering before buffering.
        """
        # Filter by event type
        if self._filter_event_types and event.event_type not in self._filter_event_types:
            return

        # Filter by severity
        if event.severity < self._min_severity:
            return

        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning("SIEM collector queue full, dropping event")

    async def start(self) -> None:
        """Start the background drain task and initialize exporters."""
        self._init_exporters_from_config()
        if self._drain_task is None or self._drain_task.done():
            self._drain_task = asyncio.create_task(self._drain_loop())
        logger.info(
            "SecurityEventCollector started (batch_size=%d, flush=%.1fs, exporters=%d)",
            self._batch_size,
            self._flush_interval,
            len(self._exporters),
        )

    def _init_exporters_from_config(self) -> None:
        """Initialize exporters from SIEM_EXPORTERS config."""
        raw = settings.SIEM_EXPORTERS
        if not raw:
            return

        exporter_names = {e.strip().lower() for e in raw.split(",") if e.strip()}

        if "webhook" in exporter_names and settings.SIEM_WEBHOOK_URL:
            from hecate.ops.siem.webhook import WebhookSIEMExporter

            self.register_exporter(WebhookSIEMExporter())

        if "syslog" in exporter_names:
            from hecate.ops.siem.syslog import SyslogSIEMExporter

            self.register_exporter(SyslogSIEMExporter())

    async def stop(self) -> None:
        """Flush remaining events and stop background task."""
        if self._drain_task and not self._drain_task.done():
            self._drain_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._drain_task
        await self._flush_batch()
        logger.info("SecurityEventCollector stopped")

    async def _drain_loop(self) -> None:
        """Background loop that drains the queue in batches."""
        while True:
            try:
                await asyncio.sleep(self._flush_interval)
                await self._flush_batch()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("SIEM collector drain loop error")
                await asyncio.sleep(1.0)

    async def _flush_batch(self) -> None:
        """Flush queued events to all registered exporters."""
        if self._queue.empty():
            return

        batch: list[SecurityEvent] = []
        while not self._queue.empty() and len(batch) < self._batch_size:
            try:
                batch.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break

        if not batch:
            return

        for exporter in self._exporters:
            try:
                await exporter.export(batch)
            except Exception:
                logger.exception(
                    "SIEM exporter '%s' failed to export %d events",
                    exporter.name,
                    len(batch),
                )

        logger.debug("Flushed %d security events to %d exporters", len(batch), len(self._exporters))


# Module-level singleton — set by main.py lifespan when SIEM_ENABLED=true
_collector: SecurityEventCollector | None = None


def get_collector() -> SecurityEventCollector | None:
    """Return the global collector singleton, or None if SIEM is disabled."""
    return _collector


def set_collector(collector: SecurityEventCollector | None) -> None:
    """Set the global collector singleton."""
    global _collector  # noqa: PLW0603
    _collector = collector


def emit_to_siem(event: SecurityEvent) -> None:
    """Emit a SecurityEvent to the global collector if available.

    Convenience function for use throughout the codebase.
    No-op if SIEM is disabled (collector is None).
    """
    if _collector is not None:
        _collector.emit(event)
