"""SIEMExporter — abstract interface for SIEM export sinks.

Multiple exporters can be registered with the SecurityEventCollector.
Each exporter receives the same batch of events and sends them to its target.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from hecate.services.security.siem.event import SecurityEvent

logger = logging.getLogger(__name__)


class SIEMExporter(ABC):
    """Abstract exporter for security events to external SIEM systems.

    Implementations must be non-blocking on the collector's emit() path.
    The export() method is called asynchronously during batch flush.
    """

    @abstractmethod
    async def export(self, events: list[SecurityEvent]) -> None:
        """Export a batch of security events to the SIEM system.

        Args:
            events: Batch of normalized security events to export.
        """
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """Exporter name for logging and identification."""
        ...


class NullSIEMExporter(SIEMExporter):
    """No-op exporter — default when no exporters are configured."""

    async def export(self, events: list[SecurityEvent]) -> None:
        """Discard all events silently."""

    @property
    def name(self) -> str:
        return "null"
