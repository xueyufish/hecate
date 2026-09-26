"""Temporal-based worker pool for distributed graph execution.

Implements the WorkerPool interface using Temporal.io for distributed
task execution with automatic retries, timeouts, and heartbeats.
"""

from __future__ import annotations

import logging
from typing import Any

from hecate.runtime.worker import Worker, WorkerPool, WorkerResult

logger = logging.getLogger(__name__)


class TemporalWorkerPool(WorkerPool):
    """Worker pool that dispatches node execution as Temporal Activities.

    Intended for distributed execution across multiple workers with
    automatic retries, timeouts, heartbeats, and durable execution. NOT
    implemented yet: ``dispatch`` fails loudly instead of silently
    degrading to local execution (see the feature catalog's Temporal
    note). Full integration requires the temporalio package, a running
    Temporal server, and registered node-execution Activities.
    """

    def __init__(
        self,
        task_queue: str = "hecate-workers",
        heartbeat_timeout: float = 30.0,
        start_to_close_timeout: float = 300.0,
    ) -> None:
        """Initialize the Temporal worker pool.

        Args:
            task_queue: Temporal task queue name.
            heartbeat_timeout: Seconds between heartbeats.
            start_to_close_timeout: Max seconds for activity execution.
        """
        self.task_queue = task_queue
        self.heartbeat_timeout = heartbeat_timeout
        self.start_to_close_timeout = start_to_close_timeout

    async def dispatch(
        self,
        worker: Worker,
        node_id: str,
        node_config: dict[str, Any],
        channel_snapshot: dict[str, Any],
        execution_context: dict[str, Any] | None = None,
    ) -> WorkerResult:
        """Dispatch node execution as a Temporal Activity.

        Not implemented: scheduling the Activity and awaiting its result
        requires the Temporal client/workflow integration that is still
        pending (see the feature catalog's Temporal note). This pool
        deliberately fails loudly instead of silently degrading to local
        execution — callers get an honest error, not a false sense of
        distributed execution.

        Args:
            worker: The worker to execute the node (unused while unimplemented).
            node_id: The node identifier.
            node_config: Node configuration dict.
            channel_snapshot: Read-only channel state snapshot.

        Raises:
            NotImplementedError: Always — distributed dispatch is not implemented.
        """
        logger.debug(f"Dispatching node {node_id} via Temporal (task_queue={self.task_queue})")

        raise NotImplementedError(
            "Distributed dispatch via Temporal is not implemented: TemporalWorkerPool "
            "does not schedule Activities yet. Use the default DirectWorkerPool for "
            "local execution, or implement the Temporal Activity integration "
            "(see runtime/temporal/run_worker.py)."
        )
