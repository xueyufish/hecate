"""Pregel/BSP execution runtime for compiled graphs.

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
import logging
import uuid
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any

# Side-effect imports (T2 turn-closure, T3 MONOTONIC.DENIAL invariant
# registration) are performed by importing the services.observability
# loginvariants modules from the harness boot path, not from the runtime
# module top level. The runtime does not depend on those invariants
# at import time; they are optional guards enabled by the harness.
from hecate.runtime.channel import ChannelManager
from hecate.runtime.checkpoint import CheckpointStore
from hecate.runtime.context import ContextEngine
from hecate.runtime.errors import MaxSuperstepsError
from hecate.runtime.eventbus import EventBus
from hecate.runtime.eventstore import Event, EventStore, EventType
from hecate.runtime.eviction import EvictionPolicy, NoEviction
from hecate.runtime.replay.continuation import Continuation
from hecate.runtime.retry import RetryExecutor, RetryStrategy
from hecate.runtime.scheduler import FIFOScheduler, SchedulerStrategy
from hecate.runtime.types import (
    CompiledGraph,
    NodeType,
    StreamMode,
    WorkerResult,
)
from hecate.runtime.worker import DirectWorkerPool, Worker, WorkerPool

if TYPE_CHECKING:
    from hecate.runtime.temporal.conflict import ConflictResolver

logger = logging.getLogger(__name__)


class PregelRuntime:
    """BSP-based graph execution runtime with checkpointing and interrupt support.

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
        context_engine: ContextEngine | None = None,
        retry_strategy: RetryStrategy | None = None,
        context_offloader: Any = None,
        environment: Any = None,
        evidence_tracker: Any = None,
    ) -> None:
        self._graph = graph
        self._worker = worker
        self._checkpoint_store = checkpoint_store
        self._pool = pool or DirectWorkerPool()
        self._max_supersteps = max_supersteps
        self._conflict_resolver = conflict_resolver
        self._scheduler = scheduler or FIFOScheduler()
        self._channel_manager = ChannelManager(
            eviction_policy=eviction_policy or NoEviction(),
            channel_access=graph.channel_access,
        )
        self._event_store = event_store
        self._event_bus = event_bus
        self._context_engine = context_engine
        self._context_offloader = context_offloader
        self._environment = environment
        self._evidence_tracker = evidence_tracker
        self._retry_executor = RetryExecutor(retry_strategy)
        self._superstep = 0
        self._interrupted = False
        self._interrupt_value: Any = None
        self._interrupted_node: str | None = None
        self._interrupt_updates: dict = {}
        # Descriptor of the interrupt being resumed, loaded from the event log
        # (checkpoint metadata fallback when no event store is wired). Payloads
        # without ``kind`` are legacy worker-authored interrupts.
        self._interrupt_descriptor: dict | None = None
        # One-shot set of nodes that resume executes without re-triggering
        # their own interrupt_before pause (the pause was already consumed).
        self._resume_skip_before: set[str] = set()

        for name, defn in graph.channels.items():
            self._channel_manager.register(name, defn)

    async def _emit(
        self,
        session_id: uuid.UUID,
        event_type: EventType,
        node_id: str | None = None,
        payload: dict | None = None,
        trace_id: str | None = None,
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
                    trace_id=trace_id,
                )
            )

    async def _current_log_version(self, session_id: uuid.UUID) -> int:
        if self._event_store is None:
            return 0
        try:
            return await self._event_store.get_version(session_id)
        except Exception:
            return 0

    def _declarative_descriptor(self, session_id: uuid.UUID, phase: str, nodes: list[str]) -> dict:
        """Build the structured descriptor carried by declarative INTERRUPT events.

        Field shape is the contract (design D11): flat keys, additively
        extendable, never renamed.
        """
        return {
            "kind": "declarative",
            "phase": phase,
            "nodes": list(nodes),
            "interrupt_id": f"{session_id}:{uuid.uuid4()}",
            "superstep": self._superstep,
            "remaining_steps": self._max_supersteps - self._superstep,
        }

    def _interrupt_checkpoint_metadata(self, descriptor: dict) -> dict:
        """Checkpoint metadata for a declarative pause (fast-path info only —
        resume derivation reads the descriptor from the event log). The caller
        merges in the current ``log_version``."""
        return {
            "interrupted": True,
            "interrupt_kind": "declarative",
            "interrupt_phase": descriptor["phase"],
            "interrupt_nodes": descriptor["nodes"],
            "interrupt_id": descriptor["interrupt_id"],
        }

    async def _emit_turn_end_interrupt(self, session_id: uuid.UUID, trace_id: str | None) -> None:
        """T0.5: close the TURN_START/TURN_END audit pair on an interrupt pause."""
        from hecate.runtime.eventstore import CURRENT_LOG_SCHEMA_VERSION

        await self._emit(
            session_id,
            EventType.TURN_END,
            payload={"log_schema_version": CURRENT_LOG_SCHEMA_VERSION, "reason": "interrupt"},
            trace_id=trace_id,
        )

    async def _append_write_batch(
        self,
        session_id: uuid.UUID,
        trace_id: str | None,
        pending_writes: list[tuple[str, Any, str | None]],
        *,
        commit_event: EventType,
        commit_node_id: str | None = None,
        commit_payload: dict | None = None,
    ) -> None:
        """Batch-append channel-write events followed by the step's commit event.

        WAL ordering: writes are logged BEFORE being applied to channels. The
        commit event (STEP_END for a regular superstep, INTERRUPT when the
        superstep ends in a pause) closes the batch; on append failure the
        entire superstep fails.
        """
        if self._event_store is None:
            return
        batch_events: list[Event] = []
        from hecate.runtime.eventstore import CURRENT_LOG_SCHEMA_VERSION
        from hecate.runtime.replay.logpolicy import should_log_channel

        for _node_id, channel_updates, _node_id_repeat in pending_writes:
            if not channel_updates:
                continue
            for ch_name, ch_value in channel_updates.items():
                if not should_log_channel(ch_name):
                    continue
                batch_events.append(
                    Event(
                        session_id=session_id,
                        superstep=self._superstep,
                        event_type=EventType.CHANNEL_WRITE,
                        node_id=_node_id,
                        payload={
                            "channel": ch_name,
                            "value": ch_value,
                            "log_schema_version": CURRENT_LOG_SCHEMA_VERSION,
                        },
                        trace_id=trace_id,
                    )
                )
        # STEP_END is only meaningful as a commit point for logged writes (kept
        # from the original semantics); an INTERRUPT commit point is always
        # emitted, even with no loggable writes — the resume gate reads it.
        if batch_events or commit_event == EventType.INTERRUPT:
            batch_events.append(
                Event(
                    session_id=session_id,
                    superstep=self._superstep,
                    event_type=commit_event,
                    node_id=commit_node_id,
                    payload=commit_payload or {},
                    trace_id=trace_id,
                )
            )
            await self._event_store.append_batch(batch_events)

    async def _append_eviction_events(self, session_id: uuid.UUID, trace_id: str | None) -> None:
        """Append any pending channel-eviction records to the event log."""
        eviction_records = self._channel_manager.consume_pending_evictions()
        if self._event_store is not None and eviction_records:
            from hecate.runtime.eventstore import CURRENT_LOG_SCHEMA_VERSION

            eviction_events = [
                Event(
                    session_id=session_id,
                    superstep=self._superstep,
                    event_type=EventType.EVICTION,
                    payload={
                        **rec,
                        "log_schema_version": CURRENT_LOG_SCHEMA_VERSION,
                    },
                    trace_id=trace_id,
                )
                for rec in eviction_records
            ]
            await self._event_store.append_batch(eviction_events)

    def _execution_context(self, session_id: uuid.UUID, trace_id: str | None = None) -> dict:
        """Build execution context dict for worker dispatch."""
        ctx: dict[str, Any] = {
            "session_id": session_id,
            "superstep": self._superstep,
            "remaining_steps": self._max_supersteps - self._superstep,
            "event_store": self._event_store,
            "trace_id": trace_id,
        }
        if self._event_bus is not None:
            ctx["event_bus"] = self._event_bus
        if self._context_engine is not None:
            ctx["context_engine"] = self._context_engine
        if self._context_offloader is not None:
            ctx["context_offloader"] = self._context_offloader
        if self._environment is not None:
            ctx["environment"] = self._environment
        if self._evidence_tracker is not None:
            ctx["evidence_tracker"] = self._evidence_tracker
        return ctx

    async def execute(
        self,
        session_id: uuid.UUID,
        initial_input: dict | None = None,
        stream_mode: StreamMode = StreamMode.VALUES,
        resume_value: Any = None,
        trace_id: str | None = None,
        execution_mode: str = "conversational",
        resume_from: int | None = None,
    ) -> AsyncGenerator[dict, None]:
        """Execute the graph and yield events based on the stream mode.

        **Initialization phase:**
        - If ``resume_value`` is provided, the runtime restores state from the
          last checkpoint and resolves the next nodes after the interrupt point.
        - Else if ``resume_from`` is provided, the runtime folds the event log
          up to that version and derives the continuation from the last commit
          point (tail-only: the version MUST equal the current log tail —
          historical points go through fork at the service layer).
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
            trace_id: Optional trace ID for observability span correlation.
            execution_mode: "conversational" or "task". Task mode disables checkpointing
                and overrides MESSAGES stream mode to VALUES.
            resume_from: Log version to fold up to before continuing (must equal
                the current log tail; requires a wired EventStore).

        Yields:
            Dicts with ``"type"`` key: ``"interrupt"``, ``"update"``, or ``"values"``.
        """
        # Task mode: override MESSAGES stream mode to VALUES
        if execution_mode == "task" and stream_mode == StreamMode.MESSAGES:
            stream_mode = StreamMode.VALUES

        # Establish execution identity (trace_id) for this invocation.
        # Priority: explicit argument > valid OTel span context > generated.
        # Guarantees events of different invokes never share a degenerate identity
        # even when OTel SDK is not configured.
        _otel_trace_mod = None
        _tracer = None
        try:
            from opentelemetry import trace as _otel_trace_mod

            _tracer = _otel_trace_mod.get_tracer("hecate.runtime")
        except Exception:
            logger.debug("OpenTelemetry not available, running without root span")

        effective_trace_id = trace_id
        if effective_trace_id is None and _otel_trace_mod is not None:
            try:
                span = _otel_trace_mod.get_current_span()
                span_ctx = span.get_span_context()
                if span_ctx and span_ctx.is_valid:
                    effective_trace_id = format(span_ctx.trace_id, "032x")
            except Exception as exc:
                logger.debug("Failed to derive trace_id from OTel span context: %s", exc)
        if effective_trace_id is None:
            effective_trace_id = uuid.uuid4().hex

        # Create root OTel trace span for session execution.
        # Child spans from Workers auto-nest via contextvars.
        if _tracer is not None:
            with _tracer.start_as_current_span(
                f"session:{session_id}",
                attributes={"session.id": str(session_id)},
            ) as _root_span:
                async for event in self._execute_inner(
                    session_id=session_id,
                    initial_input=initial_input,
                    stream_mode=stream_mode,
                    resume_value=resume_value,
                    trace_id=effective_trace_id,
                    execution_mode=execution_mode,
                    resume_from=resume_from,
                ):
                    yield event
                return

        # Fallback: no OTel available, run without root span
        async for event in self._execute_inner(
            session_id=session_id,
            initial_input=initial_input,
            stream_mode=stream_mode,
            resume_value=resume_value,
            trace_id=effective_trace_id,
            execution_mode=execution_mode,
            resume_from=resume_from,
        ):
            yield event

    async def _execute_inner(
        self,
        session_id: uuid.UUID,
        initial_input: dict | None = None,
        stream_mode: StreamMode = StreamMode.VALUES,
        resume_value: Any = None,
        trace_id: str | None = None,
        execution_mode: str = "conversational",
        resume_from: int | None = None,
    ) -> AsyncGenerator[dict, None]:
        """Inner execution logic, extracted from execute() for root span wrapping."""
        if resume_value is not None:
            await self._restore_from_checkpoint(session_id, resume_value)
            current_nodes = self._resolve_next_nodes_after_interrupt()
            await self._emit(
                session_id,
                EventType.RESUME,
                payload={"interrupted_node": self._interrupted_node},
                trace_id=trace_id,
            )
        elif resume_from is not None:
            continuation = await self._restore_at_version(session_id, resume_from)
            self._resume_skip_before = continuation.skip_before
            current_nodes = continuation.nodes
        else:
            if initial_input:
                # Log-as-truth (D11, 1.3.21②): initial_input writes MUST enter
                # the WAL, or fold(log) loses every user-supplied value and
                # the projection-equivalence check diverges from live state.
                # LogPolicy-filtered channels only — control channels stay
                # in-memory (re-injected per request). Committed by a
                # STEP_END so a crash mid-turn rewinds to a consistent anchor
                # (empty-executed STEP_END → derivation falls back to entry,
                # restarting the turn with the input already in state).
                from hecate.runtime.replay.logpolicy import should_log_channel

                loggable = {key: value for key, value in initial_input.items() if should_log_channel(key)}
                if loggable:
                    await self._append_write_batch(
                        session_id,
                        trace_id,
                        [("", loggable, None)],
                        commit_event=EventType.STEP_END,
                        commit_payload={"source": "initial_input"},
                    )
                for key, value in initial_input.items():
                    self._channel_manager.write(key, value)
            current_nodes = [self._graph.entry_point] if self._graph.entry_point else []
            await self._emit(
                session_id,
                EventType.CUSTOM,
                payload={"event_name": "SESSION_START", "initial_input_keys": list(initial_input or {})},
                trace_id=trace_id,
            )

        # T0.5 (guardrail-upgrade-trio): TURN_START marks the first event of a
        # user turn. The matching TURN_END fires at the natural exit point of
        # the while loop below (covers normal completion + interrupt break;
        # exception paths intentionally do not emit TURN_END so the audit pair
        # stays closed via invariant failure rather than synthetic close).
        from hecate.runtime.eventstore import CURRENT_LOG_SCHEMA_VERSION

        await self._emit(
            session_id,
            EventType.TURN_START,
            payload={"log_schema_version": CURRENT_LOG_SCHEMA_VERSION},
            trace_id=trace_id,
        )

        while current_nodes and not self._interrupted:
            self._superstep += 1
            if self._superstep > self._max_supersteps:
                raise MaxSuperstepsError(
                    f"Graph execution exceeded max supersteps ({self._max_supersteps}). "
                    f"Possible infinite loop in graph '{self._graph.name}'.",
                    superstep=self._superstep,
                )
            snapshot = self._channel_manager.snapshot()
            context = {"superstep": self._superstep, "channel_snapshot": snapshot}
            scheduled_nodes = self._scheduler.select_next(current_nodes, context)

            # Declarative interrupt_before: pause the whole superstep before any
            # node is dispatched. No writes occurred this step, so the INTERRUPT
            # event alone is the commit point. Nodes coming out of a before
            # pause run once without re-triggering their own pause (one-shot).
            before_hits = [
                n for n in scheduled_nodes if n in self._graph.interrupt_before and n not in self._resume_skip_before
            ]
            self._resume_skip_before = set()
            if before_hits:
                descriptor = self._declarative_descriptor(session_id, "before", scheduled_nodes)
                await self._emit(
                    session_id,
                    EventType.INTERRUPT,
                    node_id=before_hits[0],
                    payload=descriptor,
                    trace_id=trace_id,
                )
                if execution_mode == "conversational":
                    await self._checkpoint_store.save(
                        session_id=session_id,
                        superstep=self._superstep,
                        node_id=before_hits[0],
                        channel_state=self._channel_manager.snapshot(),
                        metadata={
                            **self._interrupt_checkpoint_metadata(descriptor),
                            "log_version": await self._current_log_version(session_id),
                        },
                    )
                self._interrupted = True
                self._interrupt_value = descriptor
                self._interrupted_node = before_hits[0]
                self._interrupt_updates = {}
                yield {"type": "interrupt", "value": descriptor}
                await self._emit_turn_end_interrupt(session_id, trace_id)
                return

            results: list[WorkerResult] = []
            execution_context = self._execution_context(session_id, trace_id=trace_id)
            # FAN_OUT dispatch runs inside the node's superstep, and branch
            # WorkerResults carry branch node IDs — track the fan-out node itself
            # so interrupt_after can target it.
            fan_out_dispatched: list[str] = []

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
                    trace_id=trace_id,
                )

                if node_type == NodeType.FAN_OUT:
                    fan_out_dispatched.append(node_id)
                    fan_out_results = await self._dispatch_fan_out(
                        node_id, node, snapshot, execution_context=execution_context
                    )
                    results.extend(fan_out_results)
                    continue

                if node_type == NodeType.MERGE:
                    merge_result = self._execute_merge(node_id, node)
                    results.append(merge_result)
                    continue

                retry_executor = self._retry_executor
                node_retry_cfg = node.config.get("retry")
                if node_retry_cfg:
                    per_node_strategy = self._retry_executor.strategy.with_config(**node_retry_cfg)
                    retry_executor = RetryExecutor(per_node_strategy)

                # Build per-node execution_context with handoff_targets for AGENT nodes
                node_execution_context = execution_context
                handoff_targets = self._build_handoff_targets(node_id, node_type)
                if handoff_targets:
                    node_execution_context = {**execution_context, "handoff_targets": handoff_targets}

                if stream_mode == StreamMode.MESSAGES:
                    async for item in retry_executor.execute_stream(
                        self._worker.execute_stream,
                        node_id,
                        node.config,
                        snapshot,
                        execution_context=node_execution_context,
                    ):
                        if isinstance(item, WorkerResult):
                            results.append(item)
                        elif isinstance(item, dict):
                            yield {"type": "message", "content": item.get("content", "")}
                else:
                    result = await retry_executor.execute(
                        self._pool.dispatch,
                        self._worker,
                        node_id,
                        node.config,
                        snapshot,
                        execution_context=node_execution_context,
                    )
                    results.append(result)

            pending_writes: list[tuple[str, Any, str | None]] = []
            worker_interrupt: WorkerResult | None = None
            for result in results:
                await self._emit(
                    session_id,
                    EventType.NODE_END,
                    node_id=result.node_id,
                    payload={"success": result.error is None, "has_command": result.command is not None},
                    trace_id=trace_id,
                )
                if result.error:
                    await self._emit(
                        session_id,
                        EventType.ERROR,
                        node_id=result.node_id,
                        payload={"error_type": type(result.error).__name__, "error_message": str(result.error)},
                        trace_id=trace_id,
                    )
                    raise result.error
                if result.command:
                    if result.command.is_interrupt() and worker_interrupt is None:
                        # Keep collecting: sibling nodes' writes in this superstep
                        # must still be committed before the pause (commit-point
                        # rule — the cache may never run ahead of the log). The
                        # interrupting result's own writes join pending_writes
                        # via the fall-through below.
                        worker_interrupt = result
                    elif result.command.update:
                        self._apply_writes(result.command.update, node_id=result.node_id)
                pending_writes.append((result.node_id, result.channel_updates, result.node_id))

            executed_nodes = [r.node_id for r in results] + fan_out_dispatched
            after_hits = [n for n in executed_nodes if n in self._graph.interrupt_after]

            if worker_interrupt is not None:
                # Worker-authored interrupt: commit the full superstep's writes
                # with the INTERRUPT event as commit point, then checkpoint,
                # yield, and close the turn.
                descriptor = {
                    "kind": "worker",
                    "nodes": [worker_interrupt.node_id],
                    "interrupt_id": f"{session_id}:{uuid.uuid4()}",
                    "superstep": self._superstep,
                    "remaining_steps": self._max_supersteps - self._superstep,
                    "interrupt_value_type": type(worker_interrupt.command.interrupt).__name__,
                }
                await self._append_write_batch(
                    session_id,
                    trace_id,
                    pending_writes,
                    commit_event=EventType.INTERRUPT,
                    commit_node_id=worker_interrupt.node_id,
                    commit_payload=descriptor,
                )
                for _node_id, channel_updates, _node_id_repeat in pending_writes:
                    self._apply_writes(channel_updates, node_id=_node_id)
                await self._append_eviction_events(session_id, trace_id)
                self._interrupted = True
                self._interrupt_value = worker_interrupt.command.interrupt
                self._interrupted_node = worker_interrupt.node_id
                self._interrupt_updates = worker_interrupt.channel_updates
                if execution_mode == "conversational":
                    await self._checkpoint_store.save(
                        session_id=session_id,
                        superstep=self._superstep,
                        node_id=worker_interrupt.node_id,
                        channel_state=self._channel_manager.snapshot(),
                        metadata={
                            "interrupted": True,
                            "interrupt_value": self._interrupt_value,
                            "interrupt_updates": self._interrupt_updates,
                            "log_version": await self._current_log_version(session_id),
                        },
                    )
                yield {"type": "interrupt", "value": self._interrupt_value}
                await self._emit_turn_end_interrupt(session_id, trace_id)
                return

            # WAL ordering: batch-append channel-write events (with adjudicated values
            # + log_schema_version marker) BEFORE applying them to channels. The
            # commit event (STEP_END, or INTERRUPT for a declarative pause) closes
            # the batch as a commit point.
            await self._append_write_batch(
                session_id,
                trace_id,
                pending_writes,
                commit_event=EventType.STEP_END,
            )
            for _node_id, channel_updates, _node_id_repeat in pending_writes:
                self._apply_writes(channel_updates, node_id=_node_id)

            await self._append_eviction_events(session_id, trace_id)

            if after_hits:
                # Declarative interrupt_after: all writes are committed; pause
                # here instead of the regular superstep close-out. Descriptor
                # nodes mirror regular edge resolution (result-producing nodes;
                # the structural FAN_OUT node's out-edges would re-dispatch
                # branches on resume).
                descriptor = self._declarative_descriptor(session_id, "after", [r.node_id for r in results])
                await self._emit(
                    session_id,
                    EventType.INTERRUPT,
                    node_id=after_hits[0],
                    payload=descriptor,
                    trace_id=trace_id,
                )
                if execution_mode == "conversational":
                    await self._checkpoint_store.save(
                        session_id=session_id,
                        superstep=self._superstep,
                        node_id=after_hits[0],
                        channel_state=self._channel_manager.snapshot(),
                        metadata={
                            **self._interrupt_checkpoint_metadata(descriptor),
                            "log_version": await self._current_log_version(session_id),
                        },
                    )
                self._interrupted = True
                self._interrupt_value = descriptor
                self._interrupted_node = after_hits[0]
                self._interrupt_updates = {}
                yield {"type": "interrupt", "value": descriptor}
                await self._emit_turn_end_interrupt(session_id, trace_id)
                return

            if execution_mode == "conversational":
                await self._checkpoint_store.save(
                    session_id=session_id,
                    superstep=self._superstep,
                    node_id=current_nodes[0] if len(current_nodes) == 1 else None,
                    channel_state=self._channel_manager.snapshot(),
                    metadata={
                        "log_version": await self._current_log_version(session_id),
                    },
                )
            await self._emit(
                session_id,
                EventType.CUSTOM,
                payload={"event_name": "SUPERSTEP_END", "completed_nodes": len(results)},
                trace_id=trace_id,
            )

            if stream_mode == StreamMode.UPDATES:
                for result in results:
                    yield {"type": "update", "node": result.node_id, "output": result.channel_updates}
            elif stream_mode in (StreamMode.VALUES, StreamMode.MESSAGES):
                yield {"type": "values", "state": self._channel_manager.snapshot()}

            current_nodes = self._resolve_next_nodes(results)

        # T0.5: TURN_END at natural loop exit (covered by while condition).
        # Interrupt path emits its own TURN_END before returning; this catch-all
        # fires only when while exits via ``not current_nodes`` (graph completed).
        if not self._interrupted:
            await self._emit(
                session_id,
                EventType.TURN_END,
                payload={"log_schema_version": CURRENT_LOG_SCHEMA_VERSION, "reason": "graph_complete"},
                trace_id=trace_id,
            )

    async def _restore_from_checkpoint(self, session_id: uuid.UUID, resume_value: Any) -> None:
        """Restore channel state via cache + event-log tail replay.

        Recovery precedence:
          1. If the checkpoint cache exists, hydrate channels from it (warm path).
          2. If the event store is wired, replay events from after the cache's
             snapshot point (log-derived tail). The log is authoritative; the
             cache is a discardable optimization.
          3. If neither path yields a state, return without modifying the runtime
             (caller should treat this as a cold start).

        After restore, clears the interrupted flag, sets ``superstep`` from the
        cache (or last replayed event), and injects ``resume_value`` into the
        ``_resume_value`` channel.
        """
        from hecate.runtime.replay.logfold import NonReplayablePrefix, fold_session

        checkpoint = await self._checkpoint_store.load(session_id)
        cache_log_version = 0
        if checkpoint is not None:
            self._channel_manager.restore(checkpoint["channel_state"])
            self._superstep = int(checkpoint.get("superstep", 0))
            cache_log_version = int(checkpoint.get("metadata", {}).get("log_version", 0))
            self._interrupted_node = checkpoint.get("node_id")
            self._interrupt_updates = checkpoint.get("metadata", {}).get("interrupt_updates", {})
        else:
            self._superstep = 0

        if self._event_store is not None:
            try:
                tail_events = await self._event_store.get_events(session_id, from_version=cache_log_version + 1)
            except Exception:
                tail_events = []
            try:
                if tail_events:
                    last_version = fold_session(self._channel_manager, iter(tail_events))
                    if last_version > self._superstep:
                        self._superstep = last_version
            except NonReplayablePrefix:
                pass

        # Interrupt descriptor is log-derived (log-as-truth): the last INTERRUPT
        # event's payload decides how resume resolves the next node set. Checkpoint
        # metadata is only a fallback when no event store is wired.
        self._interrupt_descriptor = await self._load_interrupt_descriptor(session_id, checkpoint)
        if (
            self._interrupt_descriptor
            and self._interrupt_descriptor.get("kind") == "declarative"
            and self._interrupt_descriptor.get("phase") == "before"
        ):
            self._resume_skip_before = {n for n in self._interrupt_descriptor.get("nodes", []) if n}
        else:
            self._resume_skip_before = set()

        self._interrupted = False
        self._interrupt_value = None
        if resume_value is not None:
            self._channel_manager.write("_resume_value", resume_value)

        await self._assert_projection_equivalent(session_id)

    def _fresh_projection(self) -> ChannelManager:
        """Build an empty projection manager mirroring the runtime's channels.

        NoEviction is intentional: evictions replay via their own EVICTION
        events (same reasoning as ``_assert_projection_equivalent``) — an
        eviction policy here would evict inline AND on replay.
        """
        projection = ChannelManager()
        for name, channel in self._channel_manager._channels.items():
            projection.register(name, channel.defn)
        return projection

    async def _restore_at_version(self, session_id: uuid.UUID, version: int) -> Continuation:
        """Fold the log up to ``version`` and derive the continuation (tail-only).

        Guard (1.3.21② spec): ``version`` MUST equal the current log tail —
        this is the crash-recovery / fork-bootstrap path. Resuming a
        historical point inside the same session would weave two timelines
        into one linear log; the service layer must fork instead.
        """
        from hecate.runtime.replay.continuation import derive_continuation
        from hecate.runtime.replay.logfold import fold_session

        if self._event_store is None:
            raise ValueError("resume_from requires a wired EventStore")
        tail = await self._current_log_version(session_id)
        if version != tail:
            raise ValueError(
                f"resume_from={version} is not the session log tail ({tail}); "
                "historical resume must create a forked child session instead"
            )

        events = await self._event_store.get_events(session_id)
        sliced = [e for e in events if e.version <= version]
        projection = self._fresh_projection()
        fold_session(projection, iter(sliced))
        snapshot = projection.snapshot()
        self._channel_manager.restore(snapshot)

        self._superstep = sliced[-1].superstep if sliced else 0
        self._interrupted = False
        self._interrupt_value = None
        self._interrupt_descriptor = None
        self._interrupted_node = None

        continuation = derive_continuation(sliced, self._graph, snapshot)
        await self._assert_projection_equivalent(session_id)
        return continuation

    async def snapshot_at_version(self, session_id: uuid.UUID, version: int | None = None) -> dict:
        """Fold the session log up to ``version`` into a fresh projection.

        Read-only helper for the fork service: returns the log-policy-filtered
        channel state (exactly the channels a FORK payload may carry), the
        superstep counter, and the effective log version. ``version=None``
        folds to the current tail.
        """
        from hecate.runtime.replay.logfold import fold_session
        from hecate.runtime.replay.logpolicy import should_log_channel

        if self._event_store is None:
            raise ValueError("snapshot_at_version requires a wired EventStore")
        events = await self._event_store.get_events(session_id)
        target = version if version is not None else (events[-1].version if events else 0)
        sliced = [e for e in events if e.version <= target]
        projection = self._fresh_projection()
        fold_session(projection, iter(sliced))
        state = {k: v for k, v in projection.snapshot().items() if should_log_channel(k)}
        return {
            "channel_state": state,
            "superstep": sliced[-1].superstep if sliced else 0,
            "log_version": target,
        }

    async def _load_interrupt_descriptor(self, session_id: uuid.UUID, checkpoint: dict | None) -> dict | None:
        """Load the descriptor of the interrupt being resumed.

        Reads the last ``INTERRUPT`` event payload from the event log when one
        is wired; falls back to the declarative-pause fields in checkpoint
        metadata otherwise. Legacy worker events without ``kind`` return their
        payload as-is, which the resume resolution treats as worker-authored.
        """
        if self._event_store is not None:
            try:
                events = await self._event_store.get_events(session_id)
            except Exception:
                events = []
            for event in reversed(events):
                if event.event_type == EventType.INTERRUPT:
                    payload = dict(event.payload or {})
                    if not payload.get("nodes"):
                        payload["nodes"] = [event.node_id] if event.node_id else []
                    return payload
            return None
        meta = (checkpoint or {}).get("metadata", {})
        if meta.get("interrupt_kind") == "declarative":
            return {
                "kind": "declarative",
                "phase": meta.get("interrupt_phase"),
                "nodes": meta.get("interrupt_nodes", []),
            }
        return None

    async def _assert_projection_equivalent(self, session_id: uuid.UUID) -> None:
        """Mechanism 3: runtime invariant — projection(log) ≢ snapshot must fail-stop.

        Compares the channels we just restored (cache + tail fold) against a
        fold of the entire event log. Divergence is treated as a bug signal;
        recovery is "discard in-memory state and re-fold", not "hot-fix".

        Also runs the registered log-invariant checks against the same event
        stream — guardrail-upgrade-trio T0.4 wires the invariant registry into
        the restore path so violations (e.g. TOOL.PAIRING, MONOTONIC.DENIAL)
        fail-stop rather than being silently logged.
        """
        if self._event_store is None:
            return
        from hecate.runtime.replay.logfold import NonReplayablePrefixError, fold_session
        from hecate.runtime.replay.loginvariants import InvariantViolationError, run_all
        from hecate.runtime.replay.logpolicy import should_log_channel

        try:
            all_events = await self._event_store.get_events(session_id)
        except Exception:
            logger.warning("projection_equivalent_get_events_failed", exc_info=True)
            return
        try:
            projection = ChannelManager()
            for name, channel in self._channel_manager._channels.items():
                projection.register(name, channel.defn)
            fold_session(projection, iter(all_events))
        except NonReplayablePrefixError:
            return
        except Exception:
            logger.warning("projection_equivalent_fold_failed", exc_info=True)
            return
        for name in self._channel_manager._channels:
            if not should_log_channel(name):
                continue
            try:
                live = self._channel_manager.read(name)
            except Exception:
                logger.warning("projection_equivalent_live_read_failed", exc_info=True, extra={"channel": name})
                continue
            try:
                replay = projection.read(name)
            except Exception:
                logger.warning("projection_equivalent_replay_read_failed", exc_info=True, extra={"channel": name})
                continue
            if live != replay:
                raise RuntimeError(
                    f"[PROJECTION.EQUIVALENT] channel '{name}' diverged between "
                    f"cache and log fold; failing closed per log-as-truth invariant"
                )
        # Invariant registry: structural checks against the same event stream.
        # Violations MUST fail-stop (log-as-truth: state has diverged from log).
        try:
            run_all(all_events)
        except InvariantViolationError as exc:
            raise RuntimeError(f"[{exc.code}] invariant violated during restore: {exc.message}") from exc

    def _build_handoff_targets(self, node_id: str, node_type: NodeType | None) -> list[dict[str, str]]:
        """Build handoff target list for an AGENT node.

        Scans outgoing edges of ``node_id`` for ``handoff`` or ``dynamic_handoff``
        triggers. For each such edge, extracts target node IDs and resolves
        descriptions from the target node's config (``description`` field) or
        falls back to the target node's name (the dict key in ``nodes``).

        Only returns a non-empty list for AGENT-type nodes with handoff edges.
        For all other node types, returns an empty list (no injection).

        Args:
            node_id: The source node to inspect.
            node_type: The NodeType of the source node (if known).

        Returns:
            A list of ``{"node_id": str, "description": str}`` dicts, one per
            reachable target. Empty list if the node has no handoff edges or is
            not an AGENT node.
        """
        if node_type != NodeType.AGENT:
            return []

        targets: list[dict[str, str]] = []
        seen: set[str] = set()
        for edge in self._graph.edges:
            if edge.source != node_id or edge.trigger not in ("handoff", "dynamic_handoff"):
                continue
            if isinstance(edge.target, str):
                target_ids = [edge.target]
            elif isinstance(edge.target, dict):
                target_ids = list(edge.target.values())
            else:
                continue

            for target_id in target_ids:
                if target_id in seen:
                    continue
                seen.add(target_id)
                target_node = self._graph.nodes.get(target_id)
                desc = ""
                if target_node:
                    desc = target_node.config.get("description", target_node.config.get("name", ""))
                if not desc:
                    desc = target_id
                targets.append({"node_id": target_id, "description": desc})

        return targets

    def _resolve_conditional_target(self, target_map: dict, route_value: str) -> str | None:
        """Resolve a conditional edge target using the route value as the dict key.

        Looks up the ``_route`` value directly in the target map.  This is
        fully generic -- any key is valid, not just ``"true"``/``"false"``.
        Falls back to ``"default"`` if the route key is not present.

        Args:
            target_map: Dict mapping route keys to node IDs.
            route_value: The value read from the ``_route`` channel.

        Returns:
            The matched node ID, or None if no branch matches.
        """
        target: str | None = target_map.get(route_value)
        if target is None:
            target = target_map.get("default")
        return target

    def _resolve_next_nodes_after_interrupt(self) -> list[str]:
        """Determine the next nodes to execute after restoring from an interrupt checkpoint.

        Declarative interrupts (descriptor from the last INTERRUPT event): a
        ``before`` pause never ran its superstep, so the paused nodes execute
        themselves; an ``after`` pause continues from the union of out-edges of
        all nodes executed in the interrupting superstep (conditional edges
        resolve against the restored ``_route`` channel — writes were committed
        before the pause).

        Worker-authored interrupts (no ``kind`` in the payload): unchanged —
        follow the interrupted node's out-edges, using the ``_route`` key from
        ``_interrupt_updates`` for conditional edges. Falls back to the entry
        point if no edges are found and one is defined.

        Returns:
            A deduplicated list of node IDs to execute next, or an empty list
            if the edge leads to ``__end__``.
        """
        descriptor = self._interrupt_descriptor
        if descriptor and descriptor.get("kind") == "declarative":
            nodes = [n for n in descriptor.get("nodes", []) if n]
            if descriptor.get("phase") == "before":
                return list(dict.fromkeys(nodes))
            resolved: list[str] = []
            snapshot = self._channel_manager.snapshot()
            for source in nodes:
                for edge in self._graph.edges:
                    if edge.source != source:
                        continue
                    if isinstance(edge.target, str):
                        resolved.append(edge.target)
                    elif isinstance(edge.target, dict):
                        route_key = str(snapshot.get("_route", "true"))
                        target = self._resolve_conditional_target(edge.target, route_key)
                        if target:
                            resolved.append(target)
            if "__end__" in resolved:
                return []
            if resolved:
                return list(dict.fromkeys(resolved))
            if self._graph.entry_point:
                return [self._graph.entry_point]
            return []

        if self._interrupted_node is None:
            return [self._graph.entry_point] if self._graph.entry_point else []
        next_nodes: list[str] = []
        for edge in self._graph.edges:
            if edge.source == self._interrupted_node:
                if isinstance(edge.target, str):
                    next_nodes.append(edge.target)
                elif isinstance(edge.target, dict):
                    route_key = str(self._interrupt_updates.get("_route", "true"))
                    target = self._resolve_conditional_target(edge.target, route_key)
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
                        target = self._resolve_conditional_target(edge.target, route_key)
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

    def _apply_writes(self, updates: dict[str, Any], node_id: str | None = None) -> None:
        """Write channel updates, applying conflict resolution if available.

        Args:
            updates: Channel key to value mapping.
            node_id: Optional node ID for channel access validation.
        """
        if not self._conflict_resolver:
            for k, v in updates.items():
                self._channel_manager.write(k, v, node_id=node_id)
            return

        from hecate.runtime.channel import get as get_channel_behavior

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
                self._channel_manager.write(k, result.final_value, node_id=node_id)

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
        from hecate.runtime.types import ChannelDef, ChannelType

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
