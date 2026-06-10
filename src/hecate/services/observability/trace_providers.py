"""Trace provider interface for external observability backends.

Defines the ABC that external tracing providers (LangFuse, OTel Collector, etc.)
must implement, along with a default no-op implementation.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger(__name__)


class TraceProvider(ABC):
    """Abstract interface for external trace providers.

    Each provider receives trace/span lifecycle events and can
    forward them to its backend (LangFuse, OTel Collector, etc.).
    """

    @abstractmethod
    async def on_trace_start(self, data: dict[str, Any]) -> None:
        """Handle trace start event."""
        ...

    @abstractmethod
    async def on_span_start(self, data: dict[str, Any]) -> None:
        """Handle span start event."""
        ...

    @abstractmethod
    async def on_span_end(self, data: dict[str, Any]) -> None:
        """Handle span end event."""
        ...

    @abstractmethod
    async def flush(self) -> None:
        """Flush any buffered trace data to the backend."""
        ...


class NoOpTraceProvider(TraceProvider):
    """Default no-op trace provider that discards all events."""

    async def on_trace_start(self, data: dict[str, Any]) -> None:
        pass

    async def on_span_start(self, data: dict[str, Any]) -> None:
        pass

    async def on_span_end(self, data: dict[str, Any]) -> None:
        pass

    async def flush(self) -> None:
        pass
