"""Real-time monitoring service with WebSocket push and metric aggregation.

Provides ConnectionManager for WebSocket lifecycle, MonitoringService for
periodic metric snapshot broadcasting, and a factory function for creating
the appropriate MetricsStore backend based on configuration.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any

from fastapi import WebSocket

from hecate.core.config import settings
from hecate.engine.metrics_store import (
    InMemoryMetricsStore,
    MetricsStore,
)

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Manages active WebSocket connections for metric streaming.

    Handles connection registration, removal, broadcasting of JSON messages,
    and graceful shutdown. Thread-safe for single-process async use.
    """

    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()

    @property
    def active_count(self) -> int:
        """Return the number of active WebSocket connections."""
        return len(self._connections)

    async def connect(self, websocket: WebSocket) -> None:
        """Accept and register a new WebSocket connection.

        Args:
            websocket: The incoming WebSocket to register.
        """
        await websocket.accept()
        self._connections.add(websocket)
        logger.info("WebSocket client connected (total: %d)", len(self._connections))

    def disconnect(self, websocket: WebSocket) -> None:
        """Remove a WebSocket connection from the active set.

        Args:
            websocket: The WebSocket to remove.
        """
        self._connections.discard(websocket)
        logger.info("WebSocket client disconnected (total: %d)", len(self._connections))

    async def broadcast(self, message: dict[str, Any]) -> None:
        """Broadcast a JSON message to all active connections.

        Connections that fail to receive are automatically removed.

        Args:
            message: Dictionary to serialize as JSON and send.
        """
        payload = json.dumps(message, default=str)
        stale: list[WebSocket] = []
        for ws in self._connections:
            try:
                await ws.send_text(payload)
            except Exception:
                stale.append(ws)
        for ws in stale:
            self._connections.discard(ws)

    async def shutdown(self) -> None:
        """Close all active connections gracefully."""
        for ws in list(self._connections):
            with contextlib.suppress(Exception):
                await ws.close()
        self._connections.clear()


class MonitoringService:
    """Periodically pushes metric snapshots to WebSocket clients.

    Runs an async loop that queries the MetricsStore at a configurable
    interval and broadcasts aggregated snapshots to all connected clients.
    """

    def __init__(
        self,
        metrics_store: MetricsStore,
        connection_manager: ConnectionManager,
        push_interval: int | None = None,
    ) -> None:
        self._store = metrics_store
        self._manager = connection_manager
        self._interval = push_interval or settings.METRICS_PUSH_INTERVAL
        self._task: asyncio.Task[None] | None = None

    @property
    def is_running(self) -> bool:
        """Return whether the push loop is currently active."""
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        """Start the background push loop."""
        if self.is_running:
            return
        self._task = asyncio.create_task(self._push_loop())
        logger.info("MonitoringService started (interval: %ds)", self._interval)

    async def stop(self) -> None:
        """Stop the push loop and close all WebSocket connections."""
        if self._task is not None and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        await self._manager.shutdown()
        logger.info("MonitoringService stopped")

    async def _push_loop(self) -> None:
        """Background loop that broadcasts metric snapshots."""
        while True:
            try:
                if self._manager.active_count > 0:
                    snapshot = self._store.get_snapshot()
                    message = {
                        "type": "metrics_snapshot",
                        "timestamp": snapshot.timestamp.isoformat(),
                        "window": snapshot.window,
                        "metrics": [
                            {
                                "name": m.name,
                                "value": m.value,
                                "aggregation": m.aggregation,
                                "window": m.window,
                                "tags": m.tags,
                                "count": m.count,
                            }
                            for m in snapshot.metrics
                        ],
                    }
                    await self._manager.broadcast(message)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Error in monitoring push loop")
            await asyncio.sleep(self._interval)


def create_metrics_store(
    store_type: str | None = None,
    max_buffer_size: int | None = None,
) -> MetricsStore:
    """Factory function to create the appropriate MetricsStore backend.

    Args:
        store_type: Backend type ("in_memory" or "timescale").
            Defaults to settings.METRICS_STORE_TYPE.
        max_buffer_size: Maximum ring buffer size for in-memory store.

    Returns:
        A MetricsStore instance.
    """
    store_type = store_type or settings.METRICS_STORE_TYPE
    if store_type == "timescale":
        from hecate.services.observability.timescale_metrics_store import TimescaleMetricsStore

        return TimescaleMetricsStore()
    return InMemoryMetricsStore(
        max_buffer_size=max_buffer_size or settings.MAX_METRICS_BUFFER_SIZE,
    )
