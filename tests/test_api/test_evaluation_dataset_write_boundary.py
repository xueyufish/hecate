"""Evaluation dataset write access boundary (step10).

Covers the shared write boundary on the three dataset write endpoints
(create, update, delete):

- Parent dataset gating: update and delete on a missing, soft-deleted,
  or cross-workspace dataset return the dataset boundary's constant
  "Dataset not found" 404 and echo no identifier; the dataset stays
  unchanged.
- Role floor: editor suffices for all three endpoints in the dataset's
  workspace; viewer and role-less members are 403 after ownership, with
  content and deletion state untouched.
- Identity rules: explicit system scope updates and deletes across
  workspaces, but creation requires an explicit active workspace for
  every identity — a workspace-less create is 403 ``NO_WORKSPACE`` and
  never produces a zero-UUID-owned dataset.
- Cascade and semantics: a legitimate editor delete cascades soft-delete
  to ALL items regardless of their historical workspace values, update
  semantics keep their request/response shape, and rejected requests are
  read-only, verified against full-column database snapshots.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.auth_context import AuthContext
from hecate.models.evaluation import EvaluationDatasetModel, EvaluationItemModel
from hecate.models.organization import OrganizationModel
from hecate.models.workspace import WorkspaceModel
from hecate.models.workspace_member import WorkspaceMemberModel, WorkspaceRole


async def _workspace_with_role(db: AsyncSession, name: str, role: WorkspaceRole) -> tuple[WorkspaceModel, AuthContext]:
    """Create an isolated org, workspace, member, and authentication context."""
    name = f"{name}-{uuid.uuid4().hex[:8]}"
    user_id = uuid.uuid4()
    org = OrganizationModel(name=f"Org {name}", slug=f"org-{name}", owner_id=user_id)
    db.add(org)
    await db.flush()
    ws = WorkspaceModel(org_id=org.id, name=name, slug=name)
    db.add(ws)
    await db.flush()
    db.add(WorkspaceMemberModel(user_id=user_id, workspace_id=ws.id, role=role))
    await db.flush()
    await db.commit()
    ctx = AuthContext(
        user_id=user_id,
        org_id=org.id,
        workspace_id=ws.id,
        role=role,
        auth_method="jwt",
        api_key_scope=None,
    )
    return ws, ctx


def _member_as(ws: WorkspaceModel, role: WorkspaceRole | None) -> AuthContext:
    """A fresh identity whose active workspace is ``ws`` (role optional)."""
    return AuthContext(
        user_id=uuid.uuid4(),
        org_id=ws.org_id,
        workspace_id=ws.id,
        role=role,
        auth_method="jwt",
        api_key_scope=None,
    )


def _system_context() -> AuthContext:
    """Deploy-time bootstrap identity: the only cross-workspace bypass."""
    return AuthContext(
        user_id=uuid.uuid4(),
        org_id=None,
        workspace_id=None,
        role=None,
        auth_method="api_key",
        api_key_scope="system",
    )


def _no_workspace_context() -> AuthContext:
    """Authenticated but with no active workspace (never system scope)."""
    return AuthContext(
        user_id=uuid.uuid4(),
        org_id=None,
        workspace_id=None,
        role=None,
        auth_method="jwt",
        api_key_scope=None,
    )


@contextlib.asynccontextmanager
async def _client_as(client: AsyncClient, ctx: AuthContext) -> AsyncIterator[AsyncClient]:
    """Yield a client authenticating as ``ctx``; restore the app override."""
    from hecate.core.deps_workspace import get_auth_context
    from hecate.main import app

    async def override_auth() -> AuthContext:
        return ctx

    saved = app.dependency_overrides.get(get_auth_context)
    app.dependency_overrides[get_auth_context] = override_auth
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
            yield http
    finally:
        if saved is None:
            app.dependency_overrides.pop(get_auth_context, None)
        else:
            app.dependency_overrides[get_auth_context] = saved


async def _seed_dataset(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    deleted: bool = False,
) -> EvaluationDatasetModel:
    """Seed a dataset directly via the ORM."""
    ds = EvaluationDatasetModel(name=f"write-boundary-ds-{uuid.uuid4().hex[:8]}", workspace_id=workspace_id)
    ds.deleted = deleted
    db.add(ds)
    await db.commit()
    return ds


async def _seed_item(
    db: AsyncSession,
    dataset_id: uuid.UUID,
    *,
    workspace_id: uuid.UUID | None = None,
    deleted: bool = False,
) -> EvaluationItemModel:
    """Seed an item with meaningful content, provenance, and deletion markers."""
    item = EvaluationItemModel(
        dataset_id=dataset_id,
        workspace_id=workspace_id if workspace_id is not None else uuid.UUID(int=0),
        query="existing query",
        expected_answer="expected",
        generated_answer="generated",
        context=["source"],
        metadata_={"source": "fixture"},
        tags=["regression"],
        known_bad=True,
        known_bad_reason="stale expected answer",
        known_bad_marked_by=uuid.uuid4(),
        known_bad_marked_at=datetime.now(UTC),
        known_bad_expires_at=datetime.now(UTC),
        deleted=deleted,
        deleted_at=datetime.now(UTC) if deleted else None,
    )
    db.add(item)
    await db.commit()
    return item


async def _dataset_snapshot(db: AsyncSession, dataset_id: uuid.UUID) -> dict[str, Any]:
    """Read every persisted dataset column directly, bypassing the identity map."""
    table = EvaluationDatasetModel.__table__
    row = (await db.execute(select(table).where(table.c.id == dataset_id))).mappings().one()
    return dict(row)


async def _items_snapshot(db: AsyncSession, dataset_id: uuid.UUID | None = None) -> list[dict[str, Any]]:
    """Read all item rows (or one dataset's) directly, bypassing the identity map."""
    table = EvaluationItemModel.__table__
    query = select(table).order_by(table.c.id)
    if dataset_id is not None:
        query = query.where(table.c.dataset_id == dataset_id)
    return [dict(row) for row in (await db.execute(query)).mappings().all()]


async def _database_snapshot(db: AsyncSession) -> list[list[dict[str, Any]]]:
    """Read all dataset and item columns, including deleted rows, from storage."""
    snapshots = []
    for model in (EvaluationDatasetModel, EvaluationItemModel):
        table = model.__table__
        rows = (await db.execute(select(table).order_by(table.c.id))).mappings().all()
        snapshots.append([dict(row) for row in rows])
    return snapshots


def _assert_dataset_not_found(resp: Any) -> None:
    assert resp.status_code == 404, resp.text
    assert resp.json() == {"detail": {"error": {"code": "NOT_FOUND", "message": "Dataset not found", "details": None}}}


def _assert_forbidden(resp: Any, role: WorkspaceRole | None) -> None:
    assert resp.status_code == 403, resp.text
    message = "Editor role required" if role is WorkspaceRole.VIEWER else "Workspace membership required"
    assert resp.json() == {"detail": {"error": {"code": "FORBIDDEN", "message": message, "details": None}}}


def _assert_no_workspace(resp: Any) -> None:
    assert resp.status_code == 403, resp.text
    assert resp.json() == {
        "detail": {"error": {"code": "NO_WORKSPACE", "message": "An active workspace is required", "details": None}}
    }


@pytest.mark.parametrize("identity", ["editor", "viewer", "roleless", "no_workspace", "system"])
async def test_writes_on_invisible_dataset_not_possible(
    client: AsyncClient, db_session: AsyncSession, identity: str
) -> None:
    """Resolve visibility before roles and keep every rejected write read-only."""
    ws1, editor = await _workspace_with_role(db_session, "ws-one", WorkspaceRole.EDITOR)
    ws2, _ = await _workspace_with_role(db_session, "ws-two", WorkspaceRole.EDITOR)
    foreign = await _seed_dataset(db_session, ws2.id)
    deleted_ds = await _seed_dataset(db_session, ws1.id, deleted=True)
    await _seed_item(db_session, foreign.id)
    await _seed_item(db_session, deleted_ds.id, workspace_id=ws1.id)
    before = await _database_snapshot(db_session)
    ctx = {
        "editor": editor,
        "viewer": _member_as(ws1, WorkspaceRole.VIEWER),
        "roleless": _member_as(ws1, None),
        "no_workspace": _no_workspace_context(),
        "system": _system_context(),
    }[identity]
    ids = [uuid.uuid4(), deleted_ds.id]
    if identity != "system":
        ids.append(foreign.id)
    if identity == "no_workspace":
        own = await _seed_dataset(db_session, ws1.id)
        ids.append(own.id)
        before = await _database_snapshot(db_session)

    async with _client_as(client, ctx) as http:
        for dataset_id in ids:
            for method in ("PUT", "DELETE"):
                response = await http.request(
                    method,
                    f"/api/evaluation/datasets/{dataset_id}",
                    **({"json": {"name": "hijacked", "metadata_": {"injected": True}}} if method == "PUT" else {}),
                )
                _assert_dataset_not_found(response)
                assert str(dataset_id) not in response.text
                assert await _database_snapshot(db_session) == before


@pytest.mark.parametrize("role", [WorkspaceRole.VIEWER, None])
async def test_viewer_and_role_less_members_cannot_write(
    client: AsyncClient, db_session: AsyncSession, role: WorkspaceRole | None
) -> None:
    """Visible rows require editor; each denial preserves all rows and columns."""
    ws, _ = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.EDITOR)
    ds = await _seed_dataset(db_session, ws.id)
    await _seed_item(db_session, ds.id)
    await _seed_item(db_session, ds.id, workspace_id=ws.id, deleted=True)
    before = await _database_snapshot(db_session)
    async with _client_as(client, _member_as(ws, role)) as http:
        for method, path, payload in (
            ("POST", "/api/evaluation/datasets", {"name": "nope"}),
            ("PUT", f"/api/evaluation/datasets/{ds.id}", {"name": "renamed", "metadata_": {"changed": True}}),
            ("DELETE", f"/api/evaluation/datasets/{ds.id}", None),
        ):
            response = await http.request(method, path, **({"json": payload} if payload else {}))
            _assert_forbidden(response, role)
            assert str(ds.id) not in response.text
            assert await _database_snapshot(db_session) == before


async def test_system_scope_updates_and_deletes_across_workspaces(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """System writes use the target resource even without an active workspace."""
    ws1, _ = await _workspace_with_role(db_session, "ws-one", WorkspaceRole.EDITOR)
    ws2, _ = await _workspace_with_role(db_session, "ws-two", WorkspaceRole.EDITOR)
    datasets = [await _seed_dataset(db_session, ws.id) for ws in (ws1, ws2)]
    async with _client_as(client, _system_context()) as http:
        for ds in datasets:
            updated = await http.put(f"/api/evaluation/datasets/{ds.id}", json={"name": "system-renamed"})
            assert updated.status_code == 200, updated.text
            assert updated.json()["name"] == "system-renamed"
            assert updated.json()["workspace_id"] == str(ds.workspace_id)
            assert (await _dataset_snapshot(db_session, ds.id))["name"] == "system-renamed"
            removed = await http.delete(f"/api/evaluation/datasets/{ds.id}")
            assert removed.status_code == 204 and removed.content == b"", removed.text
            row = await _dataset_snapshot(db_session, ds.id)
            assert row["deleted"] is True and row["deleted_at"] is not None


@pytest.mark.parametrize("system", [False, True])
async def test_create_without_active_workspace_is_no_workspace(
    client: AsyncClient, db_session: AsyncSession, system: bool
) -> None:
    """A payload cannot supply the missing ownership context, even for system."""
    before = await _database_snapshot(db_session)
    identity = _system_context() if system else _no_workspace_context()
    async with _client_as(client, identity) as http:
        resp = await http.post("/api/evaluation/datasets", json={"name": "ownerless"})
    _assert_no_workspace(resp)
    assert await _database_snapshot(db_session) == before


@pytest.mark.parametrize("role", [WorkspaceRole.EDITOR, WorkspaceRole.ADMIN])
async def test_created_dataset_belongs_to_the_active_workspace(
    client: AsyncClient, db_session: AsyncSession, role: WorkspaceRole
) -> None:
    """The response shape and persisted owner come from the server context."""
    ws, ctx = await _workspace_with_role(db_session, "ws-creator", role)
    async with _client_as(client, ctx) as http:
        resp = await http.post(
            "/api/evaluation/datasets", json={"name": "fresh", "description": "d", "metadata_": {"source": "test"}}
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert set(body) == {
        "id",
        "name",
        "description",
        "workspace_id",
        "metadata",
        "created_at",
        "updated_at",
        "deleted",
        "deleted_at",
    }
    assert body["name"] == "fresh" and body["description"] == "d" and body["metadata"] == {"source": "test"}
    assert body["workspace_id"] == str(ws.id)
    snapshot = await _dataset_snapshot(db_session, uuid.UUID(body["id"]))
    assert snapshot["workspace_id"] == ws.id and snapshot["workspace_id"] != uuid.UUID(int=0)


async def test_system_create_with_explicit_workspace(client: AsyncClient, db_session: AsyncSession) -> None:
    """An explicit workspace resolves creation ownership before system's role bypass."""
    ws, _ = await _workspace_with_role(db_session, "ws-system", WorkspaceRole.EDITOR)
    ctx = replace(_system_context(), workspace_id=ws.id)
    async with _client_as(client, ctx) as http:
        resp = await http.post("/api/evaluation/datasets", json={"name": "system-created"})
    assert resp.status_code == 201, resp.text
    assert resp.json()["workspace_id"] == str(ws.id)
    assert (await _dataset_snapshot(db_session, uuid.UUID(resp.json()["id"])))["workspace_id"] == ws.id


@pytest.mark.parametrize("system", [False, True])
async def test_payload_cannot_select_dataset_workspace(
    client: AsyncClient, db_session: AsyncSession, system: bool
) -> None:
    """Creation and update schemas reject client-supplied workspace selection."""
    ws, editor = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.EDITOR)
    other, _ = await _workspace_with_role(db_session, "ws-other", WorkspaceRole.EDITOR)
    ds = await _seed_dataset(db_session, ws.id)
    before = await _database_snapshot(db_session)
    ctx = replace(_system_context(), workspace_id=ws.id) if system else editor
    async with _client_as(client, ctx) as http:
        for method, path in (
            ("POST", "/api/evaluation/datasets"),
            ("PUT", f"/api/evaluation/datasets/{ds.id}"),
        ):
            response = await http.request(method, path, json={"name": "injected", "workspace_id": str(other.id)})
            assert response.status_code == 422, response.text
            assert await _database_snapshot(db_session) == before


@pytest.mark.parametrize("role", [WorkspaceRole.EDITOR, WorkspaceRole.ADMIN])
async def test_legitimate_delete_cascades_items_regardless_of_workspace(
    client: AsyncClient, db_session: AsyncSession, role: WorkspaceRole
) -> None:
    """Cascade covers current, legacy, and inconsistent item owners only in this dataset."""
    ws, ctx = await _workspace_with_role(db_session, "ws-editor", role)
    other, _ = await _workspace_with_role(db_session, "ws-other", WorkspaceRole.EDITOR)
    ds = await _seed_dataset(db_session, ws.id)
    for workspace_id in (uuid.UUID(int=0), ws.id, other.id):
        await _seed_item(db_session, ds.id, workspace_id=workspace_id)
    already_deleted = await _seed_item(db_session, ds.id, deleted=True)
    unrelated = await _seed_dataset(db_session, other.id)
    await _seed_item(db_session, unrelated.id, workspace_id=other.id)
    unrelated_before = await _dataset_snapshot(db_session, unrelated.id)
    unrelated_items_before = await _items_snapshot(db_session, unrelated.id)
    items_before = await _items_snapshot(db_session, ds.id)

    async with _client_as(client, ctx) as http:
        removed = await http.delete(f"/api/evaluation/datasets/{ds.id}")
    assert removed.status_code == 204 and removed.content == b"", removed.text
    ds_row = await _dataset_snapshot(db_session, ds.id)
    assert ds_row["deleted"] is True and ds_row["deleted_at"] is not None
    items_after = await _items_snapshot(db_session, ds.id)
    assert len(items_before) == len(items_after) == 4
    for before, after in zip(items_before, items_after, strict=True):
        assert after["deleted"] is True and after["deleted_at"] is not None
        if after["id"] == already_deleted.id:
            assert after == before
        else:
            preserved = set(before) - {"deleted", "deleted_at", "updated_at"}
            assert {key: before[key] for key in preserved} == {key: after[key] for key in preserved}
    assert await _dataset_snapshot(db_session, unrelated.id) == unrelated_before
    assert await _items_snapshot(db_session, unrelated.id) == unrelated_items_before


@pytest.mark.parametrize("role", [WorkspaceRole.EDITOR, WorkspaceRole.ADMIN])
async def test_update_semantics_preserved(client: AsyncClient, db_session: AsyncSession, role: WorkspaceRole) -> None:
    """Preserve metadata aliases, null/omitted values, response shape, and ownership."""
    ws, ctx = await _workspace_with_role(db_session, "ws-editor", role)
    ds = await _seed_dataset(db_session, ws.id)
    await _seed_item(db_session, ds.id)
    items_before = await _items_snapshot(db_session)
    async with _client_as(client, ctx) as http:
        resp = await http.put(
            f"/api/evaluation/datasets/{ds.id}",
            json={"name": "renamed", "description": "new", "metadata_": {"changed": True}},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["name"] == "renamed" and body["description"] == "new" and body["metadata"] == {"changed": True}
        # The established PUT clears an omitted description but retains omitted name and metadata.
        empty = await http.put(f"/api/evaluation/datasets/{ds.id}", json={})
        assert empty.status_code == 200, empty.text
        assert empty.json()["description"] is None
        assert empty.json()["name"] == "renamed" and empty.json()["metadata"] == {"changed": True}
        explicit_null = await http.put(
            f"/api/evaluation/datasets/{ds.id}", json={"name": None, "description": None, "metadata_": None}
        )
        assert explicit_null.status_code == 200, explicit_null.text
        assert explicit_null.json() == empty.json()
    row = await _dataset_snapshot(db_session, ds.id)
    assert row["workspace_id"] == ws.id and row["name"] == "renamed"
    assert row["description"] is None and row["metadata"] == {"changed": True}
    assert set(body) == set(empty.json()) == set(explicit_null.json())
    assert await _items_snapshot(db_session) == items_before
