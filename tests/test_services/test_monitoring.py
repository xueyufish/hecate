"""Tests for MonitoringService, ConnectionManager, and factory."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

from hecate.engine.metrics_store import InMemoryMetricsStore
from hecate.services.observability.monitoring import (
    ConnectionManager,
    MonitoringService,
    create_metrics_store,
)


class TestConnectionManager:
    """Tests for the WebSocket ConnectionManager."""

    async def test_connect(self) -> None:
        manager = ConnectionManager()
        ws = AsyncMock()
        await manager.connect(ws)
        assert manager.active_count == 1
        ws.accept.assert_awaited_once()

    async def test_disconnect(self) -> None:
        manager = ConnectionManager()
        ws = AsyncMock()
        await manager.connect(ws)
        manager.disconnect(ws)
        assert manager.active_count == 0

    async def test_disconnect_unknown(self) -> None:
        manager = ConnectionManager()
        ws = AsyncMock()
        manager.disconnect(ws)
        assert manager.active_count == 0

    async def test_broadcast(self) -> None:
        manager = ConnectionManager()
        ws1 = AsyncMock()
        ws2 = AsyncMock()
        await manager.connect(ws1)
        await manager.connect(ws2)
        await manager.broadcast({"type": "test", "value": 42})
        assert ws1.send_text.call_count == 1
        assert ws2.send_text.call_count == 1
        payload = json.loads(ws1.send_text.call_args[0][0])
        assert payload["type"] == "test"

    async def test_broadcast_removes_stale(self) -> None:
        manager = ConnectionManager()
        ws_ok = AsyncMock()
        ws_bad = AsyncMock()
        ws_bad.send_text.side_effect = Exception("Connection closed")
        await manager.connect(ws_ok)
        await manager.connect(ws_bad)
        await manager.broadcast({"type": "test"})
        assert manager.active_count == 1

    async def test_shutdown(self) -> None:
        manager = ConnectionManager()
        ws1 = AsyncMock()
        ws2 = AsyncMock()
        await manager.connect(ws1)
        await manager.connect(ws2)
        await manager.shutdown()
        assert manager.active_count == 0
        ws1.close.assert_awaited_once()
        ws2.close.assert_awaited_once()

    async def test_broadcast_no_connections(self) -> None:
        manager = ConnectionManager()
        await manager.broadcast({"type": "test"})  # Should not raise


class TestMonitoringService:
    """Tests for the MonitoringService push loop."""

    async def test_start(self) -> None:
        store = InMemoryMetricsStore()
        manager = ConnectionManager()
        service = MonitoringService(store, manager, push_interval=1)
        service.start()
        assert service.is_running
        if service._task:
            service._task.cancel()

    async def test_stop(self) -> None:
        store = InMemoryMetricsStore()
        manager = ConnectionManager()
        service = MonitoringService(store, manager, push_interval=1)
        service.start()
        await service.stop()
        assert not service.is_running

    async def test_start_idempotent(self) -> None:
        store = InMemoryMetricsStore()
        manager = ConnectionManager()
        service = MonitoringService(store, manager, push_interval=1)
        service.start()
        task = service._task
        service.start()  # Should not create a new task
        assert service._task is task
        if task:
            task.cancel()

    async def test_push_loop_broadcasts_snapshots(self) -> None:
        store = InMemoryMetricsStore()
        store.record_counter("test_metric", value=5.0)
        manager = ConnectionManager()
        ws = AsyncMock()
        await manager.connect(ws)

        service = MonitoringService(store, manager, push_interval=1)
        service.start()

        await asyncio.sleep(1)
        await service.stop()

        assert ws.send_text.call_count >= 1
        payload = json.loads(ws.send_text.call_args[0][0])
        assert payload["type"] == "metrics_snapshot"
        assert len(payload["metrics"]) >= 1

    async def test_push_loop_handles_exceptions(self) -> None:
        store = InMemoryMetricsStore()
        manager = ConnectionManager()
        ws = AsyncMock()
        await manager.connect(ws)

        # Force get_snapshot to raise
        original = store.get_snapshot

        def bad_snapshot(windows=None):
            if not hasattr(store, "_call_count"):
                store._call_count = 0  # type: ignore[attr-defined]
            store._call_count += 1  # type: ignore[attr-defined]
            if store._call_count == 1:  # type: ignore[attr-defined]
                raise RuntimeError("Test error")
            return original(windows)

        store.get_snapshot = bad_snapshot  # type: ignore[assignment]

        service = MonitoringService(store, manager, push_interval=1)
        service.start()
        await asyncio.sleep(1)
        await service.stop()
        # Should not have crashed

    async def test_stop_without_start(self) -> None:
        store = InMemoryMetricsStore()
        manager = ConnectionManager()
        service = MonitoringService(store, manager)
        await service.stop()  # Should not raise
        assert not service.is_running


class TestCreateMetricsStore:
    """Tests for the create_metrics_store factory."""

    def test_default_creates_in_memory(self) -> None:
        store = create_metrics_store()
        assert isinstance(store, InMemoryMetricsStore)

    def test_explicit_in_memory(self) -> None:
        store = create_metrics_store("in_memory")
        assert isinstance(store, InMemoryMetricsStore)

    def test_custom_buffer_size(self) -> None:
        store = create_metrics_store(max_buffer_size=500)
        assert isinstance(store, InMemoryMetricsStore)
        assert store._buffer_size == 500

    @patch("hecate.services.observability.monitoring.settings")
    def test_timescale_creates_timescale(self, mock_settings: MagicMock) -> None:
        mock_settings.METRICS_STORE_TYPE = "timescale"
        mock_settings.METRICS_PUSH_INTERVAL = 5
        mock_settings.MAX_METRICS_BUFFER_SIZE = 100000
        store = create_metrics_store("timescale")
        from hecate.services.observability.timescale_metrics_store import TimescaleMetricsStore

        assert isinstance(store, TimescaleMetricsStore)
