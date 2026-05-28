"""Unit tests for WorkingMemoryService."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.memory import MemoryBlockCreateSchema, MemoryBlockReadSchema, MemoryBlockUpdateSchema
from hecate.services.memory.working_memory import WorkingMemoryService


@pytest.mark.asyncio
async def test_create_block(db_session: AsyncSession) -> None:
    """Test creating a memory block."""
    service = WorkingMemoryService(db_session)
    agent_id = uuid.uuid4()

    data = MemoryBlockCreateSchema(
        label="persona",
        content="You are a helpful assistant",
        position=0,
        limit=1000,
    )

    result = await service.create_block(agent_id, data)

    assert result.label == "persona"
    assert result.content == "You are a helpful assistant"
    assert result.agent_id == agent_id


@pytest.mark.asyncio
async def test_create_duplicate_label(db_session: AsyncSession) -> None:
    """Test creating a duplicate label raises ValueError."""
    service = WorkingMemoryService(db_session)
    agent_id = uuid.uuid4()

    data = MemoryBlockCreateSchema(label="persona", content="First")

    await service.create_block(agent_id, data)

    with pytest.raises(ValueError, match="already exists"):
        await service.create_block(agent_id, data)


@pytest.mark.asyncio
async def test_get_block(db_session: AsyncSession) -> None:
    """Test getting a memory block."""
    service = WorkingMemoryService(db_session)
    agent_id = uuid.uuid4()

    data = MemoryBlockCreateSchema(label="persona", content="Test")
    created = await service.create_block(agent_id, data)

    result = await service.get_block(agent_id, created.id)

    assert result.id == created.id
    assert result.label == "persona"


@pytest.mark.asyncio
async def test_get_block_not_found(db_session: AsyncSession) -> None:
    """Test getting a non-existent block raises ValueError."""
    service = WorkingMemoryService(db_session)

    with pytest.raises(ValueError, match="not found"):
        await service.get_block(uuid.uuid4(), uuid.uuid4())


@pytest.mark.asyncio
async def test_update_block(db_session: AsyncSession) -> None:
    """Test updating a memory block."""
    service = WorkingMemoryService(db_session)
    agent_id = uuid.uuid4()

    data = MemoryBlockCreateSchema(label="persona", content="Original")
    created = await service.create_block(agent_id, data)

    update = MemoryBlockUpdateSchema(content="Updated content")
    result = await service.update_block(agent_id, created.id, update)

    assert result.content == "Updated content"
    assert result.label == "persona"


@pytest.mark.asyncio
async def test_delete_block(db_session: AsyncSession) -> None:
    """Test deleting a memory block."""
    service = WorkingMemoryService(db_session)
    agent_id = uuid.uuid4()

    data = MemoryBlockCreateSchema(label="persona", content="Test")
    created = await service.create_block(agent_id, data)

    await service.delete_block(agent_id, created.id)

    with pytest.raises(ValueError, match="not found"):
        await service.get_block(agent_id, created.id)


@pytest.mark.asyncio
async def test_list_blocks(db_session: AsyncSession) -> None:
    """Test listing memory blocks."""
    service = WorkingMemoryService(db_session)
    agent_id = uuid.uuid4()

    await service.create_block(agent_id, MemoryBlockCreateSchema(label="b", content="B", position=2))
    await service.create_block(agent_id, MemoryBlockCreateSchema(label="a", content="A", position=1))
    await service.create_block(agent_id, MemoryBlockCreateSchema(label="c", content="C", position=3))

    blocks = await service.list_blocks(agent_id)

    assert len(blocks) == 3
    assert blocks[0].label == "a"
    assert blocks[1].label == "b"
    assert blocks[2].label == "c"


@pytest.mark.asyncio
async def test_inject_blocks(db_session: AsyncSession) -> None:
    """Test injecting blocks into messages."""
    service = WorkingMemoryService(db_session)

    messages = [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "Hello"},
    ]

    blocks = [
        MemoryBlockReadSchema(
            id=uuid.uuid4(),
            agent_id=uuid.uuid4(),
            label="persona",
            content="You are a coding expert",
            position=0,
            limit=1000,
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
            deleted_at=None,
        ),
    ]

    result = service.inject_blocks(messages, blocks)

    assert len(result) == 3
    assert "[persona]" in result[1]["content"]


@pytest.mark.asyncio
async def test_inject_empty_blocks(db_session: AsyncSession) -> None:
    """Test injecting empty blocks returns original messages."""
    service = WorkingMemoryService(db_session)

    messages = [{"role": "user", "content": "Hello"}]
    result = service.inject_blocks(messages, [])

    assert result == messages
