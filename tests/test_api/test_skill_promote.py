"""API tests for the 5.9c skill promote endpoint."""

from __future__ import annotations

import pytest
from httpx import AsyncClient


async def _create_agent(client: AsyncClient) -> dict:
    resp = await client.post(
        "/api/agents",
        json={
            "name": "promote-agent",
            "model_config": {"model": "gpt-4o", "temperature": 0.7},
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _create_skill(client: AsyncClient, name: str, **overrides: object) -> dict:
    payload = {
        "name": name,
        "description": f"Description for {name}",
        "source": "project",
        "instructions": "Do the thing.",
    }
    payload.update(overrides)
    resp = await client.post("/api/skills", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


@pytest.mark.asyncio
async def test_promote_appends_skill_with_freeze_markers(client: AsyncClient) -> None:
    agent = await _create_agent(client)
    await _create_skill(client, "promotable")

    resp = await client.post(f"/api/agents/{agent['id']}/skills/promote", json={"skill_name": "promotable"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["skills"] == ["promotable"]
    assert body["frozen"] is False
    assert "next_step" in body


@pytest.mark.asyncio
async def test_promote_is_idempotent(client: AsyncClient) -> None:
    agent = await _create_agent(client)
    await _create_skill(client, "promotable")

    first = await client.post(f"/api/agents/{agent['id']}/skills/promote", json={"skill_name": "promotable"})
    assert first.status_code == 200
    second = await client.post(f"/api/agents/{agent['id']}/skills/promote", json={"skill_name": "promotable"})
    assert second.status_code == 200
    assert second.json()["skills"] == ["promotable"]


@pytest.mark.asyncio
async def test_promote_unknown_skill_404(client: AsyncClient) -> None:
    agent = await _create_agent(client)

    resp = await client.post(f"/api/agents/{agent['id']}/skills/promote", json={"skill_name": "ghost-skill"})
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"]["code"] == "SKILL_NOT_FOUND"
    detail = await client.get(f"/api/agents/{agent['id']}")
    assert detail.json()["skills"] == []


@pytest.mark.asyncio
async def test_promote_model_invisible_skill_404(client: AsyncClient) -> None:
    agent = await _create_agent(client)
    await _create_skill(client, "invisible", model_invocable=False)

    resp = await client.post(f"/api/agents/{agent['id']}/skills/promote", json={"skill_name": "invisible"})
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"]["code"] == "SKILL_NOT_MODEL_INVOCABLE"
    detail = await client.get(f"/api/agents/{agent['id']}")
    assert detail.json()["skills"] == []
