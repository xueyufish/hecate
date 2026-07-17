"""Unified execution service for all agent modes.

Provides a single entry point that accepts an agent mode (chat, three_layer,
workflow), resolves the appropriate graph template, compiles it, instantiates
production Workers with Guardrail Hooks, and executes via PregelRuntime.

Both the chat API and workflow API call this service, replacing the direct
ConversationService orchestration.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.engine.checkpoint import InMemoryCheckpointStore
from hecate.engine.compiler import GraphCompiler
from hecate.engine.context import InMemoryContextEngine
from hecate.engine.graph_dsl import parse_graph
from hecate.engine.guardrail import (
    PostLLMHook,
    PostToolHook,
    PreLLMHook,
    PreToolHook,
)
from hecate.engine.pregel import PregelRuntime
from hecate.engine.types import StreamMode
from hecate.engine.workers.agent_worker import AgentWorker
from hecate.engine.workers.condition_worker import ConditionWorker
from hecate.engine.workers.knowledge_worker import KnowledgeWorker
from hecate.engine.workers.llm_worker import LLMWorker
from hecate.engine.workers.suggestion_worker import SuggestionWorker
from hecate.engine.workers.tool_worker import ToolWorker
from hecate.engine.workers.variable_set_worker import VariableSetWorker
from hecate.models.workflow import WorkflowModel, WorkflowVersionModel
from hecate.services.state.state import AgentState
from hecate.services.state.store import AgentStateStore

logger = logging.getLogger(__name__)


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
    ) -> None:
        self._llm = llm_worker
        self._tool = tool_worker
        self._condition = condition_worker
        self._agent = agent_worker
        self._knowledge = knowledge_worker
        self._suggestion = suggestion_worker
        self._variable = variable_worker
        self._workers_by_type: dict[str, Any] = {
            "conversation": self._llm,
            "tool-call": self._tool,
            "condition": self._condition,
            "agent": self._agent,
            "knowledge-retrieval": self._knowledge,
            "suggestion": self._suggestion,
            "variable-set": self._variable,
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
        state_store: AgentStateStore | None = None,
    ) -> None:
        self._port = port
        self._db = db
        self._suggestion_service = suggestion_service
        self._pre_llm_hook = pre_llm_hook
        self._post_llm_hook = post_llm_hook
        self._pre_tool_hook = pre_tool_hook
        self._post_tool_hook = post_tool_hook
        self._environment_manager = environment_manager
        self._state_store = state_store

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

        Returns:
            Response dict (non-streaming) or AsyncGenerator (streaming).
        """
        from hecate.engine.templates import build_chat_graph, build_three_layer_graph

        if messages is None:
            messages = []

        if session_id is None:
            session_id = uuid.uuid4()
        if isinstance(session_id, str):
            session_id = uuid.UUID(session_id)

        if agent_id and self._db is not None:
            from hecate.models.agent import AgentModel
            from hecate.services.skill.loader import SkillLoader

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
        if agent_id and self._environment_manager:
            agent_str = str(agent_id) if isinstance(agent_id, uuid.UUID) else agent_id
            env = await self._environment_manager.get_or_create(agent_str)
            environment_root = str(env.root_path)

        # Load or create AgentState
        agent_state: AgentState | None = None
        if self._state_store and agent_id:
            agent_uuid = agent_id if isinstance(agent_id, uuid.UUID) else uuid.UUID(str(agent_id))
            agent_state = await self._state_store.load(agent_uuid, session_id)
        if agent_state is None:
            agent_uuid = (
                agent_id if isinstance(agent_id, uuid.UUID) else uuid.UUID(str(agent_id)) if agent_id else uuid.uuid4()
            )
            agent_state = AgentState(session_id=session_id, agent_id=agent_uuid)
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
            )
        elif agent_mode == "three_layer":
            graph_config = build_three_layer_graph(
                planner_model=model,
                sub_agent_model=model,
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
        runtime = PregelRuntime(
            graph=compiled,
            worker=composite,
            checkpoint_store=checkpoint_store,
            max_supersteps=max_iterations * 3 + 5,
            context_engine=InMemoryContextEngine(),
        )

        stream_mode = StreamMode.MESSAGES if stream else StreamMode.VALUES

        if stream:
            return self._stream_execute(runtime, session_id, initial_input, stream_mode, execution_mode, agent_state)

        response = await self._non_stream_execute(runtime, session_id, initial_input, execution_mode)
        # Save AgentState after non-streaming execution
        if self._state_store and agent_id:
            agent_uuid = agent_id if isinstance(agent_id, uuid.UUID) else uuid.UUID(str(agent_id))
            await self._state_store.save(agent_uuid, session_id, agent_state)
        return response

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
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Execute streaming and yield events.

        Args:
            runtime: The configured PregelRuntime.
            session_id: Session identifier.
            initial_input: Channel initial values.
            stream_mode: Stream mode for PregelRuntime.
            execution_mode: Execution mode (conversational or task).
            agent_state: AgentState to save after stream completes.

        Yields:
            Event dicts from PregelRuntime.
        """
        async for event in runtime.execute(
            session_id=session_id,
            initial_input=initial_input,
            stream_mode=stream_mode,
            execution_mode=execution_mode,
        ):
            yield event
        # Save AgentState after streaming completes
        if self._state_store and agent_state:
            await self._state_store.save(agent_state.agent_id, session_id, agent_state)

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
        )
        agent_worker = AgentWorker(port=self._port)
        knowledge_worker = KnowledgeWorker(port=self._port)
        suggestion_worker = SuggestionWorker(
            suggestion_service=self._suggestion_service,
        )
        condition_worker = ConditionWorker()
        variable_worker = VariableSetWorker()

        return _CompositeWorker(
            llm_worker=llm_worker,
            tool_worker=tool_worker,
            condition_worker=condition_worker,
            agent_worker=agent_worker,
            knowledge_worker=knowledge_worker,
            suggestion_worker=suggestion_worker,
            variable_worker=variable_worker,
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
