"""Worker interfaces for graph node execution.

Defines the contract for executing individual graph nodes (Worker) and the
dispatch mechanism for running them (WorkerPool). The execution engine dispatches
one worker per node per superstep, passing a read-only channel snapshot and
receiving a WorkerResult with channel updates and an optional Command.

Workers that produce streaming output (e.g., LLM token generation) can override
``execute_stream()`` to yield intermediate token events before returning the
final WorkerResult.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from typing import Any

from hecate.engine.types import WorkerResult


class Worker(ABC):
    """Abstract interface for executing a single graph node.

    A Worker receives a node ID, its configuration, and a read-only snapshot
    of all channels. It must return a WorkerResult containing channel updates
    and an optional Command (interrupt, goto, or state update).

    For streaming use cases (e.g., LLM token generation), override
    ``execute_stream()`` instead. The default ``execute_stream()`` delegates
    to ``execute()`` with no intermediate events.
    """

    def __init__(self, event_store: Any = None) -> None:
        """Initialize the worker with an optional event store.

        Args:
            event_store: Optional EventStore for recording execution events.
                When None, no events are recorded (default behavior).
        """
        self._event_store = event_store

    @abstractmethod
    async def execute(
        self,
        node_id: str,
        node_config: dict,
        channel_snapshot: dict,
        execution_context: dict | None = None,
    ) -> WorkerResult:
        """Execute a node and return the result.

        The contract is:
        1. **Read** -- extract needed values from ``channel_snapshot`` (read-only;
           mutations to the dict will not affect engine state).
        2. **Execute** -- perform the node's logic (LLM call, tool invocation,
           condition evaluation, etc.).
        3. **Return** -- produce a WorkerResult with ``channel_updates`` (a dict
           of channel names to new values) and an optional ``command``.

        Args:
            node_id: The ID of the node to execute.
            node_config: The node's configuration dict (model, prompts, channels, etc.).
            channel_snapshot: A read-only deep copy of all channel values at the
                start of this superstep.
            execution_context: Optional dict with execution metadata from PregelRuntime:
                {"session_id": UUID, "superstep": int, "event_store": EventStore}.
                Workers can use this to record execution detail events.

        Returns:
            A WorkerResult with channel_updates, optional command, node_id, and
            optional error.
        """
        ...

    async def execute_stream(
        self,
        node_id: str,
        node_config: dict,
        channel_snapshot: dict,
        execution_context: dict | None = None,
    ) -> AsyncGenerator[dict[str, Any] | WorkerResult, None]:
        """Execute a node with optional intermediate token events.

        Override this method for streaming Workers (e.g., LLM nodes that yield
        tokens). Each yielded dict is forwarded as a ``{"type": "message", ...}``
        event by PregelRuntime. The final yielded value MUST be a WorkerResult.

        The default implementation delegates to ``execute()`` with no intermediate
        events, so non-streaming Workers do not need to override this method.

        Args:
            node_id: The ID of the node to execute.
            node_config: The node's configuration dict.
            channel_snapshot: A read-only deep copy of all channel values.
            execution_context: Optional dict with execution metadata from PregelRuntime.

        Yields:
            Intermediate token dicts (``{"content": "<token>"}``), followed by
            a final WorkerResult.
        """
        result = await self.execute(node_id, node_config, channel_snapshot, execution_context)
        yield result


class WorkerPool(ABC):
    """Abstract interface for dispatching worker execution.

    A WorkerPool controls how workers are scheduled and awaited. Implementations
    may provide thread-based, process-based, or distributed dispatch.
    """

    @abstractmethod
    async def dispatch(
        self,
        worker: Worker,
        node_id: str,
        node_config: dict,
        channel_snapshot: dict,
        execution_context: dict | None = None,
    ) -> WorkerResult:
        """Dispatch a worker execution and await the result.

        Args:
            worker: The worker instance to execute.
            node_id: The ID of the node being executed.
            node_config: The node's configuration dict.
            channel_snapshot: A read-only snapshot of all channel values.
            execution_context: Optional dict with execution metadata from PregelRuntime.

        Returns:
            The WorkerResult produced by the worker.
        """
        ...


class DirectWorkerPool(WorkerPool):
    """Direct async dispatch -- runs worker in the current event loop.

    This is the P1 default pool. It awaits each worker directly without
    any parallelism, which simplifies debugging and avoids race conditions
    in the initial implementation. For production workloads with I/O-bound
    nodes, a thread or process-based pool can be substituted.
    """

    async def dispatch(
        self,
        worker: Worker,
        node_id: str,
        node_config: dict,
        channel_snapshot: dict,
        execution_context: dict | None = None,
    ) -> WorkerResult:
        """Await the worker directly in the current event loop."""
        return await worker.execute(node_id, node_config, channel_snapshot, execution_context)
