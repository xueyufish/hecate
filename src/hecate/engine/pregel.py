"""Pregel/BSP execution engine for compiled graphs.

This module implements the core graph execution runtime based on the Bulk
Synchronous Parallel (BSP) model, inspired by Google's Pregel framework.
Execution proceeds in discrete **supersteps**:

1. **Snapshot** -- capture the current channel state.
2. **Dispatch** -- send the snapshot to all workers scheduled for this superstep.
3. **Collect** -- gather WorkerResults; apply channel writes; handle interrupts.
4. **Checkpoint** -- persist the updated state.
5. **Resolve** -- determine the next set of nodes from the edge graph.
6. **Yield** -- emit streaming events based on the configured StreamMode.

The loop terminates when there are no more nodes to execute, the graph reaches
the ``__end__`` sentinel, a worker raises an error, or a worker returns a
``Command(interrupt=...)`` to pause execution for human-in-the-loop workflows.

Interrupt/resume is checkpoint-based: on interrupt the full state is persisted.
On resume, the state is restored and execution continues from the node that
follows the interrupted node in the edge graph.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncGenerator
from typing import Any

from hecate.engine.channel import ChannelManager
from hecate.engine.checkpoint import CheckpointStore
from hecate.engine.eventbus import EventBus
from hecate.engine.eventstore import Event, EventStore, EventType
from hecate.engine.eviction import EvictionPolicy, NoEviction
from hecate.engine.scheduler import FIFOScheduler, SchedulerStrategy
from hecate.engine.temporal.conflict import ConflictResolver
from hecate.engine.types import (
    CompiledGraph,
    NodeType,
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

    Key fields:
        _interrupt_updates: Stores the channel_updates dict from the worker that
            triggered the interrupt. This is needed on resume to re-evaluate
            conditional edges (dict-valued targets) using the ``_route`` key that
            the interrupted worker may have written, ensuring correct routing to
            the next node after the interrupt point.
    """

    def __init__(
        self,
        graph: CompiledGraph,
        worker: Worker,
        checkpoint_store: CheckpointStore,
        pool: WorkerPool | None = None,
        max_supersteps: int = 100,
        conflict_resolver: ConflictResolver | None = None,
        scheduler: SchedulerStrategy | None = None,
        eviction_policy: EvictionPolicy | None = None,
        event_store: EventStore | None = None,
        event_bus: EventBus | None = None,
    ) -> None:
        self._graph = graph
        self._worker = worker
        self._checkpoint_store = checkpoint_store
        self._pool = pool or DirectWorkerPool()
        self._max_supersteps = max_supersteps
        self._conflict_resolver = conflict_resolver
        self._scheduler = scheduler or FIFOScheduler()
        self._channel_manager = ChannelManager(eviction_policy=eviction_policy or NoEviction())
        self._event_store = event_store
        self._event_bus = event_bus
        self._superstep = 0
        self._interrupted = False
        self._interrupt_value: Any = None
        self._interrupted_node: str | None = None
        self._interrupt_updates: dict = {}

        for name, defn in graph.channels.items():
            self._channel_manager.register(name, defn)

    async def _emit(
        self,
        session_id: uuid.UUID,
        event_type: EventType,
        node_id: str | None = None,
        payload: dict | None = None,
    ) -> None:
        """Record an event if event_store is configured."""
        if self._event_store:
            await self._event_store.append(
                Event(
                    session_id=session_id,
                    superstep=self._superstep,
                    event_type=event_type,
                    node_id=node_id,
                    payload=payload or {},
                )
            )

    def _execution_context(self, session_id: uuid.UUID) -> dict:
        """Build execution context dict for worker dispatch."""
        ctx: dict[str, Any] = {
            "session_id": session_id,
            "superstep": self._superstep,
            "event_store": self._event_store,
        }
        if self._event_bus is not None:
            ctx["event_bus"] = self._event_bus
        return ctx

    async def execute(
        self,
        session_id: uuid.UUID,
        initial_input: dict | None = None,
        stream_mode: StreamMode = StreamMode.VALUES,
        resume_value: Any = None,
    ) -> AsyncGenerator[dict, None]:
        """Execute the graph and yield events based on the stream mode.

        **Initialization phase:**
        - If ``resume_value`` is provided, the runtime restores state from the
          last checkpoint and resolves the next nodes after the interrupt point.
        - Otherwise, ``initial_input`` is written to channels and execution
          starts from the graph's entry point.

        **Superstep loop** (repeats until no more nodes, __end__ reached, or interrupt):

        1. Increment superstep counter; raise RuntimeError if ``max_supersteps`` is
           exceeded (guards against infinite loops in cyclic graphs).
        2. Snapshot all channels and dispatch each scheduled node to the worker pool.
        3. Process results: raise on error, apply channel writes, handle commands
           (interrupt causes an immediate checkpoint save and yield).
        4. Save a regular checkpoint for the completed superstep.
        5. Yield streaming events based on ``stream_mode``:
           - UPDATES: one event per worker with its channel_updates.
           - VALUES: one event with the full channel state snapshot.
        6. Resolve the next set of nodes from the edge graph.

        Args:
            session_id: Identifies the execution session for checkpoint scoping.
            initial_input: Optional dict of channel values to write before execution starts.
            stream_mode: Controls what events are yielded (UPDATES or VALUES).
            resume_value: If provided, restores from the last checkpoint and injects
                this value as the ``_resume_value`` channel, then continues execution.

        Yields:
            Dicts with ``"type"`` key: ``"interrupt"``, ``"update"``, or ``"values"``.
        """
        if resume_value is not None:
            await self._restore_from_checkpoint(session_id, resume_value)
            current_nodes = self._resolve_next_nodes_after_interrupt()
            await self._emit(session_id, EventType.RESUME, payload={"interrupted_node": self._interrupted_node})
        else:
            if initial_input:
                for key, value in initial_input.items():
                    self._channel_manager.write(key, value)
            current_nodes = [self._graph.entry_point] if self._graph.entry_point else []
            await self._emit(
                session_id,
                EventType.CUSTOM,
                payload={"event_name": "SESSION_START", "initial_input_keys": list(initial_input or {})},
            )

        while current_nodes and not self._interrupted:
            self._superstep += 1
            if self._superstep > self._max_supersteps:
                raise RuntimeError(
                    f"Graph execution exceeded max supersteps ({self._max_supersteps}). "
                    f"Possible infinite loop in graph '{self._graph.name}'."
                )
            snapshot = self._channel_manager.snapshot()
            context = {"superstep": self._superstep, "channel_snapshot": snapshot}
            scheduled_nodes = self._scheduler.select_next(current_nodes, context)

            results: list[WorkerResult] = []
            execution_context = self._execution_context(session_id)

            for node_id in scheduled_nodes:
                node = self._graph.nodes.get(node_id)
                if node is None:
                    continue

                node_type = getattr(node, "type", None)
                await self._emit(
                    session_id,
                    EventType.NODE_START,
                    node_id=node_id,
                    payload={"node_type": str(node_type) if node_type else None},
                )

                if node_type == NodeType.FAN_OUT:
                    fan_out_results = await self._dispatch_fan_out(
                        node_id, node, snapshot, execution_context=execution_context
                    )
                    results.extend(fan_out_results)
                    continue

                if node_type == NodeType.MERGE:
                    merge_result = self._execute_merge(node_id, node)
                    results.append(merge_result)
                    continue

                if stream_mode == StreamMode.MESSAGES:
                    async for item in self._worker.execute_stream(
                        node_id, node.config, snapshot, execution_context=execution_context
                    ):
                        if isinstance(item, WorkerResult):
                            results.append(item)
                        elif isinstance(item, dict):
                            yield {"type": "message", "content": item.get("content", "")}
                else:
                    result = await self._pool.dispatch(
                        self._worker,
                        node_id,
                        node.config,
                        snapshot,
                        execution_context=execution_context,
                    )
                    results.append(result)

            interrupted = False
            for result in results:
                await self._emit(
                    session_id,
                    EventType.NODE_END,
                    node_id=result.node_id,
                    payload={"success": result.error is None, "has_command": result.command is not None},
                )
                if result.error:
                    await self._emit(
                        session_id,
                        EventType.ERROR,
                        node_id=result.node_id,
                        payload={"error_type": type(result.error).__name__, "error_message": str(result.error)},
                    )
                    raise result.error
                if result.command:
                    if result.command.is_interrupt():
                        self._interrupted = True
                        self._interrupt_value = result.command.interrupt
                        self._interrupted_node = result.node_id
                        self._apply_writes(result.channel_updates)
                        await self._emit(
                            session_id,
                            EventType.INTERRUPT,
                            node_id=result.node_id,
                            payload={"interrupt_value_type": type(self._interrupt_value).__name__},
                        )
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
                        self._apply_writes(result.command.update)
                if not self._interrupted:
                    self._apply_writes(result.channel_updates)
                    if result.channel_updates:
                        await self._emit(
                            session_id,
                            EventType.CHANNEL_WRITE,
                            node_id=result.node_id,
                            payload={"channels": list(result.channel_updates.keys())},
                        )

            if interrupted:
                return

            await self._checkpoint_store.save(
                session_id=session_id,
                superstep=self._superstep,
                node_id=current_nodes[0] if len(current_nodes) == 1 else None,
                channel_state=self._channel_manager.snapshot(),
            )
            await self._emit(
                session_id,
                EventType.CUSTOM,
                payload={"event_name": "SUPERSTEP_END", "completed_nodes": len(results)},
            )

            if stream_mode == StreamMode.UPDATES:
                for result in results:
                    yield {"type": "update", "node": result.node_id, "output": result.channel_updates}
            elif stream_mode in (StreamMode.VALUES, StreamMode.MESSAGES):
                yield {"type": "values", "state": self._channel_manager.snapshot()}

            current_nodes = self._resolve_next_nodes(results)

    async def _restore_from_checkpoint(self, session_id: uuid.UUID, resume_value: Any) -> None:
        """Restore channel state and execution context from the last checkpoint.

        After restoring, clears the interrupted flag and injects ``resume_value``
        into the ``_resume_value`` channel so that the resumed worker can access
        the human-provided input.

        Args:
            session_id: The session whose latest checkpoint to load.
            resume_value: Value to write to the ``_resume_value`` channel after restore.
        """
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
        """Determine the next nodes to execute after restoring from an interrupt checkpoint.

        Looks up all edges whose source is the interrupted node. For conditional
        edges (dict-valued targets), uses the ``_route`` key from ``_interrupt_updates``
        to select the correct branch. Falls back to the entry point if no edges
        are found and one is defined.

        Returns:
            A deduplicated list of node IDs to execute next, or an empty list
            if the edge leads to ``__end__``.
        """
        if self._interrupted_node is None:
            return [self._graph.entry_point] if self._graph.entry_point else []
        next_nodes: list[str] = []
        for edge in self._graph.edges:
            if edge.source == self._interrupted_node:
                if isinstance(edge.target, str):
                    next_nodes.append(edge.target)
                elif isinstance(edge.target, dict):
                    route_key = str(self._interrupt_updates.get("_route", "true"))
                    target: str | None = edge.target.get(route_key)
                    if not target:
                        target = edge.target.get("default")
                    if not target:
                        target = edge.target.get("false")
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
        """Determine the next set of nodes to execute based on edges and commands.

        For each worker result, checks if a ``Command(goto=...)`` was returned
        (explicit routing). If not, looks up all edges whose source matches the
        completed node. For conditional edges, reads the ``_route`` key from the
        worker's channel_updates to select the correct branch.

        Returns:
            A deduplicated list of node IDs to execute next, or an empty list
            if any edge leads to ``__end__``.
        """
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
                        target: str | None = edge.target.get(route_key)
                        if not target:
                            target = edge.target.get("default")
                        if not target:
                            target = edge.target.get("false")
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

    def _apply_writes(self, updates: dict[str, Any]) -> None:
        """Write channel updates, applying conflict resolution if available.

        Args:
            updates: Channel key to value mapping.
        """
        if not self._conflict_resolver:
            for k, v in updates.items():
                self._channel_manager.write(k, v)
            return

        from hecate.engine.channel import get as get_channel_behavior

        for k, v in updates.items():
            current = self._channel_manager.snapshot().get(k)
            behavior = get_channel_behavior(self._channel_manager._channels[k].defn.type)
            result = self._conflict_resolver.resolve(
                channel_key=k,
                current_value=current,
                proposed_value=v,
                behavior=behavior,
            )
            if result.resolved:
                self._channel_manager.write(k, result.final_value)

    async def _dispatch_fan_out(
        self,
        node_id: str,
        node: Any,
        snapshot: dict,
        execution_context: dict | None = None,
    ) -> list[WorkerResult]:
        """Dispatch all branches of a FAN_OUT node concurrently.

        Creates an isolated sub-channel for each branch, dispatches all branch
        workers via asyncio.gather, and writes each branch result to its sub-channel.

        Args:
            node_id: The FAN_OUT node ID.
            node: The NodeConfig for the FAN_OUT node.
            snapshot: Current channel state snapshot.
            execution_context: Optional dict with execution metadata from PregelRuntime.

        Returns:
            List of WorkerResults from all branches.
        """
        from hecate.engine.types import ChannelDef, ChannelType

        branches: list[str] = node.config.get("branches", [])
        if not branches:
            return []

        for branch_id in branches:
            sub_channel = f"_fanout__{node_id}__{branch_id}"
            self._channel_manager.register(sub_channel, ChannelDef(type=ChannelType.LAST_VALUE))

        async def run_branch(branch_id: str) -> WorkerResult:
            branch_node = self._graph.nodes.get(branch_id)
            if branch_node is None:
                return WorkerResult(node_id=branch_id, error=RuntimeError(f"Branch node '{branch_id}' not found"))
            result = await self._pool.dispatch(
                self._worker,
                branch_id,
                branch_node.config,
                snapshot,
                execution_context=execution_context,
            )
            if result.error is None:
                sub_channel = f"_fanout__{node_id}__{branch_id}"
                self._channel_manager.write(sub_channel, result.channel_updates)
            return result

        branch_results = await asyncio.gather(*[run_branch(b) for b in branches])

        for r in branch_results:
            if r.error is not None:
                raise r.error

        return list(branch_results)

    def _execute_merge(self, node_id: str, node: Any) -> WorkerResult:
        """Aggregate results from all branches of a preceding FAN_OUT.

        Reads all branch sub-channels, combines them into a dict keyed by
        branch node ID, and writes the result to the configured output channel.

        Args:
            node_id: The MERGE node ID.
            node: The NodeConfig for the MERGE node.

        Returns:
            WorkerResult with the aggregated output.
        """
        fan_out_source: str = node.config.get("fan_out_source", "")
        output_channel: str = node.config.get("output_channel", "merged_output")

        source_node = self._graph.nodes.get(fan_out_source)
        if source_node is None:
            return WorkerResult(node_id=node_id, error=RuntimeError(f"FAN_OUT source '{fan_out_source}' not found"))

        branches: list[str] = source_node.config.get("branches", [])
        aggregated: dict[str, Any] = {}
        for branch_id in branches:
            sub_channel = f"_fanout__{fan_out_source}__{branch_id}"
            value = self._channel_manager.snapshot().get(sub_channel)
            aggregated[branch_id] = value

        return WorkerResult(
            node_id=node_id,
            channel_updates={output_channel: aggregated},
        )
