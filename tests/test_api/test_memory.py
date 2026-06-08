"""Integration tests for memory API endpoints.

Tests L1 working memory blocks, L3 user memories, and L4 knowledge memories.
L1/L4 endpoints require a real agent (via POST /api/agents).
L3 endpoints use workspace_id from auth context (zero UUID in P1).
"""

from __future__ import annotations

import uuid

from httpx import AsyncClient


async def _create_agent(client: AsyncClient) -> str:
    """Create a test agent and return its ID as string."""
    resp = await client.post(
        "/api/agents",
        json={
            "name": "Test Agent",
            "model_config": {"model": "gpt-4o"},
            "mode": "chat",
        },
    )
    assert resp.status_code == 201
    return resp.json()["id"]


# --- L1 Memory Block Endpoints ---


async def test_create_memory_block(client: AsyncClient) -> None:
    """Test POST /api/agents/{id}/memory-blocks creates a block."""
    agent_id = await _create_agent(client)
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


async def test_list_memory_blocks(client: AsyncClient) -> None:
    """Test GET /api/agents/{id}/memory-blocks lists blocks."""
    agent_id = await _create_agent(client)

    await client.post(
        f"/api/agents/{agent_id}/memory-blocks",
        json={"label": "block1", "content": "Content 1", "position": 1},
    )
    await client.post(
        f"/api/agents/{agent_id}/memory-blocks",
        json={"label": "block2", "content": "Content 2", "position": 2},
    )

    response = await client.get(f"/api/agents/{agent_id}/memory-blocks")
    assert response.status_code == 200

    result = response.json()
    assert len(result) == 2


async def test_get_memory_block(client: AsyncClient) -> None:
    """Test GET /api/agents/{id}/memory-blocks/{block_id} returns block."""
    agent_id = await _create_agent(client)

    create_resp = await client.post(
        f"/api/agents/{agent_id}/memory-blocks",
        json={"label": "persona", "content": "Test content"},
    )
    block_id = create_resp.json()["id"]

    response = await client.get(f"/api/agents/{agent_id}/memory-blocks/{block_id}")
    assert response.status_code == 200

    result = response.json()
    assert result["id"] == block_id


async def test_update_memory_block(client: AsyncClient) -> None:
    """Test PUT /api/agents/{id}/memory-blocks/{block_id} updates block."""
    agent_id = await _create_agent(client)

    create_resp = await client.post(
        f"/api/agents/{agent_id}/memory-blocks",
        json={"label": "persona", "content": "Original"},
    )
    block_id = create_resp.json()["id"]

    response = await client.put(
        f"/api/agents/{agent_id}/memory-blocks/{block_id}",
        json={"content": "Updated"},
    )
    assert response.status_code == 200

    result = response.json()
    assert result["content"] == "Updated"


async def test_delete_memory_block(client: AsyncClient) -> None:
    """Test DELETE /api/agents/{id}/memory-blocks/{block_id} deletes block."""
    agent_id = await _create_agent(client)

    create_resp = await client.post(
        f"/api/agents/{agent_id}/memory-blocks",
        json={"label": "persona", "content": "Test"},
    )
    block_id = create_resp.json()["id"]

    response = await client.delete(f"/api/agents/{agent_id}/memory-blocks/{block_id}")
    assert response.status_code == 204

    get_resp = await client.get(f"/api/agents/{agent_id}/memory-blocks/{block_id}")
    assert get_resp.status_code == 404


async def test_memory_block_nonexistent_agent(client: AsyncClient) -> None:
    """Test L1 endpoint returns 404 for non-existent agent."""
    fake_agent = str(uuid.uuid4())
    response = await client.post(
        f"/api/agents/{fake_agent}/memory-blocks",
        json={"label": "test", "content": "test"},
    )
    assert response.status_code == 404


# --- L3 User Memory Endpoints ---


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


async def test_list_memories(client: AsyncClient) -> None:
    """Test GET /api/memory lists memories."""
    await client.post("/api/memory", json={"content": "Fact 1", "memory_type": "semantic"})
    await client.post("/api/memory", json={"content": "Fact 2", "memory_type": "procedural"})

    response = await client.get("/api/memory")
    assert response.status_code == 200

    result = response.json()
    assert len(result) >= 2


async def test_list_memories_with_filter(client: AsyncClient) -> None:
    """Test GET /api/memory with memory_type filter."""
    await client.post("/api/memory", json={"content": "Fact 1", "memory_type": "semantic"})
    await client.post("/api/memory", json={"content": "Fact 2", "memory_type": "procedural"})

    response = await client.get("/api/memory", params={"memory_type": "semantic"})
    assert response.status_code == 200

    result = response.json()
    assert all(m["memory_type"] == "semantic" for m in result)


async def test_delete_memory(client: AsyncClient) -> None:
    """Test DELETE /api/memory/{id} deletes memory."""
    create_resp = await client.post("/api/memory", json={"content": "To delete"})
    memory_id = create_resp.json()["id"]

    response = await client.delete(f"/api/memory/{memory_id}")
    assert response.status_code == 204


async def test_list_user_memories(client: AsyncClient) -> None:
    """Test GET /api/users/{user_id}/memories lists user-scoped memories."""
    user_id = str(uuid.uuid4())

    await client.post(
        "/api/memory",
        json={"content": "User fact 1", "scope": {"user_id": user_id}, "memory_type": "semantic"},
    )
    await client.post(
        "/api/memory",
        json={"content": "User fact 2", "scope": {"user_id": user_id}, "memory_type": "semantic"},
    )

    response = await client.get(f"/api/users/{user_id}/memories")
    assert response.status_code == 200

    result = response.json()
    assert "items" in result
    assert "total" in result
    assert result["total"] >= 2


async def test_list_user_memories_with_filters(client: AsyncClient) -> None:
    """Test GET /api/users/{user_id}/memories with query filters."""
    user_id = str(uuid.uuid4())

    await client.post(
        "/api/memory",
        json={"content": "Semantic fact", "scope": {"user_id": user_id}, "memory_type": "semantic"},
    )
    await client.post(
        "/api/memory",
        json={"content": "Procedural fact", "scope": {"user_id": user_id}, "memory_type": "procedural"},
    )

    response = await client.get(f"/api/users/{user_id}/memories", params={"memory_type": "semantic"})
    assert response.status_code == 200

    result = response.json()
    assert all(m["memory_type"] == "semantic" for m in result["items"])


async def test_search_user_memories(client: AsyncClient) -> None:
    """Test GET /api/users/{user_id}/memories/search?q={query}."""
    user_id = str(uuid.uuid4())

    await client.post(
        "/api/memory",
        json={
            "content": "User loves Python programming",
            "scope": {"user_id": user_id},
            "memory_type": "semantic",
            "importance": 0.9,
        },
    )

    response = await client.get(f"/api/users/{user_id}/memories/search", params={"q": "Python"})
    assert response.status_code == 200

    result = response.json()
    assert "items" in result
    assert result["query"] == "Python"


async def test_search_user_memories_missing_query(client: AsyncClient) -> None:
    """Test GET /api/users/{user_id}/memories/search without q returns 422."""
    user_id = str(uuid.uuid4())

    response = await client.get(f"/api/users/{user_id}/memories/search")
    assert response.status_code == 422


# --- L4 Knowledge Memory Endpoints ---


async def test_create_knowledge_memory(client: AsyncClient) -> None:
    """Test POST /api/agents/{id}/knowledge creates a knowledge memory."""
    agent_id = await _create_agent(client)

    response = await client.post(
        f"/api/agents/{agent_id}/knowledge",
        json={
            "content": "FastAPI is a Python web framework",
            "tags": ["python", "web"],
            "importance": 0.7,
        },
    )
    assert response.status_code == 201

    result = response.json()
    assert result["content"] == "FastAPI is a Python web framework"
    assert result["agent_id"] == agent_id
    assert result["tags"] == ["python", "web"]
    assert result["importance"] == 0.7
    assert result["source"] == "agent_tool"


async def test_list_knowledge_memories(client: AsyncClient) -> None:
    """Test GET /api/agents/{id}/knowledge lists knowledge memories."""
    agent_id = await _create_agent(client)

    for i in range(3):
        await client.post(
            f"/api/agents/{agent_id}/knowledge",
            json={"content": f"Knowledge fact {i}"},
        )

    response = await client.get(f"/api/agents/{agent_id}/knowledge")
    assert response.status_code == 200

    result = response.json()
    assert result["total"] == 3
    assert len(result["items"]) == 3


async def test_list_knowledge_memories_with_tag_filter(client: AsyncClient) -> None:
    """Test GET /api/agents/{id}/knowledge?tags=python filters by tag."""
    agent_id = await _create_agent(client)

    await client.post(
        f"/api/agents/{agent_id}/knowledge",
        json={"content": "Python fact", "tags": ["python"]},
    )
    await client.post(
        f"/api/agents/{agent_id}/knowledge",
        json={"content": "Java fact", "tags": ["java"]},
    )

    response = await client.get(f"/api/agents/{agent_id}/knowledge", params={"tags": "python"})
    assert response.status_code == 200

    result = response.json()
    assert result["total"] == 1


async def test_get_knowledge_memory(client: AsyncClient) -> None:
    """Test GET /api/agents/{id}/knowledge/{memory_id} returns knowledge."""
    agent_id = await _create_agent(client)

    create_resp = await client.post(
        f"/api/agents/{agent_id}/knowledge",
        json={"content": "Retrieve me"},
    )
    memory_id = create_resp.json()["id"]

    response = await client.get(f"/api/agents/{agent_id}/knowledge/{memory_id}")
    assert response.status_code == 200

    result = response.json()
    assert result["id"] == memory_id
    assert result["content"] == "Retrieve me"


async def test_delete_knowledge_memory(client: AsyncClient) -> None:
    """Test DELETE /api/agents/{id}/knowledge/{memory_id} deletes knowledge."""
    agent_id = await _create_agent(client)

    create_resp = await client.post(
        f"/api/agents/{agent_id}/knowledge",
        json={"content": "Delete me"},
    )
    memory_id = create_resp.json()["id"]

    response = await client.delete(f"/api/agents/{agent_id}/knowledge/{memory_id}")
    assert response.status_code == 204

    get_resp = await client.get(f"/api/agents/{agent_id}/knowledge/{memory_id}")
    assert get_resp.status_code == 404


async def test_search_knowledge_memories(client: AsyncClient) -> None:
    """Test POST /api/agents/{id}/knowledge/search calls search endpoint."""
    agent_id = await _create_agent(client)

    response = await client.post(
        f"/api/agents/{agent_id}/knowledge/search",
        json={"query": "python web framework", "top_k": 5},
    )
    assert response.status_code == 200

    result = response.json()
    assert "items" in result
    assert "query" in result
    assert result["query"] == "python web framework"


async def test_knowledge_nonexistent_agent(client: AsyncClient) -> None:
    """Test L4 endpoint returns 404 for non-existent agent."""
    fake_agent = str(uuid.uuid4())
    response = await client.post(
        f"/api/agents/{fake_agent}/knowledge",
        json={"content": "test"},
    )
    assert response.status_code == 404


async def test_knowledge_cross_agent_isolation(client: AsyncClient) -> None:
    """Test that knowledge created for one agent is not visible to another."""
    agent_a = await _create_agent(client)
    agent_b = await _create_agent(client)

    await client.post(
        f"/api/agents/{agent_a}/knowledge",
        json={"content": "Agent A secret knowledge"},
    )

    resp_b = await client.get(f"/api/agents/{agent_b}/knowledge")
    assert resp_b.status_code == 200
    assert resp_b.json()["total"] == 0


# --- Session Compression Endpoint ---


async def test_get_compression_status(client: AsyncClient) -> None:
    """Test GET /api/sessions/{session_id}/compression returns status."""
    session_id = str(uuid.uuid4())

    response = await client.get(f"/api/sessions/{session_id}/compression")
    assert response.status_code == 200

    result = response.json()
    assert result["session_id"] == session_id
    assert "levels_available" in result
    assert "snip" in result["levels_available"]
