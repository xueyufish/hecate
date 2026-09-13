"""Unified execution service for all agent modes.

Provides a single entry point that accepts an agent mode (chat, three_layer,
workflow), resolves the appropriate graph template, compiles it, instantiates
production Workers with Guardrail Hooks, and executes via PregelRuntime.

Both the chat API and workflow API call this service, replacing the direct
ConversationService orchestration.
"""

from __future__ import annotations

import asyncio
import logging
import random
import uuid
from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.workflow import WorkflowModel, WorkflowVersionModel
from hecate.runtime.checkpoint import InMemoryCheckpointStore
from hecate.runtime.compiler import GraphCompiler
from hecate.runtime.context import PriorityContextEngine
from hecate.runtime.eventstore import CURRENT_LOG_SCHEMA_VERSION, Event, EventStore, EventType
from hecate.runtime.evidence import EvidenceTracker
from hecate.runtime.guardrail import (
    PostLLMHook,
    PostToolHook,
    PreLLMHook,
    PreToolHook,
)
from hecate.runtime.pregel import PregelRuntime
from hecate.runtime.session_state import SessionState, SessionStateConflictError, SessionStateStore
from hecate.runtime.types import StreamMode
from hecate.runtime.workers.agent_worker import AgentWorker
from hecate.runtime.workers.condition_worker import ConditionWorker
from hecate.runtime.workers.knowledge_worker import KnowledgeWorker
from hecate.runtime.workers.llm_worker import LLMWorker
from hecate.runtime.workers.suggestion_worker import SuggestionWorker
from hecate.runtime.workers.tool_worker import ToolWorker
from hecate.runtime.workers.variable_set_worker import VariableSetWorker
from hecate.studio.state.state import AgentState
from hecate.studio.workflows.graph_dsl import parse_graph

logger = logging.getLogger(__name__)

# Jitter retry budget for lock acquisition when multiple replicas contend on
# the same session key (Horizontal-Scaling validation). Exposed as module
# constants so tests can tighten the sleep bounds.
_LOCK_MAX_RETRIES = 3
_LOCK_RETRY_MIN_S = 0.02
_LOCK_RETRY_MAX_S = 0.150


class TurnInFlightError(Exception):
    """update_state refused: the session has an in-flight turn (HTTP 409)."""


class InvalidStateChannelError(ValueError):
    """update_state/fork target channel is not loggable graph state (HTTP 422)."""


class InvalidForkAnchorError(ValueError):
    """fork anchor is unusable: bad version, empty log, or missing session (HTTP 422/404)."""


async def _sync_event_position(
    state: SessionState, event_store: EventStore | None, session_id: uuid.UUID
) -> SessionState:
    """Sync ``SessionState.event_position`` with ``event_store.get_version(session_id)``.

    Returns a new ``SessionState`` instance via ``model_copy`` when the store
    is provided; returns the original instance unchanged when ``event_store``
    is ``None`` (no-op, backward compatible).
    """
    if event_store is None:
        return state
    position = await event_store.get_version(session_id)
    return state.model_copy(update={"event_position": position})


class _CompositeWorker:
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


class WorkflowExecutionService:
    """Unified execution entry point for all agent modes.

        Accepts execution parameters (mode, messages, model, tools, etc.),
    resolves the appropriate graph template, compiles it, creates production
        Workers with Guardrail Hooks, and runs PregelRuntime.
    """

    def __init__(
        self,
        port: Any,
        db: AsyncSession | None = None,
        suggestion_service: Any = None,
        pre_llm_hook: PreLLMHook | None = None,
        post_llm_hook: PostLLMHook | None = None,
        pre_tool_hook: PreToolHook | None = None,
        post_tool_hook: PostToolHook | None = None,
        environment_manager: Any = None,
        checkpoint_store: SessionStateStore | None = None,
        event_store: EventStore | None = None,
        access_policy: Any = None,
        approval_callback: Any = None,
        tool_policy_rules: list | None = None,
    ) -> None:
        self._port = port
        self._db = db
        self._suggestion_service = suggestion_service
        self._pre_llm_hook = pre_llm_hook
        self._post_llm_hook = post_llm_hook
        self._pre_tool_hook = pre_tool_hook
        self._post_tool_hook = post_tool_hook
        self._environment_manager = environment_manager
        # T0.2 (guardrail-upgrade-trio): production wiring for tool gating.
        # When rules are supplied, build a fresh ToolAccessPolicy so each
        # WorkflowExecutionService instance carries its agent's resolved
        # workspace + agent rule set. The chat path (api/v1/chat.py) builds
        # these via ``assemble_guardrails``.
        self._tool_policy_rules = tool_policy_rules or []
        if access_policy is not None:
            self._access_policy = access_policy
        elif self._tool_policy_rules:
            from hecate.runtime.tool_access import ToolAccessPolicy

            self._access_policy = ToolAccessPolicy()
        else:
            self._access_policy = None
        self._approval_callback = approval_callback
        self._checkpoint_store = checkpoint_store
        self._event_store = event_store

    async def execute(
        self,
        agent_mode: str = "chat",
        messages: list[dict] | None = None,
        model: str = "gpt-4o",
        system_prompt: str = "You are a helpful assistant.",
        tools: list[dict] | None = None,
        stream: bool = False,
        session_id: str | uuid.UUID | None = None,
        agent_id: str | uuid.UUID | None = None,
        user_id: str | uuid.UUID | None = None,
        kb_ids: list[str] | None = None,
        enable_suggestions: bool = False,
        generate_opening: bool = False,
        agent_persona: str | None = None,
        workflow_id: uuid.UUID | None = None,
        max_iterations: int = 10,
        channel_id: str | None = None,
        channel_capabilities: object | None = None,
        workspace_id: uuid.UUID | None = None,
    ) -> dict[str, Any] | AsyncGenerator[dict[str, Any], None]:
        """Execute an agent through the unified graph engine.

        Args:
            agent_mode: "chat", "three_layer", or "workflow".
            messages: Conversation messages.
            model: LLM model identifier.
            system_prompt: System prompt for the agent.
            tools: Available tools for the agent.
            stream: Whether to stream the response.
            session_id: Session identifier for evidence tracking.
            agent_id: Agent identifier for memory operations.
            user_id: User identifier for memory retrieval.
            kb_ids: Knowledge base IDs for RAG.
            enable_suggestions: Whether to generate follow-up suggestions.
            generate_opening: Whether to generate opening remarks.
            agent_persona: Agent persona description.
            workflow_id: Workflow ID (required for workflow mode).
            max_iterations: Maximum tool-calling iterations.
            channel_id: Optional originating channel identifier (e.g.,
                ``"feishu"``, ``"slack"``). When set, downstream workers
                may adjust response style for the channel. ``None`` for
                the OpenAI-compatible API path (existing behavior).
            channel_capabilities: Optional :class:`ChannelCapabilities`
                describing what the channel supports. Forwarded to
                downstream rendering hooks. ``None`` for the API path.
            workspace_id: Optional workspace scope for tenant-aware
                filtering. ``None`` for the OpenAI-compatible API path.

        Returns:
            Response dict (non-streaming) or AsyncGenerator (streaming).
        """
        from hecate.studio.workflows.templates import build_chat_graph, build_three_layer_graph

        if messages is None:
            messages = []

        if session_id is None:
            session_id = uuid.uuid4()
        if isinstance(session_id, str):
            session_id = uuid.UUID(session_id)

        if agent_id and self._db is not None:
            from hecate.models.agent import AgentModel
            from hecate.tools.skill.loader import SkillLoader

            agent_uuid = agent_id if isinstance(agent_id, uuid.UUID) else uuid.UUID(str(agent_id))
            result = await self._db.execute(
                select(AgentModel).where(
                    AgentModel.id == agent_uuid,
                    ~AgentModel.deleted,
                )
            )
            agent = result.scalar_one_or_none()
            if agent is not None:
                persona = agent.persona or "You are a helpful assistant."
                loader = SkillLoader(self._db)
                skills_block = await loader.format_skills(
                    agent_id=agent_uuid,
                    workspace_id=agent.workspace_id,
                )
                system_prompt = f"{persona}\n\n{skills_block}" if skills_block else persona

        # Get or create environment for agent
        environment_root = None
        agent_env = None
        if agent_id and self._environment_manager:
            agent_str = str(agent_id) if isinstance(agent_id, uuid.UUID) else agent_id
            agent_env = await self._environment_manager.get_or_create(agent_str)
            environment_root = str(agent_env.root_path)

        # Build AgentState (13.4a-7: no longer loaded from the removed
        # AgentStateStore; SessionStateStore owns cross-call persistence
        # via the wired checkpoint_store).
        agent_state = AgentState(session_id=session_id, agent_id=uuid.uuid4())
        if environment_root:
            agent_state.environment_root = environment_root

        # Resolve graph config based on mode
        execution_mode = "conversational"
        if agent_mode == "chat":
            graph_config = build_chat_graph(
                model=model,
                system_prompt=system_prompt,
                enable_suggestions=enable_suggestions or generate_opening,
                generate_opening=generate_opening,
                tools=tools,
            )
        elif agent_mode == "three_layer":
            graph_config = build_three_layer_graph(
                planner_model=model,
            )
        elif agent_mode == "workflow":
            graph_config = await self._load_workflow_graph(workflow_id)
            execution_mode = await self._load_workflow_mode(workflow_id) if workflow_id else "conversational"
        else:
            msg = f"Unknown agent mode: {agent_mode}"
            raise ValueError(msg)

        # Compile graph
        compiler = GraphCompiler()
        compiled = compiler.compile(graph_config, execution_mode=execution_mode)

        # Inject node type info into configs for composite worker routing
        for _nid, ncfg in compiled.nodes.items():
            ncfg.config["_node_type"] = ncfg.type.value

        # Create Workers
        composite = self._create_composite_worker(tools, kb_ids, agent_persona)

        # Build initial input
        initial_input = {
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

        # Execute
        checkpoint_store = InMemoryCheckpointStore()
        if self._checkpoint_store is not None:
            from hecate.runtime.session_state_materializer import (
                SessionStateMaterializer,
            )

            tenant_uuid = uuid.UUID(str(user_id)) if user_id is not None else None
            captured_user_id = tenant_uuid

            def _tenant_provider() -> tuple[uuid.UUID, uuid.UUID] | None:
                if captured_user_id is None:
                    return None
                return captured_user_id, captured_user_id

            checkpoint_store = SessionStateMaterializer(
                session_state_store=self._checkpoint_store,
                tenant_context_provider=_tenant_provider,
                event_store=self._event_store,
            )

        context_offloader: ContextOffloader | None = None
        if agent_env is not None and self._environment_manager:
            from hecate.core.config import settings

            if settings.CONTEXT_OFFLOAD_ENABLED:
                from hecate.runtime.offloader import ContextOffloader

                context_offloader = ContextOffloader(
                    environment=agent_env,
                    threshold_tokens=settings.CONTEXT_OFFLOAD_THRESHOLD_TOKENS,
                )

        evidence_tracker = EvidenceTracker(session_id=session_id)

        runtime = PregelRuntime(
            graph=compiled,
            worker=composite,
            checkpoint_store=checkpoint_store,
            max_supersteps=max_iterations * 3 + 5,
            context_engine=PriorityContextEngine(),
            context_offloader=context_offloader,
            environment=agent_env,
            evidence_tracker=evidence_tracker,
        )

        stream_mode = StreamMode.MESSAGES if stream else StreamMode.VALUES

        if stream:
            return self._persist_evidence(
                evidence_tracker,
                self._stream_execute(runtime, session_id, initial_input, stream_mode, execution_mode, agent_state),
            )

        response = await self._non_stream_execute(runtime, session_id, initial_input, execution_mode)
        await self._persist_evidence_rows(evidence_tracker)
        # Save AgentState after non-streaming execution (single atomic snapshot)
        if agent_state is not None and user_id is not None:
            await self._persist_session_state(
                agent_state=agent_state,
                session_id=session_id,
                agent_id=agent_state.agent_id,
                org_id=user_id,  # chat path does not thread a separate org_id
                user_id=user_id,
            )
        return response

    async def _mark_session_interrupted(self, session_id: uuid.UUID) -> None:
        """Event-driven session status wiring (1.3.19 requirement, delivered with
        1.3.21①): flip the session row to ``interrupted`` when the engine yields
        an interrupt event, so the resume endpoint's state gate can pass. Resume
        back to ``active`` is owned by the resume endpoint. Best-effort: the
        event log stays the source of truth for interrupt state, so a missing
        row or DB hiccup is logged, not raised.
        """
        if self._db is None:
            return
        try:
            from sqlalchemy import update

            from hecate.models.session import SessionModel

            await self._db.execute(
                update(SessionModel).where(SessionModel.id == session_id).values(status="interrupted")
            )
            await self._db.flush()
        except Exception:
            logger.warning("failed_to_mark_session_interrupted", exc_info=True, extra={"session_id": str(session_id)})

    async def _non_stream_execute(
        self,
        runtime: PregelRuntime,
        session_id: uuid.UUID,
        initial_input: dict,
        execution_mode: str = "conversational",
    ) -> dict[str, Any]:
        """Execute non-streaming and return final response dict.

        Args:
            runtime: The configured PregelRuntime.
            session_id: Session identifier.
            initial_input: Channel initial values.
            execution_mode: Execution mode (conversational or task).

        Returns:
            Response dict with content, model, usage, etc.
        """
        final_state: dict[str, Any] = {}
        async for event in runtime.execute(
            session_id=session_id,
            initial_input=initial_input,
            stream_mode=StreamMode.VALUES,
            execution_mode=execution_mode,
        ):
            if event.get("type") == "values":
                final_state = event.get("state", {})
            elif event.get("type") == "interrupt" and execution_mode == "conversational":
                await self._mark_session_interrupted(session_id)

        messages = final_state.get("messages", [])
        content = ""
        if messages:
            last_msg = messages[-1] if isinstance(messages, list) else messages
            if isinstance(last_msg, dict):
                content = last_msg.get("content", "")

        suggested_questions = final_state.get("suggested_questions")
        result: dict[str, Any] = {
            "content": content,
            "model": initial_input.get("model", "gpt-4o"),
            "usage": {},
            "finish_reason": "stop",
        }
        if suggested_questions:
            result["suggested_questions"] = suggested_questions

        return result

    async def _stream_execute(
        self,
        runtime: PregelRuntime,
        session_id: uuid.UUID,
        initial_input: dict,
        stream_mode: StreamMode,
        execution_mode: str = "conversational",
        agent_state: AgentState | None = None,
        org_id: str | uuid.UUID | None = None,
        user_id: str | uuid.UUID | None = None,
        agent_id: str | uuid.UUID | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Execute streaming and yield events.

        Persists ``agent_state`` via the wired ``SessionStateStore`` exactly
        once when the generator exhausts normally. On client disconnect /
        mid-stream exception, best-effort persist is attempted (failures are
        swallowed) and the original exception is re-raised. The legacy
        ``self._state_store`` save path is intentionally not used.

        Args:
            runtime: The configured PregelRuntime.
            session_id: Session identifier.
            initial_input: Channel initial values.
            stream_mode: Stream mode for PregelRuntime.
            execution_mode: Execution mode (conversational or task).
            agent_state: AgentState to save after stream completes.
            org_id: Tenant identifier for the wired ``SessionStateStore``.
            user_id: User identifier for the wired ``SessionStateStore``.
            agent_id: Agent identifier for the wired ``SessionStateStore``.

        Yields:
            Event dicts from PregelRuntime.
        """
        try:
            async for event in runtime.execute(
                session_id=session_id,
                initial_input=initial_input,
                stream_mode=stream_mode,
                execution_mode=execution_mode,
            ):
                if event.get("type") == "interrupt" and execution_mode == "conversational":
                    await self._mark_session_interrupted(session_id)
                yield event
        except BaseException as exc:
            # Best-effort save on disconnect; swallow save failures so the
            # original exception remains the one that surfaces.
            if agent_state is not None:
                try:
                    await self._persist_session_state(
                        agent_state=agent_state,
                        session_id=session_id,
                        agent_id=agent_id or agent_state.agent_id,
                        org_id=org_id,
                        user_id=user_id,
                    )
                except Exception:  # noqa: BLE001 - best-effort, never masks original
                    logger.warning("session_state_persist_failed", exc_info=True)
            raise exc
        # Save AgentState exactly once after streaming completes normally.
        if agent_state is not None:
            await self._persist_session_state(
                agent_state=agent_state,
                session_id=session_id,
                agent_id=agent_id or agent_state.agent_id,
                org_id=org_id,
                user_id=user_id,
            )

    async def _persist_evidence(
        self,
        tracker: EvidenceTracker,
        event_stream: AsyncGenerator[dict[str, Any], None],
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Yield streaming events through, then persist evidence afterwards."""
        async for event in event_stream:
            yield event
        await self._persist_evidence_rows(tracker)

    async def _persist_evidence_rows(self, tracker: EvidenceTracker | None) -> None:
        """Persist captured evidence (4.8) as ``EvidenceModel`` rows.

        Best-effort: evidence persistence must never fail the run. Rows are
        written on the request-scoped session owned by the caller; when no
        session is available the snapshot is dropped (tracker contract).
        """
        if tracker is None or len(tracker) == 0 or self._db is None:
            return
        try:
            from hecate.models.evidence import EvidenceModel

            session_uuid: uuid.UUID | None
            try:
                session_uuid = uuid.UUID(tracker.session_id)
            except ValueError:
                session_uuid = None
            if session_uuid is None:
                return
            for record in tracker.snapshot():
                self._db.add(
                    EvidenceModel(
                        session_id=session_uuid,
                        tool_name=record.tool_name,
                        tool_arguments=record.tool_arguments,
                        raw_content=record.raw_content,
                        normalized_content=record.normalized_content,
                        is_error=record.is_error,
                        importance=record.importance,
                        source_type=record.source_type,
                        provenance=record.provenance,
                    )
                )
            await self._db.flush()
        except Exception:
            logger.warning("Evidence persistence failed — dropping snapshot", exc_info=True)

    async def _persist_session_state(
        self,
        *,
        agent_state: AgentState,
        session_id: uuid.UUID,
        agent_id: str | uuid.UUID | None,
        org_id: str | uuid.UUID | None,
        user_id: str | uuid.UUID | None,
    ) -> None:
        """Persist ``agent_state`` as a ``SessionState`` snapshot (best-effort).

        Builds a ``SessionState`` whose ``agent_state`` field is the JSON
        serialization of ``AgentState.model_dump(mode="json")``, synchronizes
        ``event_position`` with the wired ``EventStore``, and writes it via the
        wired ``SessionStateStore`` under the ``(org_id, user_id, session_id)``
        tenant-scoped key.

        When no ``checkpoint_store`` is wired, this is a no-op (best-effort
        persistence). The legacy ``AgentStateStore`` fallback was removed in
        13.4a-7. Non-lock save failures are swallowed. Lock-acquisition
        ``SessionStateConflictError``
        propagates so the requesting turn fails fast rather than splitting state
        across two stores.

        Args:
            agent_state: The typed ``AgentState`` to persist.
            session_id: Session identifier.
            agent_id: Agent identifier.
            org_id: Tenant identifier.
            user_id: User identifier.
        """
        if self._checkpoint_store is None:
            return

        org_uuid = org_id if isinstance(org_id, uuid.UUID) else uuid.UUID(str(org_id)) if org_id else None
        user_uuid = user_id if isinstance(user_id, uuid.UUID) else uuid.UUID(str(user_id)) if user_id else None
        if org_uuid is None or user_uuid is None:
            return

        snapshot = SessionState(agent_state=agent_state.model_dump(mode="json"))
        snapshot = await _sync_event_position(snapshot, self._event_store, session_id)

        # Lock acquisition retries with jitter; lock contention fails fast after
        # the retry budget is exhausted. Non-lock save failures are swallowed.
        for attempt in range(_LOCK_MAX_RETRIES):
            try:
                async with self._checkpoint_store.acquire_session_lock(org_uuid, user_uuid, session_id):
                    await self._checkpoint_store.save(org_uuid, user_uuid, session_id, snapshot)
                return
            except SessionStateConflictError:
                if attempt >= _LOCK_MAX_RETRIES - 1:
                    raise
                await asyncio.sleep(random.uniform(_LOCK_RETRY_MIN_S, _LOCK_RETRY_MAX_S))  # noqa: S311 - jitter, not crypto
            except Exception:  # noqa: BLE001 - best-effort persistence
                logger.warning("session_state_persist_failed", exc_info=True)
                return

    def _create_composite_worker(
        self,
        tools: list[dict] | None = None,
        kb_ids: list[str] | None = None,
        agent_persona: str | None = None,
    ) -> _CompositeWorker:
        """Create a composite worker with all production Workers.

        Args:
            tools: Available tools for ToolWorker.
            kb_ids: Knowledge base IDs for KnowledgeWorker.
            agent_persona: Agent persona for SuggestionWorker.

        Returns:
            CompositeWorker that routes to specialized Workers.
        """
        llm_worker = LLMWorker(
            port=self._port,
            pre_llm_hook=self._pre_llm_hook,
            post_llm_hook=self._post_llm_hook,
        )
        tool_worker = ToolWorker(
            port=self._port,
            pre_tool_hook=self._pre_tool_hook,
            post_tool_hook=self._post_tool_hook,
            access_policy=self._access_policy,
            approval_callback=self._approval_callback,
            tool_rules=self._tool_policy_rules,
        )
        agent_worker = AgentWorker(port=self._port)
        knowledge_worker = KnowledgeWorker(port=self._port)
        suggestion_worker = SuggestionWorker(
            suggestion_service=self._suggestion_service,
        )
        condition_worker = ConditionWorker()
        variable_worker = VariableSetWorker()
        # 2.6a controller — evidence provider wired from the composition
        # root; the runtime worker itself stays studio-free.
        from hecate.core.composition.intent_evidence import create_intent_evidence_port
        from hecate.runtime.workers.controller_worker import ControllerWorker

        controller_worker = ControllerWorker(
            port=self._port,
            evidence_port=create_intent_evidence_port(),
        )

        return _CompositeWorker(
            llm_worker=llm_worker,
            tool_worker=tool_worker,
            condition_worker=condition_worker,
            agent_worker=agent_worker,
            knowledge_worker=knowledge_worker,
            suggestion_worker=suggestion_worker,
            variable_worker=variable_worker,
            controller_worker=controller_worker,
        )

    async def _load_workflow_mode(self, workflow_id: uuid.UUID) -> str:
        """Load execution_mode from the WorkflowModel."""
        if self._db is None:
            return "conversational"
        result = await self._db.execute(
            select(WorkflowModel).where(
                WorkflowModel.id == workflow_id,
                ~WorkflowModel.deleted,
            )
        )
        workflow = result.scalar_one_or_none()
        return workflow.execution_mode if workflow else "conversational"

    async def _load_workflow_graph(self, workflow_id: uuid.UUID | None) -> Any:
        """Load a workflow's graph from the database.

        Args:
            workflow_id: The workflow to load.

        Returns:
            Parsed GraphConfig.

        Raises:
            ValueError: If workflow_id is None or workflow has no version.
        """
        if workflow_id is None:
            msg = "workflow_id is required for workflow mode"
            raise ValueError(msg)
        if self._db is None:
            msg = "Database session required for workflow mode"
            raise ValueError(msg)

        result = await self._db.execute(
            select(WorkflowVersionModel)
            .where(
                WorkflowVersionModel.workflow_id == workflow_id,
                ~WorkflowVersionModel.deleted,
            )
            .order_by(WorkflowVersionModel.version.desc())
            .limit(1)
        )
        version = result.scalar_one_or_none()
        if version is None:
            msg = f"Workflow {workflow_id} has no compiled version"
            raise ValueError(msg)

        return parse_graph(version.graph_dsl)

    # ------------------------------------------------------------------
    # 1.3.21② time-travel: commit points, update_state, fork
    # ------------------------------------------------------------------

    async def list_commit_points(self, session_id: uuid.UUID, limit: int = 20) -> list[dict[str, Any]]:
        """List log-derived resumable anchors for a session, newest first.

        The anchor set is derived purely from the event log (STEP_END /
        INTERRUPT / FORK events); the checkpoint cache is never consulted.

        STEP_END anchors from a fan-out superstep carry an extra
        ``fanout`` segment (1.3.21③) so the 8.20 / 6.26-E5 consumers can
        locate fan-out windows without re-folding the log.
        """
        if self._event_store is None:
            return []
        events = await self._event_store.get_events(session_id)
        anchors: list[dict[str, Any]] = []
        for event in reversed(events):
            etype = event.event_type.value if hasattr(event.event_type, "value") else str(event.event_type)
            if etype in {EventType.STEP_END.value, EventType.INTERRUPT.value, EventType.FORK.value}:
                anchor = {
                    "log_version": event.version,
                    "kind": etype,
                    "node_id": event.node_id,
                    "superstep": event.superstep,
                    "created_at": event.timestamp.isoformat() if event.timestamp else None,
                    "source": (event.payload or {}).get("source"),
                }
                fanout_segments = (event.payload or {}).get("fanout")
                if fanout_segments:
                    anchor["fanout"] = {
                        "packet_count": sum(len(seg.get("sub_channels") or {}) for seg in fanout_segments),
                        "sources": [seg.get("source") for seg in fanout_segments],
                    }
                anchors.append(anchor)
                if len(anchors) >= limit:
                    break
        return anchors

    async def update_state(
        self,
        session_id: uuid.UUID,
        values: dict[str, Any],
        actor: str = "api",
        model: str | None = None,
        workflow_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        """Append-recorded state mutation (update_state equivalent).

        Gate: an unclosed TURN without a subsequent ERROR event means an
        execution is in flight — refuse. The mutation batch is WAL-ordered
        (append before apply-via-fold) and closed by a
        ``STEP_END(source="update_state")`` commit point.
        """
        if self._event_store is None:
            msg = "update_state requires a wired EventStore"
            raise ValueError(msg)
        events = await self._event_store.get_events(session_id)
        if _has_open_turn(events):
            msg = "session has an in-flight turn (unclosed TURN_START without ERROR)"
            raise TurnInFlightError(msg)

        session = await self._resolve_session_row(session_id)
        compiled, _ = await self._build_continuation_graph(session, model=model, workflow_id=workflow_id)
        from hecate.runtime.replay.logpolicy import should_log_channel

        for name in values:
            if not should_log_channel(name) or name not in compiled.channels:
                msg = f"channel '{name}' is not a loggable state channel of the session's graph"
                raise InvalidStateChannelError(msg)

        await self._append_state_mutation(session_id, values, actor, superstep=events[-1].superstep if events else 0)

        # Response state is log truth: refetch and fold.
        events_after = await self._event_store.get_events(session_id)
        folded = _fold_state(compiled, events_after)
        return {"channel_state": folded, "log_version": events_after[-1].version if events_after else 0}

    async def fork_session(
        self,
        parent_session_id: uuid.UUID,
        at_version: int,
        updates: dict[str, Any] | None = None,
        actor: str = "api",
        model: str | None = None,
        workflow_id: uuid.UUID | None = None,
        tools: list[dict] | None = None,
        kb_ids: list[str] | None = None,
        user_id: str | uuid.UUID | None = None,
    ) -> dict[str, Any]:
        """Create a child session from a historical commit point and run it.

        The parent log is never touched (read-only fold). The child session's
        log bootstraps with a single FORK snapshot event (self-contained
        state + lineage + derived next_nodes), optionally followed by an
        update_state batch, then execution continues from the FORK
        continuation. Per design D7, re-dispatched nodes re-execute their
        side effects — nothing is rolled back or deduplicated.
        """
        if self._event_store is None:
            msg = "fork requires a wired EventStore"
            raise ValueError(msg)
        session = await self._resolve_session_row(parent_session_id)
        if session is None:
            msg = "parent session not found"
            raise InvalidForkAnchorError(msg)

        parent_events = await self._event_store.get_events(parent_session_id)
        if not parent_events:
            msg = "parent session has an empty event log"
            raise InvalidForkAnchorError(msg)
        tail = parent_events[-1].version
        if at_version > tail:
            msg = f"at_version {at_version} exceeds log tail {tail}"
            raise InvalidForkAnchorError(msg)
        effective = max((v for v in _commit_versions(parent_events) if v <= at_version), default=None)
        if effective is None:
            msg = f"no commit point at or below at_version {at_version}"
            raise InvalidForkAnchorError(msg)

        compiled, execution_mode = await self._build_continuation_graph(session, model=model, workflow_id=workflow_id)
        from hecate.runtime.replay.continuation import derive_continuation

        # Snapshot + continuation derive against a pristine runtime (the
        # snapshot path folds into a fresh projection, never runtime state).
        runtime = PregelRuntime(
            graph=compiled,
            worker=self._create_composite_worker(tools, kb_ids, None),
            checkpoint_store=self._build_fork_checkpoint_store(user_id),
            event_store=self._event_store,
            context_engine=PriorityContextEngine(),
        )
        snap = await runtime.snapshot_at_version(parent_session_id, effective)
        continuation = derive_continuation(
            [e for e in parent_events if e.version <= effective], compiled, snap["channel_state"]
        )

        from hecate.models.session import SessionModel as _SessionModel

        child = _SessionModel(
            agent_id=session.agent_id,
            status="active",
            workspace_id=session.workspace_id,
            metadata_={
                "parent_session_id": str(parent_session_id),
                "parent_log_version": effective,
            },
        )
        if self._db is not None:
            self._db.add(child)
            await self._db.flush()
            await self._db.refresh(child)

        await self._event_store.append(
            Event(
                session_id=child.id,
                superstep=snap["superstep"],
                event_type=EventType.FORK,
                payload={
                    "log_schema_version": CURRENT_LOG_SCHEMA_VERSION,
                    "parent_session_id": str(parent_session_id),
                    "parent_log_version": effective,
                    "channel_state": snap["channel_state"],
                    "next_nodes": continuation.nodes,
                    "superstep": snap["superstep"],
                    "agent_id": str(session.agent_id),
                },
            )
        )
        if updates:
            await self._append_state_mutation(child.id, updates, actor, superstep=snap["superstep"])

        execution: dict[str, Any] | None = None
        executed = False
        if continuation.nodes:
            child_tail = await self._event_store.get_version(child.id)
            execution = await self._fork_run(runtime, child.id, child_tail, execution_mode, model)
            executed = True

        return {
            "session_id": child.id,
            "agent_id": child.agent_id,
            "status": child.status,
            "workspace_id": child.workspace_id,
            "parent_session_id": str(parent_session_id),
            "parent_log_version": effective,
            "effective_version": effective,
            "next_nodes": continuation.nodes,
            "executed": executed,
            "execution": execution,
            "side_effects_note": (
                "Re-dispatched nodes re-execute tools and external side effects; "
                "effects from before the fork anchor are not rolled back."
            ),
        }

    async def _fork_run(
        self,
        runtime: PregelRuntime,
        session_id: uuid.UUID,
        resume_from: int,
        execution_mode: str,
        model: str | None,
    ) -> dict[str, Any]:
        """Non-stream fork continuation: run from the FORK descriptor."""
        final_state: dict[str, Any] = {}
        async for event in runtime.execute(
            session_id=session_id,
            initial_input=None,
            stream_mode=StreamMode.VALUES,
            execution_mode=execution_mode,
            resume_from=resume_from,
        ):
            if event.get("type") == "values":
                final_state = event.get("state", {})
            elif event.get("type") == "interrupt" and execution_mode == "conversational":
                await self._mark_session_interrupted(session_id)
        messages = final_state.get("messages", [])
        content = ""
        if messages:
            last_msg = messages[-1] if isinstance(messages, list) else messages
            if isinstance(last_msg, dict):
                content = last_msg.get("content", "")
        return {"content": content, "model": model or "gpt-4o", "usage": {}, "finish_reason": "stop"}

    async def _append_state_mutation(
        self, session_id: uuid.UUID, values: dict[str, Any], actor: str, superstep: int
    ) -> None:
        """Append one update_state batch: TURN pair around logged writes."""
        batch = [
            Event(
                session_id=session_id,
                superstep=superstep,
                event_type=EventType.TURN_START,
                payload={"log_schema_version": CURRENT_LOG_SCHEMA_VERSION, "reason": "update_state", "actor": actor},
            )
        ]
        for channel, value in values.items():
            batch.append(
                Event(
                    session_id=session_id,
                    superstep=superstep,
                    event_type=EventType.CHANNEL_WRITE,
                    payload={
                        "channel": channel,
                        "value": value,
                        "log_schema_version": CURRENT_LOG_SCHEMA_VERSION,
                        "source": "update_state",
                        "actor": actor,
                    },
                )
            )
        batch.append(
            Event(
                session_id=session_id,
                superstep=superstep,
                event_type=EventType.STEP_END,
                payload={"source": "update_state"},
            )
        )
        batch.append(
            Event(
                session_id=session_id,
                superstep=superstep,
                event_type=EventType.TURN_END,
                payload={"log_schema_version": CURRENT_LOG_SCHEMA_VERSION},
            )
        )
        await self._event_store.append_batch(batch)

    async def _resolve_session_row(self, session_id: uuid.UUID) -> Any:
        if self._db is None:
            return None
        from hecate.models.session import SessionModel

        result = await self._db.execute(select(SessionModel).where(SessionModel.id == session_id))
        return result.scalar_one_or_none()

    async def _build_continuation_graph(
        self,
        session: Any,
        model: str | None = None,
        workflow_id: uuid.UUID | None = None,
    ) -> tuple[Any, str]:
        """Build the graph a fork/update runs against (design D8: the current
        definition — agent persona for chat sessions, or the given workflow)."""
        from hecate.studio.workflows.templates import build_chat_graph

        if workflow_id is not None:
            graph_config = await self._load_workflow_graph(workflow_id)
            execution_mode = await self._load_workflow_mode(workflow_id)
        else:
            system_prompt = "You are a helpful assistant."
            if session is not None and self._db is not None and session.agent_id:
                from hecate.models.agent import AgentModel

                result = await self._db.execute(
                    select(AgentModel).where(AgentModel.id == session.agent_id, ~AgentModel.deleted)
                )
                agent = result.scalar_one_or_none()
                if agent is not None and getattr(agent, "persona", None):
                    system_prompt = agent.persona
            graph_config = build_chat_graph(
                model=model or "gpt-4o",
                system_prompt=system_prompt,
                enable_suggestions=False,
                generate_opening=False,
            )
            execution_mode = "conversational"

        compiled = GraphCompiler().compile(graph_config, execution_mode=execution_mode)
        for _nid, ncfg in compiled.nodes.items():
            ncfg.config["_node_type"] = ncfg.type.value
        return compiled, execution_mode

    def _build_fork_checkpoint_store(self, user_id: str | uuid.UUID | None) -> InMemoryCheckpointStore | Any:
        """Checkpoint cache for a fork run (mirrors execute()'s wiring)."""
        if self._checkpoint_store is None:
            return InMemoryCheckpointStore()
        from hecate.runtime.session_state_materializer import SessionStateMaterializer

        tenant_uuid = uuid.UUID(str(user_id)) if user_id is not None else None
        captured_user_id = tenant_uuid

        def _tenant_provider() -> tuple[uuid.UUID, uuid.UUID] | None:
            if captured_user_id is None:
                return None
            return captured_user_id, captured_user_id

        return SessionStateMaterializer(
            session_state_store=self._checkpoint_store,
            tenant_context_provider=_tenant_provider,
            event_store=self._event_store,
        )


def _has_open_turn(events: list[Any]) -> bool:
    """True when an unclosed TURN is in flight (no TURN_END and no ERROR after it)."""
    open_turn = False
    for event in events:
        etype = event.event_type.value if hasattr(event.event_type, "value") else str(event.event_type)
        if etype == EventType.TURN_START.value:
            open_turn = True
        elif etype == EventType.TURN_END.value:
            open_turn = False
        elif etype == EventType.ERROR.value and open_turn:
            # Engine error paths intentionally skip TURN_END; the ERROR event
            # marks the turn as crashed, not in flight.
            open_turn = False
    return open_turn


def _commit_versions(events: list[Any]) -> list[int]:
    from hecate.studio.replay.state_inspector import _select_commit_points

    return _select_commit_points(events)


def _fold_state(compiled: Any, events: list[Any]) -> dict[str, Any]:
    """Fold events into a fresh channel manager registered from the graph."""
    from hecate.runtime.channel import ChannelManager
    from hecate.runtime.replay.logfold import fold_session

    cm = ChannelManager()
    for name, defn in compiled.channels.items():
        cm.register(name, defn)
    fold_session(cm, iter(events))
    return cm.snapshot()
