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
from hecate_runtime.checkpoint import InMemoryCheckpointStore
from hecate_runtime.pregel import PregelRuntime
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

from .evidence import OUTCOME_FAILED, EvidenceStore
from .profile import BUILTIN_TOOL_SCHEMAS, Profile

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
    status: str  # "running" | "succeeded" | "failed" | "cancelled"
    principal: str
    domains: tuple[str, ...]
    events: list[dict] = field(default_factory=list)
    result_ref: str | None = None
    error: str | None = None
    cancel_requested: bool = False


class ExecutionEngine:
    """Serial, in-process execution of the compiled preview graph."""

    def __init__(self, profile: Profile, evidence: EvidenceStore, tool_dispatch) -> None:
        # ``tool_dispatch`` signature: (tool_name, arguments, principal, domains).
        self._profile = profile
        self._evidence = evidence
        self._tool_dispatch = tool_dispatch
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
        """Run one tool with the run's server-verified identity scope."""

        if state.cancel_requested:
            raise _CooperativeCancelError
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
        self.record_tool_result(state, tool_name, outcome)
        return outcome

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
        return {**supported, **UNSUPPORTED_CAPABILITIES}

    async def submit(
        self, principal: str, run_input: dict, domains: tuple[str, ...] = ()
    ) -> tuple[str, RunState | None]:
        """Start a run; returns (run_id, None-in-flight-refusal) — serial queue.

        ``principal``/``domains`` come from the server-verified identity at
        the HTTP entry and travel with the run: tool dispatches carry exactly
        this scope, never any request-body self-reported claims.
        """

        if self._closing or self._lock.locked():
            return "", None
        self.validate_run_input(run_input)
        # Reserve admission before yielding to the execution task. Checking
        # the lock only in _execute silently queued concurrent submissions.
        await self._lock.acquire()
        run_id = uuid.uuid4().hex
        state = RunState(run_id=run_id, status="running", principal=principal, domains=tuple(domains))
        try:
            self._evidence.append(
                "submission", principal, run_id, "accepted", {"model_source": self._profile.config.model_backend}
            )
            self._runs[run_id] = state
            task = asyncio.create_task(self._execute(run_id, state, run_input))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        except BaseException:
            self._lock.release()
            raise
        return run_id, state

    def request_cancel(self, run_id: str) -> bool:
        """Request cancellation at the next tool boundary; never revoke a completed call."""
        state = self._runs[run_id]
        if state.status != "running":
            return False
        self._evidence.append("cancel", state.principal, run_id, "requested")
        state.cancel_requested = True
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
        state.events.append(event)

    async def _execute(self, run_id: str, state: RunState, run_input: dict) -> None:
        try:
            runtime = PregelRuntime(self._graph, _FanOutWorker(self, state), self._checkpoint_store)
            try:
                async for event in runtime.execute(uuid.uuid4(), initial_input={"input": run_input}):
                    self._append_event(state, event)
                final_status = "cancelled" if state.cancel_requested else "succeeded"
            except _CooperativeCancelError:
                final_status = "cancelled"
            except asyncio.CancelledError:
                final_status = "unknown"
                state.error = "runner shut down; outcome is not established"
            except Exception as exc:  # noqa: BLE001 - the run fails; the server stays up
                if state.cancel_requested:
                    final_status = "cancelled"
                else:
                    final_status = "failed"
                    state.error = "execution failed; inspect local service logs"
                    logger.warning("Runner execution %s failed", run_id, exc_info=exc)
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
            self._lock.release()

    def record_tool_result(self, state: RunState, tool_name: str, outcome: dict) -> None:
        self._append_event(state, {"type": "tool_result", "tool": tool_name, "outcome": outcome})


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
            raise _CooperativeCancelError
        if node_id == "model":
            worker: Worker = ModelNodeWorker(self._engine._profile)
        else:
            worker = ReadToolNodeWorker(
                lambda arguments, _tool=node_config["tool_name"], _state=self._state: self._engine._dispatch_tool(
                    _state, _tool, arguments
                )
            )
        return await worker.execute(node_id, node_config, channel_snapshot, execution_context)


class _CooperativeCancelError(Exception):
    """No further tools may start after an accepted cancellation request."""
