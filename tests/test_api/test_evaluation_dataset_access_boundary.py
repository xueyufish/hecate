"""Evaluation dataset read access boundary (step10).

Covers the shared read boundary on the three evaluation dataset read
endpoints (listing, detail, item listing):

- Workspace ownership: detail and items surface a missing, soft-deleted,
  or cross-workspace dataset as the *same* 404 body (existence is never
  disclosed); listing is scoped with an accurate ``total``.
- Role floor: viewer suffices for all three endpoints in the caller's
  workspace; membership without a role is 403 (after ownership, so
  cross-workspace stays 404).
- Identity rules: missing-workspace non-system identities never degrade
  to a global query; only explicit system scope crosses workspaces.
- Read-only: rejected requests leave dataset and item state untouched,
  and visible-dataset filters/pagination keep their existing semantics.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
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
    name: str = "boundary-ds",
    deleted: bool = False,
) -> EvaluationDatasetModel:
    """Seed a dataset directly via the ORM."""
    ds = EvaluationDatasetModel(name=f"{name}-{uuid.uuid4().hex[:8]}", workspace_id=workspace_id)
    ds.deleted = deleted
    db.add(ds)
    await db.commit()
    return ds


async def _seed_item(
    db: AsyncSession,
    dataset_id: uuid.UUID,
    *,
    query: str = "what is hecate?",
    tags: list[str] | None = None,
    known_bad: bool = False,
) -> EvaluationItemModel:
    """Seed a dataset item directly via the ORM."""
    item = EvaluationItemModel(dataset_id=dataset_id, query=query, tags=tags, known_bad=known_bad)
    db.add(item)
    await db.commit()
    return item


async def _dataset_count(db: AsyncSession) -> int:
    return len((await db.execute(select(EvaluationDatasetModel))).scalars().all())


async def _item_count(db: AsyncSession) -> int:
    return len((await db.execute(select(EvaluationItemModel))).scalars().all())


def _assert_not_found(resp: Any) -> None:
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"]["error"]["code"] == "NOT_FOUND"


def _assert_forbidden(resp: Any) -> None:
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"]["error"]["code"] == "FORBIDDEN"


# ---------------------------------------------------------------------------
# Listing: scoped to the caller workspace
# ---------------------------------------------------------------------------


async def test_listing_scoped_to_caller_workspace(client: AsyncClient, db_session: AsyncSession) -> None:
    """Filter before pagination and count only live datasets in the active workspace."""
    ws1, ws1_ctx = await _workspace_with_role(db_session, "ws-one", WorkspaceRole.VIEWER)
    ws2, ws2_ctx = await _workspace_with_role(db_session, "ws-two", WorkspaceRole.VIEWER)
    start = datetime(2026, 1, 1, tzinfo=UTC)
    datasets = [
        EvaluationDatasetModel(name="own-old", workspace_id=ws1.id, created_at=start),
        EvaluationDatasetModel(name="foreign-middle", workspace_id=ws2.id, created_at=start + timedelta(seconds=1)),
        EvaluationDatasetModel(name="own-new", workspace_id=ws1.id, created_at=start + timedelta(seconds=2)),
        EvaluationDatasetModel(name="foreign-new", workspace_id=ws2.id, created_at=start + timedelta(seconds=3)),
        EvaluationDatasetModel(
            name="own-deleted", workspace_id=ws1.id, deleted=True, created_at=start + timedelta(seconds=4)
        ),
    ]
    db_session.add_all(datasets)
    await db_session.commit()

    for ctx, expected in ((ws1_ctx, [datasets[2], datasets[0]]), (ws2_ctx, [datasets[3], datasets[1]])):
        async with _client_as(client, ctx) as http:
            for page, dataset in enumerate(expected, start=1):
                response = await http.get("/api/evaluation/datasets", params={"page": page, "page_size": 1})
                assert response.status_code == 200, response.text
                body = response.json()
                assert body["total"] == 2
                assert [item["id"] for item in body["items"]] == [str(dataset.id)]
                assert body["items"][0]["workspace_id"] == str(ctx.workspace_id)
            exhausted = await http.get("/api/evaluation/datasets", params={"page": 3, "page_size": 1})
            assert exhausted.status_code == 200, exhausted.text
            assert exhausted.json() == {"items": [], "total": 2}


async def test_system_scope_reads_across_workspaces(client: AsyncClient, db_session: AsyncSession) -> None:
    """System scope sees both workspaces but still excludes deleted datasets."""
    ws1, _ = await _workspace_with_role(db_session, "ws-one", WorkspaceRole.VIEWER)
    ws2, _ = await _workspace_with_role(db_session, "ws-two", WorkspaceRole.VIEWER)
    datasets = [await _seed_dataset(db_session, ws1.id), await _seed_dataset(db_session, ws2.id)]
    deleted = await _seed_dataset(db_session, ws2.id, deleted=True)
    for dataset in datasets:
        await _seed_item(db_session, dataset.id)

    async with _client_as(client, _system_context()) as http:
        listing = await http.get("/api/evaluation/datasets")
        assert listing.status_code == 200, listing.text
        assert listing.json()["total"] == 2
        assert {item["id"] for item in listing.json()["items"]} == {str(ds.id) for ds in datasets}
        for dataset in datasets:
            detail = await http.get(f"/api/evaluation/datasets/{dataset.id}")
            items = await http.get(f"/api/evaluation/datasets/{dataset.id}/items")
            assert detail.status_code == 200, detail.text
            assert detail.json()["id"] == str(dataset.id)
            assert items.status_code == 200, items.text
            assert items.json()["total"] == 1
        _assert_not_found(await http.get(f"/api/evaluation/datasets/{deleted.id}"))
        _assert_not_found(await http.get(f"/api/evaluation/datasets/{deleted.id}/items"))


@pytest.mark.parametrize("auth_method,api_key_scope", [("jwt", None), ("api_key", "workspace")])
@pytest.mark.parametrize("role", [None, WorkspaceRole.VIEWER])
async def test_missing_workspace_identity_never_degrades_to_global(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_method: str,
    api_key_scope: str | None,
    role: WorkspaceRole | None,
) -> None:
    """Missing workspace denies real resources even with a cached viewer role."""
    ws, _ = await _workspace_with_role(db_session, "ws-seeded", WorkspaceRole.VIEWER)
    dataset = await _seed_dataset(db_session, ws.id)
    item = await _seed_item(db_session, dataset.id, query="private evidence")
    identity = replace(_no_workspace_context(), auth_method=auth_method, api_key_scope=api_key_scope, role=role)

    async with _client_as(client, identity) as http:
        listing = await http.get("/api/evaluation/datasets")
        detail = await http.get(f"/api/evaluation/datasets/{dataset.id}")
        items = await http.get(f"/api/evaluation/datasets/{dataset.id}/items")
        missing = await http.get(f"/api/evaluation/datasets/{uuid.uuid4()}")
    _assert_forbidden(listing)
    _assert_not_found(detail)
    _assert_not_found(items)
    assert detail.json() == items.json() == missing.json()
    assert item.query not in items.text
    assert str(dataset.id) not in detail.text


async def test_detail_cross_workspace_matches_missing_and_deleted(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Missing, soft-deleted and cross-workspace share the one 404 body."""
    ws, owner_ctx = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.VIEWER)
    _, outsider_ctx = await _workspace_with_role(db_session, "ws-outsider", WorkspaceRole.VIEWER)
    other = await _seed_dataset(db_session, ws.id)
    deleted = await _seed_dataset(db_session, ws.id, deleted=True)

    async with _client_as(client, outsider_ctx) as http:
        probe_cross = await http.get(f"/api/evaluation/datasets/{other.id}")
        reference_missing = await http.get(f"/api/evaluation/datasets/{uuid.uuid4()}")
        assert probe_cross.status_code == 404, probe_cross.text
        assert probe_cross.json()["detail"] == reference_missing.json()["detail"]
        assert str(other.id) not in probe_cross.text

    async with _client_as(client, owner_ctx) as http:
        probe_deleted = await http.get(f"/api/evaluation/datasets/{deleted.id}")
        assert probe_deleted.status_code == 404, probe_deleted.text
        assert probe_deleted.json()["detail"] == reference_missing.json()["detail"]


async def test_items_of_invisible_dataset_return_uniform_404(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Item listing for an invisible dataset 404s; no empty-list success."""
    ws, owner_ctx = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.VIEWER)
    _, outsider_ctx = await _workspace_with_role(db_session, "ws-outsider", WorkspaceRole.VIEWER)
    other = await _seed_dataset(db_session, ws.id)
    deleted = await _seed_dataset(db_session, ws.id, deleted=True)
    await _seed_item(db_session, other.id)

    async with _client_as(client, outsider_ctx) as http:
        probe_cross = await http.get(f"/api/evaluation/datasets/{other.id}/items")
        probe_missing = await http.get(f"/api/evaluation/datasets/{uuid.uuid4()}/items")
        assert probe_cross.status_code == 404, probe_cross.text
        assert probe_cross.json()["detail"] == probe_missing.json()["detail"]
        assert str(other.id) not in probe_cross.text

    async with _client_as(client, owner_ctx) as http:
        probe_deleted = await http.get(f"/api/evaluation/datasets/{deleted.id}/items")
        assert probe_deleted.status_code == 404, probe_deleted.text
        assert probe_deleted.json()["detail"] == probe_missing.json()["detail"]


@pytest.mark.parametrize("suffix", ["", "/items"])
async def test_ownership_precedes_role_for_cross_workspace_calls(
    client: AsyncClient,
    db_session: AsyncSession,
    suffix: str,
) -> None:
    """A role-less caller from another workspace gets the same 404 as missing."""
    ws, _ = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.VIEWER)
    other_ws, _ = await _workspace_with_role(db_session, "ws-other", WorkspaceRole.VIEWER)
    ds = await _seed_dataset(db_session, ws.id)
    cross_workspace = _member_as(other_ws, None)  # active workspace differs, no role

    async with _client_as(client, cross_workspace) as http:
        resp = await http.get(f"/api/evaluation/datasets/{ds.id}{suffix}")
        missing = await http.get(f"/api/evaluation/datasets/{uuid.uuid4()}{suffix}")
    _assert_not_found(resp)
    assert resp.json() == missing.json()


@pytest.mark.parametrize("role", [WorkspaceRole.VIEWER, WorkspaceRole.EDITOR, WorkspaceRole.ADMIN])
async def test_viewer_role_suffices_inside_own_workspace(
    client: AsyncClient,
    db_session: AsyncSession,
    role: WorkspaceRole,
) -> None:
    """A viewer sees the same successful responses a higher role would."""
    ws, viewer_ctx = await _workspace_with_role(db_session, "ws-viewer", role)
    ds = await _seed_dataset(db_session, ws.id)
    await _seed_item(db_session, ds.id)

    async with _client_as(client, viewer_ctx) as http:
        listing = await http.get("/api/evaluation/datasets")
        detail = await http.get(f"/api/evaluation/datasets/{ds.id}")
        items = await http.get(f"/api/evaluation/datasets/{ds.id}/items")
    assert listing.status_code == 200, listing.text
    assert detail.status_code == 200, detail.text
    assert detail.json()["id"] == str(ds.id)
    assert items.status_code == 200, items.text
    assert items.json()["total"] == 1


async def test_role_floor_inside_own_workspace(client: AsyncClient, db_session: AsyncSession) -> None:
    """Membership without a role is 403 on all three read endpoints."""
    ws, _ = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.EDITOR)
    ds = await _seed_dataset(db_session, ws.id)
    role_less = _member_as(ws, None)

    async with _client_as(client, role_less) as http:
        listing = await http.get("/api/evaluation/datasets")
        detail = await http.get(f"/api/evaluation/datasets/{ds.id}")
        items = await http.get(f"/api/evaluation/datasets/{ds.id}/items")
    _assert_forbidden(listing)
    _assert_forbidden(detail)
    _assert_forbidden(items)


async def test_rejections_are_read_only(client: AsyncClient, db_session: AsyncSession) -> None:
    """Rejected requests create, modify or delete no dataset or item state."""
    ws, _ = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.VIEWER)
    ds = await _seed_dataset(db_session, ws.id)
    await _seed_item(db_session, ds.id)
    datasets_before = await _dataset_count(db_session)
    items_before = await _item_count(db_session)
    dataset_rows = (await db_session.execute(select(*EvaluationDatasetModel.__table__.columns))).all()
    item_rows = (await db_session.execute(select(*EvaluationItemModel.__table__.columns))).all()

    async with _client_as(client, _no_workspace_context()) as http:
        await http.get("/api/evaluation/datasets")
        await http.get(f"/api/evaluation/datasets/{ds.id}")
        await http.get(f"/api/evaluation/datasets/{ds.id}/items")
    assert await _dataset_count(db_session) == datasets_before
    assert await _item_count(db_session) == items_before
    assert (await db_session.execute(select(*EvaluationDatasetModel.__table__.columns))).all() == dataset_rows
    assert (await db_session.execute(select(*EvaluationItemModel.__table__.columns))).all() == item_rows


async def test_visible_dataset_filters_and_pagination_keep_semantics(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Visible datasets keep filter, ordering, pagination and empty results."""
    ws, viewer_ctx = await _workspace_with_role(db_session, "ws-viewer", WorkspaceRole.VIEWER)
    tagged = await _seed_dataset(db_session, ws.id)
    empty_ds = await _seed_dataset(db_session, ws.id)
    await _seed_item(db_session, tagged.id, tags=["smoke"])
    await _seed_item(db_session, tagged.id, tags=["regression"])
    await _seed_item(db_session, tagged.id, query="bad item", known_bad=True)

    async with _client_as(client, viewer_ctx) as http:
        tagged_items = await http.get(
            f"/api/evaluation/datasets/{tagged.id}/items", params=[("tags", "smoke"), ("tags", "regression")]
        )
        assert tagged_items.status_code == 200, tagged_items.text
        # Existing semantics: tag matching narrows the returned items (OR),
        # while ``total`` keeps counting the unfiltered set for backwards
        # compatibility — the boundary must not change either part.
        assert len(tagged_items.json()["items"]) == 2
        assert tagged_items.json()["total"] == 3

        bad_only = await http.get(f"/api/evaluation/datasets/{tagged.id}/items", params={"known_bad": "true"})
        assert bad_only.status_code == 200, bad_only.text
        assert bad_only.json()["total"] == 1
        assert all(item["known_bad"] is True for item in bad_only.json()["items"])

        active_only = await http.get(f"/api/evaluation/datasets/{tagged.id}/items", params={"known_bad": "false"})
        assert active_only.status_code == 200, active_only.text
        assert active_only.json()["total"] == 2
        assert all(item["known_bad"] is False for item in active_only.json()["items"])

        unmatched = await http.get(f"/api/evaluation/datasets/{tagged.id}/items", params={"tags": "absent"})
        assert unmatched.status_code == 200, unmatched.text
        assert unmatched.json() == {"items": [], "total": 3}

        paged = await http.get(f"/api/evaluation/datasets/{tagged.id}/items", params={"page": 1, "page_size": 2})
        assert paged.status_code == 200, paged.text
        assert paged.json()["total"] == 3
        assert len(paged.json()["items"]) == 2

        empty = await http.get(f"/api/evaluation/datasets/{empty_ds.id}/items")
        assert empty.status_code == 200, empty.text
        assert empty.json()["items"] == []
        assert empty.json()["total"] == 0


@pytest.mark.parametrize("path", ["/api/evaluation/datasets"])
async def test_listing_never_leaks_other_workspace_names(
    client: AsyncClient,
    db_session: AsyncSession,
    path: str,
) -> None:
    """Cross-workspace dataset names never appear in another workspace's view."""
    ws1, ws1_ctx = await _workspace_with_role(db_session, "ws-one", WorkspaceRole.VIEWER)
    ws2, _ = await _workspace_with_role(db_session, "ws-two", WorkspaceRole.VIEWER)
    foreign = await _seed_dataset(db_session, ws2.id)

    async with _client_as(client, ws1_ctx) as http:
        resp = await http.get(path)
    assert resp.status_code == 200, resp.text
    assert str(foreign.id) not in resp.text
    assert foreign.name not in resp.text
    assert resp.json() == {"items": [], "total": 0}
