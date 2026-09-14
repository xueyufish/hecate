"""Tests for agent versioning lifecycle (1.3.20).

Covers:
- Commit creates an immutable snapshot; later draft edits do not affect it
- Version numbers increase monotonically; badge status tracks dirty state
- Publish moves the published pointer; /published reads it back
- Published versions cannot be deleted
- Rollback creates a new version and never moves the pointer
- Workflow references pin to the workflow's published version at commit
- Resolution seam: live draft vs frozen snapshot with pinned workflow version
- Evaluation gate: require mode blocks without runs, force bypasses, warn passes
- Drift: skill content changes after commit are detected
- Diff reports config and reference changes
"""

from __future__ import annotations

import uuid

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.studio.agents.versioning import AgentVersionService

VALID_DSL = {
    "version": "1.0",
    "name": "pin-workflow",
    "state": {"messages": {"type": "topic", "reduce": "append"}},
    "nodes": {"A": {"type": "conversation", "config": {"model": "gpt-4o"}}},
    "edges": [],
    "entry": "A",
}


async def _create_agent(client: AsyncClient, **overrides: object) -> dict:
    payload: dict = {
        "name": "Versioned Agent",
        "model_config": {"model": "gpt-4o"},
        "mode": "chat",
        "persona": "You are helpful",
    }
    payload.update(overrides)
    resp = await client.post("/api/agents", json=payload)
    assert resp.status_code == 201
    return resp.json()


async def _commit(client: AsyncClient, agent_id: str, **kwargs: object) -> dict:
    resp = await client.post(f"/api/agents/{agent_id}/versions/commit", json=kwargs)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _update_agent(client: AsyncClient, agent_id: str, **fields: object) -> None:
    resp = await client.put(f"/api/agents/{agent_id}", json=fields)
    assert resp.status_code == 200, resp.text


async def test_commit_creates_immutable_snapshot(client: AsyncClient) -> None:
    agent = await _create_agent(client)
    agent_id = agent["id"]

    v1 = await _commit(client, agent_id, name="first", change_summary="initial")
    assert v1["version"] == 1
    assert v1["config_snapshot"]["persona"] == "You are helpful"
    assert v1["is_published"] is False

    await _update_agent(client, agent_id, persona="Changed after commit")

    detail_resp = await client.get(f"/api/agents/{agent_id}/versions/1")
    assert detail_resp.status_code == 200
    assert detail_resp.json()["config_snapshot"]["persona"] == "You are helpful"

    status_resp = await client.get(f"/api/agents/{agent_id}/version-status")
    assert status_resp.status_code == 200
    status_data = status_resp.json()
    assert status_data["latest_version"] == 1
    assert status_data["has_uncommitted_changes"] is True

    await _commit(client, agent_id)
    status_resp = await client.get(f"/api/agents/{agent_id}/version-status")
    assert status_resp.json()["has_uncommitted_changes"] is False
    assert status_resp.json()["latest_version"] == 2


async def test_publish_moves_pointer(client: AsyncClient) -> None:
    agent = await _create_agent(client)
    agent_id = agent["id"]
    await _commit(client, agent_id)

    pub_resp = await client.post(f"/api/agents/{agent_id}/publish/1")
    assert pub_resp.status_code == 200, pub_resp.text
    assert pub_resp.json()["is_published"] is True

    read_resp = await client.get(f"/api/agents/{agent_id}")
    assert read_resp.json()["published_version"] == 1

    published_resp = await client.get(f"/api/agents/{agent_id}/published")
    assert published_resp.status_code == 200
    assert published_resp.json()["version"] == 1

    list_resp = await client.get(f"/api/agents/{agent_id}/versions")
    assert list_resp.json()["items"][0]["is_published"] is True


async def test_publish_unknown_version_404(client: AsyncClient) -> None:
    agent = await _create_agent(client)
    resp = await client.post(f"/api/agents/{agent['id']}/publish/99")
    assert resp.status_code == 404


async def test_published_version_cannot_be_deleted(client: AsyncClient) -> None:
    agent = await _create_agent(client)
    agent_id = agent["id"]
    await _commit(client, agent_id)
    await _commit(client, agent_id)
    pub_resp = await client.post(f"/api/agents/{agent_id}/publish/1")
    assert pub_resp.status_code == 200

    del_resp = await client.delete(f"/api/agents/{agent_id}/versions/1")
    assert del_resp.status_code == 409
    assert del_resp.json()["detail"]["error"]["code"] == "VERSION_PUBLISHED"

    del_resp = await client.delete(f"/api/agents/{agent_id}/versions/2")
    assert del_resp.status_code == 204


async def test_rollback_creates_new_version_not_live(client: AsyncClient) -> None:
    agent = await _create_agent(client)
    agent_id = agent["id"]
    await _commit(client, agent_id)  # v1: persona "You are helpful"
    await _update_agent(client, agent_id, persona="Second persona")
    await _commit(client, agent_id)  # v2
    await client.post(f"/api/agents/{agent_id}/publish/2")

    rb_resp = await client.post(f"/api/agents/{agent_id}/rollback/1")
    assert rb_resp.status_code == 200
    rolled = rb_resp.json()
    assert rolled["version"] == 3
    assert rolled["config_snapshot"]["persona"] == "You are helpful"
    assert "Rollback to version 1" in rolled["change_summary"]
    assert rolled["is_published"] is False

    status_resp = await client.get(f"/api/agents/{agent_id}/version-status")
    status_data = status_resp.json()
    assert status_data["published_version"] == 2
    assert status_data["latest_version"] == 3


async def test_workflow_pin_uses_published_version(client: AsyncClient) -> None:
    wf_resp = await client.post("/api/workflows", json={"name": "pin-wf", "graph_dsl": VALID_DSL})
    assert wf_resp.status_code == 201
    workflow_id = wf_resp.json()["id"]

    agent = await _create_agent(client, mode="workflow", workflow_id=workflow_id)
    agent_id = agent["id"]

    # Workflow never published: pin falls back to its highest version.
    v1 = await _commit(client, agent_id)
    assert v1["pinned_refs"] == [{"resource_type": "workflow", "resource_id": workflow_id, "version": 1}]

    # Publish workflow v1, then update it (creating v2): the next commit
    # still pins the *published* version 1, not the latest 2.
    pub_resp = await client.post(f"/api/workflows/{workflow_id}/publish/1")
    assert pub_resp.status_code == 200
    upd_resp = await client.put(
        f"/api/workflows/{workflow_id}",
        json={"graph_dsl": {**VALID_DSL, "name": "pin-wf-v2"}},
    )
    assert upd_resp.status_code == 200

    v2 = await _commit(client, agent_id)
    assert v2["pinned_refs"][0]["version"] == 1

    resolve_check = await client.get(f"/api/agents/{agent_id}/versions/2")
    assert resolve_check.json()["pinned_refs"][0]["version"] == 1


async def test_resolve_draft_vs_snapshot(db_session: AsyncSession, client: AsyncClient) -> None:
    agent = await _create_agent(client, persona="Draft persona")
    agent_id = uuid.UUID(agent["id"])
    commit_resp = await client.post(f"/api/agents/{agent_id}/versions/commit", json={})
    assert commit_resp.status_code == 200
    await _update_agent(client, agent["id"], persona="Edited draft")

    service = AgentVersionService(db_session)

    live = await service.resolve(agent_id)
    assert live.source == "live"
    assert live.config["persona"] == "Edited draft"
    assert live.workflow_version is None

    frozen = await service.resolve(agent_id, version=1)
    assert frozen.source == "version"
    assert frozen.version == 1
    assert frozen.config["persona"] == "Draft persona"

    try:
        await service.resolve(agent_id, version=99)
        raise AssertionError("expected ValueError for unknown version")
    except ValueError:
        pass


async def test_gate_require_blocks_without_runs_then_force_bypasses(client: AsyncClient) -> None:
    agent = await _create_agent(client)
    agent_id = agent["id"]
    await _commit(client, agent_id)

    gate = {"mode": "require", "require_run": True}
    await _update_agent(client, agent_id, evaluation_gate=gate)

    blocked = await client.post(f"/api/agents/{agent_id}/publish/1")
    assert blocked.status_code == 409
    body = blocked.json()["detail"]["error"]
    assert body["code"] == "EVALUATION_GATE_BLOCKED"
    assert body["details"]["gate"]["mode"] == "require"

    forced = await client.post(f"/api/agents/{agent_id}/publish/1", json={"force": True})
    assert forced.status_code == 200

    read_resp = await client.get(f"/api/agents/{agent_id}")
    assert read_resp.json()["published_version"] == 1


async def test_gate_warn_does_not_block(client: AsyncClient) -> None:
    agent = await _create_agent(client)
    agent_id = agent["id"]
    await _commit(client, agent_id)

    await _update_agent(client, agent_id, evaluation_gate={"mode": "warn", "require_run": True})

    pub_resp = await client.post(f"/api/agents/{agent_id}/publish/1")
    assert pub_resp.status_code == 200
    assert pub_resp.json()["is_published"] is True


async def test_drift_detects_skill_change(db_session: AsyncSession) -> None:
    from datetime import UTC, datetime

    from hecate.models.agent import AgentModel
    from hecate.models.skill import SkillModel

    zero_uuid = uuid.UUID(int=0)
    agent = AgentModel(
        workspace_id=zero_uuid,
        name="Drift Agent",
        model_config_db={"model": "gpt-4o"},
        mode="chat",
        skills=["translator"],
    )
    skill = SkillModel(
        name="translator",
        description="test skill",
        instructions="Translate to French",
        source="user",
        workspace_id=zero_uuid,
    )
    db_session.add_all([agent, skill])
    await db_session.flush()

    service = AgentVersionService(db_session)
    committed = await service.commit_version(agent.id)
    assert committed["ref_manifest"][0]["content_hash"] is not None

    clean = await service.version_drift(agent.id, 1)
    assert clean["drifted"] == []

    skill.instructions = "Translate to German"
    skill.updated_at = datetime.now(UTC)
    await db_session.flush()

    drifted = await service.version_drift(agent.id, 1)
    assert len(drifted["drifted"]) == 1
    entry = drifted["drifted"][0]
    assert entry["resource_type"] == "skill"
    assert entry["resource_id"] == "translator"
    assert entry["missing"] is False


async def test_diff_reports_config_and_reference_changes(db_session: AsyncSession, client: AsyncClient) -> None:
    agent = await _create_agent(client, persona="A", skills=["skill-one"])
    agent_id = agent["id"]

    await _commit(client, agent_id)  # v1
    await _update_agent(client, agent_id, persona="B", skills=["skill-two"])
    await _commit(client, agent_id)  # v2

    diff_resp = await client.get(f"/api/agents/{agent_id}/diff?v1=1&v2=2")
    assert diff_resp.status_code == 200
    diff = diff_resp.json()
    assert diff["identical"] is False
    assert diff["summary"]["values_changed"] >= 1
    ref_changes = diff["details"]["reference_changes"]
    assert {"change": "removed", "resource_type": "skill", "resource_id": "skill-one"} in ref_changes
    assert {"change": "added", "resource_type": "skill", "resource_id": "skill-two"} in ref_changes


async def test_rename_version_metadata_only(client: AsyncClient) -> None:
    agent = await _create_agent(client)
    agent_id = agent["id"]
    await _commit(client, agent_id, name="first", change_summary="initial")

    patch_resp = await client.patch(
        f"/api/agents/{agent_id}/versions/1",
        json={"name": "ga-candidate", "change_summary": "initial release"},
    )
    assert patch_resp.status_code == 200
    renamed = patch_resp.json()
    assert renamed["name"] == "ga-candidate"
    assert renamed["change_summary"] == "initial release"
    # Snapshot content is untouched by metadata edits.
    assert renamed["config_snapshot"]["persona"] == "You are helpful"
