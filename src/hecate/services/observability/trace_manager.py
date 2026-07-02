"""Async trace dispatch manager with provider plugin support.

OpsTraceManager writes traces to the local database and asynchronously
dispatches events to configured external providers (LangFuse, OTel, etc.).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from hecate.services.observability.trace_providers import TraceProvider

logger = logging.getLogger(__name__)


class OpsTraceManager:
    """Manager for trace dispatch with async provider support.

    Queues trace/span events for asynchronous dispatch to configured
    external providers without blocking the request path.
    """

    def __init__(
        self,
        providers: list[TraceProvider] | None = None,
    ) -> None:
        self._providers = providers or []
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._worker_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Start the background dispatch worker."""
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._worker())

    async def stop(self) -> None:
        """Stop the background worker and flush pending events."""
        await self._queue.put({"action": "stop"})
        if self._worker_task and not self._worker_task.done():
            try:
                await asyncio.wait_for(self._worker_task, timeout=5.0)
            except TimeoutError:
                logger.warning("OpsTraceManager worker did not stop in time")
        await self.flush()

    async def _worker(self) -> None:
        """Background coroutine that dispatches events to providers."""
        while True:
            event = await self._queue.get()
            if event.get("action") == "stop":
                break
            try:
                await self._dispatch_to_providers(event)
            except Exception:
                logger.exception("Failed to dispatch trace event to providers")
            finally:
                self._queue.task_done()

    async def _dispatch_to_providers(self, event: dict[str, Any]) -> None:
        """Dispatch a single event to all configured providers."""
        action = event.get("action")
        for provider in self._providers:
            try:
                if action == "trace_start":
                    await provider.on_trace_start(event)
                elif action == "span_start":
                    await provider.on_span_start(event)
                elif action == "span_end":
                    await provider.on_span_end(event)
            except Exception:
                logger.exception("Provider %s failed on %s", provider.__class__.__name__, action)

    async def on_trace_start(self, trace_data: dict[str, Any]) -> None:
        """Record a trace start and queue for provider dispatch."""
        await self._queue.put({"action": "trace_start", **trace_data})

    async def on_span_start(self, span_data: dict[str, Any]) -> None:
        """Record a span start and queue for provider dispatch."""
        await self._queue.put({"action": "span_start", **span_data})

    async def on_span_end(self, span_data: dict[str, Any]) -> None:
        """Record a span end and queue for provider dispatch."""
        await self._queue.put({"action": "span_end", **span_data})

    async def flush(self) -> None:
        """Flush all pending events to providers."""
        for provider in self._providers:
            try:
                await provider.flush()
            except Exception:
                logger.exception("Provider %s flush failed", provider.__class__.__name__)
