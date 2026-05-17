from __future__ import annotations

from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from hecate.engine.types import WorkerResult


class Worker(ABC):
    """Abstract interface for executing a single graph node."""

    @abstractmethod
    async def execute(self, node_id: str, node_config: dict, channel_snapshot: dict) -> WorkerResult:
        """Execute a node and return the result with channel updates and optional command."""
        ...


class ThreadPoolWorkerPool:
    """Dispatches worker execution to a thread pool for CPU-bound parallelism."""

    def __init__(self, max_workers: int = 4) -> None:
        self._executor = ThreadPoolExecutor(max_workers=max_workers)

    async def dispatch(self, worker: Worker, node_id: str, node_config: dict, channel_snapshot: dict) -> WorkerResult:
        """Dispatch a worker execution to the thread pool and await the result."""
        import asyncio
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            self._executor,
            lambda: asyncio.run(worker.execute(node_id, node_config, channel_snapshot)),
        )

    def shutdown(self) -> None:
        """Shut down the thread pool executor."""
        self._executor.shutdown(wait=False)
