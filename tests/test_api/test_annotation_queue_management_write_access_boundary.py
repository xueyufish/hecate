"""Annotation queue management write access boundary (step10).

Covers the three queue management writes:

- Create: an active workspace is required for every identity — system
  scope included (403 ``NO_WORKSPACE``) — and non-system callers need
  an editor role floor (403 ``FORBIDDEN``); both checks precede the
  create service and its metric validation.
- Update / delete: the queue resolves through the read boundary first
  (existence, soft-delete state, workspace ownership before the editor
  role check); missing, soft-deleted, and foreign queues surface as the
  same identifier-free 404 for non-system identities, and a
  workspace-less member is denied before any query.
- Semantics survive the boundary: partial updates, ``assigned_user_ids``
  replacement / clearing / omission round-tripping as strings like
  creation, soft delete retaining items and scores while reads stop
  exposing them, zero-UUID workspaces, and authorization taking
  precedence over metric validation.
- Denied writes never touch queue, item, or score data, verified with
  service spies and table-direct full-column snapshots.
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
from hecate.models.evaluation import (
    AnnotationQueueItemModel,
    AnnotationQueueModel,
    EvaluationTaskScoreModel,
)
from hecate.models.organization import OrganizationModel
from hecate.models.workspace import WorkspaceModel
from hecate.models.workspace_member import WorkspaceMemberModel, WorkspaceRole

_BASE = "/api/evaluation/annotation-queues"

_METRIC_DEFS = [{"name": "helpfulness", "data_type": "numeric", "min": 0, "max": 1}]


async def _workspace_with_role(db: AsyncSession, name: str, role: WorkspaceRole) -> tuple[WorkspaceModel, AuthContext]:
    """Create an isolated organization, workspace, member, and auth context."""
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


def _create_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"name": f"boundary-queue-{uuid.uuid4().hex[:8]}", "metric_defs": _METRIC_DEFS}
    payload.update(overrides)
    return payload


async def _seed_queue(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    assigned_user_ids: list[str] | None = None,
    deleted: bool = False,
) -> AnnotationQueueModel:
    queue = AnnotationQueueModel(
        name=f"boundary-queue-{uuid.uuid4().hex[:8]}",
        instructions="Rate the output",
        metric_defs=[{"name": "helpfulness", "data_type": "numeric", "min": 0, "max": 1}],
        assigned_user_ids=assigned_user_ids or [],
        workspace_id=workspace_id,
        deleted=deleted,
        deleted_at=datetime.now(UTC) if deleted else None,
    )
    db.add(queue)
    await db.commit()
    return queue


async def _seed_item(db: AsyncSession, queue: AnnotationQueueModel) -> AnnotationQueueItemModel:
    item = AnnotationQueueItemModel(
        queue_id=queue.id,
        target_type="trace",
        target_id=uuid.uuid4(),
        status="pending",
        added_by=uuid.uuid4(),
        workspace_id=queue.workspace_id,
    )
    db.add(item)
    await db.commit()
    return item


async def _seed_score(db: AsyncSession, workspace_id: uuid.UUID, target_id: uuid.UUID) -> EvaluationTaskScoreModel:
    row = EvaluationTaskScoreModel(
        task_id=None,
        target_id=target_id,
        metric_name="helpfulness",
        value=0.5,
        reasoning="Persisted evaluation evidence",
        source="human",
        status="completed",
        annotator_id=uuid.uuid4(),
        workspace_id=workspace_id,
        created_at=datetime.now(UTC),
    )
    db.add(row)
    await db.commit()
    return row


async def _database_snapshot(db: AsyncSession) -> list[list[dict[str, Any]]]:
    """Read every relevant column from storage, bypassing the identity map."""
    snapshots = []
    for model in (AnnotationQueueModel, AnnotationQueueItemModel, EvaluationTaskScoreModel):
        table = model.__table__
        rows = (await db.execute(select(table).order_by(table.c.id))).mappings().all()
        snapshots.append([dict(row) for row in rows])
    return snapshots


def _assert_no_workspace(resp: Any) -> None:
    assert resp.status_code == 403, resp.text
    assert resp.json() == {
        "detail": {"error": {"code": "NO_WORKSPACE", "message": "An active workspace is required", "details": None}}
    }


def _assert_forbidden(resp: Any) -> None:
    assert resp.status_code == 403, resp.text
    assert resp.json() == {
        "detail": {"error": {"code": "FORBIDDEN", "message": "Workspace membership required", "details": None}}
    }


def _assert_forbidden_viewer(resp: Any) -> None:
    assert resp.status_code == 403, resp.text
    assert resp.json() == {
        "detail": {"error": {"code": "FORBIDDEN", "message": "Editor role required", "details": None}}
    }


def _assert_queue_not_found(resp: Any) -> None:
    assert resp.status_code == 404, resp.text
    assert resp.json() == {
        "detail": {"error": {"code": "NOT_FOUND", "message": "Annotation queue not found", "details": None}}
    }


def _assert_item_not_found(resp: Any) -> None:
    assert resp.status_code == 404, resp.text
    assert resp.json() == {
        "detail": {"error": {"code": "NOT_FOUND", "message": "Annotation queue item not found", "details": None}}
    }


async def _unexpected_query(*args: Any, **kwargs: Any) -> None:
    raise AssertionError("Denied requests must not query queues")


async def _unexpected_write(*args: Any, **kwargs: Any) -> None:
    raise AssertionError("Denied requests must not run queue management writes")


@pytest.mark.parametrize("role", [WorkspaceRole.EDITOR, WorkspaceRole.ADMIN])
async def test_editor_and_admin_create_and_read_back(
    client: AsyncClient, db_session: AsyncSession, role: WorkspaceRole
) -> None:
    """Editor and above create a queue in their workspace; the read matches."""
    ws, _ = await _workspace_with_role(db_session, "ws-create", WorkspaceRole.VIEWER)
    assignee = str(uuid.uuid4())
    before = await _database_snapshot(db_session)
    async with _client_as(client, _member_as(ws, role)) as http:
        response = await http.post(_BASE, json=_create_payload(assigned_user_ids=[assignee]))
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["workspace_id"] == str(ws.id)
    assert body["assigned_user_ids"] == [assignee]
    assert body["metric_defs"][0]["name"] == "helpfulness"
    async with _client_as(client, _member_as(ws, WorkspaceRole.VIEWER)) as http:
        read_back = await http.get(f"{_BASE}/{body['id']}")
    assert read_back.status_code == 200, read_back.text
    assert read_back.json()["id"] == body["id"]
    assert read_back.json()["assigned_user_ids"] == [assignee]
    snapshot = await _database_snapshot(db_session)
    assert len(snapshot[0]) == len(before[0]) + 1


@pytest.mark.parametrize("identity", ["member", "system"])
async def test_create_without_workspace_denied_before_service(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    identity: str,
) -> None:
    """Workspace-less identities — system included — never reach the service."""
    from hecate.ops.evaluation.annotation.service import AnnotationService

    ctx = _system_context() if identity == "system" else _no_workspace_context()
    before = await _database_snapshot(db_session)
    with monkeypatch.context() as patch:
        patch.setattr(AnnotationService, "create_queue", _unexpected_write)
        patch.setattr(AsyncSession, "execute", _unexpected_query)
        async with _client_as(client, ctx) as http:
            response = await http.post(_BASE, json=_create_payload())
    _assert_no_workspace(response)
    assert await _database_snapshot(db_session) == before


@pytest.mark.parametrize("role", [WorkspaceRole.VIEWER, None])
async def test_create_below_editor_denied_before_validation(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    role: WorkspaceRole | None,
) -> None:
    """Viewers and role-less members are denied before service or validation."""
    from hecate.ops.evaluation.annotation.service import AnnotationService

    ws, _ = await _workspace_with_role(db_session, "ws-low", WorkspaceRole.VIEWER)
    before = await _database_snapshot(db_session)
    with monkeypatch.context() as patch:
        patch.setattr(AnnotationService, "create_queue", _unexpected_write)
        patch.setattr(AsyncSession, "execute", _unexpected_query)
        async with _client_as(client, _member_as(ws, role)) as http:
            denied = await http.post(_BASE, json=_create_payload())
            # Authorization precedes business metric validation: a payload that
            # passes the request schema but fails service validation still gets
            # the role denial, while schema-shape violations keep their 422.
            business_invalid = await http.post(
                _BASE, json=_create_payload(metric_defs=[{"name": "broken", "data_type": "numeric"}])
            )
            schema_invalid = await http.post(_BASE, json=_create_payload(metric_defs=[]))
    if role is None:
        _assert_forbidden(denied)
        _assert_forbidden(business_invalid)
    else:
        _assert_forbidden_viewer(denied)
        _assert_forbidden_viewer(business_invalid)
    assert schema_invalid.status_code == 422, schema_invalid.text
    assert await _database_snapshot(db_session) == before


@pytest.mark.parametrize("identity", ["viewer", "roleless", "editor", "admin", "system"])
async def test_update_delete_invisible_queue_resolves_before_role(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    identity: str,
) -> None:
    """Missing, soft-deleted, and foreign queues share one 404 before writes."""
    from hecate.ops.evaluation.annotation.service import AnnotationService

    ws, viewer = await _workspace_with_role(db_session, "ws-one", WorkspaceRole.VIEWER)
    other, _ = await _workspace_with_role(db_session, "ws-two", WorkspaceRole.VIEWER)
    deleted = await _seed_queue(db_session, ws.id, deleted=True)
    foreign = await _seed_queue(db_session, other.id)
    ctx = {
        "viewer": viewer,
        "roleless": _member_as(ws, None),
        "editor": _member_as(ws, WorkspaceRole.EDITOR),
        "admin": _member_as(ws, WorkspaceRole.ADMIN),
        "system": _system_context(),
    }[identity]
    before = await _database_snapshot(db_session)
    monkeypatch.setattr(AnnotationService, "update_queue", _unexpected_write)
    monkeypatch.setattr(AnnotationService, "delete_queue", _unexpected_write)
    queue_ids = [uuid.uuid4(), deleted.id]
    if identity != "system":
        queue_ids.append(foreign.id)
    async with _client_as(client, ctx) as http:
        for queue_id in queue_ids:
            update = await http.put(f"{_BASE}/{queue_id}", json={"name": "rewritten"})
            _assert_queue_not_found(update)
            assert str(queue_id) not in update.text
            delete = await http.delete(f"{_BASE}/{queue_id}")
            _assert_queue_not_found(delete)
            assert str(queue_id) not in delete.text
            assert await _database_snapshot(db_session) == before


async def test_member_without_workspace_denied_before_query_on_writes(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A workspace-less member gets the queue 404 before any database query."""
    ws, _ = await _workspace_with_role(db_session, "ws-seeded", WorkspaceRole.VIEWER)
    queue = await _seed_queue(db_session, ws.id)
    before = await _database_snapshot(db_session)
    with monkeypatch.context() as patch:
        patch.setattr(AsyncSession, "execute", _unexpected_query)
        async with _client_as(client, _no_workspace_context()) as http:
            update = await http.put(f"{_BASE}/{queue.id}", json={"name": "rewritten"})
            delete = await http.delete(f"{_BASE}/{queue.id}")
    _assert_queue_not_found(update)
    _assert_queue_not_found(delete)
    assert await _database_snapshot(db_session) == before


@pytest.mark.parametrize("role", [WorkspaceRole.VIEWER, None])
async def test_update_delete_below_editor_denied_without_writes(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    role: WorkspaceRole | None,
) -> None:
    """Viewers and role-less members of the owning workspace cannot write."""
    from hecate.ops.evaluation.annotation.service import AnnotationService

    ws, _ = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.VIEWER)
    queue = await _seed_queue(db_session, ws.id, assigned_user_ids=[str(uuid.uuid4())])
    before = await _database_snapshot(db_session)
    monkeypatch.setattr(AnnotationService, "update_queue", _unexpected_write)
    monkeypatch.setattr(AnnotationService, "delete_queue", _unexpected_write)
    async with _client_as(client, _member_as(ws, role)) as http:
        update = await http.put(f"{_BASE}/{queue.id}", json={"name": "rewritten"})
        invalid_update = await http.put(
            f"{_BASE}/{queue.id}", json={"metric_defs": [{"name": "broken", "data_type": "numeric"}]}
        )
        delete = await http.delete(f"{_BASE}/{queue.id}")
    if role is None:
        _assert_forbidden(update)
        _assert_forbidden(invalid_update)
        _assert_forbidden(delete)
    else:
        _assert_forbidden_viewer(update)
        _assert_forbidden_viewer(invalid_update)
        _assert_forbidden_viewer(delete)
    assert await _database_snapshot(db_session) == before


@pytest.mark.parametrize("active_workspace", ["missing", "other"])
async def test_system_updates_and_deletes_in_real_workspace(
    client: AsyncClient, db_session: AsyncSession, active_workspace: str
) -> None:
    """Explicit system scope operates on the live queue's real workspace."""
    ws, _ = await _workspace_with_role(db_session, "ws-target", WorkspaceRole.VIEWER)
    other, _ = await _workspace_with_role(db_session, "ws-other", WorkspaceRole.VIEWER)
    queue = await _seed_queue(db_session, ws.id)
    decoy = await _seed_queue(db_session, other.id)
    system = replace(_system_context(), workspace_id=other.id if active_workspace == "other" else None)
    async with _client_as(client, system) as http:
        updated = await http.put(f"{_BASE}/{queue.id}", json={"name": "system-renamed"})
        assert updated.status_code == 200, updated.text
        assert updated.json()["name"] == "system-renamed"
        assert updated.json()["workspace_id"] == str(ws.id)
        deleted = await http.delete(f"{_BASE}/{queue.id}")
        assert deleted.status_code == 204, deleted.text
    snapshot = await _database_snapshot(db_session)
    stored = {row["id"]: row for row in snapshot[0]}
    assert stored[queue.id]["name"] == "system-renamed"
    assert stored[queue.id]["deleted"] == 1
    assert stored[decoy.id]["deleted"] == 0


async def test_system_create_requires_active_workspace(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """System scope keeps its role exemption but still needs a workspace to create."""
    from hecate.ops.evaluation.annotation.service import AnnotationService

    ws, _ = await _workspace_with_role(db_session, "ws-sys", WorkspaceRole.VIEWER)
    before = await _database_snapshot(db_session)
    with monkeypatch.context() as patch:
        patch.setattr(AnnotationService, "create_queue", _unexpected_write)
        async with _client_as(client, _system_context()) as http:
            denied = await http.post(_BASE, json=_create_payload())
        _assert_no_workspace(denied)
    async with _client_as(client, replace(_system_context(), workspace_id=ws.id)) as http:
        created = await http.post(_BASE, json=_create_payload())
    assert created.status_code == 201, created.text
    assert created.json()["workspace_id"] == str(ws.id)
    assert len((await _database_snapshot(db_session))[0]) == len(before[0]) + 1


@pytest.mark.parametrize("role", [WorkspaceRole.EDITOR, WorkspaceRole.ADMIN])
async def test_partial_update_and_assigned_user_ids_round_trip(
    client: AsyncClient, db_session: AsyncSession, role: WorkspaceRole
) -> None:
    """Updates persist as strings like creation; replace, clear, and omit."""
    ws, _ = await _workspace_with_role(db_session, "ws-update", WorkspaceRole.VIEWER)
    queue = await _seed_queue(db_session, ws.id, assigned_user_ids=[str(uuid.uuid4())])
    seeded_assigned = list(queue.assigned_user_ids)
    editor = _member_as(ws, role)
    first, second = str(uuid.uuid4()), str(uuid.uuid4())
    async with _client_as(client, editor) as http:
        # Name-only partial update leaves the stored list untouched.
        renamed = await http.put(f"{_BASE}/{queue.id}", json={"name": "renamed"})
        assert renamed.status_code == 200, renamed.text
        assert renamed.json()["name"] == "renamed"
        assert renamed.json()["assigned_user_ids"] == seeded_assigned
        # Replacement round-trips through the UUID schema field as strings.
        replaced = await http.put(f"{_BASE}/{queue.id}", json={"assigned_user_ids": [first, second]})
        assert replaced.status_code == 200, replaced.text
        assert replaced.json()["assigned_user_ids"] == [first, second]
        snapshot = await _database_snapshot(db_session)
        row = next(r for r in snapshot[0] if r["id"] == queue.id)
        assert row["assigned_user_ids"] == [first, second]
        assert all(isinstance(value, str) for value in row["assigned_user_ids"])
        read_back = await http.get(f"{_BASE}/{queue.id}")
        assert read_back.status_code == 200, read_back.text
        assert read_back.json()["assigned_user_ids"] == [first, second]
        # An empty list clears.
        cleared = await http.put(f"{_BASE}/{queue.id}", json={"assigned_user_ids": []})
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["assigned_user_ids"] == []
        # An omitted field keeps the (now empty) value; an empty body is a no-op.
        untouched = await http.put(f"{_BASE}/{queue.id}", json={})
        assert untouched.status_code == 200, untouched.text
        assert untouched.json()["assigned_user_ids"] == []
        assert untouched.json()["name"] == "renamed"

    snapshot = await _database_snapshot(db_session)
    row = next(r for r in snapshot[0] if r["id"] == queue.id)
    assert row["assigned_user_ids"] == []


async def test_update_runs_metric_validation_after_authorization(client: AsyncClient, db_session: AsyncSession) -> None:
    """An editor reaching the service still gets its metric validation."""
    ws, _ = await _workspace_with_role(db_session, "ws-validate", WorkspaceRole.VIEWER)
    queue = await _seed_queue(db_session, ws.id)
    before = await _database_snapshot(db_session)
    async with _client_as(client, _member_as(ws, WorkspaceRole.EDITOR)) as http:
        invalid = await http.put(
            f"{_BASE}/{queue.id}",
            # Schema-valid but business-invalid: numeric without min/max.
            json={
                "name": "must-roll-back",
                "assigned_user_ids": [str(uuid.uuid4())],
                "metric_defs": [{"name": "broken", "data_type": "numeric"}],
            },
        )
    assert invalid.status_code == 400, invalid.text
    assert invalid.json()["detail"]["error"]["code"] == "INVALID_REQUEST"
    assert await _database_snapshot(db_session) == before


@pytest.mark.parametrize("role", [WorkspaceRole.EDITOR, WorkspaceRole.ADMIN])
async def test_delete_soft_deletes_and_retains_children(
    client: AsyncClient, db_session: AsyncSession, role: WorkspaceRole
) -> None:
    """Soft delete keeps items and score rows while reads stop exposing them."""
    ws, _ = await _workspace_with_role(db_session, "ws-delete", WorkspaceRole.VIEWER)
    queue = await _seed_queue(db_session, ws.id)
    item = await _seed_item(db_session, queue)
    await _seed_score(db_session, ws.id, item.target_id)
    viewer = _member_as(ws, WorkspaceRole.VIEWER)
    before = await _database_snapshot(db_session)
    async with _client_as(client, _member_as(ws, role)) as http:
        deleted = await http.delete(f"{_BASE}/{queue.id}")
    assert deleted.status_code == 204, deleted.text
    snapshot = await _database_snapshot(db_session)
    queue_rows = {row["id"]: row for row in snapshot[0]}
    assert queue_rows[queue.id]["deleted"] == 1
    assert queue_rows[queue.id]["deleted_at"] is not None
    assert snapshot[1:] == before[1:]
    async with _client_as(client, viewer) as http:
        queue_read = await http.get(f"{_BASE}/{queue.id}")
        items_read = await http.get(f"{_BASE}/{queue.id}/items")
        item_read = await http.get(f"{_BASE}/{queue.id}/items/{item.id}")
        listing = await http.get(_BASE)
    _assert_queue_not_found(queue_read)
    _assert_queue_not_found(items_read)
    _assert_item_not_found(item_read)
    assert [row["id"] for row in listing.json()["items"]] == []


async def test_zero_uuid_workspace_is_a_legal_scope_for_writes(client: AsyncClient, db_session: AsyncSession) -> None:
    """The zero-UUID workspace stays a valid explicit scope for management."""
    zero_ws = uuid.UUID(int=0)
    editor = AuthContext(
        user_id=uuid.uuid4(),
        org_id=None,
        workspace_id=zero_ws,
        role=WorkspaceRole.EDITOR,
        auth_method="jwt",
        api_key_scope=None,
    )
    async with _client_as(client, editor) as http:
        created = await http.post(_BASE, json=_create_payload())
        assert created.status_code == 201, created.text
        assert created.json()["workspace_id"] == str(zero_ws)
        updated = await http.put(f"{_BASE}/{created.json()['id']}", json={"name": "zero-renamed"})
        assert updated.status_code == 200, updated.text
        assert updated.json()["name"] == "zero-renamed"
        removed = await http.delete(f"{_BASE}/{created.json()['id']}")
        assert removed.status_code == 204, removed.text


@pytest.mark.parametrize(
    "payload",
    [
        {"workspace_id": "00000000-0000-0000-0000-000000000001"},
        {"unknown_field": "value"},
    ],
)
async def test_request_body_cannot_change_ownership_or_add_fields(
    client: AsyncClient, db_session: AsyncSession, payload: dict[str, Any]
) -> None:
    """The schema stays forbid-extra; ownership never comes from the body."""
    ws, _ = await _workspace_with_role(db_session, "ws-schema", WorkspaceRole.VIEWER)
    queue = await _seed_queue(db_session, ws.id)
    before = await _database_snapshot(db_session)
    async with _client_as(client, _member_as(ws, WorkspaceRole.EDITOR)) as http:
        create = await http.post(_BASE, json=_create_payload(**payload))
        assert create.status_code == 422, create.text
    async with _client_as(client, _member_as(ws, WorkspaceRole.EDITOR)) as http:
        update = await http.put(f"{_BASE}/{queue.id}", json=payload)
        assert update.status_code == 422, update.text
    assert await _database_snapshot(db_session) == before
