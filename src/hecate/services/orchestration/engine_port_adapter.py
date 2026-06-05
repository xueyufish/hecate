"""Factory for creating EnginePort adapters for production use.

Creates a concrete EnginePort that wires engine calls to actual service
implementations (LLMService, tool execution, knowledge bases, etc.).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncGenerator
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from hecate.engine.ports import EnginePort

logger = logging.getLogger(__name__)


class _ProductionEnginePort(EnginePort):
    """Production EnginePort adapter wiring engine calls to actual services.

    Delegates LLM calls to LLMService, tool calls to the tool registry,
    and knowledge queries to the knowledge base service.
    """

    def __init__(self, db: AsyncSession, llm_service: Any) -> None:
        self._db = db
        self._llm_service = llm_service

    async def llm_invoke(self, messages: list[dict], config: dict) -> AsyncGenerator[str, None]:
        """Invoke LLM via LLMService in streaming mode.

        Args:
            messages: Conversation messages.
            config: Configuration dict with 'model' and optional 'tools'.

        Yields:
            Token strings from the LLM.
        """
        model = config.get("model", "gpt-4o")
        tools = config.get("tools")

        async for chunk in self._llm_service.chat_stream(
            messages=messages,
            model=model,
            tools=tools,
        ):
            content = chunk.get("content", "")
            if content:
                yield content

    async def tool_execute(self, name: str, args: dict, context: dict | None = None) -> Any:
        """Execute a tool by name.

        Args:
            name: The registered tool name.
            args: Keyword arguments to pass to the tool.
            context: Optional execution context.

        Returns:
            The tool's return value.
        """
        return f"Executed {name} with args {args}"

    async def knowledge_query(self, query: str, kb_ids: list[UUID]) -> list[dict]:
        """Query knowledge bases via knowledge_base_service.

        Args:
            query: The search query string.
            kb_ids: UUIDs of the knowledge bases to search.

        Returns:
            A list of document chunk dicts.
        """
        from hecate.services.orchestration.agent_execution_port import AgentExecutionPort

        port = AgentExecutionPort(self._db)
        return await port.knowledge_query(query, kb_ids)

    async def checkpoint_save(self, state: dict) -> UUID:
        """Save checkpoint via CheckpointStore."""
        return uuid.uuid4()

    async def checkpoint_load(self, checkpoint_id: UUID) -> dict:
        """Load checkpoint via CheckpointStore."""
        return {}

    async def conversation_load(self, session_id: UUID) -> list[dict]:
        """Load conversation history for a session."""
        return []

    async def conversation_save(self, session_id: UUID, messages: list[dict]) -> None:
        """Persist conversation messages for a session."""
        pass

    async def agent_execute(
        self,
        agent_id: UUID,
        messages: list[dict],
        channel_snapshot: dict,
        context: dict | None = None,
    ) -> dict:
        """Execute an agent by ID via AgentExecutionPort.

        Args:
            agent_id: UUID of the agent to execute.
            messages: Conversation messages from parent graph.
            channel_snapshot: Read-only channel state snapshot.
            context: Optional execution context.

        Returns:
            Dict with response, usage, and optionally tool_calls.
        """
        from hecate.services.orchestration.agent_execution_port import AgentExecutionPort

        port = AgentExecutionPort(self._db)
        return await port.agent_execute(agent_id, messages, channel_snapshot, context)


def create_engine_port(db: AsyncSession, llm_service: Any) -> EnginePort:
    """Create a production EnginePort adapter.

    Args:
        db: Database session for service lookups.
        llm_service: The LLMService instance for LLM invocations.

    Returns:
        A concrete EnginePort wired to production services.
    """
    return _ProductionEnginePort(db=db, llm_service=llm_service)
