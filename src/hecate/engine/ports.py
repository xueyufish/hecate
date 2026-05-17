from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from typing import Any
from uuid import UUID


class EnginePort(ABC):
    """Boundary interface between the execution engine and external capability services.

    The engine calls these methods to invoke LLM, execute tools, query knowledge,
    and manage conversation/checkpoint state without importing any service module.
    """

    @abstractmethod
    async def llm_invoke(self, messages: list[dict], config: dict) -> AsyncGenerator[str, None]:
        """Invoke an LLM with the given messages and return a stream of tokens."""
        ...

    @abstractmethod
    async def tool_execute(self, name: str, args: dict, context: dict | None = None) -> Any:
        """Execute a tool by name with the given arguments."""
        ...

    @abstractmethod
    async def knowledge_query(self, query: str, kb_ids: list[UUID]) -> list[dict]:
        """Query knowledge bases and return relevant document chunks."""
        ...

    @abstractmethod
    async def checkpoint_save(self, state: dict) -> UUID:
        """Persist an execution state checkpoint and return its ID."""
        ...

    @abstractmethod
    async def checkpoint_load(self, checkpoint_id: UUID) -> dict:
        """Load a previously saved checkpoint by ID."""
        ...

    @abstractmethod
    async def conversation_load(self, session_id: UUID) -> list[dict]:
        """Load conversation message history for a session."""
        ...

    @abstractmethod
    async def conversation_save(self, session_id: UUID, messages: list[dict]) -> None:
        """Persist conversation messages for a session."""
        ...
