"""Evaluation dataset item write access boundary (step10).

Covers the shared write boundary on the three dataset item write
endpoints (add, known-bad marking, delete):

- Parent dataset gating: items under a missing, soft-deleted, or
  cross-workspace dataset are not addable, markable, or deletable; the
  404 body is the dataset boundary's "Dataset not found" and echoes no
  identifier.
- Item binding: a missing, soft-deleted, or foreign-dataset item —
  including item IDs spliced from another dataset or workspace — is one
  constant "Item not found" 404, and the victim record is untouched.
- Role floor: editor suffices for all three endpoints in the dataset's
  workspace; viewer and role-less members are 403 after ownership, with
  known-bad provenance unchanged.
- Identity rules: only explicit system scope crosses workspaces;
  missing-workspace non-system identities never degrade to an unscoped
  write.
- Workspace inheritance: new items carry the parent dataset's real
  workspace; historical zero-UUID items stay readable and writable.
- Semantics: response shapes, blank-query skipping, marking validation,
  and server-side provenance survive the boundary unchanged.
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
    deleted: bool = False,
) -> EvaluationDatasetModel:
    """Seed a dataset directly via the ORM."""
    ds = EvaluationDatasetModel(name=f"item-boundary-ds-{uuid.uuid4().hex[:8]}", workspace_id=workspace_id)
    ds.deleted = deleted
    db.add(ds)
    await db.commit()
    return ds


async def _seed_item(
    db: AsyncSession,
    dataset_id: uuid.UUID,
    *,
    query: str = "existing item",
    known_bad: bool = False,
) -> EvaluationItemModel:
    """Seed a live dataset item directly via the ORM."""
    item = EvaluationItemModel(dataset_id=dataset_id, query=query, known_bad=known_bad)
    db.add(item)
    await db.commit()
    return item


async def _mark_item_known_bad(db: AsyncSession, item_id: uuid.UUID) -> None:
    """Seed a known-bad mark (with provenance) directly via the ORM."""
    from datetime import UTC, datetime

    item = (await db.execute(select(EvaluationItemModel).where(EvaluationItemModel.id == item_id))).scalar_one()
    item.known_bad = True
    item.known_bad_reason = "stale fixture"
    item.known_bad_marked_by = uuid.uuid4()
    item.known_bad_marked_at = datetime.now(UTC)
    await db.commit()


async def _item_snapshot(db: AsyncSession, item_id: uuid.UUID) -> dict[str, Any]:
    """Read every persisted column without reusing ORM identity-map state."""
    table = EvaluationItemModel.__table__
    row = (await db.execute(select(table).where(table.c.id == item_id))).mappings().one()
    return dict(row)


async def _database_snapshot(db: AsyncSession) -> list[list[dict[str, Any]]]:
    """Read all dataset and item rows, including deleted rows and timestamps."""
    snapshots = []
    for model in (EvaluationDatasetModel, EvaluationItemModel):
        table = model.__table__
        rows = (await db.execute(select(table).order_by(table.c.id))).mappings().all()
        snapshots.append([dict(row) for row in rows])
    return snapshots


def _assert_dataset_not_found(resp: Any) -> None:
    assert resp.status_code == 404, resp.text
    assert resp.json() == {"detail": {"error": {"code": "NOT_FOUND", "message": "Dataset not found", "details": None}}}


def _assert_item_not_found(resp: Any) -> None:
    assert resp.status_code == 404, resp.text
    assert resp.json() == {"detail": {"error": {"code": "NOT_FOUND", "message": "Item not found", "details": None}}}


def _assert_forbidden(resp: Any) -> None:
    assert resp.status_code == 403, resp.text
    message = resp.json()["detail"]["error"]["message"]
    assert message in {"Editor role required", "Workspace membership required"}
    assert resp.json() == {"detail": {"error": {"code": "FORBIDDEN", "message": message, "details": None}}}


# ---------------------------------------------------------------------------
# Parent dataset gating
# ---------------------------------------------------------------------------


async def test_items_of_invisible_dataset_not_writable(client: AsyncClient, db_session: AsyncSession) -> None:
    """Missing, soft-deleted, and cross-workspace datasets reject all writes."""
    ws1, ws1_ctx = await _workspace_with_role(db_session, "ws-one", WorkspaceRole.EDITOR)
    ws2, _ = await _workspace_with_role(db_session, "ws-two", WorkspaceRole.EDITOR)
    foreign = await _seed_dataset(db_session, ws2.id)
    deleted_ds = await _seed_dataset(db_session, ws1.id, deleted=True)

    async with _client_as(client, ws1_ctx) as http:
        probes = [
            await http.post(f"/api/evaluation/datasets/{foreign.id}/items", json=[{"query": "x"}]),
            await http.post(f"/api/evaluation/datasets/{uuid.uuid4()}/items", json=[{"query": "x"}]),
            await http.post(f"/api/evaluation/datasets/{deleted_ds.id}/items", json=[{"query": "x"}]),
            await http.patch(f"/api/evaluation/datasets/{foreign.id}/items/{uuid.uuid4()}", json={"known_bad": True}),
            await http.delete(f"/api/evaluation/datasets/{foreign.id}/items/{uuid.uuid4()}"),
        ]
    for probe in probes:
        _assert_dataset_not_found(probe)
        assert str(foreign.id) not in probe.text


# ---------------------------------------------------------------------------
# Item binding: cross-dataset and cross-workspace references
# ---------------------------------------------------------------------------


async def test_cross_dataset_item_references_have_zero_effect(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Spliced item IDs modify or delete nothing, and victims stay intact."""
    ws, editor_ctx = await _workspace_with_role(db_session, "ws-editor", WorkspaceRole.EDITOR)
    own = await _seed_dataset(db_session, ws.id)
    other = await _seed_dataset(db_session, ws.id)  # same workspace, different dataset
    victim = await _seed_item(db_session, other.id, known_bad=True)
    before = await _item_snapshot(db_session, victim.id)

    async with _client_as(client, editor_ctx) as http:
        marked = await http.patch(f"/api/evaluation/datasets/{own.id}/items/{victim.id}", json={"known_bad": False})
        deleted = await http.delete(f"/api/evaluation/datasets/{own.id}/items/{victim.id}")
    _assert_item_not_found(marked)
    _assert_item_not_found(deleted)
    assert str(victim.id) not in marked.text
    assert str(victim.id) not in deleted.text
    assert await _item_snapshot(db_session, victim.id) == before  # content, markers, deletion untouched


async def test_cross_workspace_item_ids_are_not_writable(client: AsyncClient, db_session: AsyncSession) -> None:
    """Another workspace's items are unreachable even with its dataset ID."""
    ws1, ws1_ctx = await _workspace_with_role(db_session, "ws-one", WorkspaceRole.EDITOR)
    ws2, _ = await _workspace_with_role(db_session, "ws-two", WorkspaceRole.EDITOR)
    foreign_ds = await _seed_dataset(db_session, ws2.id)
    victim = await _seed_item(db_session, foreign_ds.id, known_bad=True)
    before = await _item_snapshot(db_session, victim.id)

    async with _client_as(client, ws1_ctx) as http:
        marked = await http.patch(
            f"/api/evaluation/datasets/{foreign_ds.id}/items/{victim.id}", json={"known_bad": False}
        )
        deleted = await http.delete(f"/api/evaluation/datasets/{foreign_ds.id}/items/{victim.id}")
    _assert_dataset_not_found(marked)
    _assert_dataset_not_found(deleted)
    assert await _item_snapshot(db_session, victim.id) == before


# ---------------------------------------------------------------------------
# Role floor
# ---------------------------------------------------------------------------


async def test_editor_role_floor_inside_own_workspace(client: AsyncClient, db_session: AsyncSession) -> None:
    """Viewer and role-less members are 403 with provenance untouched."""
    ws, _ = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.EDITOR)
    ds = await _seed_dataset(db_session, ws.id)
    marked_item = await _seed_item(db_session, ds.id)
    await _mark_item_known_bad(db_session, marked_item.id)
    before = await _item_snapshot(db_session, marked_item.id)
    ds_before_count = len((await db_session.execute(select(EvaluationItemModel))).scalars().all())

    for identity in (_member_as(ws, WorkspaceRole.VIEWER), _member_as(ws, None)):
        async with _client_as(client, identity) as http:
            added = await http.post(f"/api/evaluation/datasets/{ds.id}/items", json=[{"query": "nope"}])
            marked = await http.patch(
                f"/api/evaluation/datasets/{ds.id}/items/{marked_item.id}", json={"known_bad": False}
            )
            deleted = await http.delete(f"/api/evaluation/datasets/{ds.id}/items/{marked_item.id}")
        _assert_forbidden(added)
        _assert_forbidden(marked)
        _assert_forbidden(deleted)

    assert len((await db_session.execute(select(EvaluationItemModel))).scalars().all()) == ds_before_count
    assert await _item_snapshot(db_session, marked_item.id) == before


@pytest.mark.parametrize("role", [WorkspaceRole.EDITOR, WorkspaceRole.ADMIN])
async def test_editor_and_admin_can_write(client: AsyncClient, db_session: AsyncSession, role: WorkspaceRole) -> None:
    """Editor and above receive the same successful write responses."""
    ws, ctx = await _workspace_with_role(db_session, "ws-writer", role)
    ds = await _seed_dataset(db_session, ws.id)
    item = await _seed_item(db_session, ds.id)

    async with _client_as(client, ctx) as http:
        added = await http.post(
            f"/api/evaluation/datasets/{ds.id}/items", json=[{"query": "new item", "tags": ["smoke"]}]
        )
        assert added.status_code == 201, added.text
        assert added.json() == {"added": 1}

        marked = await http.patch(
            f"/api/evaluation/datasets/{ds.id}/items/{item.id}", json={"known_bad": True, "known_bad_reason": "bad"}
        )
        assert marked.status_code == 200, marked.text
        assert marked.json()["known_bad"] is True
        assert marked.json()["known_bad_reason"] == "bad"
        assert marked.json()["known_bad_marked_by"] == str(ctx.user_id)
        assert marked.json()["known_bad_marked_at"] is not None

        cleared = await http.patch(f"/api/evaluation/datasets/{ds.id}/items/{item.id}", json={"known_bad": False})
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["known_bad"] is False
        assert cleared.json()["known_bad_reason"] is None
        assert cleared.json()["known_bad_marked_by"] is None
        assert cleared.json()["known_bad_marked_at"] is None
        assert cleared.json()["known_bad_expires_at"] is None

        removed = await http.delete(f"/api/evaluation/datasets/{ds.id}/items/{item.id}")
        assert removed.status_code == 204, removed.text


async def test_system_scope_writes_across_workspaces(client: AsyncClient, db_session: AsyncSession) -> None:
    """Explicit system scope keeps the pre-boundary cross-workspace writes."""
    ws, _ = await _workspace_with_role(db_session, "ws-owned", WorkspaceRole.EDITOR)
    ds = await _seed_dataset(db_session, ws.id)
    item = await _seed_item(db_session, ds.id)

    async with _client_as(client, _system_context()) as http:
        added = await http.post(f"/api/evaluation/datasets/{ds.id}/items", json=[{"query": "sys item"}])
        marked = await http.patch(
            f"/api/evaluation/datasets/{ds.id}/items/{item.id}", json={"known_bad": True, "known_bad_reason": "x"}
        )
        removed = await http.delete(f"/api/evaluation/datasets/{ds.id}/items/{item.id}")
    assert marked.status_code == 200, marked.text
    assert added.status_code == 201, added.text
    assert removed.status_code == 204, removed.text
    rows = (
        (
            await db_session.execute(
                select(EvaluationItemModel).where(
                    EvaluationItemModel.dataset_id == ds.id, EvaluationItemModel.query == "sys item"
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].workspace_id == ws.id


async def test_missing_workspace_identity_denied_without_unscoped_write(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """No active workspace: denied everywhere, no unscoped write fallback."""
    ws, _ = await _workspace_with_role(db_session, "ws-seeded", WorkspaceRole.EDITOR)
    ds = await _seed_dataset(db_session, ws.id)
    item = await _seed_item(db_session, ds.id)
    before = await _item_snapshot(db_session, item.id)

    async with _client_as(client, _no_workspace_context()) as http:
        added = await http.post(f"/api/evaluation/datasets/{ds.id}/items", json=[{"query": "x"}])
        marked = await http.patch(f"/api/evaluation/datasets/{ds.id}/items/{item.id}", json={"known_bad": False})
        deleted = await http.delete(f"/api/evaluation/datasets/{ds.id}/items/{item.id}")
    _assert_dataset_not_found(added)
    _assert_dataset_not_found(marked)
    _assert_dataset_not_found(deleted)
    assert await _item_snapshot(db_session, item.id) == before


# ---------------------------------------------------------------------------
# Workspace inheritance and semantics preservation
# ---------------------------------------------------------------------------


async def test_new_items_inherit_parent_workspace_and_history_stays_writable(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """New items carry the parent workspace; zero-UUID history stays usable."""
    ws, editor_ctx = await _workspace_with_role(db_session, "ws-editor", WorkspaceRole.EDITOR)
    ds = await _seed_dataset(db_session, ws.id)
    legacy = await _seed_item(db_session, ds.id)  # seeded without workspace: zero UUID
    assert str(legacy.workspace_id) == "00000000-0000-0000-0000-000000000000"

    async with _client_as(client, editor_ctx) as http:
        added = await http.post(f"/api/evaluation/datasets/{ds.id}/items", json=[{"query": "fresh"}])
        assert added.status_code == 201, added.text
        listing = await http.get(f"/api/evaluation/datasets/{ds.id}/items")
        assert listing.status_code == 200, listing.text
        items = {entry["query"]: entry for entry in listing.json()["items"]}
        assert items["fresh"]["workspace_id"] == str(ws.id)  # new rows inherit the parent workspace
        # The historical zero-UUID row remains fully readable and writable.
        legacy_id = items["existing item"]["id"]
        marked = await http.patch(
            f"/api/evaluation/datasets/{ds.id}/items/{legacy_id}", json={"known_bad": True, "known_bad_reason": "old"}
        )
        assert marked.status_code == 200, marked.text
        removed = await http.delete(f"/api/evaluation/datasets/{ds.id}/items/{legacy_id}")
        assert removed.status_code == 204, removed.text


async def test_blank_query_skipped_from_added_count(client: AsyncClient, db_session: AsyncSession) -> None:
    """Blank-query entries stay excluded from the count and persistence."""
    ws, editor_ctx = await _workspace_with_role(db_session, "ws-editor", WorkspaceRole.EDITOR)
    ds = await _seed_dataset(db_session, ws.id)

    async with _client_as(client, editor_ctx) as http:
        resp = await http.post(f"/api/evaluation/datasets/{ds.id}/items", json=[{"query": "   "}, {"query": "valid"}])
    assert resp.status_code == 201, resp.text
    assert resp.json() == {"added": 1}
    rows = (
        (await db_session.execute(select(EvaluationItemModel).where(EvaluationItemModel.dataset_id == ds.id)))
        .scalars()
        .all()
    )
    assert [row.query for row in rows] == ["valid"]


async def test_invalid_marking_keeps_422_and_item_unchanged(client: AsyncClient, db_session: AsyncSession) -> None:
    """Marking without a reason keeps the 422 semantics; the item is untouched."""
    ws, editor_ctx = await _workspace_with_role(db_session, "ws-editor", WorkspaceRole.EDITOR)
    ds = await _seed_dataset(db_session, ws.id)
    item = await _seed_item(db_session, ds.id)
    before = await _item_snapshot(db_session, item.id)

    async with _client_as(client, editor_ctx) as http:
        resp = await http.patch(f"/api/evaluation/datasets/{ds.id}/items/{item.id}", json={"known_bad": True})
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["error"]["code"] == "INVALID_EXEMPTION"
    assert await _item_snapshot(db_session, item.id) == before


async def test_rejections_are_read_only(client: AsyncClient, db_session: AsyncSession) -> None:
    """Rejected requests create, modify, or delete no dataset or item state."""
    ws, _ = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.EDITOR)
    ds = await _seed_dataset(db_session, ws.id)
    item = await _seed_item(db_session, ds.id)
    before = await _database_snapshot(db_session)

    async with _client_as(client, _member_as(ws, WorkspaceRole.VIEWER)) as http:
        await http.post(f"/api/evaluation/datasets/{ds.id}/items", json=[{"query": "x"}])
        await http.patch(f"/api/evaluation/datasets/{ds.id}/items/{item.id}", json={"known_bad": False})
        await http.delete(f"/api/evaluation/datasets/{ds.id}/items/{item.id}")
    assert await _database_snapshot(db_session) == before


@pytest.mark.parametrize("role", [WorkspaceRole.VIEWER, None])
@pytest.mark.parametrize("method", ["POST", "PATCH", "DELETE"])
async def test_parent_visibility_precedes_role_checks(
    client: AsyncClient, db_session: AsyncSession, role: WorkspaceRole | None, method: str
) -> None:
    """Hidden parents return identical 404s even when the role is insufficient."""
    ws, _ = await _workspace_with_role(db_session, "parent-order", WorkspaceRole.EDITOR)
    foreign_ws, _ = await _workspace_with_role(db_session, "foreign-parent", WorkspaceRole.EDITOR)
    foreign = await _seed_dataset(db_session, foreign_ws.id)
    deleted = await _seed_dataset(db_session, ws.id, deleted=True)
    before = await _database_snapshot(db_session)
    bodies = []
    async with _client_as(client, _member_as(ws, role)) as http:
        for dataset_id in (uuid.uuid4(), deleted.id, foreign.id):
            path = f"/api/evaluation/datasets/{dataset_id}/items"
            if method != "POST":
                path += f"/{uuid.uuid4()}"
            payload = [{"query": "denied"}] if method == "POST" else {"known_bad": False}
            response = await http.request(method, path, **({"json": payload} if method != "DELETE" else {}))
            _assert_dataset_not_found(response)
            bodies.append(response.json())
            assert await _database_snapshot(db_session) == before
    assert bodies[0] == bodies[1] == bodies[2]


@pytest.mark.parametrize("role", [WorkspaceRole.VIEWER, None, WorkspaceRole.EDITOR])
@pytest.mark.parametrize("method", ["PATCH", "DELETE"])
async def test_item_binding_precedes_role_checks(
    client: AsyncClient, db_session: AsyncSession, role: WorkspaceRole | None, method: str
) -> None:
    """Missing, deleted and foreign children share one 404 before role checks."""
    ws, _ = await _workspace_with_role(db_session, "child-order", WorkspaceRole.EDITOR)
    foreign_ws, _ = await _workspace_with_role(db_session, "foreign-child", WorkspaceRole.EDITOR)
    own = await _seed_dataset(db_session, ws.id)
    other = await _seed_dataset(db_session, ws.id)
    foreign = await _seed_dataset(db_session, foreign_ws.id)
    other_item = await _seed_item(db_session, other.id, known_bad=True)
    foreign_item = await _seed_item(db_session, foreign.id, known_bad=True)
    deleted = await _seed_item(db_session, own.id)
    deleted.deleted = True
    await db_session.commit()
    before = await _database_snapshot(db_session)
    bodies = []
    async with _client_as(client, _member_as(ws, role)) as http:
        for item_id in (uuid.uuid4(), deleted.id, other_item.id, foreign_item.id):
            path = f"/api/evaluation/datasets/{own.id}/items/{item_id}"
            response = await http.request(method, path, **({"json": {"known_bad": False}} if method == "PATCH" else {}))
            _assert_item_not_found(response)
            bodies.append(response.json())
            assert await _database_snapshot(db_session) == before
    assert all(body == bodies[0] for body in bodies)
