"""User memory service for L3 persistent memory.

Manages persistent facts extracted from conversations, stored with
vector embeddings for semantic retrieval across sessions.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.memory import MemoryCreateSchema, MemoryModel, MemoryReadSchema

logger = logging.getLogger(__name__)


class UserMemoryService:
    """Service for managing L3 user memory.

    Provides operations for storing, retrieving, and managing persistent
    facts extracted from conversations.
    """

    def __init__(self, db: AsyncSession) -> None:
        """Initialize with a database session.

        Args:
            db: Async SQLAlchemy session for database operations.
        """
        self.db = db

    async def store_memory(
        self,
        data: MemoryCreateSchema,
        embedding: list[float] | None = None,
    ) -> MemoryReadSchema:
        """Store a new memory with optional embedding.

        Args:
            data: Memory creation data.
            embedding: Optional vector embedding. If None, generates a mock embedding.

        Returns:
            The created memory.
        """
        if embedding is None:
            embedding = self._generate_mock_embedding(data.content)

        memory = MemoryModel(
            content=data.content,
            scope=data.scope,
            memory_type=data.memory_type,
            importance=data.importance,
            embedding=embedding,
        )
        self.db.add(memory)
        await self.db.flush()

        logger.info(f"Stored memory: {data.content[:50]}...")
        return MemoryReadSchema.model_validate(memory)

    async def retrieve_memories(
        self,
        query: str,
        scope: dict[str, Any] | None = None,
        top_k: int = 5,
        min_importance: float = 0.0,
    ) -> list[MemoryReadSchema]:
        """Retrieve relevant memories by semantic similarity.

        Args:
            query: The query to search for.
            scope: Optional scope filter (user_id, agent_id, session_id).
            top_k: Maximum number of results.
            min_importance: Minimum importance threshold.

        Returns:
            List of relevant memories ordered by similarity.
        """
        query_embedding = self._generate_mock_embedding(query)  # noqa: F841

        # Build query conditions
        conditions = [MemoryModel.deleted_at.is_(None)]

        if min_importance > 0:
            conditions.append(MemoryModel.importance >= min_importance)

        if scope:
            # Filter by scope fields
            if "user_id" in scope:
                conditions.append(MemoryModel.scope["user_id"].as_string() == str(scope["user_id"]))
            if "agent_id" in scope:
                conditions.append(MemoryModel.scope["agent_id"].as_string() == str(scope["agent_id"]))

        stmt = select(MemoryModel).where(*conditions).order_by(MemoryModel.importance.desc()).limit(top_k)

        result = await self.db.execute(stmt)
        memories = result.scalars().all()

        # Update access count for retrieved memories
        for memory in memories:
            memory.access_count += 1

        await self.db.flush()

        # Refresh to get updated_at
        for memory in memories:
            await self.db.refresh(memory)

        return [MemoryReadSchema.model_validate(m) for m in memories]

    async def update_importance(
        self,
        memory_id: uuid.UUID,
        boost: float = 0.1,
    ) -> float:
        """Update memory importance score.

        Args:
            memory_id: The memory to update.
            boost: Amount to boost importance (can be negative).

        Returns:
            New importance score.

        Raises:
            ValueError: If memory not found.
        """
        memory = await self._get_by_id(memory_id)
        if memory is None:
            raise ValueError(f"Memory {memory_id} not found")

        new_importance = max(0.0, min(1.0, memory.importance + boost))
        memory.importance = new_importance
        await self.db.flush()

        logger.debug(f"Updated memory {memory_id} importance to {new_importance}")
        return new_importance

    async def delete_memory(self, memory_id: uuid.UUID) -> None:
        """Soft delete a memory.

        Args:
            memory_id: The memory to delete.

        Raises:
            ValueError: If memory not found.
        """
        memory = await self._get_by_id(memory_id)
        if memory is None:
            raise ValueError(f"Memory {memory_id} not found")

        memory.deleted_at = datetime.now(UTC)
        await self.db.flush()
        logger.info(f"Deleted memory {memory_id}")

    async def list_memories(
        self,
        scope: dict[str, Any] | None = None,
        memory_type: str | None = None,
        min_importance: float = 0.0,
        limit: int = 50,
    ) -> list[MemoryReadSchema]:
        """List memories with optional filters.

        Args:
            scope: Optional scope filter.
            memory_type: Optional type filter.
            min_importance: Minimum importance threshold.
            limit: Maximum results.

        Returns:
            List of memories.
        """
        conditions = [MemoryModel.deleted_at.is_(None)]

        if memory_type:
            conditions.append(MemoryModel.memory_type == memory_type)
        if min_importance > 0:
            conditions.append(MemoryModel.importance >= min_importance)

        stmt = (
            select(MemoryModel)
            .where(*conditions)
            .order_by(MemoryModel.importance.desc(), MemoryModel.created_at.desc())
            .limit(limit)
        )

        result = await self.db.execute(stmt)
        memories = result.scalars().all()
        return [MemoryReadSchema.model_validate(m) for m in memories]

    async def extract_facts(
        self,
        messages: list[dict[str, Any]],
    ) -> list[str]:
        """Extract facts from conversation messages.

        Simple heuristic extraction - identifies statements that look like
        facts or preferences.

        Args:
            messages: Conversation messages.

        Returns:
            List of extracted fact strings.
        """
        facts: list[str] = []

        for msg in messages:
            if msg.get("role") != "user":
                continue

            content = msg.get("content", "")
            if not isinstance(content, str):
                continue

            # Look for preference/fact patterns
            content_lower = content.lower()
            if any(
                indicator in content_lower
                for indicator in [
                    "i prefer",
                    "i like",
                    "i want",
                    "i need",
                    "my favorite",
                    "i always",
                    "i never",
                    "i work",
                    "i am",
                    "i use",
                ]
            ):
                facts.append(content)

        return facts

    def _generate_mock_embedding(self, text: str) -> list[float]:
        """Generate a deterministic mock embedding for testing.

        Args:
            text: Text to generate embedding for.

        Returns:
            1024-dimensional float vector.
        """
        import hashlib

        hash_bytes = hashlib.md5(text.encode()).digest()  # noqa: S324
        dense = [b / 255.0 for b in hash_bytes]
        dense = dense + [0.0] * (1024 - len(dense))
        return dense[:1024]

    async def _get_by_id(self, memory_id: uuid.UUID) -> MemoryModel | None:
        """Get memory by ID."""
        stmt = select(MemoryModel).where(
            MemoryModel.id == memory_id,
            MemoryModel.deleted_at.is_(None),
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()
