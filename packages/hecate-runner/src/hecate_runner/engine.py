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
import uuid
from dataclasses import dataclass, field

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

from .evidence import OUTCOME_FAILED, EvidenceStore
from .profile import Profile

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
    same payload shape; the configured endpoint is called by the server
    layer (keeps this worker sync-free) and its text is recorded in the
    run's evidence.
    """

    def __init__(self, tool_names: list[str]) -> None:
        super().__init__()
        self._tool_names = tool_names

    async def execute(
        self, node_id: str, node_config: dict, channel_snapshot: dict, execution_context: dict | None = None
    ) -> WorkerResult:
        prompt = (channel_snapshot.get("input") or {}).get("prompt", "")
        return WorkerResult(
            node_id=node_id,
            channel_updates={
                "plan": {
                    "tools": list(self._tool_names),
                    "prompt": prompt,
                    "model_source": node_config.get("model_source", "stub"),
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

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Record the loop runs are scheduled on (set by the server layer)."""

        self._loop = loop

    async def _dispatch_tool(self, state: RunState, tool_name: str, arguments: dict) -> dict:
        """Run one tool with the run's server-verified identity scope."""

        outcome = await self._tool_dispatch(tool_name, arguments, state.principal, list(state.domains))
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

        if self._lock.locked():
            return "", None
        run_id = uuid.uuid4().hex
        state = RunState(run_id=run_id, status="running", principal=principal, domains=tuple(domains))
        self._runs[run_id] = state
        asyncio.create_task(self._execute(run_id, state, run_input))
        return run_id, state

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
        async with self._lock:
            runtime = PregelRuntime(self._graph, _FanOutWorker(self, state), self._checkpoint_store)
            try:
                async for event in runtime.execute(uuid.uuid4(), initial_input={"input": run_input}):
                    self._append_event(state, event)
                state.status = "succeeded"
                state.result_ref = f"runs/{run_id}/artifacts"
                self._evidence.append(
                    "execution",
                    state.principal,
                    run_id,
                    OUTCOME_FAILED if state.error else "ok",
                    {
                        "status": state.status,
                        "model_source": self._profile.config.model_backend,
                    },
                )
            except Exception as exc:  # noqa: BLE001 - the run fails; the server stays up
                state.status = "failed"
                state.error = str(exc)
                self._evidence.append("execution", state.principal, run_id, OUTCOME_FAILED, {"error": str(exc)})

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
        if node_id == "model":
            worker: Worker = ModelNodeWorker(list(self._engine._profile.config.tool_allowlist))
        else:
            worker = ReadToolNodeWorker(
                lambda arguments, _tool=node_config["tool_name"], _state=self._state: self._engine._dispatch_tool(
                    _state, _tool, arguments
                )
            )
        return await worker.execute(node_id, node_config, channel_snapshot, execution_context)
