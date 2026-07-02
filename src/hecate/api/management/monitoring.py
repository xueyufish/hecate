"""API router for real-time monitoring endpoints.

Provides a WebSocket endpoint for live metric streaming and REST
endpoints for querying aggregated metrics and current snapshots.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect

from hecate.core.database import get_db
from hecate.engine.metrics_store import MetricsStore
from hecate.services.observability.monitoring import ConnectionManager, MonitoringService

router = APIRouter()

_window_q = Query(default="5m")
_aggregation_q = Query(default="sum")
_db_dep = Depends(get_db)

_manager = ConnectionManager()
_service: MonitoringService | None = None


def get_metrics_store() -> MetricsStore:
    """Return the application-level MetricsStore singleton."""
    from hecate.services.observability.monitoring import create_metrics_store

    return create_metrics_store()


def get_monitoring_service() -> MonitoringService:
    """Return or create the MonitoringService singleton."""
    global _service
    if _service is None:
        _service = MonitoringService(
            metrics_store=get_metrics_store(),
            connection_manager=_manager,
        )
    return _service


@router.websocket("/ws/monitoring")
async def monitoring_websocket(websocket: WebSocket) -> None:
    """WebSocket endpoint for real-time metric streaming.

    Clients connect and receive periodic metric snapshots pushed
    by the MonitoringService at the configured interval.
    """
    get_monitoring_service()
    await _manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        _manager.disconnect(websocket)


@router.get("/monitoring/metrics")
async def query_metrics(
    name: str | None = Query(default=None),
    window: str = _window_q,
    aggregation: str = _aggregation_q,
) -> Any:
    """Query aggregated metrics with optional name filter.

    Args:
        name: Optional metric name to query. If None, returns snapshot.
        window: Time window for aggregation (e.g., "5m", "1h").
        aggregation: Aggregation method ("sum", "avg", "min", "max").
    """
    store = get_metrics_store()
    if name:
        result = store.query_metrics(name, window=window, aggregation=aggregation)
        if result is None:
            return {"metrics": [], "window": window}
        return {
            "metrics": [
                {
                    "name": result.name,
                    "value": result.value,
                    "aggregation": result.aggregation,
                    "window": result.window,
                    "tags": result.tags,
                    "count": result.count,
                    "timestamp": result.timestamp.isoformat() if result.timestamp else None,
                }
            ],
            "window": window,
        }
    snapshot = store.get_snapshot(windows=[window])
    return {
        "metrics": [
            {
                "name": m.name,
                "value": m.value,
                "aggregation": m.aggregation,
                "window": m.window,
                "tags": m.tags,
                "count": m.count,
                "timestamp": m.timestamp.isoformat() if m.timestamp else None,
            }
            for m in snapshot.metrics
        ],
        "window": window,
        "timestamp": snapshot.timestamp.isoformat(),
    }


@router.get("/monitoring/snapshot")
async def get_snapshot(
    windows: str = Query(default="5m"),
) -> Any:
    """Get a full snapshot of all metrics across specified windows.

    Args:
        windows: Comma-separated list of time windows (e.g., "1m,5m,1h").
    """
    store = get_metrics_store()
    window_list = [w.strip() for w in windows.split(",")]
    snapshot = store.get_snapshot(windows=window_list)
    return {
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
        "timestamp": snapshot.timestamp.isoformat(),
        "window": snapshot.window,
    }
