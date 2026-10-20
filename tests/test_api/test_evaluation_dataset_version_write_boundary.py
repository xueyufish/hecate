"""Evaluation dataset version write access boundary (step10).

Covers the shared write boundary on the three dataset version write
endpoints (create, delete, checkout):

- Role floor: editor minimum inside the dataset's workspace; viewer and
  role-less identities receive 403 on a visible resource.
- Ownership before role: cross-workspace and missing-workspace callers
  get the dataset boundary's identifier-free 404, never a role denial.
- Version binding on delete/checkout: missing, soft-deleted,
  cross-workspace, and foreign-dataset versions share one constant
  "Dataset version not found" 404 echoing no IDs.
- System scope: explicit system identities keep cross-workspace writes,
  attributed to the target dataset's real workspace (never a zero-UUID).
- Write inertia: every rejected request leaves versions, live items, and
  soft-delete markers unchanged; successful writes keep freezing,
  duplicate-name 409, soft delete, and checkout semantics unchanged.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.auth_context import AuthContext
from hecate.models.evaluation import (
    EvaluationDatasetModel,
    EvaluationDatasetVersionModel,
    EvaluationItemModel,
)
from hecate.models.organization import OrganizationModel
from hecate.models.workspace import WorkspaceModel
from hecate.models.workspace_member import WorkspaceMemberModel, WorkspaceRole
from hecate.ops.evaluation.dataset_version_service import (
    DatasetVersionNotFoundError,
    EvaluationDatasetVersionService,
)


async def _workspace_with_role(db: AsyncSession, name: str, role: WorkspaceRole) -> tuple[WorkspaceModel, AuthContext]:
    """Create org + workspace + member and return (workspace, context)."""
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
    db: AsyncSession, workspace_id: uuid.UUID, *, name: str = "write-boundary-ds"
) -> EvaluationDatasetModel:
    ds = EvaluationDatasetModel(name=f"{name}-{uuid.uuid4().hex[:8]}", workspace_id=workspace_id)
    db.add(ds)
    await db.commit()
    return ds


async def _seed_item(db: AsyncSession, dataset_id: uuid.UUID, *, query: str = "probe?") -> EvaluationItemModel:
    dataset = await db.get(EvaluationDatasetModel, dataset_id)
    assert dataset is not None
    item = EvaluationItemModel(dataset_id=dataset_id, query=query, workspace_id=dataset.workspace_id)
    db.add(item)
    await db.commit()
    return item


async def _create_version(
    db: AsyncSession,
    dataset_id: uuid.UUID,
    workspace_id: uuid.UUID,
    name: str,
) -> Any:
    """Freeze a version through the service (setup helper, not under test)."""
    svc = EvaluationDatasetVersionService(db)
    version = await svc.create_version(
        dataset_id,
        name=name,
        description=None,
        workspace_id=workspace_id,
        created_by=uuid.uuid4(),
    )
    await db.commit()
    return version


async def _soft_delete_version(db: AsyncSession, version_id: uuid.UUID) -> None:
    await EvaluationDatasetVersionService(db).delete_version(version_id)
    await db.commit()


def _assert_dataset_not_found(resp: Any) -> None:
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"]["error"]["code"] == "NOT_FOUND"
    assert resp.json()["detail"]["error"]["message"] == "Dataset not found"


def _assert_version_not_found(resp: Any) -> None:
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"]["error"]["code"] == "NOT_FOUND"
    assert resp.json()["detail"]["error"]["message"] == "Dataset version not found"


def _assert_forbidden(resp: Any) -> None:
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"]["error"]["code"] == "FORBIDDEN"


async def _write_requests(http: AsyncClient, dataset_id: uuid.UUID, version_id: uuid.UUID, name: str):
    return (
        await http.post(
            f"/api/evaluation/datasets/{dataset_id}/versions",
            json={"name": name},
        ),
        await http.delete(f"/api/evaluation/datasets/{dataset_id}/versions/{version_id}"),
        await http.post(f"/api/evaluation/datasets/{dataset_id}/versions/{version_id}/checkout"),
    )


async def _write_state_snapshot(db: AsyncSession) -> list[list[dict[str, Any]]]:
    states = []
    for model in (EvaluationDatasetModel, EvaluationDatasetVersionModel, EvaluationItemModel):
        rows = await db.execute(select(model.__table__).order_by(model.id))
        states.append([dict(row) for row in rows.mappings()])
    return states


@pytest.mark.parametrize("role", [WorkspaceRole.VIEWER, None])
async def test_viewer_denied_on_all_three_writes_with_no_side_effects(
    client: AsyncClient,
    db_session: AsyncSession,
    role: WorkspaceRole | None,
) -> None:
    """Viewer or absent membership is 403 on every visible write."""
    ws, _ = await _workspace_with_role(db_session, "ws-viewer", WorkspaceRole.VIEWER)
    ds = await _seed_dataset(db_session, ws.id)
    await _seed_item(db_session, ds.id)
    version = await _create_version(db_session, ds.id, ws.id, "v1")

    before = await _write_state_snapshot(db_session)
    async with _client_as(client, _member_as(ws, role)) as http:
        create, delete, checkout = await _write_requests(http, ds.id, version.id, "viewer-version")
    _assert_forbidden(create)
    _assert_forbidden(delete)
    _assert_forbidden(checkout)
    assert await _write_state_snapshot(db_session) == before


@pytest.mark.parametrize("role", [WorkspaceRole.EDITOR, WorkspaceRole.ADMIN])
async def test_editor_and_admin_write_with_unchanged_semantics(
    client: AsyncClient,
    db_session: AsyncSession,
    role: WorkspaceRole,
) -> None:
    """Editor and above keep freezing, checkout, and soft-delete semantics."""
    ws, ctx = await _workspace_with_role(db_session, "ws-writer", role)
    ds = await _seed_dataset(db_session, ws.id)
    await _seed_item(db_session, ds.id, query="kept")
    v1 = await _create_version(db_session, ds.id, ws.id, "v1")
    await _seed_item(db_session, ds.id, query="added after freeze")

    async with _client_as(client, ctx) as http:
        create = await http.post(f"/api/evaluation/datasets/{ds.id}/versions", json={"name": "v2"})
        assert create.status_code == 201, create.text
        created = create.json()
        assert created["name"] == "v2"
        assert created["content_hash"]
        assert len(created["items"]) == 2  # frozen at creation time
        assert created["created_by"] == str(ctx.user_id)
        created_row = await db_session.get(EvaluationDatasetVersionModel, uuid.UUID(created["id"]))
        assert created_row is not None and created_row.workspace_id == ws.id

        checkout = await http.post(f"/api/evaluation/datasets/{ds.id}/versions/{v1.id}/checkout")
        assert checkout.status_code == 200, checkout.text
        assert "removed" in checkout.json()  # diff summary present
        live_items = (
            (
                await db_session.execute(
                    select(EvaluationItemModel).where(
                        EvaluationItemModel.dataset_id == ds.id, ~EvaluationItemModel.deleted
                    )
                )
            )
            .scalars()
            .all()
        )
        assert [(item.query, item.workspace_id) for item in live_items] == [("kept", ws.id)]
        frozen = await http.get(f"/api/evaluation/datasets/{ds.id}/versions/{created['id']}")
        assert frozen.status_code == 200, frozen.text
        assert frozen.json()["items"] == created["items"]
        assert frozen.json()["content_hash"] == created["content_hash"]

        delete = await http.delete(f"/api/evaluation/datasets/{ds.id}/versions/{created['id']}")
        assert delete.status_code == 204, delete.text

        listing = await http.get(f"/api/evaluation/datasets/{ds.id}/versions")
        assert listing.status_code == 200, listing.text
        assert listing.json()["total"] == 1  # soft-deleted version excluded


async def test_ownership_precedes_role_for_cross_workspace_writes(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """A role-less caller from another workspace gets the dataset 404."""
    ws, _ = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.EDITOR)
    other_ws, _ = await _workspace_with_role(db_session, "ws-other", WorkspaceRole.VIEWER)
    ds = await _seed_dataset(db_session, ws.id)
    version = await _create_version(db_session, ds.id, ws.id, "v1")

    before = await _write_state_snapshot(db_session)
    async with _client_as(client, _member_as(other_ws, None)) as http:
        create, delete, checkout = await _write_requests(http, ds.id, version.id, "cross-version")
    for resp in (create, delete, checkout):
        _assert_dataset_not_found(resp)
    assert await _write_state_snapshot(db_session) == before


@pytest.mark.parametrize("role", [WorkspaceRole.EDITOR, WorkspaceRole.VIEWER, None])
async def test_invisible_version_on_delete_and_checkout_uniform_and_id_free(
    client: AsyncClient,
    db_session: AsyncSession,
    role: WorkspaceRole | None,
) -> None:
    """Every version reference is checked before the caller's write role."""
    ws1, _ = await _workspace_with_role(db_session, "ws-one", WorkspaceRole.EDITOR)
    ws2, _ = await _workspace_with_role(db_session, "ws-two", WorkspaceRole.EDITOR)
    own = await _seed_dataset(db_session, ws1.id)
    own_other = await _seed_dataset(db_session, ws1.id)
    deleted_version = await _create_version(db_session, own.id, ws1.id, "gone")
    await _soft_delete_version(db_session, deleted_version.id)
    foreign_version = await _create_version(db_session, own_other.id, ws1.id, "other-ds")
    ws2_ds = await _seed_dataset(db_session, ws2.id)
    other_ws_version = await _create_version(db_session, ws2_ds.id, ws2.id, "ws2")

    before = await _write_state_snapshot(db_session)
    async with _client_as(client, _member_as(ws1, role)) as http:
        probes = []
        for version_id in (
            uuid.uuid4(),
            deleted_version.id,
            foreign_version.id,
            other_ws_version.id,
        ):
            probes.append(await http.delete(f"/api/evaluation/datasets/{own.id}/versions/{version_id}"))
            probes.append(await http.post(f"/api/evaluation/datasets/{own.id}/versions/{version_id}/checkout"))
        reference_missing = await http.get(f"/api/evaluation/datasets/{own.id}/versions/{uuid.uuid4()}")
    for probe in probes:
        _assert_version_not_found(probe)
        assert probe.json()["detail"] == reference_missing.json()["detail"]
        for version in (deleted_version, foreign_version, other_ws_version):
            assert str(version.id) not in probe.text
    assert await _write_state_snapshot(db_session) == before


async def test_system_scope_writes_across_workspaces_with_real_attribution(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """System scope writes into another workspace, attributed to its real workspace."""
    ws, _ = await _workspace_with_role(db_session, "ws-owned", WorkspaceRole.VIEWER)
    ds = await _seed_dataset(db_session, ws.id)
    await _seed_item(db_session, ds.id, query="kept")
    v1 = await _create_version(db_session, ds.id, ws.id, "v1")

    async with _client_as(client, _system_context()) as http:
        create = await http.post(f"/api/evaluation/datasets/{ds.id}/versions", json={"name": "v2"})
        assert create.status_code == 201, create.text

        checkout = await http.post(f"/api/evaluation/datasets/{ds.id}/versions/{v1.id}/checkout")
        assert checkout.status_code == 200, checkout.text

        delete = await http.delete(f"/api/evaluation/datasets/{ds.id}/versions/{create.json()['id']}")
        assert delete.status_code == 204, delete.text

    row = await db_session.get(EvaluationDatasetVersionModel, uuid.UUID(create.json()["id"]))
    assert row is not None and row.deleted is True
    assert row.workspace_id == ws.id
    zero_uuid_count = await db_session.execute(
        select(EvaluationDatasetVersionModel).where(EvaluationDatasetVersionModel.workspace_id == uuid.UUID(int=0))
    )
    assert zero_uuid_count.scalars().all() == []


async def test_missing_workspace_identity_cannot_create_zero_uuid_version(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """No active workspace: denied at the dataset boundary, no zero-UUID rows."""
    ws, _ = await _workspace_with_role(db_session, "ws-seeded", WorkspaceRole.EDITOR)
    ds = await _seed_dataset(db_session, ws.id)
    await _seed_item(db_session, ds.id)
    version = await _create_version(db_session, ds.id, ws.id, "v1")

    before = await _write_state_snapshot(db_session)
    async with _client_as(client, _no_workspace_context()) as http:
        create, delete, checkout = await _write_requests(http, ds.id, version.id, "no-ws-version")
    for resp in (create, delete, checkout):
        _assert_dataset_not_found(resp)
    assert await _write_state_snapshot(db_session) == before

    zero_uuid_count = await db_session.execute(
        select(EvaluationDatasetVersionModel).where(EvaluationDatasetVersionModel.workspace_id == uuid.UUID(int=0))
    )
    assert zero_uuid_count.scalars().all() == []


@pytest.mark.parametrize("deleted", [False, True])
async def test_duplicate_name_409_preserved_for_editor(
    client: AsyncClient,
    db_session: AsyncSession,
    deleted: bool,
) -> None:
    """The boundary does not alter freeze semantics: duplicate names stay 409."""
    ws, editor_ctx = await _workspace_with_role(db_session, "ws-editor", WorkspaceRole.EDITOR)
    ds = await _seed_dataset(db_session, ws.id)
    await _seed_item(db_session, ds.id)
    version = await _create_version(db_session, ds.id, ws.id, "v1")
    if deleted:
        await _soft_delete_version(db_session, version.id)
    before = await _write_state_snapshot(db_session)

    async with _client_as(client, editor_ctx) as http:
        duplicate = await http.post(f"/api/evaluation/datasets/{ds.id}/versions", json={"name": "v1"})
    assert duplicate.status_code == 409, duplicate.text
    assert duplicate.json()["detail"]["error"]["code"] == "NAME_CONFLICT"
    assert await _write_state_snapshot(db_session) == before


async def test_rejections_are_write_inert(client: AsyncClient, db_session: AsyncSession) -> None:
    """Role/ownership denials across identities leave all dataset state unchanged."""
    ws, _ = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.EDITOR)
    other_ws, _ = await _workspace_with_role(db_session, "ws-other", WorkspaceRole.VIEWER)
    ds = await _seed_dataset(db_session, ws.id)
    await _seed_item(db_session, ds.id)
    source = await _create_version(db_session, ds.id, ws.id, "v1")
    target = await _create_version(db_session, ds.id, ws.id, "v2")

    before = await _write_state_snapshot(db_session)
    for ctx in (
        _member_as(ws, WorkspaceRole.VIEWER),
        _member_as(other_ws, WorkspaceRole.ADMIN),
        _member_as(other_ws, None),
        _no_workspace_context(),
    ):
        async with _client_as(client, ctx) as http:
            for response in await _write_requests(http, ds.id, source.id, "inert-version"):
                assert response.status_code in (403, 404), response.text
    assert await _write_state_snapshot(db_session) == before
    assert target.id  # both versions remain addressable


@pytest.mark.parametrize("legacy_workspace", ["zero", "foreign"])
async def test_system_rejects_version_with_inconsistent_workspace(
    client: AsyncClient,
    db_session: AsyncSession,
    legacy_workspace: str,
) -> None:
    """System scope cannot mix a parent with a differently attributed version."""
    ws, _ = await _workspace_with_role(db_session, "owner", WorkspaceRole.EDITOR)
    other, _ = await _workspace_with_role(db_session, "foreign", WorkspaceRole.EDITOR)
    ds = await _seed_dataset(db_session, ws.id)
    await _seed_item(db_session, ds.id, query="keep live data")
    version = await _create_version(db_session, ds.id, ws.id, "legacy")
    version.workspace_id = uuid.UUID(int=0) if legacy_workspace == "zero" else other.id
    await db_session.commit()
    before = await _write_state_snapshot(db_session)
    async with _client_as(client, _system_context()) as http:
        _assert_version_not_found(await http.delete(f"/api/evaluation/datasets/{ds.id}/versions/{version.id}"))
        _assert_version_not_found(await http.post(f"/api/evaluation/datasets/{ds.id}/versions/{version.id}/checkout"))
    assert await _write_state_snapshot(db_session) == before


@pytest.mark.parametrize("parent_state", ["missing", "deleted"])
@pytest.mark.parametrize("identity", ["editor", "viewer", "roleless", "system"])
async def test_invisible_parent_rejects_every_write_without_mutation(
    client: AsyncClient,
    db_session: AsyncSession,
    parent_state: str,
    identity: str,
) -> None:
    """Parent existence is required even for editors and system identities."""
    ws, editor = await _workspace_with_role(db_session, "owner", WorkspaceRole.EDITOR)
    ds = await _seed_dataset(db_session, ws.id)
    await _seed_item(db_session, ds.id)
    version = await _create_version(db_session, ds.id, ws.id, "frozen")
    if parent_state == "deleted":
        ds.deleted = True
        await db_session.commit()
    target_id = ds.id if parent_state == "deleted" else uuid.uuid4()
    identities = {
        "editor": editor,
        "viewer": _member_as(ws, WorkspaceRole.VIEWER),
        "roleless": _member_as(ws, None),
        "system": _system_context(),
    }
    before = await _write_state_snapshot(db_session)
    async with _client_as(client, identities[identity]) as http:
        for response in await _write_requests(http, target_id, version.id, "denied"):
            _assert_dataset_not_found(response)
            assert str(target_id) not in response.text
            assert str(version.id) not in response.text
    assert await _write_state_snapshot(db_session) == before


@pytest.mark.parametrize("operation", ["delete", "checkout"])
async def test_version_disappearing_after_authorization_returns_identifier_free_404(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    """Normalize service lookup failures even after the write precheck succeeds."""
    ws, editor = await _workspace_with_role(db_session, "owner", WorkspaceRole.EDITOR)
    ds = await _seed_dataset(db_session, ws.id)
    await _seed_item(db_session, ds.id)
    version = await _create_version(db_session, ds.id, ws.id, "frozen")
    before = await _write_state_snapshot(db_session)

    async def missing_version(*args, **kwargs):
        raise DatasetVersionNotFoundError(f"Dataset version {version.id} not found")

    monkeypatch.setattr(
        EvaluationDatasetVersionService,
        "delete_version" if operation == "delete" else "checkout",
        missing_version,
    )
    async with _client_as(client, editor) as http:
        url = f"/api/evaluation/datasets/{ds.id}/versions/{version.id}"
        response = await http.delete(url) if operation == "delete" else await http.post(f"{url}/checkout")
    _assert_version_not_found(response)
    assert str(version.id) not in response.text
    assert await _write_state_snapshot(db_session) == before
