"""Execution assembly: a minimal fixed graph per profile.

The runner compiles one linear graph per manifest at startup:

    model node -> (one read-tool node per allowlisted tool) -> end

The model node produces the tool-selection payload; each tool node
dispatches to the business API with the server-verified principal and
domain scope. Execution is serial (``max_concurrency = 1``): a new run
is refused while another is in flight rather than queued. Checkpoints
are in-memory only — durable recovery is declared ``unsupported`` via
the capabilities endpoint and lands in step6.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from dataclasses import dataclass, field

import httpx
from hecate_durable.contracts.references import BackendRef
from hecate_durable.contracts.tools import ToolSideEffectClass
from hecate_runtime.action_ledger import ActionLedgerHook
from hecate_runtime.checkpoint import InMemoryCheckpointStore
from hecate_runtime.execution_service import (
    CooperativeCancellationError,
    RuntimeEventDecision,
    RuntimeExecutionRequest,
    RuntimeScheduleCancelledError,
    runtime_execution_service,
)
from hecate_runtime.types import (
    ChannelDef,
    ChannelType,
    Edge,
    NodeConfig,
    NodeType,
    WorkerResult,
)
from hecate_runtime.types import (
    CompiledGraph as CompiledGraphT,
)
from hecate_runtime.worker import Worker
from jsonschema import Draft202012Validator

from .durable import DurableRuntime, gate_dispatch_async, record_outcome_async
from .evidence import OUTCOME_FAILED, EvidenceStore
from .profile import BUILTIN_TOOL_SCHEMAS, Profile

DURABLE_CAPABILITIES: dict[str, str] = {
    "durable_tasks": "supported: persistent task/action ledger (hecate-durable)",
    "long_task_recovery": "supported: replay-based restart recovery, ledger-gated",
    "write_tools": "supported: manifest-declared write tools through the action ledger",
}

logger = logging.getLogger(__name__)

# Capabilities the preview profile does not provide; surfaced verbatim on
# /capabilities so absence is explicit, not a silent default.
UNSUPPORTED_CAPABILITIES: dict[str, str] = {
    "durable_tasks": "unsupported: persistence lands in step6",
    "background_retry": "unsupported: automatic retries are a step6/7 concern",
    "long_task_recovery": "unsupported: checkpoint recovery is step6 scope",
    "write_tools": "unsupported: preview profile serves read tools only",
    "approval_tools": "unsupported: approval gating lands in step7",
    "event_stream": "unsupported: use cursor-based /events polling in the preview",
    "pause": "unsupported: cooperative pause is a later-step capability",
}


class ModelNodeWorker(Worker):
    """Produces the tool-selection payload from the run input.

    Stub backend: deterministic selection of every allowlisted tool in
    manifest order, consuming the run input verbatim. Endpoint backend:
    calls the configured JSON endpoint and retains its text in graph events.
    Both modes execute a fixed tool plan; model-driven tool selection is
    outside this preview.
    """

    def __init__(self, profile: Profile) -> None:
        super().__init__()
        self._profile = profile

    async def execute(
        self, node_id: str, node_config: dict, channel_snapshot: dict, execution_context: dict | None = None
    ) -> WorkerResult:
        run_input = channel_snapshot.get("input") or {}
        prompt = run_input.get("prompt", (run_input.get("input") or {}).get("prompt", ""))
        config = self._profile.config
        content = ""
        if config.model_backend == "endpoint":
            headers = {}
            if config.model_auth_env:
                headers["Authorization"] = f"Bearer {os.environ[config.model_auth_env]}"
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    config.model_endpoint,
                    json={"prompt": prompt, "tools": list(config.tool_allowlist)},
                    headers=headers,
                )
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict) or not isinstance(payload.get("content"), str):
                    raise ValueError("model endpoint must return an object with string content")
                content = payload["content"]
        return WorkerResult(
            node_id=node_id,
            channel_updates={
                "plan": {
                    "tools": list(config.tool_allowlist),
                    "prompt": prompt,
                    "model_source": node_config.get("model_source", "stub"),
                    "content": content,
                },
            },
        )


class ReadToolNodeWorker(Worker):
    """Dispatches one allowlisted read tool against the business API."""

    def __init__(self, dispatch) -> None:
        super().__init__()
        self._dispatch = dispatch

    async def execute(
        self, node_id: str, node_config: dict, channel_snapshot: dict, execution_context: dict | None = None
    ) -> WorkerResult:
        tool_name = node_config["tool_name"]
        raw = (channel_snapshot.get("input") or {}).get("tool_arguments") or {}
        # Two accepted shapes: flat (the preview graph's single-tool node
        # consumes the arguments directly) or tool-name-namespaced.
        namespaced = raw.get(tool_name) if isinstance(raw.get(tool_name), dict) else None
        arguments = namespaced if namespaced is not None else raw
        outcome = await self._dispatch(arguments)
        return WorkerResult(node_id=node_id, channel_updates={"tool_results": {tool_name: outcome}})


@dataclass
class RunState:
    run_id: str
    status: str  # "running" | "succeeded" | "failed" | "cancelled" | "unknown" | task lifecycle
    principal: str
    domains: tuple[str, ...]
    events: list[dict] = field(default_factory=list)
    result_ref: str | None = None
    error: str | None = None
    cancel_requested: bool = False
    # Durable profile correlation (None in the preview).
    task_ref: BackendRef | None = None
    run_ref: BackendRef | None = None
    action_hook: ActionLedgerHook | None = None
    needs_reconciliation: bool = False
    replayed: bool = False
    cancel_command_id: str | None = None
    received_at: str | None = None
    idempotency_key: str | None = None


class EvidenceUnavailableError(Exception):
    """The local evidence store cannot accept writes (SC06 gate)."""


class ExecutionEngine:
    """Serial, in-process execution of the compiled preview graph."""

    def __init__(
        self,
        profile: Profile,
        evidence: EvidenceStore,
        tool_dispatch,
        durable: DurableRuntime | None = None,
    ) -> None:
        # ``tool_dispatch`` signature: (tool_name, arguments, principal, domains).
        self._profile = profile
        self._evidence = evidence
        self._tool_dispatch = tool_dispatch
        self._durable = durable
        # Side-effect class per allowlisted tool from the manifest's declared
        # permission: read → readonly, write → conservative non-idempotent.
        self._side_effects: dict[str, ToolSideEffectClass] = {
            tool.name: (
                ToolSideEffectClass.READONLY if tool.permission == "read" else ToolSideEffectClass.NON_IDEMPOTENT_WRITE
            )
            for tool in profile.manifest.tools
            if tool.name in profile.config.tool_allowlist
        }
        self._checkpoint_store = InMemoryCheckpointStore()
        self._lock = asyncio.Lock()
        self._runs: dict[str, RunState] = {}
        self._graph = self._compile_graph()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._tasks: set[asyncio.Task] = set()
        self._closing = False

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Record the loop runs are scheduled on (set by the server layer)."""

        self._loop = loop

    async def _dispatch_tool(self, state: RunState, tool_name: str, arguments: dict) -> dict:
        """Run one tool with the run's server-verified identity scope.

        In the durable profile every dispatch first passes the persistent
        ledger gate (recovery verdict + atomic claim) and, for protected
        tools, the evidence-writability gate; the real outcome is mirrored
        back to the ledger after the business call.
        """

        if state.cancel_requested:
            raise CooperativeCancellationError
        effect = self._side_effects.get(tool_name, ToolSideEffectClass.UNKNOWN)
        if state.action_hook is not None:
            withheld, claim_blocked = await gate_dispatch_async(
                state.action_hook,
                run_id=state.run_id,
                tool_name=tool_name,
                arguments=arguments,
                side_effect_class=effect,
            )
            blocked = withheld if withheld is not None else claim_blocked
            if blocked is not None:
                self._mark_reconciliation(state, blocked)
                self.record_tool_result(state, tool_name, blocked)
                return blocked
            if effect is not ToolSideEffectClass.READONLY:
                # SC06 local half: stop new protected actions when the local
                # evidence store cannot accept writes; the failure carries an
                # explicit category (unwritable vs over-capacity policy).
                try:
                    self._evidence.probe()
                except OSError as exc:
                    category = getattr(exc, "category", "unwritable")
                    self._evidence.record_gate_failure(category, f"local evidence gate: {exc}")
                    blocked = {
                        "status": "store_unavailable",
                        "detail": f"local evidence unavailable ({category}): {exc}",
                    }
                    self._mark_reconciliation(state, blocked)
                    self.record_tool_result(state, tool_name, blocked)
                    return blocked
        self._evidence.append("tool_dispatch", state.principal, state.run_id, "started", {"tool": tool_name})
        if arguments.get("domain") not in state.domains:
            outcome = {"status": "authorization", "detail": "requested domain is outside the trusted identity scope"}
        else:
            outcome = await self._tool_dispatch(tool_name, arguments, state.principal, list(state.domains))
        status = outcome.get("status")
        evidence_outcome = "ok" if status == "ok" else "denied" if status == "authorization" else "failed"
        self._evidence.append(
            "tool_result", state.principal, state.run_id, evidence_outcome, {"tool": tool_name, "status": status}
        )
        if state.action_hook is not None:
            await record_outcome_async(
                state.action_hook,
                run_id=state.run_id,
                tool_name=tool_name,
                arguments=arguments,
                outcome=outcome,
            )
        self.record_tool_result(state, tool_name, outcome)
        return outcome

    def _mark_reconciliation(self, state: RunState, outcome: dict) -> None:
        """A withheld/blocked durable action keeps the task pending reconciliation."""

        if outcome.get("status") in {"needs_review", "reconciliation_required", "conflict", "store_unavailable"}:
            state.needs_reconciliation = True

    def _compile_graph(self) -> CompiledGraphT:
        allowlist = list(self._profile.config.tool_allowlist)
        model_source = self._profile.config.model_backend
        nodes = {
            "model": NodeConfig(
                id="model",
                type=NodeType.CONVERSATION,
                config={"model": model_source, "model_source": model_source},
            ),
        }
        edges = []
        previous = "model"
        for index, tool_name in enumerate(allowlist):
            node_id = f"tool_{index}"
            nodes[node_id] = NodeConfig(id=node_id, type=NodeType.TOOL_CALL, config={"tool_name": tool_name})
            edges.append(Edge(source=previous, target=node_id))
            previous = node_id
        edges.append(Edge(source=previous, target="__end__"))
        return CompiledGraphT(
            nodes=nodes,
            edges=edges,
            channels={
                "input": ChannelDef(type=ChannelType.LAST_VALUE, default={}),
                "plan": ChannelDef(type=ChannelType.LAST_VALUE, default={}),
                "tool_results": ChannelDef(type=ChannelType.TOPIC, default=[]),
            },
            entry_point="model",
            name="runner-preview",
        )

    @property
    def busy(self) -> bool:
        return self._lock.locked()

    @property
    def closing(self) -> bool:
        """Whether shutdown has begun and new work must be refused."""
        return self._closing

    def validate_run_input(self, run_input: dict) -> None:
        """Validate every scheduled tool before accepting any execution."""
        if not isinstance(run_input, dict):
            raise ValueError("run request must be an object")
        if "input" in run_input and not isinstance(run_input["input"], dict):
            raise ValueError("input must be an object")
        raw = run_input.get("tool_arguments", {})
        if not isinstance(raw, dict):
            raise ValueError("tool_arguments must be an object")
        for name in self._profile.config.tool_allowlist:
            arguments = raw.get(name) if isinstance(raw.get(name), dict) else raw
            for schema in (BUILTIN_TOOL_SCHEMAS[name], self._profile.tool_schemas[name]):
                if next(Draft202012Validator(schema).iter_errors(arguments), None) is not None:
                    raise ValueError(f"invalid arguments for tool {name}")

    def capabilities(self) -> dict:
        supported = {
            "submit": "enforced",
            "read_events_cursor": "enforced",
            "cancel": "cooperative",
            "local_evidence": "enforced",
        }
        if self._durable is not None:
            return {**supported, **DURABLE_CAPABILITIES, **UNSUPPORTED_CAPABILITIES}
        return {**supported, **UNSUPPORTED_CAPABILITIES}

    async def submit(
        self,
        principal: str,
        run_input: dict,
        domains: tuple[str, ...] = (),
        *,
        idempotency_key: str | None = None,
    ) -> tuple[str, RunState | None]:
        """Start a run; returns (run_id, None-in-flight-refusal) — serial queue.

        ``principal``/``domains`` come from the server-verified identity at
        the HTTP entry and travel with the run: tool dispatches carry exactly
        this scope, never any request-body self-reported claims. In the
        durable profile the submission registers under an idempotency key
        bound to that verified identity and the canonical request body:
        replays return the original run; a key rebound to a different body
        raises :class:`IdempotencyConflictError`.
        """

        if self._closing or self._lock.locked():
            return "", None
        self.validate_run_input(run_input)
        if self._durable is not None:
            self._admit_protected(run_input)
            submission = self._durable.submit(principal=principal, run_input=run_input, idempotency_key=idempotency_key)
            run_ref = submission.association.run_ref
            task_ref = submission.association.task_ref
            if submission.replayed:
                # Same key + same body: never a second execution. Report the
                # task's durable state (post-restart runs resolve via the
                # same path because startup reconcile already re-drove them).
                state = self._runs.get(run_ref.id)
                if state is None:
                    state = self._durable_task_state(task_ref, principal, domains, run_ref)
                state.replayed = True
                return run_ref.id, state
        # Reserve admission before yielding to the execution task. Checking
        # the lock only in _execute silently queued concurrent submissions.
        await self._lock.acquire()
        if self._durable is not None:
            state = RunState(
                run_id=run_ref.id,
                status="running",
                principal=principal,
                domains=tuple(domains),
                task_ref=task_ref,
                run_ref=run_ref,
                received_at=self._durable.task_state(task_ref.id).recorded_at,
                idempotency_key=idempotency_key,
            )
            state.action_hook = self._durable.hook_for(task_ref, run_ref)
        else:
            run_id = uuid.uuid4().hex
            state = RunState(
                run_id=run_id,
                status="running",
                principal=principal,
                domains=tuple(domains),
                received_at=_now_iso(),
                idempotency_key=idempotency_key,
            )
        try:
            self._evidence.append(
                "submission", principal, state.run_id, "accepted", {"model_source": self._profile.config.model_backend}
            )
            self._runs[state.run_id] = state
            task = asyncio.create_task(self._execute(state.run_id, state, run_input))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        except BaseException:
            self._lock.release()
            raise
        return state.run_id, state

    def _admit_protected(self, run_input: dict) -> None:
        """SC06 admission gate: refuse protected runs while evidence is unavailable.

        The failure carries an explicit category (unwritable vs over-capacity
        policy) and is recorded as an auditable gate denial.
        """

        if any(effect is not ToolSideEffectClass.READONLY for effect in self._side_effects.values()):
            try:
                self._evidence.probe()
            except OSError as exc:
                category = getattr(exc, "category", "unwritable")
                self._evidence.record_gate_failure(category, f"local evidence gate: {exc}")
                raise EvidenceUnavailableError(f"local evidence unavailable ({category}): {exc}") from exc

    def _durable_task_state(
        self, task_ref: BackendRef, principal: str, domains: tuple[str, ...], run_ref: BackendRef
    ) -> RunState:
        record = self._durable.store.get_task_state(task_ref) if self._durable else None
        status = record.lifecycle_state.value if record is not None else "unknown"
        return RunState(
            run_id=run_ref.id,
            status=status,
            principal=principal,
            domains=tuple(domains),
            task_ref=task_ref,
            run_ref=run_ref,
            replayed=True,
            received_at=record.recorded_at if record is not None else None,
        )

    def resume(self, task_ref: BackendRef, run_ref: BackendRef, run_input: dict) -> str | None:
        """Re-drive a non-terminal durable task after a restart (replay path).

        Returns the run id when dispatched, or ``None`` when the serial slot
        is busy (the caller retries on the next tick) — recovery decisions
        per action come from the ledger inside the normal dispatch gate.
        """

        if self._durable is None or self._closing or self._lock.locked():
            return None
        state = RunState(
            run_id=run_ref.id,
            status="running",
            principal="host-recovery",
            domains=(),
            task_ref=task_ref,
            run_ref=run_ref,
        )
        state.action_hook = self._durable.hook_for(task_ref, run_ref)

        async def _runner() -> None:
            # The slot was free when resume checked (same loop turn, no await
            # between), so this acquire completes immediately; a same-turn
            # contender merely serializes behind the run in flight.
            # _execute's finally releases the slot, exactly like the submit
            # path — no double release here.
            await self._lock.acquire()
            self._runs[state.run_id] = state
            await self._execute(state.run_id, state, run_input, resume=True)

        try:
            task = asyncio.create_task(_runner())
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        except BaseException:
            raise
        return state.run_id

    def request_cancel(self, run_id: str, *, command_id: str | None = None) -> bool:
        """Request cancellation at the next tool boundary; never revoke a completed call."""
        state = self._runs[run_id]
        if state.status != "running":
            return False
        self._evidence.append("cancel", state.principal, run_id, "requested")
        state.cancel_requested = True
        state.cancel_command_id = command_id
        return True

    def begin_shutdown(self) -> None:
        """Stop admission immediately without promising durable recovery."""
        self._closing = True

    async def close(self) -> None:
        """Stop in-process tasks and retain an honest unknown-outcome record."""
        self.begin_shutdown()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        for state in self._runs.values():
            if state.status == "running":
                try:
                    self._evidence.append(
                        "execution",
                        state.principal,
                        state.run_id,
                        OUTCOME_FAILED,
                        {
                            "status": "unknown",
                            "reason": "shutdown before execution task started",
                        },
                    )
                except OSError:
                    logger.exception("Could not record shutdown of run %s", state.run_id)
                state.status = "unknown"
                state.error = "runner shut down; outcome is not established"
        if self._lock.locked():
            self._lock.release()

    async def wait_for(self, run_id: str, timeout: float = 60.0) -> RunState:
        for _ in range(int(timeout / 0.05)):
            state = self._runs.get(run_id)
            if state is not None and state.status != "running":
                return state
            await asyncio.sleep(0.05)
        state = self._runs.get(run_id)
        if state is None:
            raise KeyError(run_id)
        return state

    def wait_for_sync(self, run_id: str, timeout: float = 60.0) -> RunState:
        """Blocking variant for threads driving the server from outside the loop."""

        if self._loop is not None and self._loop.is_running():
            future = asyncio.run_coroutine_threadsafe(self.wait_for(run_id, timeout), self._loop)
            return future.result(timeout + 5)
        return asyncio.run(self.wait_for(run_id, timeout))

    def get_state(self, run_id: str) -> RunState | None:
        return self._runs.get(run_id)

    def _append_event(self, state: RunState, event: dict) -> None:
        if self._durable is not None and state.task_ref is not None and state.run_ref is not None:
            try:
                self._durable.emit_execution_event(
                    task_ref=state.task_ref,
                    run_ref=state.run_ref,
                    source_sequence=len(state.events) + 1,
                    event_type="tool_result" if event.get("type") == "tool_result" else "engine_event",
                    payload={"event": event},
                )
            except Exception:
                state.needs_reconciliation = True
                raise
        state.events.append(event)

    async def _execute(self, run_id: str, state: RunState, run_input: dict, *, resume: bool = False) -> None:
        try:
            if self._durable is not None and state.task_ref is not None:
                # RUNNING→RUNNING on resume is an absorbing no-op; a fresh
                # submission moves QUEUED→RUNNING here, never inside submit.
                self._durable.begin_run(state.task_ref)
            # Stable session id per run so replay-based recovery (restarts,
            # retries) derives identical action keys for the same logical
            # execution instead of fresh random identities.
            session_id = uuid.uuid5(uuid.NAMESPACE_URL, f"runner-run:{run_id}")
            try:

                def observe(event: dict) -> RuntimeEventDecision:
                    self._append_event(state, event)
                    return RuntimeEventDecision.CONTINUE

                result = await runtime_execution_service.execute(
                    RuntimeExecutionRequest(
                        runtime=self._assemble_runtime(state),
                        session_id=session_id,
                        initial_input={"input": run_input},
                        observer=observe,
                        should_cancel=lambda: state.cancel_requested,
                    )
                )
                final_status = result.state.value
                if result.state.value == "failed":
                    state.error = "execution failed; inspect local service logs"
                    logger.warning("Runner execution %s failed: %s", run_id, result.error)
            except RuntimeScheduleCancelledError:
                final_status = "unknown"
                state.error = "runner shut down; outcome is not established"
            result_ref = f"runs/{run_id}/artifacts" if final_status == "succeeded" else None
            self._evidence.append(
                "execution",
                state.principal,
                run_id,
                "ok" if final_status == "succeeded" else OUTCOME_FAILED,
                {
                    "status": final_status,
                    "model_source": self._profile.config.model_backend,
                    "result_ref": result_ref,
                },
            )
            state.result_ref = result_ref
            state.status = final_status
        except Exception:
            logger.exception("Runner execution %s could not retain evidence", run_id)
            state.status = "unknown"
            state.result_ref = None
            state.error = "local evidence write failed; outcome requires inspection"
        finally:
            if self._durable is not None and state.task_ref is not None:
                hook_failed = getattr(state.action_hook, "has_failed_outcomes", False)
                try:
                    record = self._durable.finish_run(
                        state.task_ref,
                        state.status,
                        needs_reconciliation=state.needs_reconciliation or bool(hook_failed),
                    )
                    state.status = record.lifecycle_state.value
                except Exception:
                    logger.exception("Could not persist final task state for %s", run_id)
                    state.status = "unknown"
                if state.cancel_command_id is not None:
                    # Cancel receipts converge only on actual effect: applied
                    # when the cooperative boundary took hold, rejected when
                    # the run had already finished another way.
                    try:
                        if state.status == "cancelled":
                            self._durable.cancel_applied(state.cancel_command_id)
                        else:
                            self._durable.cancel_rejected(state.cancel_command_id)
                    except Exception:
                        logger.exception("Could not converge cancel command %s", state.cancel_command_id)
            self._lock.release()

    def record_tool_result(self, state: RunState, tool_name: str, outcome: dict) -> None:
        self._append_event(state, {"type": "tool_result", "tool": tool_name, "outcome": outcome})

    def _assemble_runtime(self, state: RunState):
        from hecate_runtime.pregel import PregelRuntime

        return PregelRuntime(self._graph, _FanOutWorker(self, state), self._checkpoint_store)


class _FanOutWorker(Worker):
    """Routes each node to the model or read-tool worker by node id."""

    def __init__(self, engine: ExecutionEngine, state: RunState) -> None:
        super().__init__()
        self._engine = engine
        self._state = state

    async def execute(
        self, node_id: str, node_config: dict, channel_snapshot: dict, execution_context: dict | None = None
    ) -> WorkerResult:
        if self._state.cancel_requested:
            raise CooperativeCancellationError
        if node_id == "model":
            worker: Worker = ModelNodeWorker(self._engine._profile)
        else:
            worker = ReadToolNodeWorker(
                lambda arguments, _tool=node_config["tool_name"], _state=self._state: self._engine._dispatch_tool(
                    _state, _tool, arguments
                )
            )
        return await worker.execute(node_id, node_config, channel_snapshot, execution_context)


def _now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(tz=UTC).isoformat()
