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

import copy
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.agent import AgentModel
from hecate.models.tool import ToolModel
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


# ---------------------------------------------------------------------------
# Tool-contract snapshots and drift diagnostics
# ---------------------------------------------------------------------------


async def _tool_row(
    db_session: AsyncSession,
    workspace_id: uuid.UUID,
    name: str,
    *,
    description: str = "workspace tool",
    parameters: dict | None = None,
) -> ToolModel:

    from hecate.models.tool import ToolModel

    row = ToolModel(
        workspace_id=workspace_id,
        name=name,
        description=description,
        source="custom",
        parameters=parameters or {"type": "object", "properties": {"sku": {"type": "string"}}},
    )
    db_session.add(row)
    await db_session.flush()
    return row


async def _drift_agent(db_session: AsyncSession, tools: list[str]) -> AgentModel:
    from hecate.models.agent import AgentModel

    agent = AgentModel(
        workspace_id=uuid.UUID(int=0),
        name="Tool Contract Agent",
        model_config_db={"model": "gpt-4o"},
        mode="chat",
        tools=tools,
    )
    db_session.add(agent)
    await db_session.flush()
    return agent


def _tool_entry(manifest: list[dict], name: str) -> dict:
    return next(entry for entry in manifest if entry["resource_type"] == "tool" and entry["resource_id"] == name)


async def test_tool_contract_snapshot_records_resolved_definitions(db_session: AsyncSession) -> None:
    agent = await _drift_agent(db_session, ["web_search", "stock_lookup"])
    row = await _tool_row(db_session, agent.workspace_id, "stock_lookup", description="look up stock")

    service = AgentVersionService(db_session)
    committed = await service.commit_version(agent.id)

    builtin = _tool_entry(committed["ref_manifest"], "web_search")["tool_contract"]
    assert builtin["snapshot_version"] == 1
    assert builtin["digest_scope"] == ["name", "description", "parameters"]
    assert builtin["resolution"]["source"] == "builtin"
    assert builtin["resolution"]["resource_id"] == "builtin:web_search"
    assert builtin["contract"]["name"] == "web_search"
    assert "Search the web" in builtin["contract"]["description"]
    assert builtin["contract_hash"]

    workspace = _tool_entry(committed["ref_manifest"], "stock_lookup")["tool_contract"]
    assert workspace["resolution"]["source"] == "workspace_tool"
    assert workspace["resolution"]["resource_id"] == str(row.id)
    assert workspace["resolution"]["tool_source"] == "custom"
    assert workspace["contract"]["description"] == "look up stock"
    assert workspace["contract"]["parameters"]["properties"]["sku"] == {"type": "string"}
    assert workspace["contract_hash"]


async def test_unresolvable_tool_commits_unverified_and_never_blocks(db_session: AsyncSession) -> None:
    agent = await _drift_agent(db_session, ["ghost_tool"])
    service = AgentVersionService(db_session)

    committed = await service.commit_version(agent.id)
    snapshot = _tool_entry(committed["ref_manifest"], "ghost_tool")["tool_contract"]
    assert snapshot["contract"] is None and snapshot["contract_hash"] is None
    assert snapshot["resolution_note"].startswith("unresolved at commit")

    report = await service.version_drift(agent.id, 1)
    diagnostic = report["tool_contracts"]["tools"][0]
    assert diagnostic["status"] == "unverified"
    assert report["tool_contracts"]["summary"]["unverified"] == 1


async def test_drift_detects_tool_parameter_change(db_session: AsyncSession) -> None:
    from datetime import UTC, datetime

    agent = await _drift_agent(db_session, ["stock_lookup"])
    row = await _tool_row(db_session, agent.workspace_id, "stock_lookup")
    service = AgentVersionService(db_session)
    await service.commit_version(agent.id)

    row.parameters = {"type": "object", "properties": {"sku": {"type": "string"}, "region": {"type": "string"}}}
    row.updated_at = datetime.now(UTC)
    await db_session.flush()

    report = await service.version_drift(agent.id, 1)
    diagnostic = report["tool_contracts"]["tools"][0]
    assert diagnostic["status"] == "changed"
    assert diagnostic["committed_contract_hash"] != diagnostic["live_contract_hash"]
    assert report["tool_contracts"]["summary"]["changed"] == 1
    # The name did not move, so the legacy reference list stays clean.
    assert report["drifted"] == []


async def test_drift_flags_same_name_rebuild_as_binding_change(db_session: AsyncSession) -> None:
    agent = await _drift_agent(db_session, ["stock_lookup"])
    row = await _tool_row(db_session, agent.workspace_id, "stock_lookup")
    service = AgentVersionService(db_session)
    await service.commit_version(agent.id)

    row.deleted = True
    rebuilt = await _tool_row(db_session, agent.workspace_id, "stock_lookup")

    report = await service.version_drift(agent.id, 1)
    diagnostic = report["tool_contracts"]["tools"][0]
    assert diagnostic["status"] == "consistent"
    assert diagnostic["binding_changed"] is True
    assert "different database row" in diagnostic["detail"]
    assert diagnostic["live_resolution"]["resource_id"] == str(rebuilt.id)


async def test_drift_reports_missing_after_soft_delete(db_session: AsyncSession) -> None:
    agent = await _drift_agent(db_session, ["stock_lookup"])
    row = await _tool_row(db_session, agent.workspace_id, "stock_lookup")
    service = AgentVersionService(db_session)
    await service.commit_version(agent.id)

    row.deleted = True
    await db_session.flush()

    report = await service.version_drift(agent.id, 1)
    diagnostic = report["tool_contracts"]["tools"][0]
    assert diagnostic["status"] == "missing"
    assert diagnostic["committed_contract_hash"]


async def test_drift_contract_hash_ignores_json_key_order(db_session: AsyncSession) -> None:
    from datetime import UTC, datetime

    agent = await _drift_agent(db_session, ["stock_lookup"])
    row = await _tool_row(
        db_session,
        agent.workspace_id,
        "stock_lookup",
        parameters={"type": "object", "required": ["sku"], "properties": {"sku": {"type": "string"}}},
    )
    service = AgentVersionService(db_session)
    await service.commit_version(agent.id)

    row.parameters = {"properties": {"sku": {"type": "string"}}, "required": ["sku"], "type": "object"}
    row.updated_at = datetime.now(UTC)
    await db_session.flush()

    report = await service.version_drift(agent.id, 1)
    assert report["tool_contracts"]["tools"][0]["status"] == "consistent"


async def test_drift_baseline_ignores_draft_tool_edits(db_session: AsyncSession) -> None:
    agent = await _drift_agent(db_session, ["stock_lookup"])
    await _tool_row(db_session, agent.workspace_id, "stock_lookup")
    service = AgentVersionService(db_session)
    await service.commit_version(agent.id)

    # The draft drops the tool; the checked version's frozen list stays the
    # comparison object and its name still resolves in the registry.
    agent.tools = []
    await db_session.flush()

    report = await service.version_drift(agent.id, 1)
    assert report["tool_contracts"]["tools"][0]["status"] == "consistent"
    assert report["tool_contracts"]["summary"]["consistent"] == 1
    # The legacy name-level reference list keeps reporting the draft change.
    assert any(
        entry["resource_type"] == "tool" and entry["resource_id"] == "stock_lookup" and entry["missing"]
        for entry in report["drifted"]
    )


async def test_legacy_snapshot_without_contract_is_unverified_and_never_backfilled(db_session: AsyncSession) -> None:
    agent = await _drift_agent(db_session, ["stock_lookup"])
    await _tool_row(db_session, agent.workspace_id, "stock_lookup")
    service = AgentVersionService(db_session)
    await service.commit_version(agent.id)

    # Simulate a version committed before tool contracts existed: strip the
    # contract evidence from the stored manifest, never touching the rest.
    current = (await service.get_version(agent.id, 1, include_snapshot=True))["ref_manifest"]
    stripped = [{key: value for key, value in entry.items() if key != "tool_contract"} for entry in current]

    from sqlalchemy import update

    from hecate.models.agent_version import AgentVersionModel

    await db_session.execute(
        update(AgentVersionModel).where(AgentVersionModel.agent_id == agent.id).values(ref_manifest=stripped)
    )
    await db_session.flush()

    report = await service.version_drift(agent.id, 1)
    diagnostic = report["tool_contracts"]["tools"][0]
    assert diagnostic["status"] == "unverified"
    assert "snapshot predates tool contract capture" in diagnostic["detail"]

    # History is never rewritten: the stored manifest keeps its legacy shape.
    stored = (await service.get_version(agent.id, 1, include_snapshot=True))["ref_manifest"]
    assert all("tool_contract" not in entry for entry in stored)


async def test_same_name_in_other_workspace_does_not_resolve(db_session: AsyncSession) -> None:
    agent = await _drift_agent(db_session, ["stock_lookup"])
    await _tool_row(db_session, uuid.UUID(int=1234), "stock_lookup")
    service = AgentVersionService(db_session)

    committed = await service.commit_version(agent.id)
    assert _tool_entry(committed["ref_manifest"], "stock_lookup")["tool_contract"]["resolution"] is None

    report = await service.version_drift(agent.id, 1)
    assert report["tool_contracts"]["tools"][0]["status"] == "unverified"


async def test_tool_contract_snapshot_is_immutable_across_versions(db_session: AsyncSession) -> None:
    from datetime import UTC, datetime

    agent = await _drift_agent(db_session, ["stock_lookup"])
    row = await _tool_row(db_session, agent.workspace_id, "stock_lookup")
    service = AgentVersionService(db_session)
    await service.commit_version(agent.id)
    v1_manifest = (await service.get_version(agent.id, 1, include_snapshot=True))["ref_manifest"]
    v1_hash = _tool_entry(v1_manifest, "stock_lookup")["tool_contract"]["contract_hash"]

    row.parameters = {"type": "object", "properties": {"region": {"type": "string"}}}
    row.updated_at = datetime.now(UTC)
    await db_session.flush()
    await service.commit_version(agent.id)

    v1_entry = _tool_entry(
        (await service.get_version(agent.id, 1, include_snapshot=True))["ref_manifest"], "stock_lookup"
    )["tool_contract"]
    v2_entry = _tool_entry(
        (await service.get_version(agent.id, 2, include_snapshot=True))["ref_manifest"], "stock_lookup"
    )["tool_contract"]
    assert v1_entry["contract_hash"] == v1_hash
    assert v2_entry["contract_hash"] != v1_hash


async def test_drift_endpoint_returns_tool_contract_diagnostics(db_session: AsyncSession, client: AsyncClient) -> None:
    agent = await _drift_agent(db_session, ["stock_lookup"])
    await _tool_row(db_session, agent.workspace_id, "stock_lookup")
    service = AgentVersionService(db_session)
    await service.commit_version(agent.id)

    resp = await client.get(f"/api/agents/{agent.id}/versions/1/drift")
    assert resp.status_code == 200
    report = resp.json()
    assert report["tool_contracts"]["summary"]["consistent"] == 1
    assert report["tool_contracts"]["digest_scope"] == ["name", "description", "parameters"]


@pytest.mark.parametrize("damage", ["format", "scope", "digest", "shape", "missing_entry"])
async def test_unusable_tool_snapshot_never_verifies_consistent(db_session: AsyncSession, damage: str) -> None:
    from sqlalchemy import update

    from hecate.models.agent_version import AgentVersionModel

    agent = await _drift_agent(db_session, ["stock_lookup"])
    await _tool_row(db_session, agent.workspace_id, "stock_lookup")
    service = AgentVersionService(db_session)
    committed = await service.commit_version(agent.id)
    manifest = copy.deepcopy(committed["ref_manifest"])
    snapshot = manifest[0]["tool_contract"]
    if damage == "format":
        snapshot["snapshot_version"] = 99
    elif damage == "scope":
        snapshot["digest_scope"] = ["name"]
    elif damage == "digest":
        snapshot["contract"]["description"] = "corrupted snapshot"
    elif damage == "shape":
        manifest[0]["tool_contract"] = "corrupted snapshot"
    else:
        manifest = []
    await db_session.execute(
        update(AgentVersionModel).where(AgentVersionModel.agent_id == agent.id).values(ref_manifest=manifest)
    )
    report = await service.version_drift(agent.id, 1)
    assert report["tool_contracts"]["summary"]["consistent"] == 0
    assert report["tool_contracts"]["summary"]["unverified"] == 1


async def test_builtin_snapshot_matches_the_exact_model_definition(db_session: AsyncSession, monkeypatch) -> None:
    from hecate.core.composition.entry_assembly import load_agent_tools
    from hecate.tools.tool.builtin import BUILTIN_TOOL_DEFINITIONS

    monkeypatch.setitem(BUILTIN_TOOL_DEFINITIONS, "empty_schema", {"description": "test", "parameters": {}})
    agent = await _drift_agent(db_session, ["empty_schema"])
    service = AgentVersionService(db_session)
    committed = await service.commit_version(agent.id)
    expected = await load_agent_tools(db_session, agent.tools, workspace_id=agent.workspace_id)
    assert committed["ref_manifest"][0]["tool_contract"]["contract"] == expected[0]["function"]


async def test_same_name_rebuild_with_changed_schema_does_not_claim_equal_hashes(db_session: AsyncSession) -> None:
    agent = await _drift_agent(db_session, ["stock_lookup"])
    row = await _tool_row(db_session, agent.workspace_id, "stock_lookup")
    service = AgentVersionService(db_session)
    await service.commit_version(agent.id)
    row.deleted = True
    await _tool_row(db_session, agent.workspace_id, "stock_lookup", description="new contract")
    report = await service.version_drift(agent.id, 1)
    tool = report["tool_contracts"]["tools"][0]
    assert tool["status"] == "changed" and tool["binding_changed"] is True
    assert "hashes are equal" not in tool["detail"]


async def test_public_drift_schema_accepts_the_report(db_session: AsyncSession) -> None:
    from hecate.models.agent_version import AgentVersionDriftSchema

    agent = await _drift_agent(db_session, ["web_search"])
    service = AgentVersionService(db_session)
    await service.commit_version(agent.id)
    report = await service.version_drift(agent.id, 1)
    parsed = AgentVersionDriftSchema.model_validate(report)
    assert parsed.model_dump()["tool_contracts"]["summary"]["consistent"] == 1


async def test_tool_contract_and_binding_use_one_database_lookup(db_session: AsyncSession, monkeypatch) -> None:
    agent = await _drift_agent(db_session, ["stock_lookup"])
    row = await _tool_row(db_session, agent.workspace_id, "stock_lookup")
    real_execute = db_session.execute
    tool_reads = []

    async def execute(statement, *args, **kwargs):
        if any(item.get("entity") is ToolModel for item in getattr(statement, "column_descriptions", [])):
            tool_reads.append(statement)
        return await real_execute(statement, *args, **kwargs)

    monkeypatch.setattr(db_session, "execute", execute)
    result = await AgentVersionService(db_session)._resolve_tool_contracts(agent, agent.tools)
    assert len(tool_reads) == 1
    assert result["stock_lookup"]["resolution"]["resource_id"] == str(row.id)
    assert result["stock_lookup"]["contract"]["parameters"] == row.parameters


async def test_declared_source_change_is_reported_as_a_binding_change(db_session: AsyncSession) -> None:
    agent = await _drift_agent(db_session, ["stock_lookup"])
    row = await _tool_row(db_session, agent.workspace_id, "stock_lookup")
    service = AgentVersionService(db_session)
    await service.commit_version(agent.id)
    row.source = "mcp"
    await db_session.flush()
    report = await service.version_drift(agent.id, 1)
    diagnostic = report["tool_contracts"]["tools"][0]
    assert diagnostic["status"] == "consistent"
    assert diagnostic["binding_changed"] is True
    assert diagnostic["live_resolution"]["tool_source"] == "mcp"


async def test_tool_contract_capture_keeps_builtin_precedence(db_session: AsyncSession) -> None:
    agent = await _drift_agent(db_session, ["web_search"])
    await _tool_row(db_session, agent.workspace_id, "web_search", description="shadow definition")
    committed = await AgentVersionService(db_session).commit_version(agent.id)
    snapshot = committed["ref_manifest"][0]["tool_contract"]
    assert snapshot["resolution"]["source"] == "builtin"
    assert snapshot["contract"]["description"] != "shadow definition"
