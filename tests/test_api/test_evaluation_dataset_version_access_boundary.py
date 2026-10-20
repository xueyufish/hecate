"""Evaluation dataset version read access boundary (step10).

Covers the shared read boundary on the three dataset version read
endpoints (listing, detail, diff):

- Parent dataset gating: versions under a missing, soft-deleted, or
  cross-workspace dataset are not listable or readable; the 404 body is
  the dataset boundary's "Dataset not found" and echoes no identifier.
- Version binding: a missing, soft-deleted, cross-workspace, or
  foreign-dataset version — including a diff target — is one constant
  "Dataset version not found" 404, never a 500, echoing no IDs.
- Role floor: viewer suffices for all three endpoints in the dataset's
  workspace; membership without a role is 403 after ownership.
- Identity rules: only explicit system scope crosses workspaces;
  missing-workspace non-system identities never degrade to a global
  query; a malformed ``against`` keeps its 422.
- Read-only: rejected requests leave dataset/version/item state
  untouched, and visible-version semantics (pagination, content hash,
  diff classification) survive the boundary unchanged.
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
from hecate.models.evaluation import EvaluationDatasetModel, EvaluationItemModel
from hecate.models.organization import OrganizationModel
from hecate.models.workspace import WorkspaceModel
from hecate.models.workspace_member import WorkspaceMemberModel, WorkspaceRole
from hecate.ops.evaluation.dataset_version_service import EvaluationDatasetVersionService


async def _workspace_with_role(db: AsyncSession, name: str, role: WorkspaceRole) -> tuple[WorkspaceModel, AuthContext]:
    """Create org + workspace + member and return (workspace, context).

    Slugs carry a random suffix: API sessions commit through the shared
    connection, so rows survive a test's ``db_session`` rollback and slug
    collisions would leak between tests.
    """
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
    name: str = "version-boundary-ds",
    deleted: bool = False,
) -> EvaluationDatasetModel:
    """Seed a dataset directly via the ORM."""
    ds = EvaluationDatasetModel(name=f"{name}-{uuid.uuid4().hex[:8]}", workspace_id=workspace_id)
    ds.deleted = deleted
    db.add(ds)
    await db.commit()
    return ds


async def _seed_item(db: AsyncSession, dataset_id: uuid.UUID, *, query: str = "probe?") -> EvaluationItemModel:
    """Seed a live dataset item directly via the ORM."""
    item = EvaluationItemModel(dataset_id=dataset_id, query=query)
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
    """Soft-delete a version via the service (setup helper)."""
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


# ---------------------------------------------------------------------------
# Parent dataset gating
# ---------------------------------------------------------------------------


async def test_versions_of_invisible_dataset_not_readable(client: AsyncClient, db_session: AsyncSession) -> None:
    """Missing, soft-deleted, and cross-workspace datasets hide their versions."""
    ws1, ws1_ctx = await _workspace_with_role(db_session, "ws-one", WorkspaceRole.VIEWER)
    ws2, ws2_ctx = await _workspace_with_role(db_session, "ws-two", WorkspaceRole.VIEWER)
    own = await _seed_dataset(db_session, ws1.id)
    await _create_version(db_session, own.id, ws1.id, "v1")
    foreign = await _seed_dataset(db_session, ws2.id)
    await _create_version(db_session, foreign.id, ws2.id, "v1")
    deleted_ds = await _seed_dataset(db_session, ws2.id)
    await _create_version(db_session, deleted_ds.id, ws2.id, "v1")
    deleted_ds.deleted = True  # seed the version first: create_version rejects deleted datasets
    await db_session.commit()

    async with _client_as(client, ws1_ctx) as http:
        cross = await http.get(f"/api/evaluation/datasets/{foreign.id}/versions")
        missing = await http.get(f"/api/evaluation/datasets/{uuid.uuid4()}/versions")
        assert cross.status_code == 404, cross.text
        assert cross.json()["detail"] == missing.json()["detail"]
        assert str(foreign.id) not in cross.text

    async with _client_as(client, ws2_ctx) as http:
        deleted = await http.get(f"/api/evaluation/datasets/{deleted_ds.id}/versions")
        assert deleted.status_code == 404, deleted.text
        assert deleted.json()["detail"] == missing.json()["detail"]


async def test_listing_visible_dataset_keeps_pagination_and_empty_semantics(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """A visible dataset paginates versions as before; no versions is 200/0."""
    ws, viewer_ctx = await _workspace_with_role(db_session, "ws-viewer", WorkspaceRole.VIEWER)
    ds = await _seed_dataset(db_session, ws.id)
    for index in range(3):
        await _seed_item(db_session, ds.id, query=f"q{index}")
        await _create_version(db_session, ds.id, ws.id, f"v{index}")
    empty_ds = await _seed_dataset(db_session, ws.id)

    async with _client_as(client, viewer_ctx) as http:
        listing = await http.get(f"/api/evaluation/datasets/{ds.id}/versions")
        assert listing.status_code == 200, listing.text
        assert listing.json()["total"] == 3

        paged = await http.get(f"/api/evaluation/datasets/{ds.id}/versions", params={"page": 1, "page_size": 2})
        assert paged.status_code == 200, paged.text
        assert paged.json()["total"] == 3
        assert len(paged.json()["items"]) == 2

        empty = await http.get(f"/api/evaluation/datasets/{empty_ds.id}/versions")
        assert empty.status_code == 200, empty.text
        assert empty.json()["items"] == []
        assert empty.json()["total"] == 0


# ---------------------------------------------------------------------------
# Version binding: detail / diff uniform 404 without ID echo
# ---------------------------------------------------------------------------


async def test_detail_invisible_version_uniform_and_id_free(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Missing, soft-deleted, cross-workspace, and foreign-dataset versions 404 alike."""
    ws1, ws1_ctx = await _workspace_with_role(db_session, "ws-one", WorkspaceRole.VIEWER)
    ws2, _ = await _workspace_with_role(db_session, "ws-two", WorkspaceRole.VIEWER)
    own = await _seed_dataset(db_session, ws1.id)
    own_other = await _seed_dataset(db_session, ws1.id)  # same ws, different dataset
    deleted_version = await _create_version(db_session, own.id, ws1.id, "gone")
    await _soft_delete_version(db_session, deleted_version.id)
    foreign_version = await _create_version(db_session, own_other.id, ws1.id, "other-ds")
    ws2_ds = await _seed_dataset(db_session, ws2.id)
    other_ws_version = await _create_version(db_session, ws2_ds.id, ws2.id, "ws2")

    async with _client_as(client, ws1_ctx) as http:
        probes = [
            await http.get(f"/api/evaluation/datasets/{own.id}/versions/{uuid.uuid4()}"),
            await http.get(f"/api/evaluation/datasets/{own.id}/versions/{deleted_version.id}"),
            await http.get(f"/api/evaluation/datasets/{own.id}/versions/{foreign_version.id}"),
            await http.get(f"/api/evaluation/datasets/{own.id}/versions/{other_ws_version.id}"),
        ]
        reference_missing = await http.get(f"/api/evaluation/datasets/{own.id}/versions/{uuid.uuid4()}")
    for probe in probes:
        _assert_version_not_found(probe)
        assert probe.json()["detail"] == reference_missing.json()["detail"]
        for version in (deleted_version, foreign_version, other_ws_version):
            assert str(version.id) not in probe.text


async def test_diff_invisible_target_returns_404_not_500(client: AsyncClient, db_session: AsyncSession) -> None:
    """A missing, soft-deleted, cross-dataset, or cross-workspace target is 404."""
    ws1, ws1_ctx = await _workspace_with_role(db_session, "ws-one", WorkspaceRole.VIEWER)
    ws2, _ = await _workspace_with_role(db_session, "ws-two", WorkspaceRole.VIEWER)
    own = await _seed_dataset(db_session, ws1.id)
    own_other = await _seed_dataset(db_session, ws1.id)
    source = await _create_version(db_session, own.id, ws1.id, "source")
    deleted_target = await _create_version(db_session, own.id, ws1.id, "deleted-target")
    await _soft_delete_version(db_session, deleted_target.id)
    foreign_target = await _create_version(db_session, own_other.id, ws1.id, "foreign-target")
    other_ws_target = await _create_version(
        db_session, (await _seed_dataset(db_session, ws2.id)).id, ws2.id, "ws2-target"
    )

    async with _client_as(client, ws1_ctx) as http:
        base_path = f"/api/evaluation/datasets/{own.id}/versions/{source.id}/diff"
        probes = [
            await http.get(base_path, params={"against": str(uuid.uuid4())}),
            await http.get(base_path, params={"against": str(deleted_target.id)}),
            await http.get(base_path, params={"against": str(foreign_target.id)}),
            await http.get(base_path, params={"against": str(other_ws_target.id)}),
        ]
        reference_missing = await http.get(f"/api/evaluation/datasets/{own.id}/versions/{uuid.uuid4()}")
    for probe in probes:
        _assert_version_not_found(probe)
        assert probe.json()["detail"] == reference_missing.json()["detail"]
        assert "500" not in str(probe.status_code)


async def test_diff_invisible_source_returns_404(client: AsyncClient, db_session: AsyncSession) -> None:
    """An invisible source version 404s before any diff computation."""
    ws, _ = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.VIEWER)
    ds = await _seed_dataset(db_session, ws.id)
    deleted_source = await _create_version(db_session, ds.id, ws.id, "deleted-source")
    await _soft_delete_version(db_session, deleted_source.id)

    async with _client_as(client, _member_as(ws, WorkspaceRole.VIEWER)) as http:
        resp = await http.get(
            f"/api/evaluation/datasets/{ds.id}/versions/{deleted_source.id}/diff", params={"against": "live"}
        )
    _assert_version_not_found(resp)


# ---------------------------------------------------------------------------
# Role floor and identity rules
# ---------------------------------------------------------------------------


async def test_ownership_precedes_role_for_cross_workspace_calls(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """A role-less caller from another workspace gets the dataset 404."""
    ws, _ = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.VIEWER)
    other_ws, _ = await _workspace_with_role(db_session, "ws-other", WorkspaceRole.VIEWER)
    ds = await _seed_dataset(db_session, ws.id)
    version = await _create_version(db_session, ds.id, ws.id, "v1")
    cross_workspace = _member_as(other_ws, None)

    async with _client_as(client, cross_workspace) as http:
        listing = await http.get(f"/api/evaluation/datasets/{ds.id}/versions")
        detail = await http.get(f"/api/evaluation/datasets/{ds.id}/versions/{version.id}")
        diff = await http.get(f"/api/evaluation/datasets/{ds.id}/versions/{version.id}/diff")
    for resp in (listing, detail, diff):
        _assert_dataset_not_found(resp)


@pytest.mark.parametrize("role", [WorkspaceRole.VIEWER, WorkspaceRole.EDITOR, WorkspaceRole.ADMIN])
async def test_roles_at_or_above_viewer_can_read(
    client: AsyncClient,
    db_session: AsyncSession,
    role: WorkspaceRole,
) -> None:
    """Viewer and above receive the same successful version reads."""
    ws, ctx = await _workspace_with_role(db_session, "ws-role", role)
    ds = await _seed_dataset(db_session, ws.id)
    await _seed_item(db_session, ds.id)
    version = await _create_version(db_session, ds.id, ws.id, "v1")

    async with _client_as(client, ctx) as http:
        listing = await http.get(f"/api/evaluation/datasets/{ds.id}/versions")
        detail = await http.get(f"/api/evaluation/datasets/{ds.id}/versions/{version.id}")
        diff = await http.get(f"/api/evaluation/datasets/{ds.id}/versions/{version.id}/diff")
    assert listing.status_code == 200, listing.text
    assert listing.json()["total"] == 1
    assert detail.status_code == 200, detail.text
    assert detail.json()["name"] == "v1"
    assert diff.status_code == 200, diff.text
    assert diff.json()["added"] == [] and diff.json()["removed"] == [] and diff.json()["changed"] == []


async def test_role_floor_inside_own_workspace(client: AsyncClient, db_session: AsyncSession) -> None:
    """Membership without a role is 403 on all three version read endpoints."""
    ws, _ = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.EDITOR)
    ds = await _seed_dataset(db_session, ws.id)
    version = await _create_version(db_session, ds.id, ws.id, "v1")
    other = await _create_version(db_session, ds.id, ws.id, "v2")
    role_less = _member_as(ws, None)

    async with _client_as(client, role_less) as http:
        listing = await http.get(f"/api/evaluation/datasets/{ds.id}/versions")
        detail = await http.get(f"/api/evaluation/datasets/{ds.id}/versions/{version.id}")
        diff = await http.get(
            f"/api/evaluation/datasets/{ds.id}/versions/{version.id}/diff", params={"against": str(other.id)}
        )
    _assert_forbidden(listing)
    _assert_forbidden(detail)
    _assert_forbidden(diff)


async def test_system_scope_reads_across_workspaces(client: AsyncClient, db_session: AsyncSession) -> None:
    """Explicit system scope keeps the pre-boundary cross-workspace reads."""
    ws, _ = await _workspace_with_role(db_session, "ws-owned", WorkspaceRole.VIEWER)
    ds = await _seed_dataset(db_session, ws.id)
    version = await _create_version(db_session, ds.id, ws.id, "v1")
    other = await _create_version(db_session, ds.id, ws.id, "v2")

    async with _client_as(client, _system_context()) as http:
        listing = await http.get(f"/api/evaluation/datasets/{ds.id}/versions")
        detail = await http.get(f"/api/evaluation/datasets/{ds.id}/versions/{version.id}")
        diff = await http.get(
            f"/api/evaluation/datasets/{ds.id}/versions/{version.id}/diff", params={"against": str(other.id)}
        )
    assert listing.status_code == 200, listing.text
    assert listing.json()["total"] == 2
    assert detail.status_code == 200, detail.text
    assert diff.status_code == 200, diff.text


async def test_missing_workspace_identity_denied_without_global_fallback(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """No active workspace: denied everywhere, no unscoped version reads."""
    ws, _ = await _workspace_with_role(db_session, "ws-seeded", WorkspaceRole.VIEWER)
    ds = await _seed_dataset(db_session, ws.id)
    version = await _create_version(db_session, ds.id, ws.id, "v1")

    async with _client_as(client, _no_workspace_context()) as http:
        listing = await http.get(f"/api/evaluation/datasets/{ds.id}/versions")
        detail = await http.get(f"/api/evaluation/datasets/{ds.id}/versions/{version.id}")
        diff = await http.get(f"/api/evaluation/datasets/{ds.id}/versions/{version.id}/diff")
    _assert_dataset_not_found(listing)
    _assert_dataset_not_found(detail)
    _assert_dataset_not_found(diff)


# ---------------------------------------------------------------------------
# Semantics preservation and read-only guarantee
# ---------------------------------------------------------------------------


async def test_invalid_against_keeps_422(client: AsyncClient, db_session: AsyncSession) -> None:
    """A malformed ``against`` value keeps the existing 422 response."""
    ws, viewer_ctx = await _workspace_with_role(db_session, "ws-viewer", WorkspaceRole.VIEWER)
    ds = await _seed_dataset(db_session, ws.id)
    version = await _create_version(db_session, ds.id, ws.id, "v1")

    async with _client_as(client, viewer_ctx) as http:
        resp = await http.get(
            f"/api/evaluation/datasets/{ds.id}/versions/{version.id}/diff", params={"against": "not-a-uuid"}
        )
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["error"]["code"] == "INVALID_REQUEST"


async def test_diff_success_semantics_preserved(client: AsyncClient, db_session: AsyncSession) -> None:
    """Content hashes and the classification algorithm are boundary-independent."""
    ws, viewer_ctx = await _workspace_with_role(db_session, "ws-viewer", WorkspaceRole.VIEWER)
    ds = await _seed_dataset(db_session, ws.id)
    await _seed_item(db_session, ds.id, query="kept")
    v1 = await _create_version(db_session, ds.id, ws.id, "v1")
    await _seed_item(db_session, ds.id, query="added after freeze")
    v2 = await _create_version(db_session, ds.id, ws.id, "v2")

    async with _client_as(client, viewer_ctx) as http:
        vs_live = await http.get(f"/api/evaluation/datasets/{ds.id}/versions/{v1.id}/diff")
        vs_v2 = await http.get(
            f"/api/evaluation/datasets/{ds.id}/versions/{v1.id}/diff", params={"against": str(v2.id)}
        )
        vs_self = await http.get(
            f"/api/evaluation/datasets/{ds.id}/versions/{v1.id}/diff", params={"against": str(v1.id)}
        )
    assert vs_live.status_code == 200, vs_live.text
    body = vs_live.json()
    assert body["base"]["content_hash"] == str(v1.content_hash)
    assert len(body["added"]) == 1  # the item added after the freeze
    assert vs_v2.json()["changed"] == [] and len(vs_v2.json()["added"]) == 1
    assert vs_self.json()["added"] == [] and vs_self.json()["removed"] == [] and vs_self.json()["changed"] == []


async def test_rejections_are_read_only(client: AsyncClient, db_session: AsyncSession) -> None:
    """Successful reads and role/ownership denials leave all dataset state unchanged."""
    from hecate.models.evaluation import EvaluationDatasetVersionModel

    ws, viewer_ctx = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.VIEWER)
    ds = await _seed_dataset(db_session, ws.id)
    await _seed_item(db_session, ds.id)
    source = await _create_version(db_session, ds.id, ws.id, "v1")
    target = await _create_version(db_session, ds.id, ws.id, "v2")

    async def snapshot():
        states = []
        for model in (EvaluationDatasetModel, EvaluationDatasetVersionModel, EvaluationItemModel):
            rows = await db_session.execute(select(model.__table__).order_by(model.id))
            states.append([dict(row) for row in rows.mappings()])
        return states

    before = await snapshot()
    for ctx, expected in ((viewer_ctx, 200), (_member_as(ws, None), 403), (_no_workspace_context(), 404)):
        async with _client_as(client, ctx) as http:
            responses = (
                await http.get(f"/api/evaluation/datasets/{ds.id}/versions"),
                await http.get(f"/api/evaluation/datasets/{ds.id}/versions/{source.id}"),
                await http.get(
                    f"/api/evaluation/datasets/{ds.id}/versions/{source.id}/diff", params={"against": str(target.id)}
                ),
            )
        for response in responses:
            assert response.status_code == expected, response.text
    assert await snapshot() == before


@pytest.mark.parametrize("side", ["source", "target"])
@pytest.mark.parametrize("fault", ["missing", "deleted", "other_dataset", "other_workspace"])
@pytest.mark.parametrize("role", [WorkspaceRole.VIEWER, None])
async def test_diff_checks_both_version_ownerships_before_role(
    client: AsyncClient,
    db_session: AsyncSession,
    side: str,
    fault: str,
    role: WorkspaceRole | None,
) -> None:
    """Any invisible side yields the same 404 before role denial or diff work."""
    ws, _ = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.VIEWER)
    ds = await _seed_dataset(db_session, ws.id)
    visible = await _create_version(db_session, ds.id, ws.id, "visible")
    if fault == "missing":
        invisible_id = uuid.uuid4()
    else:
        if fault == "other_workspace":
            foreign_ws, _ = await _workspace_with_role(db_session, "ws-foreign", WorkspaceRole.VIEWER)
            target_ws = foreign_ws.id
        else:
            target_ws = ws.id
        other_ds = await _seed_dataset(db_session, target_ws) if fault != "deleted" else ds
        invisible = await _create_version(db_session, other_ds.id, target_ws, "invisible")
        invisible_id = invisible.id
        if fault == "deleted":
            await _soft_delete_version(db_session, invisible.id)
    source_id = invisible_id if side == "source" else visible.id
    target_id = invisible_id if side == "target" else visible.id
    async with _client_as(client, _member_as(ws, role)) as http:
        response = await http.get(
            f"/api/evaluation/datasets/{ds.id}/versions/{source_id}/diff", params={"against": str(target_id)}
        )
        missing = await http.get(f"/api/evaluation/datasets/{ds.id}/versions/{uuid.uuid4()}")
    _assert_version_not_found(response)
    assert response.json() == missing.json()
    for identifier in (source_id, target_id, ds.id, ws.id):
        assert str(identifier) not in response.text


@pytest.mark.parametrize("endpoint", ["list", "detail", "diff"])
@pytest.mark.parametrize("fault", ["missing", "deleted"])
async def test_parent_gate_precedes_all_version_reads_and_role(
    client: AsyncClient, db_session: AsyncSession, endpoint: str, fault: str
) -> None:
    """An unavailable parent hides existing child versions on every read route."""
    ws, _ = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.VIEWER)
    ds = await _seed_dataset(db_session, ws.id)
    version = await _create_version(db_session, ds.id, ws.id, "v1")
    if fault == "deleted":
        ds.deleted = True
        await db_session.commit()
    dataset_id = ds.id if fault == "deleted" else uuid.uuid4()
    path = f"/api/evaluation/datasets/{dataset_id}/versions"
    if endpoint != "list":
        path += f"/{version.id}"
    if endpoint == "diff":
        path += "/diff"
    async with _client_as(client, _member_as(ws, None)) as http:
        response = await http.get(path)
    _assert_dataset_not_found(response)
    assert str(dataset_id) not in response.text
    assert str(version.id) not in response.text
