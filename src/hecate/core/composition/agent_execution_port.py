"""Concrete RuntimePort adapter for agent execution.

Implements the ``agent_execute`` method on RuntimePort by resolving the
AgentModel from the database, building an isolated execution context
(system prompt from persona, agent-specific tools, agent-specific knowledge
bases), and invoking via the unified WorkflowExecutionService.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.composition.memory_provider import resolve_memory_provider
from hecate.models.agent import AgentModel
from hecate.models.knowledge import KnowledgeBaseModel
from hecate.models.tool import ToolModel
from hecate.runtime.guardrail import (
    GuardrailAction,
    NoOpPostLLMHook,
    NoOpPreLLMHook,
    PostLLMHook,
    PreLLMHook,
)
from hecate.runtime.ports import RuntimePort, SpanContext

logger = logging.getLogger(__name__)


class AgentExecutionPort(RuntimePort):
    """RuntimePort adapter that handles agent execution via database lookups.

    This adapter implements ``agent_execute`` by:
    1. Loading the AgentModel from the database by ID.
    2. Building an isolated context using the agent's persona, model config,
       tools, and knowledge bases.
    3. Applying guard hooks (PreLLMHook / PostLLMHook).
    4. Calling context_assemble for context engineering.
    5. Invoking the LLM with tools via LLMService.
    6. Returning the response.

    Also implements ``knowledge_query`` by delegating to the KnowledgeBaseService
    for hybrid search (dense + sparse vectors).
    """

    def __init__(
        self,
        db: AsyncSession,
        pre_hook: PreLLMHook | None = None,
        post_hook: PostLLMHook | None = None,
        context_engine: Any | None = None,
    ) -> None:
        self._db = db
        self._pre_hook = pre_hook or NoOpPreLLMHook()
        self._post_hook = post_hook or NoOpPostLLMHook()
        self._context_engine = context_engine

    async def agent_execute(
        self,
        agent_id: UUID,
        messages: list[dict],
        channel_snapshot: dict,
        context: dict | None = None,
        agent_definition: Any | None = None,
    ) -> dict:
        """Execute an agent by ID with full pipeline.

        Loads tools, queries knowledge bases, applies guard hooks,
        calls context_assemble, and invokes the LLM with tools.

        Args:
            agent_id: UUID of the agent to execute.
            messages: Conversation messages from parent graph.
            channel_snapshot: Read-only channel state snapshot.
            context: Optional execution context (node_id, etc.).
            agent_definition: Optional AgentDefinition for per-invocation overrides.

        Returns:
            Dict with ``response``, ``usage``, and optionally ``tool_calls``.

        Raises:
            ValueError: If agent_id does not resolve to a valid agent.
        """
        from hecate_llm.service import llm_service

        from hecate.tools.skill.loader import SkillLoader

        agent = await self._load_agent(agent_id)
        if agent is None:
            raise ValueError(f"Agent {agent_id} not found")

        # Version-bound evaluation: a frozen-snapshot definition replaces the
        # live row for every direct-loop config field (model, persona via
        # prompt_override below, tools via the whitelist filter below, skill
        # bindings, knowledge bases). Lazy + isinstance-checked so the generic
        # duck-typed overrides (prompt-optimization, self-evolution) and any
        # Pydantic-based definition object are unaffected.
        version_definition = None
        if agent_definition is not None:
            from hecate.studio.agents.versioning import AgentVersionExecutionDefinition

            if isinstance(agent_definition, AgentVersionExecutionDefinition):
                version_definition = agent_definition

        model_name = (
            agent.model_config_db.get("model", "gpt-4o") if isinstance(agent.model_config_db, dict) else "gpt-4o"
        )
        if version_definition is not None:
            if version_definition.agent_id not in (None, agent.id) or version_definition.workspace_id not in (
                None,
                agent.workspace_id,
            ):
                raise ValueError("version definition does not belong to this agent/workspace")
            await self._validate_version_resources(agent, version_definition)
            model_name = version_definition.model_config.get("model") or "gpt-4o"

        # Build system prompt from persona + skills. A per-invocation
        # prompt_override (top-level agent_definition, or the delegation
        # context key written by AgentTool) replaces the agent's configured
        # persona; tools / knowledge bases / model config stay unchanged.
        prompt_override: str | None = None
        if agent_definition is not None:
            prompt_override = getattr(agent_definition, "prompt_override", None)
        if prompt_override is None and context is not None:
            prompt_override = context.get("prompt_override")
        persona = (
            version_definition.prompt_override or "You are a helpful assistant."
            if version_definition is not None
            else prompt_override or agent.persona or "You are a helpful assistant."
        )
        loader = SkillLoader(self._db, ref_manifest=version_definition.ref_manifest if version_definition else None)
        skills_block = await loader.format_skills(
            agent_id=agent_id,
            workspace_id=agent.workspace_id,
            explicit_skill_names=version_definition.skills if version_definition is not None else None,
        )
        # Per-invocation extra skill instructions (agent_definition attribute,
        # mirroring the prompt_override pattern) — appends one ad-hoc skill
        # block without touching the agent's configured skills. Used by the
        # self-evolution gate to roll out a candidate skill for evaluation.
        extra_skill: str | None = None
        if agent_definition is not None:
            extra_skill = getattr(agent_definition, "extra_skill_instructions", None)
        if extra_skill:
            skills_block = f"{skills_block}\n\n{extra_skill}" if skills_block else extra_skill
        system_content = f"{persona}\n\n{skills_block}" if skills_block else persona
        system_message = {"role": "system", "content": system_content}
        full_messages = [system_message] + messages

        # Load agent tools from database, narrowed by the agent's resolved
        # memory policy (memory-policy capability — the policy may drop
        # memory tools from the surface, never add beyond the platform gate).
        from hecate.core.composition.memory_policy import narrowed_tool_names

        configured_tools = version_definition.tools if version_definition is not None else list(agent.tools or [])
        policy_tool_names = await narrowed_tool_names(self._db, agent.workspace_id, agent.id, configured_tools)
        if version_definition is not None:
            tools = await self._load_agent_tools(policy_tool_names, workspace_id=agent.workspace_id)
            if set(policy_tool_names) - {tool["name"] for tool in tools}:
                raise ValueError("version-bound tool does not resolve in the agent workspace")
        else:
            tools = await self._load_agent_tools(policy_tool_names)

        if version_definition is not None and version_definition.mode != "chat":
            return await self._execute_versioned_mode(agent, version_definition, messages, tools)

        # Apply AgentDefinition tool filtering (whitelist/blacklist)
        if agent_definition is not None and hasattr(agent_definition, "tools"):
            allowed = agent_definition.tools
            if allowed is not None:
                tools = [t for t in tools if t.get("name", "") in allowed]

        # Inject handoff tool if handoff_targets are present in context
        handoff_targets: list[dict[str, str]] = []
        if context is not None:
            handoff_targets = context.get("handoff_targets", [])
        if handoff_targets:
            # Lazy: studio is a sibling domain — a module-level import would
            # break runtime self-sufficiency (the hecate-runtime wheel).
            from hecate.studio.agents.handoff import (
                inject_handoff_tools_from_targets,
                is_handoff_tool_call,
                validate_handoff_target_from_list,
            )

            handoff_desc = None
            if context is not None:
                handoff_desc = context.get("handoff_description")
            tools = inject_handoff_tools_from_targets(tools, handoff_targets, source_description=handoff_desc)

        # Query agent's knowledge bases and inject as context
        kb_ids = (
            version_definition.knowledge_base_ids
            if version_definition is not None
            else (agent.knowledge_base_ids or [])
        )
        kb_ids = list(kb_ids or [])
        if kb_ids:
            query_text = messages[-1].get("content", "") if messages else ""
            kb_chunks = await self.knowledge_query(query_text, [UUID(kb_id) for kb_id in kb_ids])
            if kb_chunks:
                kb_context = "\n\n".join(chunk.get("content", "") for chunk in kb_chunks[:5])
                full_messages.insert(1, {"role": "system", "content": f"Relevant context:\n{kb_context}"})

        # PreLLMHook
        pre_result = await self._pre_hook.on_pre_llm_call(
            messages=full_messages,
            model=model_name,
            tools=tools if tools else None,
        )
        if pre_result.action == GuardrailAction.BLOCK:
            return {
                "response": f"I cannot process this request: {pre_result.reason}",
                "usage": {},
                "model": model_name,
            }
        if (
            pre_result.action == GuardrailAction.SANITIZE
            and pre_result.modified_data
            and "messages" in pre_result.modified_data
        ):
            full_messages = pre_result.modified_data["messages"]

        # Context assembly
        assembled = await self.context_assemble(
            messages=full_messages,
            tools=tools if tools else None,
            session_id=channel_snapshot.get("_session_id", agent_id),
            model=model_name,
        )
        shaped_messages = assembled.get("messages", full_messages)
        shaped_tools = assembled.get("tools", tools if tools else None)

        model_options: dict[str, Any] = {}
        if version_definition is not None:
            for key in ("temperature", "max_tokens", "routing_config", "timeout", "num_retries"):
                if key in version_definition.model_config:
                    model_options[key] = version_definition.model_config[key]

        # LLM invocation
        response = await llm_service.chat(
            messages=shaped_messages,
            model=model_name,
            tools=shaped_tools if shaped_tools else None,
            **model_options,
        )

        # Check for handoff tool call in LLM response
        if handoff_targets and hasattr(response, "tool_calls") and response.tool_calls:
            for tool_call in response.tool_calls:
                tc_name = tool_call.get("name", "") if isinstance(tool_call, dict) else getattr(tool_call, "name", "")
                if not is_handoff_tool_call(tc_name):
                    continue

                if isinstance(tool_call, dict):
                    args = tool_call.get("arguments", {})
                else:
                    args = getattr(tool_call, "arguments", {})
                if isinstance(args, str):
                    import json

                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}
                target = args.get("target", "") if isinstance(args, dict) else ""

                try:
                    validate_handoff_target_from_list(handoff_targets, target)
                except ValueError:
                    logger.warning("Invalid handoff target '%s' from node", target)
                    valid = ", ".join(t["node_id"] for t in handoff_targets)
                    return {
                        "response": f"Invalid handoff target '{target}'. Please choose from: {valid}",
                        "usage": response.usage,
                        "model": model_name,
                    }

                tc_id = tool_call.get("id", "") if isinstance(tool_call, dict) else getattr(tool_call, "id", "")
                return {
                    "response": "",
                    "usage": response.usage,
                    "model": model_name,
                    "handoff_to": target,
                    "_handoff_tool_call_id": tc_id,
                    "_handoff_tool_call_message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": tc_id,
                                "type": "function",
                                "function": {"name": "handoff_to_agent", "arguments": f'{{"target": "{target}"}}'},
                            }
                        ],
                    },
                    "_handoff_messages_snapshot": list(full_messages),
                }

        response_dict: dict[str, Any] = {
            "content": response.content,
            "model": response.model or model_name,
        }

        # PostLLMHook
        post_result = await self._post_hook.on_post_llm_call(
            response=response_dict,
            messages=shaped_messages,
        )
        if post_result.action == GuardrailAction.BLOCK:
            return {
                "response": "I cannot provide that response due to safety policy.",
                "usage": response.usage,
                "model": model_name,
            }
        if (
            post_result.action == GuardrailAction.SANITIZE
            and post_result.modified_data
            and "response" in post_result.modified_data
        ):
            response_dict = post_result.modified_data["response"]

        return {
            "response": response_dict.get("content", response.content),
            "usage": response.usage,
            "model": response.model or model_name,
        }

    async def _validate_version_resources(self, agent, definition) -> None:
        """Reject unresolved or foreign references instead of silently falling back to live content."""
        from hecate.models.skill_version import SkillVersionModel
        from hecate.models.workflow import WorkflowModel, WorkflowVersionModel

        for entry in definition.ref_manifest:
            if entry.get("resource_type") != "skill" or entry.get("version") is None:
                continue
            record = (
                await self._db.execute(
                    select(SkillVersionModel).where(
                        SkillVersionModel.skill_id == UUID(str(entry["skill_id"])),
                        SkillVersionModel.version == int(entry["version"]),
                        SkillVersionModel.workspace_id.in_([agent.workspace_id, UUID(int=0)]),
                        ~SkillVersionModel.deleted,
                    )
                )
            ).scalar_one_or_none()
            if record is None:
                raise ValueError("pinned skill version does not resolve in the agent workspace")
        for kb_id in definition.knowledge_base_ids:
            kb = await self._db.get(KnowledgeBaseModel, UUID(str(kb_id)))
            if kb is None or kb.deleted or kb.workspace_id != agent.workspace_id:
                raise ValueError("version-bound knowledge base does not resolve in the agent workspace")
        if definition.mode == "workflow":
            workflow = await self._db.get(WorkflowModel, definition.workflow_id) if definition.workflow_id else None
            record = (
                await self._db.execute(
                    select(WorkflowVersionModel).where(
                        WorkflowVersionModel.workflow_id == definition.workflow_id,
                        WorkflowVersionModel.workspace_id == agent.workspace_id,
                        WorkflowVersionModel.version == definition.workflow_version,
                        ~WorkflowVersionModel.deleted,
                    )
                )
            ).scalar_one_or_none()
            if workflow is None or workflow.deleted or workflow.workspace_id != agent.workspace_id or record is None:
                raise ValueError("pinned workflow version does not resolve in the agent workspace")

    async def _execute_versioned_mode(self, agent, definition, messages, tools) -> dict:
        """Execute frozen non-chat modes through the existing shared entry service."""
        from hecate_llm.service import llm_service

        from hecate.core.composition.entry_assembly import (
            build_tool_registry,
            get_shared_event_store,
            get_shared_session_state_store,
        )
        from hecate.core.composition.guardrail_platform import assemble_guardrails
        from hecate.core.composition.runtime_port_adapter import create_runtime_port
        from hecate.execution.entry_service import EntryExecutionService

        if definition.mode not in {"workflow", "three_layer"}:
            raise ValueError("unsupported version-bound agent mode")
        if definition.mode == "workflow" and (definition.workflow_id is None or definition.workflow_version is None):
            raise ValueError("version-bound workflow requires a pinned workflow version")
        event_store = get_shared_event_store()
        bundle = await assemble_guardrails(
            self._db,
            workspace_id=agent.workspace_id,
            agent_id=agent.id,
            guardrail_config=definition.guardrail_config,
            event_store=event_store,
        )
        port = create_runtime_port(
            self._db,
            llm_service,
            tool_registry=build_tool_registry(
                self._db,
                skill_ref_manifest=definition.ref_manifest,
                workspace_id=agent.workspace_id,
                explicit_skill_names=definition.skills,
            ),
        )
        entry = EntryExecutionService(
            port=port,
            entry_name="evaluation",
            db=self._db,
            event_store=event_store,
            checkpoint_store=get_shared_session_state_store(),
            access_policy=bundle.access_policy,
            approval_callback=bundle.approval_callback,
            tool_policy_rules=bundle.rules,
            middleware_chains=bundle.middleware_chains,
            denial_tracker=bundle.denial_tracker,
        )
        outcome = await entry.execute(
            agent_mode=definition.mode,
            model=definition.model_config.get("model") or "gpt-4o",
            agent_persona=definition.prompt_override,
            agent_id=agent.id,
            agent_version=definition.version,
            workspace_id=agent.workspace_id,
            workflow_id=definition.workflow_id,
            workflow_version=definition.workflow_version,
            messages=messages,
            tools=tools,
            kb_ids=definition.knowledge_base_ids,
            skill_ref_manifest=definition.ref_manifest,
            stream=False,
            session_id=uuid4(),
        )
        return {
            "response": str((outcome.result or {}).get("content") or ""),
            "usage": (outcome.result or {}).get("usage") or {},
        }

    async def _load_agent_tools(
        self, tool_names: list[str], *, workspace_id: UUID | None = None
    ) -> list[dict[str, Any]]:
        """Load tool definitions from the database by name.

        Args:
            tool_names: List of tool names from AgentModel.tools.

        Returns:
            List of tool definition dicts with name, description, parameters.
        """
        if not tool_names:
            return []

        result = await self._db.execute(
            select(ToolModel).where(
                ToolModel.name.in_(tool_names),
                ~ToolModel.deleted,
                *([ToolModel.workspace_id == workspace_id] if workspace_id is not None else []),
            )
        )
        tool_models = result.scalars().all()
        return [
            {
                "name": tool.name,
                "description": tool.description or "",
                "parameters": tool.parameters or {"type": "object", "properties": {}},
            }
            for tool in tool_models
        ]

    async def knowledge_query(self, query: str, kb_ids: list[UUID]) -> list[dict]:
        """Query knowledge bases in parallel and return relevant document chunks.

        Looks up the Qdrant collection names for the given kb_ids and performs
        hybrid search (dense + sparse) on each in parallel, aggregating results.

        Args:
            query: The search query string.
            kb_ids: UUIDs of the knowledge bases to search.

        Returns:
            A list of document chunk dicts with content and metadata.
        """
        if not kb_ids:
            return []

        provider = resolve_memory_provider()
        if provider is None:
            return []

        async def _search_one_kb(kb_id: UUID) -> list[dict]:
            """Search a single KB and return chunk dicts. Returns [] on failure."""
            try:
                result = await self._db.execute(
                    select(KnowledgeBaseModel).where(
                        KnowledgeBaseModel.id == kb_id,
                        ~KnowledgeBaseModel.deleted,
                    )
                )
                kb = result.scalar_one_or_none()
                if kb is None:
                    logger.warning(f"Knowledge base {kb_id} not found. Skipping.")
                    return []

                search_results = await provider.search(
                    collection_name=kb.collection_name,
                    query=query,
                    limit=10,
                    mode="hybrid",
                )
                return [
                    {
                        "content": r.content,
                        "metadata": {
                            **r.metadata,
                            "score": r.score,
                            "kb_id": str(kb_id),
                            "kb_name": kb.name,
                        },
                    }
                    for r in search_results
                ]
            except Exception as e:
                logger.error(f"Knowledge search failed for kb {kb_id}: {e}")
                return []

        results = await asyncio.gather(*[_search_one_kb(kb_id) for kb_id in kb_ids])
        all_chunks = [chunk for sublist in results for chunk in sublist]
        all_chunks.sort(key=lambda x: x["metadata"].get("score", 0), reverse=True)
        return all_chunks[:20]

    async def _load_agent(self, agent_id: UUID) -> AgentModel | None:
        """Load an agent from the database by ID.

        Args:
            agent_id: The UUID of the agent to load.

        Returns:
            The AgentModel if found and not deleted, None otherwise.
        """
        result = await self._db.execute(
            select(AgentModel).where(
                AgentModel.id == agent_id,
                ~AgentModel.deleted,
            )
        )
        return result.scalar_one_or_none()

    # Stub implementations for other RuntimePort methods.
    # These are required by the ABC but not used for agent execution.

    def llm_invoke(self, messages: list[dict], config: dict) -> AsyncGenerator[str, None]:
        raise NotImplementedError("Use LLMService directly for streaming LLM invocation")

    async def tool_execute(self, name: str, args: dict, context: dict | None = None) -> Any:
        raise NotImplementedError("Use tool execution service directly")

    async def checkpoint_save(self, state: dict) -> UUID:
        raise NotImplementedError("Use CheckpointStore directly")

    async def checkpoint_load(self, checkpoint_id: UUID) -> dict:
        raise NotImplementedError("Use CheckpointStore directly")

    async def conversation_load(self, session_id: UUID) -> list[dict]:
        raise NotImplementedError("Use conversation API directly")

    async def conversation_save(self, session_id: UUID, messages: list[dict]) -> None:
        raise NotImplementedError("Use conversation API directly")

    async def create_span(
        self,
        name: str,
        parent_id: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> SpanContext | None:
        """Create an observability span via the shared OTel adapter."""
        # Lazy: hecate_ops is a workspace wheel — a module-level import
        # would break runtime self-sufficiency (the hecate-runtime wheel).
        from hecate_ops.span_adapter import create_otel_span

        return create_otel_span(name, parent_id=parent_id, attributes=attributes)

    async def end_span(
        self,
        span_id: str,
        output_data: dict[str, Any] | None = None,
        usage: dict[str, int] | None = None,
    ) -> None:
        """End an observability span via the shared OTel adapter."""
        from hecate_ops.span_adapter import end_otel_span

        end_otel_span(span_id, output_data=output_data, usage=usage)
