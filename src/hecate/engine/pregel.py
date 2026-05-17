from __future__ import annotations

import uuid
from typing import Any, AsyncGenerator

from hecate.engine.channel import ChannelManager
from hecate.engine.checkpoint import CheckpointStore
from hecate.engine.types import (
    ChannelDef,
    Command,
    CompiledGraph,
    StreamMode,
    WorkerResult,
)
from hecate.engine.worker import Worker, ThreadPoolWorkerPool


class PregelRuntime:
    """BSP-based graph execution engine with checkpointing and interrupt support.

    Executes a compiled graph in superstep cycles: read channels, dispatch workers,
    collect results, write channels, save checkpoint, resolve next nodes.
    """

    def __init__(
        self,
        graph: CompiledGraph,
        worker: Worker,
        checkpoint_store: CheckpointStore,
        pool: ThreadPoolWorkerPool | None = None,
    ) -> None:
        self._graph = graph
        self._worker = worker
        self._checkpoint_store = checkpoint_store
        self._pool = pool or ThreadPoolWorkerPool()
        self._channel_manager = ChannelManager()
        self._superstep = 0
        self._interrupted = False
        self._interrupt_value: Any = None

        for name, defn in graph.channels.items():
            self._channel_manager.register(name, defn)

    async def execute(
        self,
        session_id: uuid.UUID,
        initial_input: dict | None = None,
        stream_mode: StreamMode = StreamMode.VALUES,
        resume_value: Any = None,
    ) -> AsyncGenerator[dict, None]:
        """Execute the graph and yield events based on the stream mode.

        Supports interrupt/resume: when a worker returns a Command(interrupt=...),
        execution pauses, checkpoint is saved, and an interrupt event is yielded.
        Call again with resume_value to continue from the checkpoint.
        """
        if initial_input:
            for key, value in initial_input.items():
                self._channel_manager.write(key, value)

        if resume_value is not None and self._interrupted:
            self._interrupted = False
            self._interrupt_value = None

        current_nodes = [self._graph.entry_point] if self._graph.entry_point else []

        while current_nodes and not self._interrupted:
            self._superstep += 1
            snapshot = self._channel_manager.snapshot()

            results: list[WorkerResult] = []
            for node_id in current_nodes:
                node = self._graph.nodes.get(node_id)
                if node is None:
                    continue
                result = await self._pool.dispatch(
                    self._worker,
                    node_id,
                    node.config,
                    snapshot,
                )
                results.append(result)

            for result in results:
                if result.error:
                    raise result.error
                if result.command:
                    if result.command.is_interrupt():
                        self._interrupted = True
                        self._interrupt_value = result.command.interrupt
                        for k, v in result.channel_updates.items():
                            self._channel_manager.write(k, v)
                        await self._checkpoint_store.save(
                            session_id=session_id,
                            superstep=self._superstep,
                            node_id=result.node_id,
                            channel_state=self._channel_manager.snapshot(),
                            metadata={"interrupted": True, "interrupt_value": self._interrupt_value},
                        )
                        yield {"type": "interrupt", "value": self._interrupt_value}
                        return
                    if result.command.update:
                        for k, v in result.command.update.items():
                            self._channel_manager.write(k, v)
                for k, v in result.channel_updates.items():
                    self._channel_manager.write(k, v)

            await self._checkpoint_store.save(
                session_id=session_id,
                superstep=self._superstep,
                node_id=current_nodes[0] if len(current_nodes) == 1 else None,
                channel_state=self._channel_manager.snapshot(),
            )

            if stream_mode == StreamMode.UPDATES:
                for result in results:
                    yield {"type": "update", "node": result.node_id, "output": result.channel_updates}
            elif stream_mode == StreamMode.VALUES:
                yield {"type": "values", "state": self._channel_manager.snapshot()}

            current_nodes = self._resolve_next_nodes(results)

    def _resolve_next_nodes(self, results: list[WorkerResult]) -> list[str]:
        """Determine the next set of nodes to execute based on edges and commands."""
        next_nodes: list[str] = []
        for result in results:
            if result.command and result.command.is_goto():
                next_nodes.append(result.command.goto)
                continue
            for edge in self._graph.edges:
                if edge.source == result.node_id:
                    if isinstance(edge.target, str):
                        next_nodes.append(edge.target)
                    elif isinstance(edge.target, dict):
                        route_key = str(result.channel_updates.get("_route", "true"))
                        target = edge.target.get(route_key, edge.target.get("false"))
                        if target:
                            next_nodes.append(target)
        if "__end__" in next_nodes:
            return []
        return list(dict.fromkeys(next_nodes))

    @property
    def is_interrupted(self) -> bool:
        """Return True if execution is paused at an interrupt point."""
        return self._interrupted

    @property
    def interrupt_value(self) -> Any:
        """Return the interrupt payload if execution is paused."""
        return self._interrupt_value
