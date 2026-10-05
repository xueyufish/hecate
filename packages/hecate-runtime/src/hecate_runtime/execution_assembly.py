"""Shared execution assembly — graph compile, worker construction, runtime wiring.

Platform-agnostic extraction of the assembly half of the execution chain
(plan step5a): given an already-resolved graph config and explicit inputs,
this module compiles the graph, builds the production worker bundle, and
wires ``PregelRuntime``. The platform adapter (``WorkflowExecutionService``)
owns everything that touches definitions or persistence: Agent/Workflow ORM
resolution, template building, skill loading, and session/evidence
persistence. This module therefore imports nothing from ``hecate.studio``,
``hecate.models``, or SQLAlchemy — the layering tests pin that boundary.

Two opaque parameters cross the boundary by design:

- ``agent_state`` is a studio ``AgentState`` instance; the assembly forwards
  it into the engine's initial input without inspecting it.
- ``environment`` is a sandbox environment object from the wired environment
  manager; the assembly forwards it to the runtime and offloader.

Both are typed ``Any`` here so the runtime domain never imports studio.

Single-event-loop note: the assembled ``PregelRuntime`` executes on the
caller's running loop; there is no internal thread or loop handoff.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any

from hecate_runtime.checkpoint import InMemoryCheckpointStore
from hecate_runtime.compaction import CompactionSummarizer
from hecate_runtime.compiler import GraphCompiler
from hecate_runtime.context import PriorityContextEngine
from hecate_runtime.evidence import EvidenceTracker
from hecate_runtime.pregel import PregelRuntime
from hecate_runtime.session_state import SessionStateStore
from hecate_runtime.session_state_materializer import SessionStateMaterializer
from hecate_runtime.workers.agent_worker import AgentWorker
from hecate_runtime.workers.condition_worker import ConditionWorker
from hecate_runtime.workers.controller_worker import ControllerWorker
from hecate_runtime.workers.knowledge_worker import KnowledgeWorker
from hecate_runtime.workers.llm_worker import LLMWorker
from hecate_runtime.workers.suggestion_worker import SuggestionWorker
from hecate_runtime.workers.tool_worker import ToolWorker
from hecate_runtime.workers.variable_set_worker import VariableSetWorker

__all__ = [
    "AssembledExecution",
    "CompositeWorker",
    "PortCompactionSummarizer",
    "WorkerDependencies",
    "assemble_execution",
    "create_composite_worker",
]


class PortCompactionSummarizer(CompactionSummarizer):
    """Produces the structured compaction summary via the runtime port.

    Moved here from the platform service (step5a): the summarizer only needs
    ``port.llm_invoke``, so it belongs next to the assembly that wires it into
    the context chain factory. The summarizer prompt is isolated from the
    tenant conversation namespace and rides the routing configuration of the
    underlying port; route pinning and cache-namespace isolation never leak
    into callers (which only see the :class:`CompactionSummarizer` ABC).
    """

    _PROMPT = (
        "You are a conversation compaction engine. Summarize the conversation segment below so an "
        "agent can continue the work with no other history. Respond with ONLY a JSON object "
        "(no markdown fences) with exactly these keys:\n"
        '- "objective": what the user is trying to accomplish (string)\n'
        '- "key_decisions": important decisions, constraints and fixed choices so far (string[])\n'
        '- "current_state": what has been done and what is in progress (string)\n'
        '- "next_steps": concrete pending actions (string[])\n'
        '- "critical_context": data, identifiers, file paths, errors and gotchas that must survive '
        "verbatim (string)"
    )

    def __init__(self, port: Any) -> None:
        self._port = port

    async def summarize(self, messages: list[dict]) -> dict[str, Any]:
        request = [
            {"role": "system", "content": self._PROMPT},
            {"role": "user", "content": json.dumps(messages, ensure_ascii=False, default=str)},
        ]
        text = ""
        async for token in self._port.llm_invoke(messages=request, config={}):
            text += token
        return self._parse_summary(text)

    def _parse_summary(self, text: str) -> dict[str, Any]:
        """Parse the model response into the structured node; raise on any defect."""
        stripped = text.strip()
        if stripped.startswith("```"):
            stripped = stripped.strip("`").lstrip()
            if stripped.lower().startswith("json"):
                stripped = stripped[4:]
        start, end = stripped.find("{"), stripped.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("summarizer response contains no JSON object")
        parsed = json.loads(stripped[start : end + 1])
        if not isinstance(parsed, dict):
            raise ValueError("summarizer response is not a JSON object")
        return parsed


@dataclass(frozen=True)
class WorkerDependencies:
    """Engine-injected dependencies the worker bundle needs.

    Everything here is supplied by the caller (platform adapter or future
    standalone host): the runtime port, guardrail hooks, policy objects, and
    the composition-built controller evidence port. The assembly never
    constructs these itself.
    """

    port: Any
    pre_llm_hook: Any | None = None
    post_llm_hook: Any | None = None
    pre_tool_hook: Any | None = None
    post_tool_hook: Any | None = None
    middleware_chains: dict | None = None
    access_policy: Any | None = None
    approval_callback: Any | None = None
    tool_policy_rules: list | None = None
    event_store: Any | None = None
    denial_tracker: Any | None = None
    suggestion_service: Any | None = None
    controller_evidence_port: Any | None = None
    action_hook: Any | None = None


class CompositeWorker:
    """Routes node execution to the appropriate Worker based on NodeType.

    This composite pattern allows PregelRuntime to use a single Worker
    instance that internally delegates to the correct specialized Worker.
    """

    def __init__(
        self,
        llm_worker: LLMWorker,
        tool_worker: ToolWorker,
        condition_worker: ConditionWorker,
        agent_worker: AgentWorker,
        knowledge_worker: KnowledgeWorker,
        suggestion_worker: SuggestionWorker,
        variable_worker: VariableSetWorker,
        controller_worker: Any | None = None,
    ) -> None:
        self._llm = llm_worker
        self._tool = tool_worker
        self._condition = condition_worker
        self._agent = agent_worker
        self._knowledge = knowledge_worker
        self._suggestion = suggestion_worker
        self._variable = variable_worker
        self._controller = controller_worker
        self._workers_by_type: dict[str, Any] = {
            "conversation": self._llm,
            "tool-call": self._tool,
            "condition": self._condition,
            "agent": self._agent,
            "knowledge-retrieval": self._knowledge,
            "suggestion": self._suggestion,
            "variable-set": self._variable,
            "controller": self._controller,
        }

    def _get_worker(self, node_type_value: str) -> Any:
        return self._workers_by_type.get(node_type_value, self._llm)

    async def execute(
        self, node_id: str, node_config: dict, channel_snapshot: dict, execution_context: dict | None = None
    ) -> Any:
        """Delegate to the appropriate worker based on node_type in config."""
        node_type = node_config.get("_node_type", "conversation")
        worker = self._get_worker(node_type)
        return await worker.execute(node_id, node_config, channel_snapshot, execution_context=execution_context)

    async def execute_stream(
        self, node_id: str, node_config: dict, channel_snapshot: dict, execution_context: dict | None = None
    ) -> AsyncGenerator:
        """Delegate streaming execution to the appropriate worker."""
        node_type = node_config.get("_node_type", "conversation")
        worker = self._get_worker(node_type)
        async for item in worker.execute_stream(
            node_id, node_config, channel_snapshot, execution_context=execution_context
        ):
            yield item


def create_composite_worker(
    deps: WorkerDependencies,
    tools: list[dict] | None = None,
    kb_ids: list[str] | None = None,
    agent_persona: str | None = None,
) -> CompositeWorker:
    """Build the production worker bundle with guardrail wiring.

    ``tools``/``kb_ids``/``agent_persona`` flow into the tool, knowledge, and
    suggestion workers respectively; the rest comes from ``deps``.
    """
    llm_worker = LLMWorker(
        port=deps.port,
        pre_llm_hook=deps.pre_llm_hook,
        post_llm_hook=deps.post_llm_hook,
        middleware_chains=deps.middleware_chains,
    )
    tool_worker = ToolWorker(
        port=deps.port,
        pre_tool_hook=deps.pre_tool_hook,
        post_tool_hook=deps.post_tool_hook,
        access_policy=deps.access_policy,
        approval_callback=deps.approval_callback,
        tool_rules=deps.tool_policy_rules,
        event_store=deps.event_store,
        middleware_chains=deps.middleware_chains,
        denial_tracker=deps.denial_tracker,
        action_hook=deps.action_hook,
    )
    agent_worker = AgentWorker(port=deps.port)
    knowledge_worker = KnowledgeWorker(port=deps.port)
    suggestion_worker = SuggestionWorker(
        suggestion_service=deps.suggestion_service,
    )
    condition_worker = ConditionWorker()
    variable_worker = VariableSetWorker()
    controller_worker = ControllerWorker(
        port=deps.port,
        evidence_port=deps.controller_evidence_port,
    )

    return CompositeWorker(
        llm_worker=llm_worker,
        tool_worker=tool_worker,
        condition_worker=condition_worker,
        agent_worker=agent_worker,
        knowledge_worker=knowledge_worker,
        suggestion_worker=suggestion_worker,
        variable_worker=variable_worker,
        controller_worker=controller_worker,
    )


@dataclass
class AssembledExecution:
    """Everything the engine needs to execute one assembled run."""

    runtime: PregelRuntime
    initial_input: dict[str, Any]
    evidence_tracker: EvidenceTracker
    execution_mode: str


def assemble_execution(
    *,
    graph_config: Any,
    execution_mode: str,
    messages: list[dict],
    session_id: uuid.UUID,
    agent_id: str | uuid.UUID | None,
    user_id: str | uuid.UUID | None,
    agent_state: Any,
    tools: list[dict] | None,
    kb_ids: list[str] | None,
    agent_persona: str | None,
    environment_root: str | None,
    environment: Any | None,
    max_iterations: int,
    deps: WorkerDependencies,
    context_chain_factory: Any,
    citation_provenance: Any | None,
    grounding_scoring: dict | None,
    session_state_store: SessionStateStore | None,
    context_offload_enabled: bool = False,
    context_offload_threshold_tokens: int | None = None,
) -> AssembledExecution:
    """Compile the graph, build workers, and wire ``PregelRuntime``.

    ``graph_config`` is an already-resolved template/DSL config — resolving
    one (chat/three-layer builders, workflow DSL parsing, ORM lookups) is the
    platform adapter's job. Same for ``agent_state`` and ``environment``:
    forwarded opaquely, never inspected.
    """
    compiler = GraphCompiler()
    compiled = compiler.compile(graph_config, execution_mode=execution_mode)

    # Inject node type info into configs for composite worker routing
    for _nid, ncfg in compiled.nodes.items():
        ncfg.config["_node_type"] = ncfg.type.value

    composite = create_composite_worker(deps, tools, kb_ids, agent_persona)

    initial_input: dict[str, Any] = {
        "messages": messages,
        "_session_id": str(session_id),
        "_agent_id": str(agent_id) if agent_id else "",
        "_user_id": str(user_id) if user_id else "",
        "_turn_index": 0,
        "_agent_state": agent_state,
    }
    if environment_root:
        initial_input["_environment_root"] = environment_root
    if kb_ids:
        initial_input["_kb_ids"] = kb_ids
    if tools:
        initial_input["_tools"] = tools

    initial_input["sys.execution_mode"] = execution_mode
    if execution_mode == "conversational":
        initial_input["sys.conversation_id"] = str(session_id)
        initial_input["sys.dialogue_count"] = 0

    checkpoint_store: Any = InMemoryCheckpointStore()
    if session_state_store is not None:
        tenant_uuid = uuid.UUID(str(user_id)) if user_id is not None else None
        captured_user_id = tenant_uuid

        def _tenant_provider() -> tuple[uuid.UUID, uuid.UUID] | None:
            if captured_user_id is None:
                return None
            return captured_user_id, captured_user_id

        checkpoint_store = SessionStateMaterializer(
            session_state_store=session_state_store,
            tenant_context_provider=_tenant_provider,
            event_store=deps.event_store,
        )

    context_offloader: Any | None = None
    if environment is not None and context_offload_enabled:
        from hecate_runtime.offloader import ContextOffloader

        context_offloader = ContextOffloader(
            environment=environment,
            threshold_tokens=context_offload_threshold_tokens,
        )

    evidence_tracker = EvidenceTracker(session_id=session_id)

    runtime = PregelRuntime(
        graph=compiled,
        worker=composite,
        checkpoint_store=checkpoint_store,
        event_store=deps.event_store,
        max_supersteps=max_iterations * 3 + 5,
        context_engine=PriorityContextEngine(),
        context_chain=context_chain_factory,
        citation_provenance=citation_provenance,
        grounding_scoring=grounding_scoring,
        context_offloader=context_offloader,
        environment=environment,
        evidence_tracker=evidence_tracker,
    )

    return AssembledExecution(
        runtime=runtime,
        initial_input=initial_input,
        evidence_tracker=evidence_tracker,
        execution_mode=execution_mode,
    )
