from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from typing import Any

from hecate.engine.channel import ChannelManager
from hecate.engine.checkpoint import CheckpointStore
from hecate.engine.types import (
    CompiledGraph,
    StreamMode,
    WorkerResult,
)
from hecate.engine.worker import DirectWorkerPool, Worker, WorkerPool


class PregelRuntime:
    """BSP-based graph execution engine with checkpointing and interrupt support.

    Executes a compiled graph in superstep cycles: read channels, dispatch workers,
    collect results, write channels, save checkpoint, resolve next nodes.

    Supports interrupt/resume via CheckpointStore: when a worker returns
    Command(interrupt=...), execution pauses and checkpoint is saved. Calling
    execute() again with resume_value restores state from the last checkpoint
    and continues from the node following the interrupt.
    """

    def __init__(
        self,
        graph: CompiledGraph,
        worker: Worker,
        checkpoint_store: CheckpointStore,
        pool: WorkerPool | None = None,
        max_supersteps: int = 100,
    ) -> None:
        self._graph = graph
        self._worker = worker
        self._checkpoint_store = checkpoint_store
        self._pool = pool or DirectWorkerPool()
        self._max_supersteps = max_supersteps
        self._channel_manager = ChannelManager()
        self._superstep = 0
        self._interrupted = False
        self._interrupt_value: Any = None
        self._interrupted_node: str | None = None
        self._interrupt_updates: dict = {}

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

        If resume_value is provided, restores state from the last checkpoint
        and continues execution from the node after the interrupt point.
        """
        if resume_value is not None:
            await self._restore_from_checkpoint(session_id, resume_value)
            current_nodes = self._resolve_next_nodes_after_interrupt()
        else:
            if initial_input:
                for key, value in initial_input.items():
                    self._channel_manager.write(key, value)
            current_nodes = [self._graph.entry_point] if self._graph.entry_point else []

        while current_nodes and not self._interrupted:
            self._superstep += 1
            if self._superstep > self._max_supersteps:
                raise RuntimeError(
                    f"Graph execution exceeded max supersteps ({self._max_supersteps}). "
                    f"Possible infinite loop in graph '{self._graph.name}'."
                )
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

            interrupted = False
            for result in results:
                if result.error:
                    raise result.error
                if result.command:
                    if result.command.is_interrupt():
                        self._interrupted = True
                        self._interrupt_value = result.command.interrupt
                        self._interrupted_node = result.node_id
                        for k, v in result.channel_updates.items():
                            self._channel_manager.write(k, v)
                        await self._checkpoint_store.save(
                            session_id=session_id,
                            superstep=self._superstep,
                            node_id=result.node_id,
                            channel_state=self._channel_manager.snapshot(),
                            metadata={
                                "interrupted": True,
                                "interrupt_value": self._interrupt_value,
                                "interrupt_updates": result.channel_updates,
                            },
                        )
                        yield {"type": "interrupt", "value": self._interrupt_value}
                        interrupted = True
                        break
                    if result.command.update:
                        for k, v in result.command.update.items():
                            self._channel_manager.write(k, v)
                if not self._interrupted:
                    for k, v in result.channel_updates.items():
                        self._channel_manager.write(k, v)

            if interrupted:
                return

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

    async def _restore_from_checkpoint(self, session_id: uuid.UUID, resume_value: Any) -> None:
        """Restore channel state and execution context from the last checkpoint."""
        checkpoint = await self._checkpoint_store.load(session_id)
        if checkpoint is None:
            return
        self._channel_manager.restore(checkpoint["channel_state"])
        self._superstep = checkpoint["superstep"]
        self._interrupted_node = checkpoint.get("node_id")
        self._interrupted = False
        self._interrupt_value = None
        self._interrupt_updates = checkpoint.get("metadata", {}).get("interrupt_updates", {})
        if resume_value is not None:
            self._channel_manager.write("_resume_value", resume_value)

    def _resolve_next_nodes_after_interrupt(self) -> list[str]:
        """Determine the next nodes to execute after restoring from an interrupt checkpoint."""
        if self._interrupted_node is None:
            return [self._graph.entry_point] if self._graph.entry_point else []
        next_nodes: list[str] = []
        for edge in self._graph.edges:
            if edge.source == self._interrupted_node:
                if isinstance(edge.target, str):
                    next_nodes.append(edge.target)
                elif isinstance(edge.target, dict):
                    route_key = str(self._interrupt_updates.get("_route", "true"))
                    target = edge.target.get(route_key, edge.target.get("false"))
                    if target:
                        next_nodes.append(target)
        if "__end__" in next_nodes:
            return []
        if next_nodes:
            return list(dict.fromkeys(next_nodes))
        if self._graph.entry_point:
            return [self._graph.entry_point]
        return []

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
