"""Tests for KnowledgeMemoryModel ORM, KnowledgeMemoryService, and workspace isolation.

Covers:
- Task 6.1: KnowledgeMemoryModel ORM — create, read, soft-delete, workspace_id filtering
- Task 6.2: KnowledgeMemoryService — insert, list, get, delete, duplicate detection, search
- Task 6.3: Workspace isolation — L1/L3/L4 queries only return data within correct workspace
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.memory import KnowledgeMemoryModel
from hecate.services.memory.knowledge_memory import KnowledgeMemoryService
from hecate.services.memory.user_memory import UserMemoryService
from hecate.services.memory.working_memory import WorkingMemoryService

# ---------------------------------------------------------------------------
# 6.1 — KnowledgeMemoryModel ORM
# ---------------------------------------------------------------------------


async def test_create_knowledge_memory_model(db_session: AsyncSession) -> None:
    """Test creating a KnowledgeMemoryModel with all fields."""
    agent_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    memory = KnowledgeMemoryModel(
        workspace_id=workspace_id,
        agent_id=agent_id,
        content="Python is a great language for data science",
        tags=["programming", "python"],
        importance=0.8,
        source="agent_tool",
    )
    db_session.add(memory)
    await db_session.flush()

    assert memory.id is not None
    assert memory.workspace_id == workspace_id
    assert memory.agent_id == agent_id
    assert memory.content == "Python is a great language for data science"
    assert memory.tags == ["programming", "python"]
    assert memory.importance == 0.8
    assert memory.access_count == 0
    assert memory.source == "agent_tool"
    assert memory.user_id is None
    assert memory.deleted is False


async def test_knowledge_memory_model_defaults(db_session: AsyncSession) -> None:
    """Test KnowledgeMemoryModel default values."""
    memory = KnowledgeMemoryModel(
        workspace_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        content="Test content",
    )
    db_session.add(memory)
    await db_session.flush()

    assert memory.tags == []
    assert memory.importance == 0.5
    assert memory.access_count == 0
    assert memory.source == "agent_tool"
    assert memory.user_id is None


async def test_knowledge_memory_model_with_user_id(db_session: AsyncSession) -> None:
    """Test KnowledgeMemoryModel with optional user_id."""
    user_id = uuid.uuid4()
    memory = KnowledgeMemoryModel(
        workspace_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        content="User-specific knowledge",
        user_id=user_id,
    )
    db_session.add(memory)
    await db_session.flush()

    assert memory.user_id == user_id


async def test_knowledge_memory_model_soft_delete(db_session: AsyncSession) -> None:
    """Test soft-delete sets deleted flag and deleted_at."""
    from datetime import UTC, datetime

    memory = KnowledgeMemoryModel(
        workspace_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        content="To be deleted",
    )
    db_session.add(memory)
    await db_session.flush()

    memory_id = memory.id
    memory.deleted = True
    memory.deleted_at = datetime.now(UTC)
    await db_session.flush()

    stmt = select(KnowledgeMemoryModel).where(
        KnowledgeMemoryModel.id == memory_id,
        ~KnowledgeMemoryModel.deleted,
    )
    result = await db_session.execute(stmt)
    assert result.scalar_one_or_none() is None


async def test_knowledge_memory_model_workspace_filtering(db_session: AsyncSession) -> None:
    """Test that queries filter by workspace_id correctly."""
    ws_a = uuid.uuid4()
    ws_b = uuid.uuid4()
    agent_id = uuid.uuid4()

    for ws in (ws_a, ws_b):
        db_session.add(
            KnowledgeMemoryModel(
                workspace_id=ws,
                agent_id=agent_id,
                content=f"Knowledge in workspace {ws}",
            )
        )
    await db_session.flush()

    stmt = select(KnowledgeMemoryModel).where(
        KnowledgeMemoryModel.workspace_id == ws_a,
        ~KnowledgeMemoryModel.deleted,
    )
    result = await db_session.execute(stmt)
    rows = result.scalars().all()
    assert len(rows) == 1
    assert rows[0].workspace_id == ws_a


# ---------------------------------------------------------------------------
# 6.2 — KnowledgeMemoryService
# ---------------------------------------------------------------------------


async def test_insert_knowledge(db_session: AsyncSession) -> None:
    """Test inserting knowledge without vector store (DB-only)."""
    agent_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    service = KnowledgeMemoryService(db_session)

    result = await service.insert_knowledge(
        agent_id=agent_id,
        workspace_id=workspace_id,
        content="FastAPI is a modern Python web framework",
        tags=["python", "web"],
        importance=0.7,
    )

    assert result.content == "FastAPI is a modern Python web framework"
    assert result.agent_id == agent_id
    assert result.workspace_id == workspace_id
    assert result.tags == ["python", "web"]
    assert result.importance == 0.7
    assert result.source == "agent_tool"
    assert result.access_count == 0


async def test_insert_knowledge_duplicate_detection(db_session: AsyncSession) -> None:
    """Test that inserting duplicate content increments access_count instead of creating new row."""
    agent_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    service = KnowledgeMemoryService(db_session)

    first = await service.insert_knowledge(
        agent_id=agent_id,
        workspace_id=workspace_id,
        content="Duplicate content test",
    )
    assert first.access_count == 0

    second = await service.insert_knowledge(
        agent_id=agent_id,
        workspace_id=workspace_id,
        content="Duplicate content test",
    )
    assert second.id == first.id
    assert second.access_count == 1


async def test_insert_knowledge_no_cross_agent_duplicate(db_session: AsyncSession) -> None:
    """Test that duplicate detection is scoped to agent + workspace."""
    ws = uuid.uuid4()
    agent_a = uuid.uuid4()
    agent_b = uuid.uuid4()
    service = KnowledgeMemoryService(db_session)

    await service.insert_knowledge(agent_id=agent_a, workspace_id=ws, content="Shared fact")
    result = await service.insert_knowledge(agent_id=agent_b, workspace_id=ws, content="Shared fact")

    stmt = select(KnowledgeMemoryModel).where(~KnowledgeMemoryModel.deleted)
    rows = (await db_session.execute(stmt)).scalars().all()
    assert len(rows) == 2
    assert result.agent_id == agent_b


async def test_list_knowledge(db_session: AsyncSession) -> None:
    """Test listing knowledge memories with pagination."""
    agent_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    service = KnowledgeMemoryService(db_session)

    for i in range(5):
        await service.insert_knowledge(
            agent_id=agent_id,
            workspace_id=workspace_id,
            content=f"Knowledge fact {i}",
        )

    memories, total = await service.list_knowledge(agent_id=agent_id, workspace_id=workspace_id)
    assert total == 5
    assert len(memories) == 5


async def test_list_knowledge_pagination(db_session: AsyncSession) -> None:
    """Test list_knowledge with limit and offset."""
    agent_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    service = KnowledgeMemoryService(db_session)

    for i in range(5):
        await service.insert_knowledge(
            agent_id=agent_id,
            workspace_id=workspace_id,
            content=f"Fact {i}",
        )

    memories, total = await service.list_knowledge(
        agent_id=agent_id,
        workspace_id=workspace_id,
        limit=2,
        offset=0,
    )
    assert total == 5
    assert len(memories) == 2


async def test_list_knowledge_with_tag_filter(db_session: AsyncSession) -> None:
    """Test list_knowledge with tag filtering."""
    agent_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    service = KnowledgeMemoryService(db_session)

    await service.insert_knowledge(
        agent_id=agent_id,
        workspace_id=workspace_id,
        content="Tagged fact",
        tags=["python"],
    )
    await service.insert_knowledge(
        agent_id=agent_id,
        workspace_id=workspace_id,
        content="Untagged fact",
        tags=["java"],
    )

    memories, total = await service.list_knowledge(
        agent_id=agent_id,
        workspace_id=workspace_id,
        tags=["python"],
    )
    assert total == 1
    assert memories[0].tags == ["python"]


async def test_get_knowledge(db_session: AsyncSession) -> None:
    """Test getting a single knowledge memory by ID."""
    agent_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    service = KnowledgeMemoryService(db_session)

    created = await service.insert_knowledge(
        agent_id=agent_id,
        workspace_id=workspace_id,
        content="Retrieve me",
    )

    result = await service.get_knowledge(agent_id, workspace_id, created.id)
    assert result.id == created.id
    assert result.content == "Retrieve me"


async def test_get_knowledge_not_found(db_session: AsyncSession) -> None:
    """Test get_knowledge raises ValueError for non-existent ID."""
    service = KnowledgeMemoryService(db_session)
    with pytest.raises(ValueError, match="not found"):
        await service.get_knowledge(uuid.uuid4(), uuid.uuid4(), uuid.uuid4())


async def test_delete_knowledge(db_session: AsyncSession) -> None:
    """Test soft-deleting a knowledge memory."""
    agent_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    service = KnowledgeMemoryService(db_session)

    created = await service.insert_knowledge(
        agent_id=agent_id,
        workspace_id=workspace_id,
        content="Delete me",
    )

    await service.delete_knowledge(agent_id, workspace_id, created.id)

    with pytest.raises(ValueError, match="not found"):
        await service.get_knowledge(agent_id, workspace_id, created.id)


async def test_delete_knowledge_not_found(db_session: AsyncSession) -> None:
    """Test delete_knowledge raises ValueError for non-existent ID."""
    service = KnowledgeMemoryService(db_session)
    with pytest.raises(ValueError, match="not found"):
        await service.delete_knowledge(uuid.uuid4(), uuid.uuid4(), uuid.uuid4())


async def test_search_knowledge_no_vector_store(db_session: AsyncSession) -> None:
    """Test search returns empty list when no vector store is configured."""
    service = KnowledgeMemoryService(db_session)
    results = await service.search_knowledge(
        agent_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        query="test query",
    )
    assert results == []


# ---------------------------------------------------------------------------
# 6.3 — Workspace Isolation
# ---------------------------------------------------------------------------


async def test_l1_workspace_isolation(db_session: AsyncSession) -> None:
    """Test L1 memory blocks are isolated by workspace."""
    from hecate.models.memory import MemoryBlockCreateSchema

    ws_a = uuid.uuid4()
    ws_b = uuid.uuid4()
    agent_a = uuid.uuid4()
    agent_b = uuid.uuid4()
    service = WorkingMemoryService(db_session)

    await service.create_block(agent_a, ws_a, MemoryBlockCreateSchema(label="ctx", content="WS-A"))
    await service.create_block(agent_b, ws_b, MemoryBlockCreateSchema(label="ctx", content="WS-B"))

    blocks_a = await service.list_blocks(agent_a, ws_a)
    blocks_b = await service.list_blocks(agent_b, ws_b)

    assert len(blocks_a) == 1
    assert blocks_a[0].content == "WS-A"
    assert len(blocks_b) == 1
    assert blocks_b[0].content == "WS-B"


async def test_l3_workspace_isolation(db_session: AsyncSession) -> None:
    """Test L3 user memories are isolated by workspace."""
    from hecate.models.memory import MemoryCreateSchema

    ws_a = uuid.uuid4()
    ws_b = uuid.uuid4()
    service = UserMemoryService(db_session)

    await service.store_memory(ws_a, MemoryCreateSchema(content="WS-A fact"))
    await service.store_memory(ws_b, MemoryCreateSchema(content="WS-B fact"))

    count_a = await service.count_memories(ws_a)
    count_b = await service.count_memories(ws_b)
    assert count_a == 1
    assert count_b == 1


async def test_l4_workspace_isolation(db_session: AsyncSession) -> None:
    """Test L4 knowledge memories are isolated by workspace."""
    ws_a = uuid.uuid4()
    ws_b = uuid.uuid4()
    agent_a = uuid.uuid4()
    agent_b = uuid.uuid4()
    service = KnowledgeMemoryService(db_session)

    await service.insert_knowledge(agent_a, ws_a, content="WS-A knowledge")
    await service.insert_knowledge(agent_b, ws_b, content="WS-B knowledge")

    _, total_a = await service.list_knowledge(agent_a, ws_a)
    _, total_b = await service.list_knowledge(agent_b, ws_b)
    assert total_a == 1
    assert total_b == 1

    with pytest.raises(ValueError):
        await service.get_knowledge(agent_a, ws_a, (await service.list_knowledge(agent_b, ws_b))[0][0].id)


async def test_l4_agent_isolation_same_workspace(db_session: AsyncSession) -> None:
    """Test L4 knowledge is isolated by agent_id even within same workspace."""
    ws = uuid.uuid4()
    agent_a = uuid.uuid4()
    agent_b = uuid.uuid4()
    service = KnowledgeMemoryService(db_session)

    await service.insert_knowledge(agent_a, ws, content="Agent A knowledge")
    await service.insert_knowledge(agent_b, ws, content="Agent B knowledge")

    memories_a, total_a = await service.list_knowledge(agent_a, ws)
    memories_b, total_b = await service.list_knowledge(agent_b, ws)

    assert total_a == 1
    assert total_b == 1
    assert memories_a[0].content == "Agent A knowledge"
    assert memories_b[0].content == "Agent B knowledge"
