"""Unit tests for UserMemoryService."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.memory import MemoryCreateSchema
from hecate.services.memory.user_memory import UserMemoryService


@pytest.mark.asyncio
async def test_store_memory(db_session: AsyncSession) -> None:
    """Test storing a memory."""
    service = UserMemoryService(db_session)

    data = MemoryCreateSchema(
        content="User prefers Python over JavaScript",
        scope={"user_id": "user1"},
        memory_type="semantic",
        importance=0.8,
    )

    result = await service.store_memory(data)

    assert result.content == "User prefers Python over JavaScript"
    assert result.memory_type == "semantic"
    assert result.importance == 0.8


@pytest.mark.asyncio
async def test_retrieve_memories(db_session: AsyncSession) -> None:
    """Test retrieving memories."""
    service = UserMemoryService(db_session)

    await service.store_memory(MemoryCreateSchema(content="Fact 1", importance=0.8))
    await service.store_memory(MemoryCreateSchema(content="Fact 2", importance=0.5))
    await service.store_memory(MemoryCreateSchema(content="Fact 3", importance=0.3))

    results = await service.retrieve_memories("query", top_k=2)

    assert len(results) == 2


@pytest.mark.asyncio
async def test_update_importance(db_session: AsyncSession) -> None:
    """Test updating memory importance."""
    service = UserMemoryService(db_session)

    data = MemoryCreateSchema(content="Test memory", importance=0.5)
    memory = await service.store_memory(data)

    new_importance = await service.update_importance(memory.id, boost=0.2)

    assert new_importance == 0.7


@pytest.mark.asyncio
async def test_update_importance_capped(db_session: AsyncSession) -> None:
    """Test that importance is capped at 1.0."""
    service = UserMemoryService(db_session)

    data = MemoryCreateSchema(content="Test memory", importance=0.9)
    memory = await service.store_memory(data)

    new_importance = await service.update_importance(memory.id, boost=0.5)

    assert new_importance == 1.0


@pytest.mark.asyncio
async def test_delete_memory(db_session: AsyncSession) -> None:
    """Test deleting a memory."""
    service = UserMemoryService(db_session)

    data = MemoryCreateSchema(content="To delete")
    memory = await service.store_memory(data)

    await service.delete_memory(memory.id)

    with pytest.raises(ValueError, match="not found"):
        await service.update_importance(memory.id)


@pytest.mark.asyncio
async def test_list_memories(db_session: AsyncSession) -> None:
    """Test listing memories."""
    service = UserMemoryService(db_session)

    await service.store_memory(MemoryCreateSchema(content="Fact 1", memory_type="semantic"))
    await service.store_memory(MemoryCreateSchema(content="Fact 2", memory_type="procedural"))
    await service.store_memory(MemoryCreateSchema(content="Fact 3", memory_type="semantic"))

    results = await service.list_memories(memory_type="semantic")

    assert len(results) == 2


@pytest.mark.asyncio
async def test_extract_facts(db_session: AsyncSession) -> None:
    """Test extracting facts from messages."""
    service = UserMemoryService(db_session)

    messages = [
        {"role": "user", "content": "I prefer Python over JavaScript"},
        {"role": "assistant", "content": "Python is great!"},
        {"role": "user", "content": "What time is it?"},
    ]

    facts = await service.extract_facts(messages)

    assert len(facts) == 1
    assert "Python" in facts[0]
