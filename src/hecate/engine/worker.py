from __future__ import annotations

from abc import ABC, abstractmethod

from hecate.engine.types import WorkerResult


class Worker(ABC):
    """Abstract interface for executing a single graph node."""

    @abstractmethod
    async def execute(self, node_id: str, node_config: dict, channel_snapshot: dict) -> WorkerResult:
        """Execute a node and return the result with channel updates and optional command."""
        ...


class WorkerPool(ABC):
    """Abstract interface for dispatching worker execution."""

    @abstractmethod
    async def dispatch(self, worker: Worker, node_id: str, node_config: dict, channel_snapshot: dict) -> WorkerResult:
        """Dispatch a worker execution and await the result."""
        ...


class DirectWorkerPool(WorkerPool):
    """Direct async dispatch — runs worker in the current event loop (P1 default)."""

    async def dispatch(self, worker: Worker, node_id: str, node_config: dict, channel_snapshot: dict) -> WorkerResult:
        """Await the worker directly in the current event loop."""
        return await worker.execute(node_id, node_config, channel_snapshot)
