"""Unit tests for TemporalWorkerPool."""

from __future__ import annotations

import pytest

from hecate.engine.temporal.worker_pool import TemporalWorkerPool
from hecate.engine.worker import Worker, WorkerResult


class SimpleWorker(Worker):
    """Simple worker for testing."""

    async def execute(self, node_id, node_config, channel_snapshot, execution_context=None):
        return WorkerResult(
            node_id=node_id,
            channel_updates={"result": "done"},
        )


class TestTemporalWorkerPool:
    """Tests for the TemporalWorkerPool class."""

    def test_initialization(self) -> None:
        """Test worker pool initialization."""
        pool = TemporalWorkerPool()
        assert pool.task_queue == "hecate-workers"
        assert pool.heartbeat_timeout == 30.0
        assert pool.start_to_close_timeout == 300.0

    def test_custom_initialization(self) -> None:
        """Test worker pool with custom config."""
        pool = TemporalWorkerPool(
            task_queue="custom-queue",
            heartbeat_timeout=60.0,
            start_to_close_timeout=600.0,
        )
        assert pool.task_queue == "custom-queue"
        assert pool.heartbeat_timeout == 60.0
        assert pool.start_to_close_timeout == 600.0

    @pytest.mark.asyncio
    async def test_dispatch_fallback(self) -> None:
        """Test that dispatch falls back to direct execution."""
        pool = TemporalWorkerPool()
        worker = SimpleWorker()

        result = await pool.dispatch(
            worker=worker,
            node_id="test-node",
            node_config={"model": "gpt-4o"},
            channel_snapshot={"messages": []},
        )

        assert isinstance(result, WorkerResult)
        assert result.node_id == "test-node"
