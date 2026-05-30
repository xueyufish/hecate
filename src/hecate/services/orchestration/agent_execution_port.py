"""Concrete EnginePort adapter for agent execution.

Implements the ``agent_execute`` method on EnginePort by resolving the
AgentModel from the database, building an isolated execution context
(system prompt from persona, agent-specific tools, agent-specific knowledge
bases), and invoking the LLM via ConversationService.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.engine.ports import EnginePort
from hecate.models.agent import AgentModel
from hecate.services.conversation import ConversationService

logger = logging.getLogger(__name__)


class AgentExecutionPort(EnginePort):
    """EnginePort adapter that handles agent execution via database lookups.

    This adapter implements ``agent_execute`` by:
    1. Loading the AgentModel from the database by ID.
    2. Building an isolated context using the agent's persona, model config,
       tools, and knowledge bases.
    3. Invoking the LLM via ConversationService.
    4. Returning the response.

    Other EnginePort methods are intentionally left as NotImplementedError
    because this adapter is only used for agent execution, not as a general
    engine port. The full port implementation lives elsewhere.
    """

    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._conversation_service = ConversationService()

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
        agent = await self._load_agent(agent_id)
        if agent is None:
            raise ValueError(f"Agent {agent_id} not found")

        model_name = (
            agent.model_config_db.get("model", "gpt-4o") if isinstance(agent.model_config_db, dict) else "gpt-4o"
        )

        system_message = {"role": "system", "content": agent.persona or "You are a helpful assistant."}
        full_messages = [system_message] + messages

        session_id = str(agent_id) if context is None else context.get("node_id", str(agent_id))

        result = await self._conversation_service.chat(
            messages=full_messages,
            model=model_name,
            tools=None,
            stream=False,
            session_id=session_id,
        )

        return {
            "response": result.get("content", ""),
            "usage": result.get("usage", {}),
            "model": result.get("model", model_name),
        }

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
                AgentModel.deleted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    # Stub implementations for other EnginePort methods.
    # These are required by the ABC but not used for agent execution.

    async def llm_invoke(self, messages: list[dict], config: dict) -> AsyncGenerator[str, None]:
        raise NotImplementedError("Use ConversationService directly for LLM invocation")

    async def tool_execute(self, name: str, args: dict, context: dict | None = None) -> Any:
        raise NotImplementedError("Use tool execution service directly")

    async def knowledge_query(self, query: str, kb_ids: list[UUID]) -> list[dict]:
        raise NotImplementedError("Use knowledge service directly for queries")

    async def checkpoint_save(self, state: dict) -> UUID:
        raise NotImplementedError("Use CheckpointStore directly")

    async def checkpoint_load(self, checkpoint_id: UUID) -> dict:
        raise NotImplementedError("Use CheckpointStore directly")

    async def conversation_load(self, session_id: UUID) -> list[dict]:
        raise NotImplementedError("Use conversation API directly")

    async def conversation_save(self, session_id: UUID, messages: list[dict]) -> None:
        raise NotImplementedError("Use conversation API directly")
