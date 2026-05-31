"""Tests for KB validation on agent create/update, cascade cleanup, and reverse lookup.

Covers:
- Valid KB IDs accepted on create
- Non-existent KB IDs rejected with 400
- Soft-deleted KB IDs rejected with 400
- Empty KB list accepted
- No KB IDs field accepted
- Cascade cleanup on KB delete
- Reverse lookup: agents using a KB
- Reverse lookup: non-existent KB returns 404
- Reverse lookup: pagination
"""

from __future__ import annotations

import uuid

from httpx import AsyncClient


def _agent_payload(**overrides: object) -> dict:
    payload = {
        "name": "test-agent",
        "model_config": {"model": "gpt-4o"},
        "mode": "chat",
        "knowledge_base_ids": [],
    }
    payload.update(overrides)
    return payload


def _kb_payload(**overrides: object) -> dict:
    payload = {
        "name": "test-kb",
        "description": "A test knowledge base",
    }
    payload.update(overrides)
    return payload


async def _create_kb(client: AsyncClient, name: str = "test-kb") -> dict:
    resp = await client.post("/api/knowledge-bases", json=_kb_payload(name=name))
    assert resp.status_code == 201
    return resp.json()


async def _create_agent(client: AsyncClient, kb_ids: list[str] | None = None, name: str = "test-agent") -> dict:
    payload = _agent_payload(name=name, knowledge_base_ids=kb_ids or [])
    resp = await client.post("/api/agents", json=payload)
    assert resp.status_code == 201
    return resp.json()


# --- Task 1.4: Validation tests ---


async def test_create_agent_with_valid_kb_ids(client: AsyncClient) -> None:
    kb = await _create_kb(client, "kb-valid")
    agent = await _create_agent(client, kb_ids=[kb["id"]])
    assert agent["knowledge_base_ids"] == [kb["id"]]


async def test_create_agent_with_nonexistent_kb_id(client: AsyncClient) -> None:
    fake_id = str(uuid.uuid4())
    payload = _agent_payload(knowledge_base_ids=[fake_id])
    resp = await client.post("/api/agents", json=payload)
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert detail["error"]["code"] == "INVALID_KB_IDS"
    assert fake_id in detail["error"]["message"]


async def test_create_agent_with_deleted_kb_id(client: AsyncClient) -> None:
    kb = await _create_kb(client, "kb-to-delete")
    await client.delete(f"/api/knowledge-bases/{kb['id']}")

    payload = _agent_payload(knowledge_base_ids=[kb["id"]])
    resp = await client.post("/api/agents", json=payload)
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert detail["error"]["code"] == "INVALID_KB_IDS"


async def test_create_agent_with_empty_kb_ids(client: AsyncClient) -> None:
    agent = await _create_agent(client, kb_ids=[])
    assert agent["knowledge_base_ids"] == []


async def test_create_agent_without_kb_ids(client: AsyncClient) -> None:
    payload = {"name": "no-kb-agent", "model_config": {"model": "gpt-4o"}, "mode": "chat"}
    resp = await client.post("/api/agents", json=payload)
    assert resp.status_code == 201
    assert resp.json()["knowledge_base_ids"] == []


async def test_update_agent_with_valid_kb_ids(client: AsyncClient) -> None:
    kb = await _create_kb(client, "kb-for-update")
    agent = await _create_agent(client)

    resp = await client.put(f"/api/agents/{agent['id']}", json={"knowledge_base_ids": [kb["id"]]})
    assert resp.status_code == 200
    assert resp.json()["knowledge_base_ids"] == [kb["id"]]


async def test_update_agent_with_invalid_kb_ids(client: AsyncClient) -> None:
    agent = await _create_agent(client)
    fake_id = str(uuid.uuid4())

    resp = await client.put(f"/api/agents/{agent['id']}", json={"knowledge_base_ids": [fake_id]})
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert detail["error"]["code"] == "INVALID_KB_IDS"


# --- Task 2.3: Cascade cleanup tests ---


async def test_cascade_cleanup_on_kb_delete(client: AsyncClient) -> None:
    kb1 = await _create_kb(client, "kb-cascade-1")
    kb2 = await _create_kb(client, "kb-cascade-2")
    agent = await _create_agent(client, kb_ids=[kb1["id"], kb2["id"]])

    await client.delete(f"/api/knowledge-bases/{kb1['id']}")

    resp = await client.get(f"/api/agents/{agent['id']}")
    assert resp.status_code == 200
    assert resp.json()["knowledge_base_ids"] == [kb2["id"]]


async def test_cascade_cleanup_multiple_agents(client: AsyncClient) -> None:
    kb = await _create_kb(client, "kb-shared")
    agent_a = await _create_agent(client, kb_ids=[kb["id"]], name="agent-a")
    agent_b = await _create_agent(client, kb_ids=[kb["id"]], name="agent-b")

    await client.delete(f"/api/knowledge-bases/{kb['id']}")

    resp_a = await client.get(f"/api/agents/{agent_a['id']}")
    resp_b = await client.get(f"/api/agents/{agent_b['id']}")
    assert resp_a.json()["knowledge_base_ids"] == []
    assert resp_b.json()["knowledge_base_ids"] == []


async def test_cascade_cleanup_no_agents(client: AsyncClient) -> None:
    kb = await _create_kb(client, "kb-orphan")
    resp = await client.delete(f"/api/knowledge-bases/{kb['id']}")
    assert resp.status_code == 204


async def test_cascade_cleanup_preserves_other_kbs(client: AsyncClient) -> None:
    kb1 = await _create_kb(client, "kb-keep")
    kb2 = await _create_kb(client, "kb-remove")
    agent = await _create_agent(client, kb_ids=[kb1["id"], kb2["id"]])

    await client.delete(f"/api/knowledge-bases/{kb2['id']}")

    resp = await client.get(f"/api/agents/{agent['id']}")
    assert resp.json()["knowledge_base_ids"] == [kb1["id"]]


# --- Task 3.3: Reverse lookup tests ---


async def test_reverse_lookup_agents_using_kb(client: AsyncClient) -> None:
    kb = await _create_kb(client, "kb-reverse")
    agent = await _create_agent(client, kb_ids=[kb["id"]])

    resp = await client.get(f"/api/knowledge-bases/{kb['id']}/agents")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["id"] == agent["id"]


async def test_reverse_lookup_no_agents(client: AsyncClient) -> None:
    kb = await _create_kb(client, "kb-empty-reverse")

    resp = await client.get(f"/api/knowledge-bases/{kb['id']}/agents")
    assert resp.status_code == 200
    assert resp.json()["total"] == 0
    assert resp.json()["items"] == []


async def test_reverse_lookup_nonexistent_kb(client: AsyncClient) -> None:
    fake_id = str(uuid.uuid4())
    resp = await client.get(f"/api/knowledge-bases/{fake_id}/agents")
    assert resp.status_code == 404


async def test_reverse_lookup_deleted_kb(client: AsyncClient) -> None:
    kb = await _create_kb(client, "kb-deleted-reverse")
    await client.delete(f"/api/knowledge-bases/{kb['id']}")

    resp = await client.get(f"/api/knowledge-bases/{kb['id']}/agents")
    assert resp.status_code == 404


async def test_reverse_lookup_pagination(client: AsyncClient) -> None:
    kb = await _create_kb(client, "kb-paginated")
    for i in range(5):
        await _create_agent(client, kb_ids=[kb["id"]], name=f"agent-{i}")

    resp = await client.get(f"/api/knowledge-bases/{kb['id']}/agents?page=1&page_size=2")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 5
    assert len(data["items"]) == 2

    resp2 = await client.get(f"/api/knowledge-bases/{kb['id']}/agents?page=3&page_size=2")
    data2 = resp2.json()
    assert data2["total"] == 5
    assert len(data2["items"]) == 1
