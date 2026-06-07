"""Concrete EnginePort adapter for agent execution.

Implements the ``agent_execute`` method on EnginePort by resolving the
AgentModel from the database, building an isolated execution context
(system prompt from persona, agent-specific tools, agent-specific knowledge
bases), and invoking via the unified WorkflowExecutionService.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.engine.ports import EnginePort
from hecate.models.agent import AgentModel
from hecate.models.knowledge import KnowledgeBaseModel
from hecate.services.rag.service import knowledge_base_service

logger = logging.getLogger(__name__)


class AgentExecutionPort(EnginePort):
    """EnginePort adapter that handles agent execution via database lookups.

    This adapter implements ``agent_execute`` by:
    1. Loading the AgentModel from the database by ID.
    2. Building an isolated context using the agent's persona, model config,
       tools, and knowledge bases.
    3. Invoking the LLM via LLMService (fallback for sub-agent execution).
    4. Returning the response.

    Also implements ``knowledge_query`` by delegating to the KnowledgeBaseService
    for hybrid search (dense + sparse vectors).
    """

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def agent_execute(
        self,
        agent_id: UUID,
        messages: list[dict],
        channel_snapshot: dict,
        context: dict | None = None,
    ) -> dict:
        """Execute an agent by ID with isolated context.

        Args:
            agent_id: UUID of the agent to execute.
            messages: Conversation messages from parent graph.
            channel_snapshot: Read-only channel state snapshot.
            context: Optional execution context (node_id, etc.).

        Returns:
            Dict with ``response``, ``usage``, and optionally ``tool_calls``.

        Raises:
            ValueError: If agent_id does not resolve to a valid agent.
        """
        from hecate.services.llm.service import llm_service

        agent = await self._load_agent(agent_id)
        if agent is None:
            raise ValueError(f"Agent {agent_id} not found")

        model_name = (
            agent.model_config_db.get("model", "gpt-4o") if isinstance(agent.model_config_db, dict) else "gpt-4o"
        )

        persona = agent.persona or "You are a helpful assistant."

        from hecate.services.skill.loader import SkillLoader

        loader = SkillLoader(self._db)
        skills_block = await loader.format_skills(
            agent_id=agent_id,
            workspace_id=agent.workspace_id,
        )
        system_content = f"{persona}\n\n{skills_block}" if skills_block else persona
        system_message = {"role": "system", "content": system_content}
        full_messages = [system_message] + messages

        response = await llm_service.chat(
            messages=full_messages,
            model=model_name,
            tools=None,
        )

        return {
            "response": response.content,
            "usage": response.usage,
            "model": response.model or model_name,
        }

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

                search_results = await knowledge_base_service.search(
                    collection_name=kb.qdrant_collection,
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

    # Stub implementations for other EnginePort methods.
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
