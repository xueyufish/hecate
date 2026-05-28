"""Working memory service for L1 memory blocks.

Manages named memory blocks that agents can read and write each turn.
Blocks are inserted into the context at configured positions during
context assembly.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.memory import (
    MemoryBlockCreateSchema,
    MemoryBlockModel,
    MemoryBlockReadSchema,
    MemoryBlockUpdateSchema,
)

logger = logging.getLogger(__name__)


class WorkingMemoryService:
    """Service for managing L1 working memory blocks.

    Provides CRUD operations for memory blocks and a method to inject
    blocks into the message context at configured positions.
    """

    def __init__(self, db: AsyncSession) -> None:
        """Initialize with a database session.

        Args:
            db: Async SQLAlchemy session for database operations.
        """
        self.db = db

    async def create_block(
        self,
        agent_id: uuid.UUID,
        data: MemoryBlockCreateSchema,
    ) -> MemoryBlockReadSchema:
        """Create a new memory block for an agent.

        Args:
            agent_id: The agent to create the block for.
            data: Block creation data.

        Returns:
            The created block.

        Raises:
            ValueError: If a block with the same label already exists.
        """
        # Check for duplicate label
        existing = await self._get_by_label(agent_id, data.label)
        if existing is not None:
            raise ValueError(f"Memory block '{data.label}' already exists for agent {agent_id}")

        block = MemoryBlockModel(
            agent_id=agent_id,
            label=data.label,
            content=data.content,
            position=data.position,
            limit=data.limit,
        )
        self.db.add(block)
        await self.db.flush()

        logger.info(f"Created memory block '{data.label}' for agent {agent_id}")
        return MemoryBlockReadSchema.model_validate(block)

    async def get_block(
        self,
        agent_id: uuid.UUID,
        block_id: uuid.UUID,
    ) -> MemoryBlockReadSchema:
        """Get a memory block by ID.

        Args:
            agent_id: The agent that owns the block.
            block_id: The block ID.

        Returns:
            The block data.

        Raises:
            ValueError: If block not found.
        """
        block = await self._get_by_id(agent_id, block_id)
        if block is None:
            raise ValueError(f"Memory block {block_id} not found")
        return MemoryBlockReadSchema.model_validate(block)

    async def update_block(
        self,
        agent_id: uuid.UUID,
        block_id: uuid.UUID,
        data: MemoryBlockUpdateSchema,
    ) -> MemoryBlockReadSchema:
        """Update a memory block.

        Args:
            agent_id: The agent that owns the block.
            block_id: The block ID.
            data: Update data.

        Returns:
            The updated block.

        Raises:
            ValueError: If block not found.
        """
        block = await self._get_by_id(agent_id, block_id)
        if block is None:
            raise ValueError(f"Memory block {block_id} not found")

        if data.content is not None:
            block.content = data.content
        if data.position is not None:
            block.position = data.position
        if data.limit is not None:
            block.limit = data.limit

        await self.db.flush()
        await self.db.refresh(block)
        return MemoryBlockReadSchema.model_validate(block)

    async def delete_block(
        self,
        agent_id: uuid.UUID,
        block_id: uuid.UUID,
    ) -> None:
        """Delete a memory block.

        Args:
            agent_id: The agent that owns the block.
            block_id: The block ID.

        Raises:
            ValueError: If block not found.
        """
        from datetime import UTC, datetime

        block = await self._get_by_id(agent_id, block_id)
        if block is None:
            raise ValueError(f"Memory block {block_id} not found")

        block.deleted_at = datetime.now(UTC)
        await self.db.flush()
        logger.info(f"Deleted memory block {block_id} for agent {agent_id}")

    async def list_blocks(
        self,
        agent_id: uuid.UUID,
    ) -> list[MemoryBlockReadSchema]:
        """List all memory blocks for an agent.

        Args:
            agent_id: The agent to list blocks for.

        Returns:
            List of blocks ordered by position.
        """
        stmt = (
            select(MemoryBlockModel)
            .where(
                MemoryBlockModel.agent_id == agent_id,
                MemoryBlockModel.deleted_at.is_(None),
            )
            .order_by(MemoryBlockModel.position.asc())
        )
        result = await self.db.execute(stmt)
        blocks = result.scalars().all()
        return [MemoryBlockReadSchema.model_validate(b) for b in blocks]

    def inject_blocks(
        self,
        messages: list[dict[str, Any]],
        blocks: list[MemoryBlockReadSchema],
    ) -> list[dict[str, Any]]:
        """Inject memory blocks into message context.

        Inserts blocks as system messages at their configured positions.

        Args:
            messages: Original message list.
            blocks: Memory blocks to inject (ordered by position).

        Returns:
            Messages with blocks injected.
        """
        if not blocks:
            return messages

        # Build block messages
        block_messages = []
        for block in blocks:
            if block.content:
                block_messages.append(
                    {
                        "role": "system",
                        "content": f"[{block.label}]: {block.content}",
                    }
                )

        if not block_messages:
            return messages

        # Insert after system messages but before user messages
        insert_idx = 0
        for i, msg in enumerate(messages):
            if msg.get("role") == "system":
                insert_idx = i + 1
            else:
                break

        return messages[:insert_idx] + block_messages + messages[insert_idx:]

    async def _get_by_id(
        self,
        agent_id: uuid.UUID,
        block_id: uuid.UUID,
    ) -> MemoryBlockModel | None:
        """Get block by ID with agent ownership check."""
        stmt = select(MemoryBlockModel).where(
            MemoryBlockModel.id == block_id,
            MemoryBlockModel.agent_id == agent_id,
            MemoryBlockModel.deleted_at.is_(None),
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def _get_by_label(
        self,
        agent_id: uuid.UUID,
        label: str,
    ) -> MemoryBlockModel | None:
        """Get block by label within an agent."""
        stmt = select(MemoryBlockModel).where(
            MemoryBlockModel.agent_id == agent_id,
            MemoryBlockModel.label == label,
            MemoryBlockModel.deleted_at.is_(None),
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()
