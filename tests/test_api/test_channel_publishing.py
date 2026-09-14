"""Tests for publishing channels (1.3.20).

Covers:
- Channel creation requires a published version; im requires provider config
- embed/webhook types are created as unwired placeholders
- Pinned bindings validate the target version exists
- published-mode channels follow new publishes; pinned channels do not
- X-Agent-Version / ?version= explicit debugging override (unknown → 404)
- The direct agent endpoint keeps serving the live draft
- Deleting a channel-pinned version is refused (VERSION_PINNED)
- Repointing and mode switches behave (published clears pinned_version)
- Disabled channels are not invokable
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

from httpx import AsyncClient


async def _create_agent(client: AsyncClient, persona: str = "Draft persona") -> dict:
    resp = await client.post(
        "/api/agents",
        json={
            "name": "Channel Agent",
            "model_config": {"model": "gpt-4o"},
            "mode": "chat",
            "persona": persona,
        },
    )
    assert resp.status_code == 201
    return resp.json()


async def _commit(client: AsyncClient, agent_id: str) -> dict:
    resp = await client.post(f"/api/agents/{agent_id}/versions/commit", json={})
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _publish(client: AsyncClient, agent_id: str, version: int) -> None:
    resp = await client.post(f"/api/agents/{agent_id}/publish/{version}")
    assert resp.status_code == 200, resp.text


async def _create_channel(client: AsyncClient, agent_id: str, **overrides: object) -> dict:
    payload: dict = {"name": "api-chan", "type": "api", "agent_id": agent_id}
    payload.update(overrides)
    return await client.post("/api/channels", json=payload)


async def test_create_channel_requires_published_version(client: AsyncClient) -> None:
    agent = await _create_agent(client)
    resp = await _create_channel(client, agent["id"])
    assert resp.status_code == 400
    assert "no published version" in resp.json()["detail"]["error"]["message"]


async def test_create_channel_wired_and_placeholder(client: AsyncClient) -> None:
    agent = await _create_agent(client)
    await _commit(client, agent["id"])
    await _publish(client, agent["id"], 1)

    api_resp = await _create_channel(client, agent["id"], name="api-main")
    assert api_resp.status_code == 201, api_resp.text
    assert api_resp.json()["status"] == "active"
    assert api_resp.json()["bind_mode"] == "published"

    embed_resp = await _create_channel(client, agent["id"], name="embed-hold", type="webhook")
    assert embed_resp.status_code == 201
    assert embed_resp.json()["status"] == "unwired"

    im_resp = await _create_channel(client, agent["id"], name="im-missing", type="im")
    assert im_resp.status_code == 400
    im_resp = await _create_channel(client, agent["id"], name="im-ok", type="im", config={"provider": "feishu"})
    assert im_resp.status_code == 201


async def test_pinned_create_requires_existing_version(client: AsyncClient) -> None:
    agent = await _create_agent(client)
    await _commit(client, agent["id"])
    await _publish(client, agent["id"], 1)

    resp = await _create_channel(client, agent["id"], bind_mode="pinned", pinned_version=99)
    assert resp.status_code == 400

    resp = await _create_channel(client, agent["id"], bind_mode="pinned")
    assert resp.status_code == 400

    resp = await _create_channel(client, agent["id"], name="pin-ok", bind_mode="pinned", pinned_version=1)
    assert resp.status_code == 201
    assert resp.json()["pinned_version"] == 1


async def _invoke_channel(
    client: AsyncClient,
    channel_id: str,
    *,
    json: dict | None = None,
    headers: dict | None = None,
    version: int | None = None,
) -> object:
    """Invoke a channel with ``_process_chat`` patched out; return the response.

    The patch captures the ``preloaded_agent`` the route resolved so tests
    assert on the frozen config without running the LLM stack.
    """
    url = f"/v1/channels/{channel_id}/chat/completions"
    if version is not None:
        url += f"?version={version}"
    with patch(
        "hecate.channel.api.v1.channels._process_chat",
        new=AsyncMock(return_value={"choices": []}),
    ) as mock_chat:
        resp = await client.post(
            url,
            json=json or {"model": "ignored", "messages": [{"role": "user", "content": "hi"}]},
            headers=headers or {},
        )
        return resp, mock_chat


async def test_published_channel_follows_new_publish(client: AsyncClient) -> None:
    agent = await _create_agent(client, persona="Persona V1")
    agent_id = agent["id"]
    await _commit(client, agent_id)
    await _publish(client, agent_id, 1)
    channel = (await _create_channel(client, agent_id, name="pub")).json()

    resp, mock = await _invoke_channel(client, channel["id"])
    assert resp.status_code == 200
    assert mock.call_args.kwargs["preloaded_agent"].persona == "Persona V1"

    await client.put(f"/api/agents/{agent_id}", json={"persona": "Persona V2"})
    await _commit(client, agent_id)
    await _publish(client, agent_id, 2)

    resp, mock = await _invoke_channel(client, channel["id"])
    assert resp.status_code == 200
    assert mock.call_args.kwargs["preloaded_agent"].persona == "Persona V2"


async def test_pinned_channel_ignores_new_publish(client: AsyncClient) -> None:
    agent = await _create_agent(client, persona="Persona V1")
    agent_id = agent["id"]
    await _commit(client, agent_id)
    await _publish(client, agent_id, 1)
    channel = (await _create_channel(client, agent_id, name="pin", bind_mode="pinned", pinned_version=1)).json()

    await client.put(f"/api/agents/{agent_id}", json={"persona": "Persona V2"})
    await _commit(client, agent_id)
    await _publish(client, agent_id, 2)

    resp, mock = await _invoke_channel(client, channel["id"])
    assert resp.status_code == 200
    assert mock.call_args.kwargs["preloaded_agent"].persona == "Persona V1"


async def test_explicit_version_override(client: AsyncClient) -> None:
    agent = await _create_agent(client, persona="Persona V1")
    agent_id = agent["id"]
    await _commit(client, agent_id)
    await _publish(client, agent_id, 1)
    channel = (await _create_channel(client, agent_id, name="pub")).json()

    # Header override pins the call to v1 even after v2 goes live.
    await client.put(f"/api/agents/{agent_id}", json={"persona": "Persona V2"})
    await _commit(client, agent_id)
    await _publish(client, agent_id, 2)

    resp, mock = await _invoke_channel(client, channel["id"], headers={"X-Agent-Version": "1"})
    assert resp.status_code == 200
    assert mock.call_args.kwargs["preloaded_agent"].persona == "Persona V1"

    resp, mock = await _invoke_channel(client, channel["id"], version=1)
    assert resp.status_code == 200
    assert mock.call_args.kwargs["preloaded_agent"].persona == "Persona V1"

    resp, _ = await _invoke_channel(client, channel["id"], headers={"X-Agent-Version": "99"})
    assert resp.status_code == 404

    resp, _ = await _invoke_channel(client, channel["id"], headers={"X-Agent-Version": "abc"})
    assert resp.status_code == 400


async def test_direct_agent_endpoint_serves_live_draft(client: AsyncClient) -> None:
    agent = await _create_agent(client, persona="Live draft")
    agent_id = agent["id"]
    await _commit(client, agent_id)
    await _publish(client, agent_id, 1)
    await client.put(f"/api/agents/{agent_id}", json={"persona": "Newer draft"})

    with patch(
        "hecate.channel.api.v1.agents._process_chat",
        new=AsyncMock(return_value={"choices": []}),
    ) as mock:
        resp = await client.post(
            f"/v1/agents/{agent_id}/chat/completions",
            json={"model": "ignored", "messages": [{"role": "user", "content": "hi"}]},
        )
    assert resp.status_code == 200
    assert mock.call_args.kwargs["preloaded_agent"].persona == "Newer draft"


async def test_delete_pinned_version_conflict(client: AsyncClient) -> None:
    agent = await _create_agent(client)
    agent_id = agent["id"]
    await _commit(client, agent_id)
    await _publish(client, agent_id, 1)
    await _commit(client, agent_id)  # v2 unpublished — pin this one

    await _create_channel(client, agent_id, name="pin", bind_mode="pinned", pinned_version=2)

    resp = await client.delete(f"/api/agents/{agent_id}/versions/2")
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"]["code"] == "VERSION_PINNED"
    assert "pin" in resp.json()["detail"]["error"]["message"]

    # The published version is separately protected.
    resp = await client.delete(f"/api/agents/{agent_id}/versions/1")
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"]["code"] == "VERSION_PUBLISHED"


async def test_repoint_and_mode_switch(client: AsyncClient) -> None:
    agent = await _create_agent(client)
    agent_id = agent["id"]
    await _commit(client, agent_id)
    await _publish(client, agent_id, 1)
    await _commit(client, agent_id)
    channel = (await _create_channel(client, agent_id, name="pin", bind_mode="pinned", pinned_version=1)).json()

    resp = await client.put(f"/api/channels/{channel['id']}", json={"pinned_version": 2})
    assert resp.status_code == 200
    assert resp.json()["pinned_version"] == 2

    resp = await client.put(f"/api/channels/{channel['id']}", json={"bind_mode": "published"})
    assert resp.status_code == 200
    assert resp.json()["bind_mode"] == "published"
    assert resp.json()["pinned_version"] is None

    resp = await client.put(f"/api/channels/{channel['id']}", json={"bind_mode": "pinned", "pinned_version": 99})
    assert resp.status_code == 400


async def test_disabled_channel_not_invokable(client: AsyncClient) -> None:
    agent = await _create_agent(client)
    agent_id = agent["id"]
    await _commit(client, agent_id)
    await _publish(client, agent_id, 1)
    channel = (await _create_channel(client, agent_id, name="off")).json()

    await client.put(f"/api/channels/{channel['id']}", json={"status": "disabled"})
    resp, _ = await _invoke_channel(client, channel["id"])
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"]["code"] == "CHANNEL_NOT_INVOKABLE"


async def test_unwired_channel_not_invokable(client: AsyncClient) -> None:
    agent = await _create_agent(client)
    agent_id = agent["id"]
    await _commit(client, agent_id)
    await _publish(client, agent_id, 1)
    channel = (await _create_channel(client, agent_id, name="hold", type="embed")).json()

    resp, _ = await _invoke_channel(client, channel["id"])
    assert resp.status_code == 409


async def test_channel_not_found(client: AsyncClient) -> None:
    fake = str(uuid.uuid4())
    resp, _ = await _invoke_channel(client, fake)
    assert resp.status_code == 404
