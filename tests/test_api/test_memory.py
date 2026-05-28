"""Integration tests for memory API endpoints."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_create_memory_block(client: AsyncClient) -> None:
    """Test POST /api/agents/{id}/memory-blocks creates a block."""
    agent_id = str(uuid.uuid4())
    data = {
        "label": "persona",
        "content": "You are a helpful assistant",
        "position": 0,
        "limit": 1000,
    }

    response = await client.post(f"/api/agents/{agent_id}/memory-blocks", json=data)
    assert response.status_code == 201

    result = response.json()
    assert result["label"] == "persona"
    assert result["agent_id"] == agent_id


@pytest.mark.asyncio
async def test_list_memory_blocks(client: AsyncClient) -> None:
    """Test GET /api/agents/{id}/memory-blocks lists blocks."""
    agent_id = str(uuid.uuid4())

    # Create blocks
    await client.post(
        f"/api/agents/{agent_id}/memory-blocks",
        json={
            "label": "block1",
            "content": "Content 1",
            "position": 1,
        },
    )
    await client.post(
        f"/api/agents/{agent_id}/memory-blocks",
        json={
            "label": "block2",
            "content": "Content 2",
            "position": 2,
        },
    )

    response = await client.get(f"/api/agents/{agent_id}/memory-blocks")
    assert response.status_code == 200

    result = response.json()
    assert len(result) == 2


@pytest.mark.asyncio
async def test_get_memory_block(client: AsyncClient) -> None:
    """Test GET /api/agents/{id}/memory-blocks/{block_id} returns block."""
    agent_id = str(uuid.uuid4())

    create_resp = await client.post(
        f"/api/agents/{agent_id}/memory-blocks",
        json={
            "label": "persona",
            "content": "Test content",
        },
    )
    block_id = create_resp.json()["id"]

    response = await client.get(f"/api/agents/{agent_id}/memory-blocks/{block_id}")
    assert response.status_code == 200

    result = response.json()
    assert result["id"] == block_id


@pytest.mark.asyncio
async def test_update_memory_block(client: AsyncClient) -> None:
    """Test PUT /api/agents/{id}/memory-blocks/{block_id} updates block."""
    agent_id = str(uuid.uuid4())

    create_resp = await client.post(
        f"/api/agents/{agent_id}/memory-blocks",
        json={
            "label": "persona",
            "content": "Original",
        },
    )
    block_id = create_resp.json()["id"]

    response = await client.put(
        f"/api/agents/{agent_id}/memory-blocks/{block_id}",
        json={"content": "Updated"},
    )
    assert response.status_code == 200

    result = response.json()
    assert result["content"] == "Updated"


@pytest.mark.asyncio
async def test_delete_memory_block(client: AsyncClient) -> None:
    """Test DELETE /api/agents/{id}/memory-blocks/{block_id} deletes block."""
    agent_id = str(uuid.uuid4())

    create_resp = await client.post(
        f"/api/agents/{agent_id}/memory-blocks",
        json={
            "label": "persona",
            "content": "Test",
        },
    )
    block_id = create_resp.json()["id"]

    response = await client.delete(f"/api/agents/{agent_id}/memory-blocks/{block_id}")
    assert response.status_code == 204

    # Verify deleted
    get_resp = await client.get(f"/api/agents/{agent_id}/memory-blocks/{block_id}")
    assert get_resp.status_code == 404


@pytest.mark.asyncio
async def test_create_memory(client: AsyncClient) -> None:
    """Test POST /api/memory creates a memory."""
    data = {
        "content": "User prefers Python",
        "scope": {"user_id": "user1"},
        "memory_type": "semantic",
        "importance": 0.8,
    }

    response = await client.post("/api/memory", json=data)
    assert response.status_code == 201

    result = response.json()
    assert result["content"] == "User prefers Python"
    assert result["memory_type"] == "semantic"


@pytest.mark.asyncio
async def test_list_memories(client: AsyncClient) -> None:
    """Test GET /api/memory lists memories."""
    await client.post("/api/memory", json={"content": "Fact 1", "memory_type": "semantic"})
    await client.post("/api/memory", json={"content": "Fact 2", "memory_type": "procedural"})

    response = await client.get("/api/memory")
    assert response.status_code == 200

    result = response.json()
    assert len(result) >= 2


@pytest.mark.asyncio
async def test_list_memories_with_filter(client: AsyncClient) -> None:
    """Test GET /api/memory with memory_type filter."""
    await client.post("/api/memory", json={"content": "Fact 1", "memory_type": "semantic"})
    await client.post("/api/memory", json={"content": "Fact 2", "memory_type": "procedural"})

    response = await client.get("/api/memory?memory_type=semantic")
    assert response.status_code == 200

    result = response.json()
    assert all(m["memory_type"] == "semantic" for m in result)


@pytest.mark.asyncio
async def test_delete_memory(client: AsyncClient) -> None:
    """Test DELETE /api/memory/{id} deletes memory."""
    create_resp = await client.post("/api/memory", json={"content": "To delete"})
    memory_id = create_resp.json()["id"]

    response = await client.delete(f"/api/memory/{memory_id}")
    assert response.status_code == 204
