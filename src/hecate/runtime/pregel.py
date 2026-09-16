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
from hecate.runtime.node_cache import (
    CachePolicy,
    InMemoryNodeCache,
    derive_cache_key,
    short_key_hash,
)
from hecate.runtime.replay.continuation import Continuation
from hecate.runtime.retry import RetryExecutor, RetryStrategy
from hecate.runtime.scheduler import FIFOScheduler, SchedulerStrategy
from hecate.runtime.types import (
    ABSOLUTE_MAX_FANOUT,
    DEFAULT_MAX_FANOUT_PER_DISPATCH,
    DEFAULT_MAX_INVOCATIONS_PER_SUPERSTEP,
    ChannelDef,
    ChannelType,
    CompiledGraph,
    DispatchPacket,
    Invocation,
    InvocationIdentity,
    NodeType,
    StreamMode,
    WorkerResult,
)
from hecate.runtime.worker import DirectWorkerPool, Worker, WorkerPool


class FanoutLimitError(Exception):
    """Raised when a planner would exceed an engine-level fan-out ceiling.

    Failure mode is fail-closed: the engine emits the offending packet count
    and the violated ceiling so the planner can self-correct. Existing graphs
    that stay under the ceiling are unaffected.
    """

    def __init__(self, scope: str, requested: int, ceiling: int) -> None:
        self.scope = scope
        self.requested = requested
        self.ceiling = ceiling
        super().__init__(f"Fan-out limit exceeded at {scope}: requested {requested}, ceiling {ceiling}")


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
        context_chain: Any = None,
        retry_strategy: RetryStrategy | None = None,
        context_offloader: Any = None,
        environment: Any = None,
        evidence_tracker: Any = None,
        node_cache: InMemoryNodeCache | None = None,
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
        # 4.13 context processor chain (ContextChainFactory or chain).
        self._context_chain = context_chain
        self._context_offloader = context_offloader
        self._environment = environment
        self._evidence_tracker = evidence_tracker
        self._retry_executor = RetryExecutor(retry_strategy)
        # 1.3.21IV node cache: default is a per-runtime instance (session-
        # scope hits inside one execution loop still work); pass a shared
        # instance to serve tenant-scoped entries across sessions.
        self._node_cache = node_cache or InMemoryNodeCache()
        # Tenant identity for tenant-scoped cache keys (set per execute()).
        self._tenant_id: str | None = None
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
        # Per-superstep: maps each dynamic Invocation to its planner's
        # packet.state — used by the dynamic dispatcher to seed sub-channels.
        # Initialized inside the superstep loop.
        self._pending_packet_state: dict[Invocation, dict[str, Any]] = {}
        self._pending_invocations_by_target: dict[str, list[Invocation]] = {}
        # Per-superstep: session_id + trace_id stashed for branch-level
        # NODE_START/NODE_END emission inside the dynamic dispatcher.
        self._session_id_for_event: Any = None
        self._current_trace_id: str | None = None

        for name, defn in graph.channels.items():
            self._channel_manager.register(name, defn)

        # 1.3.21③: register the engine-owned control channels. They are
        # not in ``graph.channels`` (the planner is just a CONDITION node
        # that happens to write ``_dispatch``) but they must exist in the
        # channel manager so writes actually land on them — otherwise the
        # logpolicy exemption is moot (no channel == nothing to log).
        self._channel_manager.register("_dispatch", ChannelDef(type=ChannelType.LAST_VALUE, default=None))

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
        if self._context_chain is not None:
            ctx["context_chain"] = self._context_chain
        if self._context_offloader is not None:
            ctx["context_offloader"] = self._context_offloader
        if self._environment is not None:
            ctx["environment"] = self._environment
        if self._evidence_tracker is not None:
            ctx["evidence_tracker"] = self._evidence_tracker
        return ctx

    def _cache_policy_for(self, node_id: str) -> CachePolicy | None:
        """Return the parsed cache policy for a node, or None (zero queries)."""
        node = self._graph.nodes.get(node_id)
        if node is None:
            return None
        raw = node.config.get("cache")
        if not raw:
            return None
        return CachePolicy.from_config(raw)

    def _consult_node_cache(
        self,
        policy: CachePolicy,
        node_id: str,
        node_config: dict,
        snapshot: dict,
        session_id: uuid.UUID,
        branch_slice: dict | None = None,
    ) -> tuple[WorkerResult | None, str, str]:
        """Look up a node result in the cache.

        Returns ``(hit_or_none, full_key, key_hash)`` — the fabricated
        ``WorkerResult`` on a hit (worker will be skipped), the full key for
        the miss-path store, and the short hash for NODE_END markers.
        """
        readable: list[str] | None = None
        access = self._graph.channel_access.get(node_id)
        if access is not None and access.readable:
            readable = sorted(access.readable)
        key = derive_cache_key(
            policy,
            node_id,
            node_config,
            snapshot,
            session_id=session_id,
            tenant_id=self._tenant_id,
            readable=readable,
            branch_slice=branch_slice,
        )
        key_hash = short_key_hash(key)
        cached = self._node_cache.get(key, ttl=policy.ttl)
        if cached is None:
            return None, key, key_hash
        logger.debug("node cache hit for '%s' (key=%s)", node_id, key_hash)
        return (
            WorkerResult(node_id=node_id, channel_updates=cached, cache_hit=True, cache_key=key_hash),
            key,
            key_hash,
        )

    def _record_node_cache_miss(self, policy: CachePolicy, key: str, key_hash: str, result: WorkerResult) -> None:
        """Decorate a miss result and store it (successful, command-free only).

        Results carrying ``command`` (interrupt/goto) or ``error`` are never
        cached — control flow and failures must re-execute.
        """
        result.cache_hit = False
        result.cache_key = key_hash
        if result.error is None and result.command is None:
            self._node_cache.set(key, result.channel_updates)

    async def execute(
        self,
        session_id: uuid.UUID,
        initial_input: dict | None = None,
        stream_mode: StreamMode = StreamMode.VALUES,
        resume_value: Any = None,
        trace_id: str | None = None,
        execution_mode: str = "conversational",
        resume_from: int | None = None,
        tenant_id: str | None = None,
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
            tenant_id: Tenant identity for tenant-scoped node cache keys
                (1.3.21IV). Required when any node declares ``cache.scope="tenant"``;
                omitted tenant context fails those nodes closed at dispatch.

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
                    tenant_id=tenant_id,
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
            tenant_id=tenant_id,
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
        tenant_id: str | None = None,
    ) -> AsyncGenerator[dict, None]:
        """Inner execution logic, extracted from execute() for root span wrapping."""
        self._tenant_id = tenant_id
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
            # Planners that triggered dynamic fan-out this superstep — used
            # to enrich the STEP_END commit payload (T2b fanout segment).
            dynamic_dispatched_planners: list[str] = []
            # Planner writes for the current superstep — captured after the
            # dispatch loop finishes but used here to seed per-invocation
            # sub-channels (1.3.21③ dynamic path). Pre-populated from the
            # previous superstep's snapshot so subsequent supersteps' planners
            # can resolve without waiting for the commit cycle.
            self._pending_dispatch_state: dict[str, list[dict[str, Any]]] = {}
            # Guard against the dispatch loop firing the same planner's
            # dynamic fan-out more than once per superstep (the FIFO
            # scheduler may emit the target node N times in a row when N
            # packets share it).
            self._dynamic_dispatched_in_superstep: set[str] = set()

            for node_id in scheduled_nodes:
                node = self._graph.nodes.get(node_id)
                if node is None:
                    continue

                node_type = getattr(node, "type", None)
                invocations = self._pending_invocations_by_target.get(node_id, [])
                dynamic_invocations = [inv for inv in invocations if inv.identity.fanout_source is not None]

                if dynamic_invocations:
                    # 1.3.21③ dynamic path: dispatch each invocation in
                    # parallel with its seeded sub-channel. The structural
                    # target node is invoked once per branch — unlike
                    # static FAN_OUT, the engine does not visit the planner
                    # node itself (the planner's result carries the plan).
                    planner_id = dynamic_invocations[0].identity.fanout_source
                    # Guard against the scheduler emitting the same target
                    # multiple times in one superstep (e.g. when N packets
                    # share a target). Dispatching only on the first hit
                    # prevents N² branch invocations — see #dynamic-loop
                    # regression test.
                    if planner_id in self._dynamic_dispatched_in_superstep:
                        continue
                    self._dynamic_dispatched_in_superstep.add(planner_id)
                    dynamic_dispatched_planners.append(planner_id)
                    # 1.3.21③ dynamic path: dispatch each invocation in
                    # parallel with its seeded sub-channel. The structural
                    # target node is invoked once per branch — unlike
                    # static FAN_OUT, the engine does not visit the planner
                    # node itself (the planner's result carries the plan).
                    self._session_id_for_event = session_id
                    self._current_trace_id = trace_id
                    # Rebuild per-invocation packet state from the planner's
                    # original _dispatch write — applied to channels at the
                    # previous superstep's commit, so it's part of snapshot.
                    planner_dispatch = snapshot.get("_dispatch") or []
                    if not isinstance(planner_dispatch, list):
                        planner_dispatch = []
                    inv_packet_state: dict[Invocation, dict[str, Any]] = {}
                    for inv, raw in zip(dynamic_invocations, planner_dispatch, strict=False):
                        if isinstance(raw, dict) and isinstance(raw.get("state"), dict):
                            inv_packet_state[inv] = raw["state"]
                    self._pending_packet_state = inv_packet_state
                    fan_out_results = await self._dispatch_dynamic_fan_out(
                        planner_id,
                        node,
                        dynamic_invocations,
                        snapshot,
                        execution_context=execution_context,
                    )
                    results.extend(fan_out_results)
                    # Emit the planner's own NODE_START/NODE_END markers so
                    # callers observing a planner still see a complete frame.
                    await self._emit(
                        session_id,
                        EventType.NODE_START,
                        node_id=planner_id,
                        payload={
                            "node_type": NodeType.CONDITION.value,
                            "fanout_source": True,
                            "branch_count": len(dynamic_invocations),
                        },
                        trace_id=trace_id,
                    )
                    await self._emit(
                        session_id,
                        EventType.NODE_END,
                        node_id=planner_id,
                        payload={"success": True, "fanout_source": True, "cached": False, "cache_key": None},
                        trace_id=trace_id,
                    )
                    continue

                await self._emit(
                    session_id,
                    EventType.NODE_START,
                    node_id=node_id,
                    payload={"node_type": str(node_type) if node_type else None},
                    trace_id=trace_id,
                )

                # 1.3.21IV node cache seam: consult before dispatching a
                # cache-policy node. A hit fabricates the WorkerResult and
                # skips the worker entirely (all dispatch modalities — the
                # hit yields no message chunks, which is byte-identical to
                # a normal execution of any non-streaming worker). Misses
                # execute normally and store before the WAL commit.
                cache_policy = self._cache_policy_for(node_id)
                cache_key: str | None = None
                cache_key_hash: str | None = None
                if cache_policy is not None:
                    cached_result, cache_key, cache_key_hash = self._consult_node_cache(
                        cache_policy, node_id, node.config, snapshot, session_id
                    )
                    if cached_result is not None:
                        results.append(cached_result)
                        continue

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
                    stream_result: WorkerResult | None = None
                    async for item in retry_executor.execute_stream(
                        self._worker.execute_stream,
                        node_id,
                        node.config,
                        snapshot,
                        execution_context=node_execution_context,
                    ):
                        if isinstance(item, WorkerResult):
                            stream_result = item
                            results.append(item)
                        elif isinstance(item, dict):
                            yield {"type": "message", "content": item.get("content", "")}
                    if cache_policy is not None and stream_result is not None:
                        self._record_node_cache_miss(cache_policy, cache_key, cache_key_hash, stream_result)
                else:
                    result = await retry_executor.execute(
                        self._pool.dispatch,
                        self._worker,
                        node_id,
                        node.config,
                        snapshot,
                        execution_context=node_execution_context,
                    )
                    if cache_policy is not None:
                        self._record_node_cache_miss(cache_policy, cache_key, cache_key_hash, result)
                    results.append(result)

            pending_writes: list[tuple[str, Any, str | None]] = []
            worker_interrupt: WorkerResult | None = None
            for result in results:
                await self._emit(
                    session_id,
                    EventType.NODE_END,
                    node_id=result.node_id,
                    payload={
                        "success": result.error is None,
                        "has_command": result.command is not None,
                        "cached": result.cache_hit,
                        "cache_key": result.cache_key,
                    },
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
            fanout_payload = self._build_fanout_commit_payload(fan_out_dispatched, dynamic_dispatched_planners)
            await self._append_write_batch(
                session_id,
                trace_id,
                pending_writes,
                commit_event=EventType.STEP_END,
                commit_payload=fanout_payload,
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
                # WAL-order: append CHANNEL_WRITE events with the INTERRUPT
                # as commit point before applying them to channels (matches
                # the worker_interrupt path's ordering). Without this the
                # post-pause snapshot diverges from fold(log) and the
                # projection-equivalent invariant fails on resume.
                await self._append_write_batch(
                    session_id,
                    trace_id,
                    pending_writes,
                    commit_event=EventType.INTERRUPT,
                    commit_node_id=after_hits[0],
                    commit_payload=descriptor,
                )
                for _node_id, channel_updates, _node_id_repeat in pending_writes:
                    self._apply_writes(channel_updates, node_id=_node_id)
                await self._append_eviction_events(session_id, trace_id)
                # Note: _append_write_batch already emitted the INTERRUPT
                # event as the commit-point closer, so no extra _emit here.
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

            invocations = self._resolve_next_nodes(results)
            self._enforce_superstep_invocation_cap(invocations)
            # Stash per-node invocation list so the dispatch loop knows the
            # branch identity (dynamic fan-out) and seed payload for each
            # scheduled target. Static invocations map to a single-entry list.
            self._pending_invocations_by_target = {}
            for inv in invocations:
                self._pending_invocations_by_target.setdefault(inv.target, []).append(inv)
            # Clear the planner's _dispatch write after the superstep
            # consumes it — the plan is "spent" once the engine has resolved
            # it into invocations. Without this, sessions on a long-lived
            # runtime accumulate stale plans on the TOPIC channel (the
            # session-isolated log retains the full history for replay/fork
            # via the WAL, so this in-memory clear is safe).
            if any(r.channel_updates.get("_dispatch") is not None for r in results):
                self._channel_manager.restore({"_dispatch": None})
            current_nodes = [inv.target for inv in invocations]

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

    def _resolve_next_nodes(self, results: list[WorkerResult]) -> list[Invocation]:
        """Determine the next invocations to execute based on edges and commands.

        Per 1.3.21③, the dispatch unit is now an :class:`Invocation` (target +
        identity + optional sub-channel). Three input paths feed it:

        1. ``Command(goto=...)`` from a worker — single invocation.
        2. ``_dispatch`` channel write from a planner node — N invocations,
           each carrying the planner's branch_index and a per-call sub-channel.
        3. Static edge resolution — each completed node's outgoing edges
           produce one invocation per resolved target. Conditional edges
           resolve against the worker's ``_route`` channel write (kept
           unchanged from the legacy semantics). Static invocations carry
           ``identity.fanout_source = None`` and an empty sub-channel.

        Priority mirrors the legacy goto-first ordering: a single result with
        both ``Command(goto)`` and ``_dispatch`` SHALL be resolved via goto
        (the more explicit signal); ``_dispatch`` is the planner's "soft
        goto". Both paths log a debug-level note when they coexist.

        Returns:
            An ordered list of invocations. The list is deduplicated only on
            the *static* branch — dynamic invocations are preserved as-is so
            that N calls of the same target reach the scheduler. The caller
            is responsible for fan-out limit checks before scheduling.
        """
        invocations: list[Invocation] = []
        next_targets_dedup: list[str] = []
        for result in results:
            # --- 1.3.21③ dynamic path: planner wrote _dispatch ---
            dispatch = result.channel_updates.get("_dispatch")
            if dispatch is not None:
                if result.command and result.command.is_goto():
                    logger.debug(
                        "_resolve_next_nodes: result from '%s' has goto and _dispatch; "
                        "goto takes precedence, _dispatch ignored",
                        result.node_id,
                    )
                else:
                    packets = self._validate_dispatch_packets(result.node_id, dispatch)
                    for idx, packet in enumerate(packets):
                        invocations.append(
                            Invocation(
                                target=packet.node,
                                identity=InvocationIdentity(fanout_source=result.node_id, branch_index=idx),
                                sub_channel=f"_fanout__{result.node_id}__idx{idx}",
                            )
                        )
                    continue  # dispatch takes the slot, skip static edge walk

            # --- legacy Command(goto) ---
            if result.command and result.command.is_goto():
                target = result.command.goto
                if target not in next_targets_dedup:
                    next_targets_dedup.append(target)
                    invocations.append(
                        Invocation(
                            target=target,
                            identity=InvocationIdentity(fanout_source=None, branch_index=-1),
                        )
                    )
                continue

            # --- legacy static edge resolution ---
            for edge in self._graph.edges:
                if edge.source != result.node_id:
                    continue
                if isinstance(edge.target, str):
                    target = edge.target
                elif isinstance(edge.target, dict):
                    route_key = str(result.channel_updates.get("_route", "true"))
                    target = self._resolve_conditional_target(edge.target, route_key)
                    if not target:
                        continue
                else:
                    continue
                if target not in next_targets_dedup:
                    next_targets_dedup.append(target)
                    invocations.append(
                        Invocation(
                            target=target,
                            identity=InvocationIdentity(fanout_source=None, branch_index=-1),
                        )
                    )
        if "__end__" in next_targets_dedup:
            return []
        return invocations

    def _validate_dispatch_packets(self, source_id: str, raw: Any) -> list[DispatchPacket]:
        """Parse + validate the planner's ``_dispatch`` write.

        Each planner is responsible for emitting a list of ``DispatchPacket``
        (dict with ``node`` and optional ``state``). Anything else is a
        planner contract violation and fails the superstep loud rather than
        silently dropping the dispatch.

        Engine-level fan-out ceilings are enforced here (D6). Per-node
        ``max_fanout`` (from the planner node config) trims the engine
        default; the absolute platform cap is the final guard.
        """
        if not isinstance(raw, list):
            raise FanoutLimitError(
                scope=f"planner:{source_id}",
                requested=0,
                ceiling=DEFAULT_MAX_FANOUT_PER_DISPATCH,
            )
        source_node = self._graph.nodes.get(source_id)
        per_node_cap = DEFAULT_MAX_FANOUT_PER_DISPATCH
        if source_node is not None:
            fanout_cfg = source_node.config.get("fanout") or {}
            per_node_cap = int(fanout_cfg.get("max_fanout", per_node_cap))
        per_node_cap = min(per_node_cap, ABSOLUTE_MAX_FANOUT)

        if len(raw) > per_node_cap:
            raise FanoutLimitError(
                scope=f"planner:{source_id}:per_node",
                requested=len(raw),
                ceiling=per_node_cap,
            )
        if len(raw) > ABSOLUTE_MAX_FANOUT:
            raise FanoutLimitError(
                scope=f"planner:{source_id}:absolute",
                requested=len(raw),
                ceiling=ABSOLUTE_MAX_FANOUT,
            )

        packets: list[DispatchPacket] = []
        node_ids = set(self._graph.nodes.keys())
        for i, item in enumerate(raw):
            if not isinstance(item, dict):
                raise FanoutLimitError(
                    scope=f"planner:{source_id}:packet_shape",
                    requested=0,
                    ceiling=0,
                )
            target = item.get("node")
            if not isinstance(target, str) or target not in node_ids:
                raise FanoutLimitError(
                    scope=f"planner:{source_id}:unknown_target[{i}]",
                    requested=0,
                    ceiling=0,
                )
            state = item.get("state") or {}
            if not isinstance(state, dict):
                raise FanoutLimitError(
                    scope=f"planner:{source_id}:state_shape[{i}]",
                    requested=0,
                    ceiling=0,
                )
            packets.append(DispatchPacket(node=target, state=state))
        return packets

    def _enforce_superstep_invocation_cap(self, invocations: list[Invocation]) -> None:
        """Enforce superstep-wide invocation ceiling (D6)."""
        if len(invocations) > DEFAULT_MAX_INVOCATIONS_PER_SUPERSTEP:
            raise FanoutLimitError(
                scope="superstep",
                requested=len(invocations),
                ceiling=DEFAULT_MAX_INVOCATIONS_PER_SUPERSTEP,
            )

    def _build_fanout_commit_payload(
        self,
        static_fan_out_dispatched: list[str],
        dynamic_planners_dispatched: list[str],
    ) -> dict[str, Any]:
        """Enrich STEP_END commit_payload with fanout branch outputs (T2b).

        Both static FAN_OUT and dynamic (1.3.21③) paths funnel through this
        helper: the engine reads the corresponding ``_fanout__*`` sub-channels
        out of the in-memory channel manager (which already holds the
        branch results from this superstep) and bakes them into the STEP_END
        payload. ``fold_session`` rebuilds sub-channels from this payload
        during replay / fork / log-only recovery — closing the T2b gap.

        The payload is empty when no fan-out ran in this superstep; the
        STEP_END is unchanged for non-fanout supersteps.
        """
        segments: list[dict[str, Any]] = []
        for fan_out_id in static_fan_out_dispatched:
            segments.append(self._snapshot_fanout_subchannels(fan_out_id, dynamic=False))
        for planner_id in dynamic_planners_dispatched:
            segments.append(self._snapshot_fanout_subchannels(planner_id, dynamic=True))
        if not segments:
            return {}
        return {"fanout": segments}

    def _snapshot_fanout_subchannels(self, source_id: str, dynamic: bool) -> dict[str, Any]:
        """Snapshot all ``_fanout__{source}__*`` sub-channels.

        For static FAN_OUT the suffix is the branch node ID; for dynamic
        fan-out the suffix is ``idx{i}``. Sub-channels that are not
        registered (e.g. a branch crashed before any sub-channel was written)
        are recorded as ``None`` so the fold path can detect and skip them.
        """
        prefix = f"_fanout__{source_id}__"
        snapshot = self._channel_manager.snapshot()
        entries: dict[str, Any] = {}
        for name, value in snapshot.items():
            if not name.startswith(prefix):
                continue
            suffix = name[len(prefix) :]
            entries[suffix] = value
        return {
            "source": source_id,
            "dynamic": dynamic,
            "sub_channels": entries,
        }

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

    async def _dispatch_dynamic_fan_out(
        self,
        planner_id: str,
        target_node: Any,
        invocations: list[Invocation],
        snapshot: dict,
        execution_context: dict | None = None,
    ) -> list[WorkerResult]:
        """Dispatch dynamic-fan-out invocations in one superstep (1.3.21③).

        For every invocation the engine:

        1. Seeds the invocation's sub-channel (``_fanout__{planner}__idx{i}``)
           with the planner's packet state. The seed runs before any branch
           worker is dispatched so the worker reads the slice from its own
           sub-channel — branches never see each other's payloads.
        2. Dispatches the same target node with the per-call sub-channel name
           carried in ``execution_context`` so the worker can read its slice.
        3. Writes the result back into the invocation's sub-channel on
           success; on error, honors ``on_branch_error`` (``fail_fast``
           default keeps existing behavior; ``collect`` records the failure
           locally and lets the rest of the batch finish).
        """
        from hecate.runtime.types import ChannelDef, ChannelType

        planner_node = self._graph.nodes.get(planner_id)
        error_mode = "fail_fast"
        if planner_node is not None:
            error_mode = planner_node.config.get("on_branch_error", "fail_fast")

        # Per-invocation packet state lives on the runtime instance, set by
        # the dispatch loop in the same superstep. Falling back to {} is a
        # defense-in-depth: if the dispatch loop forgets to populate it, the
        # branch just runs without a slice (still correct, just less useful).
        packet_state_by_inv: dict[Invocation, dict[str, Any]] = getattr(self, "_pending_packet_state", {})

        # Pre-register and seed all sub-channels before any worker runs so
        # branch isolation is guaranteed at the channel layer.
        for inv in invocations:
            if inv.sub_channel is None:  # defensive: dynamic invocations always carry sub_channel
                continue
            self._channel_manager.register(inv.sub_channel, ChannelDef(type=ChannelType.LAST_VALUE))
            self._channel_manager.write(inv.sub_channel, packet_state_by_inv.get(inv, {}))

        async def run_invocation(inv: Invocation) -> WorkerResult:
            branch_payload = packet_state_by_inv.get(inv, {})
            branch_execution_context = {
                **(execution_context or {}),
                "_fanout_sub_channel": inv.sub_channel,
                "_fanout_planner": inv.identity.fanout_source,
                "_fanout_branch_index": inv.identity.branch_index,
                "_fanout_branch_payload": branch_payload,
            }
            # 1.3.21IV: the packet state IS the branch input slice — identical
            # slices hash to identical keys (dedup stays v1-accepted: parallel
            # misses all execute, no coalescing).
            policy = self._cache_policy_for(inv.target)
            if policy is not None:
                cached, full_key, key_hash = self._consult_node_cache(
                    policy,
                    inv.target,
                    target_node.config,
                    snapshot,
                    session_id=self._session_id_for_event,
                    branch_slice=branch_payload,
                )
                if cached is not None:
                    return cached
                result = await self._pool.dispatch(
                    self._worker,
                    inv.target,
                    target_node.config,
                    snapshot,
                    execution_context=branch_execution_context,
                )
                self._record_node_cache_miss(policy, full_key, key_hash, result)
                return result
            return await self._pool.dispatch(
                self._worker,
                inv.target,
                target_node.config,
                snapshot,
                execution_context=branch_execution_context,
            )

        # Emit branch NODE_START markers before fan-out so consumers see the
        # full frame for each invocation.
        for inv in invocations:
            await self._emit(
                self._session_id_for_event,
                EventType.NODE_START,
                node_id=inv.target,
                payload={
                    "node_type": getattr(target_node.type, "value", None),
                    "fanout_source": inv.identity.fanout_source,
                    "branch_index": inv.identity.branch_index,
                },
                trace_id=self._current_trace_id,
            )

        branch_results = await asyncio.gather(*[run_invocation(inv) for inv in invocations])

        out: list[WorkerResult] = []
        for inv, result in zip(invocations, branch_results, strict=True):
            await self._emit(
                self._session_id_for_event,
                EventType.NODE_END,
                node_id=inv.target,
                payload={
                    "success": result.error is None,
                    "fanout_source": inv.identity.fanout_source,
                    "branch_index": inv.identity.branch_index,
                    "cached": result.cache_hit,
                    "cache_key": result.cache_key,
                },
                trace_id=self._current_trace_id,
            )
            if result.error is None:
                if inv.sub_channel is not None:
                    self._channel_manager.write(inv.sub_channel, result.channel_updates)
                out.append(result)
            elif error_mode == "collect":
                if inv.sub_channel is not None:
                    self._channel_manager.write(
                        inv.sub_channel,
                        {
                            "__branch_error__": {
                                "type": type(result.error).__name__,
                                "message": str(result.error),
                            }
                        },
                    )
                # Return a clean result (no .error) so the superstep driver
                # does not re-raise. The error is observable via the
                # sub-channel contents, which downstream MERGE / consumers
                # can introspect.
                out.append(
                    WorkerResult(
                        node_id=result.node_id,
                        channel_updates=result.channel_updates,
                        command=result.command,
                    )
                )
            else:
                # fail_fast default — preserve legacy raise-on-error contract.
                out.append(result)

        if error_mode == "fail_fast":
            for r in out:
                if r.error is not None:
                    raise r.error

        return out

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
            # 1.3.21IV: branches share the pre-dispatch snapshot as input —
            # identical readable slices may hit (v1: parallel misses all
            # execute, stampede accepted).
            policy = self._cache_policy_for(branch_id)
            if policy is not None:
                cached, full_key, key_hash = self._consult_node_cache(
                    policy,
                    branch_id,
                    branch_node.config,
                    snapshot,
                    session_id=execution_context.get("session_id") if execution_context else None,
                )
                if cached is not None:
                    result = cached
                else:
                    result = await self._pool.dispatch(
                        self._worker,
                        branch_id,
                        branch_node.config,
                        snapshot,
                        execution_context=execution_context,
                    )
                    self._record_node_cache_miss(policy, full_key, key_hash, result)
            else:
                result = await self._pool.dispatch(
                    self._worker,
                    branch_id,
                    branch_node.config,
                    snapshot,
                    execution_context=execution_context,
                )
            sub_channel = f"_fanout__{node_id}__{branch_id}"
            if result.error is None:
                self._channel_manager.write(sub_channel, result.channel_updates)
            return result

        branch_results = await asyncio.gather(*[run_branch(b) for b in branches])

        for r in branch_results:
            if r.error is not None:
                raise r.error

        return list(branch_results)

    def _execute_merge(self, node_id: str, node: Any) -> WorkerResult:
        """Aggregate results from all branches of a preceding FAN_OUT.

        Two aggregation modes (1.3.21③):

        * Static FAN_OUT source — the source node's ``branches`` config is
          authoritative; ``{branch_id: sub_channel_value}`` is the legacy
          contract and remains unchanged.
        * Dynamic fan-out source — the source is a planner node that wrote
          ``_dispatch``. We resolve the dispatch plan from the folded
          channel state and read each invocation's sub-channel by its
          ``branch_index`` suffix (``idx{i}``). The aggregated result keys
          branches by ``branch_index`` (the per-call identity).

        Args:
            node_id: The MERGE node ID.
            node: The NodeConfig for the MERGE node.

        Returns:
            WorkerResult with the aggregated output on ``output_channel``.
        """
        fan_out_source: str = node.config.get("fan_out_source", "")
        output_channel: str = node.config.get("output_channel", "merged_output")

        source_node = self._graph.nodes.get(fan_out_source)
        if source_node is None:
            return WorkerResult(node_id=node_id, error=RuntimeError(f"FAN_OUT source '{fan_out_source}' not found"))

        snapshot = self._channel_manager.snapshot()
        is_dynamic = bool(source_node.config.get("fanout"))
        aggregated: dict[str, Any] = {}

        if is_dynamic:
            # Discover branches by scanning the snapshot for sub-channels
            # matching the planner's fanout prefix. The planner's
            # ``_dispatch`` write may have been cleared by the engine after
            # consumption (it served its role when resolving the next
            # superstep), but the per-call sub-channels persist in the
            # channel manager and are the canonical source of branch output.
            prefix = f"_fanout__{fan_out_source}__idx"
            for name, value in snapshot.items():
                if not name.startswith(prefix):
                    continue
                suffix = name[len(prefix) :]
                # Defensive: only accept integer-suffixed sub-channels.
                if not suffix.isdigit():
                    continue
                aggregated[suffix] = value
        else:
            branches: list[str] = source_node.config.get("branches", [])
            for branch_id in branches:
                sub_channel = f"_fanout__{fan_out_source}__{branch_id}"
                aggregated[branch_id] = snapshot.get(sub_channel)

        return WorkerResult(
            node_id=node_id,
            channel_updates={output_channel: aggregated},
        )
