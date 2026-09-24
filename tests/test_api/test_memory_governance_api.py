"""Integration tests for the memory governance REST API.

Covers the governance endpoint family: policy CRUD + validation errors +
resolved view, edit-log query, archive/restore round trip, archived
listing with reasons, lifecycle stats, and role enforcement (viewer
rejected).
"""

from __future__ import annotations

import uuid

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.auth_context import AuthContext
from hecate.core.composition.memory_policy import invalidate_memory_policy_cache
from hecate.models.memory import MemoryModel
from hecate.models.workspace import WorkspaceModel


async def test_policy_crud_and_validation(client: AsyncClient) -> None:
    # Unknown tool name → 422 with readable message.
    resp = await client.put(
        "/api/memory/policies/workspace",
        json={"tool_subset": ["memory_transmogrify"]},
    )
    assert resp.status_code == 422
    assert "unknown memory tool" in resp.json()["detail"]

    # Valid workspace policy.
    resp = await client.put(
        "/api/memory/policies/workspace",
        json={
            "tool_subset": ["memory_search", "memory_add"],
            "sharing_ceiling": "team",
            "params": {"ttl": {"l3_episodic_days": 30}},
        },
    )
    assert resp.status_code == 200
    policy_id = resp.json()["id"]

    # Listed with scope label.
    resp = await client.get("/api/memory/policies")
    assert resp.status_code == 200
    scopes = [item["scope"] for item in resp.json()["items"]]
    assert "workspace" in scopes

    # Resolved view names the source level.
    resp = await client.get("/api/memory/policies/resolved")
    assert resp.status_code == 200
    body = resp.json()
    assert body["values"]["sharing_ceiling"] == "team"
    assert body["values"]["ttl_days"]["l3_episodic"] == 30
    assert body["sources"]["sharing_ceiling"] == "workspace"

    # Agent widening the ceiling → 422.
    resp = await client.put(
        f"/api/memory/policies/agents/{uuid.uuid4()}",
        json={"sharing_ceiling": "workspace"},
    )
    assert resp.status_code == 422
    assert "widens" in resp.json()["detail"]

    # Delete → gone.
    resp = await client.delete("/api/memory/policies/workspace")
    assert resp.status_code == 204
    invalidate_memory_policy_cache()
    assert policy_id


async def test_policy_audit_visible_in_edit_log(client: AsyncClient) -> None:
    await client.put(
        "/api/memory/policies/workspace",
        json={"params": {"ttl": {"l3_episodic_days": 30}}},
    )
    resp = await client.get("/api/memory/governance/audit?tool_name=policy_upsert")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) >= 1
    assert items[0]["target_type"] == "memory_policy"
    invalidate_memory_policy_cache()


async def test_archive_restore_roundtrip(
    client: AsyncClient, db_session: AsyncSession, default_workspace: WorkspaceModel
) -> None:
    memory = MemoryModel(
        workspace_id=default_workspace.id,
        content="archive me via api",
        scope={},
        memory_type="semantic",
        embedding=[],
    )
    db_session.add(memory)
    await db_session.flush()
    memory_id = memory.id  # capture before commit expires the instance
    await db_session.commit()

    resp = await client.post(
        "/api/memory/governance/archive",
        json={"target_type": "user_memory", "memory_id": str(memory_id)},
    )
    assert resp.status_code == 200

    db_session.expire_all()
    row = (await db_session.execute(select(MemoryModel).where(MemoryModel.id == memory_id))).scalar_one()
    assert row.archived_at is not None

    resp = await client.get("/api/memory/governance/archived?target_type=user_memory")
    assert resp.status_code == 200
    archived_ids = [item["id"] for item in resp.json()["items"]]
    assert str(memory_id) in archived_ids
    item = next(i for i in resp.json()["items"] if i["id"] == str(memory_id))
    assert item["archive_reason"] in (None, "manual_archive")

    resp = await client.post(
        "/api/memory/governance/restore",
        json={"target_type": "user_memory", "memory_id": str(memory_id)},
    )
    assert resp.status_code == 200

    db_session.expire_all()
    row = (await db_session.execute(select(MemoryModel).where(MemoryModel.id == memory_id))).scalar_one()
    assert row.archived_at is None


async def test_lifecycle_stats_shape(client: AsyncClient) -> None:
    resp = await client.get("/api/memory/governance/stats")
    assert resp.status_code == 200
    body = resp.json()
    assert "l3_active" in body["counts"]
    assert "pending_flush_windows" in body["counts"]
    assert "lifecycle_operations_by_reason" in body
    assert "recent_consolidation_runs" in body


async def test_runs_endpoint_lists_empty(client: AsyncClient) -> None:
    resp = await client.get("/api/memory/governance/runs")
    assert resp.status_code == 200
    assert resp.json() == {"items": [], "total": 0}


async def test_recall_search_validates_payload(client: AsyncClient) -> None:
    """Validation runs before the provider is touched (422 contract)."""
    resp = await client.post("/api/memory/governance/recall/search", json={})
    assert resp.status_code == 422
    resp = await client.post("/api/memory/governance/recall/search", json={"query": "hello"})
    assert resp.status_code == 422
    assert "agent_id" in resp.json()["detail"]


async def test_viewer_role_rejected_on_governance(
    client: AsyncClient,
    auth_context: AuthContext,
    default_workspace: WorkspaceModel,
) -> None:
    """A viewer-context client gets 403 on governance endpoints."""
    from hecate.core.deps_workspace import get_auth_context
    from hecate.main import app
    from hecate.models.workspace_member import WorkspaceRole

    viewer_ctx = AuthContext(
        user_id=auth_context.user_id,
        org_id=default_workspace.org_id,
        workspace_id=default_workspace.id,
        role=WorkspaceRole.VIEWER,
        auth_method="jwt",
        api_key_scope=None,
    )
    app.dependency_overrides[get_auth_context] = lambda: viewer_ctx
    try:
        resp = await client.get("/api/memory/governance/stats")
        assert resp.status_code == 403
        resp = await client.get("/api/memory/governance/audit")
        assert resp.status_code == 403
        resp = await client.get("/api/memory/policies")
        assert resp.status_code == 403
    finally:
        app.dependency_overrides[get_auth_context] = lambda: auth_context
