"""Annotation queue read access boundary (step10).

Covers the four annotation read endpoints:

- Queue list: an active workspace and a viewer role floor are required
  before any query; system scope has no global listing, and a missing
  workspace — system included — is 403 ``NO_WORKSPACE``.
- By-id reads: existence, soft-delete state, workspace ownership, and
  queue/item binding resolve before the role check; invisible resources
  surface as the same identifier-free 404, and explicit system scope
  reads a visible resource through its real workspace.
- Item review context: trace reads apply the traces API tenant scope
  (session or agent belongs to the workspace); an invisible trace keeps
  the missing-context null semantics and never reads events. Score
  suggestions are constrained to the queue's workspace with the
  latest-automated-score-per-metric rule intact.
- Semantics: pagination, status filters, ordering, response shapes,
  zero-UUID workspaces, and legitimate empty results survive the
  boundary; successful and denied reads are read-only, verified against
  table-direct full-column snapshots.
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
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.auth_context import AuthContext
from hecate.models.agent import AgentModel
from hecate.models.evaluation import (
    AnnotationQueueItemModel,
    AnnotationQueueModel,
    EvaluationTaskScoreModel,
)
from hecate.models.organization import OrganizationModel
from hecate.models.session import SessionModel
from hecate.models.trace import TraceModel
from hecate.models.workspace import WorkspaceModel
from hecate.models.workspace_member import WorkspaceMemberModel, WorkspaceRole

_BASE = "/api/evaluation/annotation-queues"


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


async def _seed_queue(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    deleted: bool = False,
    created_at: datetime | None = None,
) -> AnnotationQueueModel:
    queue = AnnotationQueueModel(
        name=f"boundary-queue-{uuid.uuid4().hex[:8]}",
        instructions="Rate the output",
        metric_defs=[{"name": "helpfulness", "data_type": "numeric", "min": 0, "max": 1}],
        workspace_id=workspace_id,
        deleted=deleted,
        deleted_at=datetime.now(UTC) if deleted else None,
    )
    if created_at is not None:
        queue.created_at = created_at
    db.add(queue)
    await db.commit()
    return queue


async def _seed_item(
    db: AsyncSession,
    queue: AnnotationQueueModel,
    *,
    target_id: uuid.UUID | None = None,
    status: str = "pending",
    deleted: bool = False,
    created_at: datetime | None = None,
) -> AnnotationQueueItemModel:
    item = AnnotationQueueItemModel(
        queue_id=queue.id,
        target_type="trace",
        target_id=target_id or uuid.uuid4(),
        status=status,
        added_by=uuid.uuid4(),
        workspace_id=queue.workspace_id,
        deleted=deleted,
        deleted_at=datetime.now(UTC) if deleted else None,
    )
    if created_at is not None:
        item.created_at = created_at
    db.add(item)
    await db.commit()
    return item


async def _seed_trace(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    deleted: bool = False,
) -> TraceModel:
    """A completed root trace whose owning session belongs to ``workspace_id``."""
    session_row = SessionModel(conversation_id=uuid.uuid4(), agent_id=uuid.uuid4(), workspace_id=workspace_id)
    db.add(session_row)
    await db.flush()
    trace = TraceModel(
        trace_id=uuid.uuid4(),
        type="trace",
        name=f"session:{session_row.id}",
        session_id=session_row.id,
        status="completed",
        start_time=datetime.now(UTC),
        end_time=datetime.now(UTC) + timedelta(seconds=5),
        deleted=deleted,
        deleted_at=datetime.now(UTC) if deleted else None,
    )
    db.add(trace)
    await db.commit()
    return trace


async def _seed_score(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    target_id: uuid.UUID,
    *,
    metric_name: str = "helpfulness",
    value: float = 0.5,
    source: str = "llm_judge",
    deleted: bool = False,
    created_at: datetime | None = None,
) -> EvaluationTaskScoreModel:
    row = EvaluationTaskScoreModel(
        task_id=None if source == "human" else uuid.uuid4(),
        target_id=target_id,
        metric_name=metric_name,
        value=value,
        reasoning="Persisted evaluation evidence",
        source=source,
        status="completed",
        annotator_id=uuid.uuid4() if source == "human" else None,
        workspace_id=workspace_id,
        deleted=deleted,
        deleted_at=datetime.now(UTC) if deleted else None,
        created_at=created_at or datetime.now(UTC),
    )
    db.add(row)
    await db.commit()
    return row


async def _database_snapshot(db: AsyncSession) -> list[list[dict[str, Any]]]:
    """Read every relevant column from storage, bypassing the identity map."""
    snapshots = []
    for model in (
        AnnotationQueueModel,
        AnnotationQueueItemModel,
        EvaluationTaskScoreModel,
        TraceModel,
        SessionModel,
        AgentModel,
    ):
        table = model.__table__
        rows = (await db.execute(select(table).order_by(table.c.id))).mappings().all()
        snapshots.append([dict(row) for row in rows])
    return snapshots


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


def _assert_forbidden(resp: Any) -> None:
    assert resp.status_code == 403, resp.text
    assert resp.json() == {
        "detail": {"error": {"code": "FORBIDDEN", "message": "Workspace membership required", "details": None}}
    }


def _assert_no_workspace(resp: Any) -> None:
    assert resp.status_code == 403, resp.text
    assert resp.json() == {
        "detail": {"error": {"code": "NO_WORKSPACE", "message": "An active workspace is required", "details": None}}
    }


async def _unexpected_queue_query(*args: Any, **kwargs: Any) -> None:
    raise AssertionError("Denied requests must not query queues")


async def _unexpected_context_read(*args: Any, **kwargs: Any) -> None:
    raise AssertionError("Denied requests must not read review context")


async def _unexpected_event_read(*args: Any, **kwargs: Any) -> None:
    raise AssertionError("An invisible trace must not trigger event reads")


@pytest.mark.parametrize("role", [WorkspaceRole.VIEWER, WorkspaceRole.EDITOR, WorkspaceRole.ADMIN])
async def test_member_list_scopes_queues_counts_and_order(
    client: AsyncClient, db_session: AsyncSession, role: WorkspaceRole
) -> None:
    """The list shows only the caller's live queues; counts exclude deleted rows."""
    ws, _ = await _workspace_with_role(db_session, "ws-one", WorkspaceRole.VIEWER)
    other, _ = await _workspace_with_role(db_session, "ws-two", WorkspaceRole.VIEWER)
    base = datetime(2025, 3, 1, tzinfo=UTC)
    older = await _seed_queue(db_session, ws.id, created_at=base)
    newer = await _seed_queue(db_session, ws.id, created_at=base + timedelta(days=1))
    await _seed_queue(db_session, other.id)
    await _seed_queue(db_session, ws.id, deleted=True)
    await _seed_item(db_session, older, status="pending")
    await _seed_item(db_session, older, status="completed", created_at=base)
    await _seed_item(db_session, older, deleted=True)
    # An anomalous cross-workspace item row under a visible queue id.
    stray = AnnotationQueueItemModel(
        queue_id=older.id,
        target_type="trace",
        target_id=uuid.uuid4(),
        status="pending",
        workspace_id=other.id,
    )
    db_session.add(stray)
    await db_session.commit()
    before = await _database_snapshot(db_session)
    async with _client_as(client, _member_as(ws, role)) as http:
        response = await http.get(_BASE)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 2
    assert [row["id"] for row in body["items"]] == [str(newer.id), str(older.id)]
    counts = {row["id"]: row["counts"] for row in body["items"]}
    assert counts[str(older.id)] == {"pending": 1, "completed": 1}
    assert counts[str(newer.id)] == {}
    assert all(row["workspace_id"] == str(ws.id) for row in body["items"])
    assert await _database_snapshot(db_session) == before


async def test_system_with_workspace_lists_only_that_workspace(client: AsyncClient, db_session: AsyncSession) -> None:
    """Explicit system scope has no global listing; the active workspace wins."""
    ws, _ = await _workspace_with_role(db_session, "ws-target", WorkspaceRole.VIEWER)
    other, _ = await _workspace_with_role(db_session, "ws-other", WorkspaceRole.VIEWER)
    own = await _seed_queue(db_session, ws.id)
    await _seed_queue(db_session, other.id)
    before = await _database_snapshot(db_session)
    async with _client_as(client, replace(_system_context(), workspace_id=ws.id)) as http:
        response = await http.get(_BASE)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 1
    assert [row["id"] for row in body["items"]] == [str(own.id)]
    assert await _database_snapshot(db_session) == before


@pytest.mark.parametrize("identity", ["member", "system"])
@pytest.mark.parametrize(
    ("path", "not_found"),
    [
        (f"{_BASE}/{{queue_id}}", "queue"),
        (f"{_BASE}/{{queue_id}}/items", "queue"),
        (f"{_BASE}/{{queue_id}}/items/{{item_id}}", "item"),
    ],
)
async def test_missing_workspace_denied_before_any_query(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    identity: str,
    path: str,
    not_found: str,
) -> None:
    """Resource-shaped 404s and the list 403 fire before any database query."""
    ws, _ = await _workspace_with_role(db_session, "ws-seeded", WorkspaceRole.VIEWER)
    queue = await _seed_queue(db_session, ws.id)
    ctx = _system_context() if identity == "system" else _no_workspace_context()
    before = await _database_snapshot(db_session)
    with monkeypatch.context() as patch:
        patch.setattr(AsyncSession, "execute", _unexpected_queue_query)
        async with _client_as(client, ctx) as http:
            list_response = await http.get(_BASE)
            _assert_no_workspace(list_response)
            if identity == "member":
                # System scope may still read explicit ids; members cannot.
                by_id = await http.get(path.format(queue_id=queue.id, item_id=uuid.uuid4()))
                if not_found == "queue":
                    _assert_queue_not_found(by_id)
                else:
                    _assert_item_not_found(by_id)
    assert await _database_snapshot(db_session) == before


async def test_roleless_member_denied_on_all_four_reads(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Membership without a role is 403 before service reads or context reads."""
    from hecate.ops.evaluation.annotation.service import AnnotationService

    ws, _ = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.VIEWER)
    queue = await _seed_queue(db_session, ws.id)
    item = await _seed_item(db_session, queue)
    before = await _database_snapshot(db_session)
    monkeypatch.setattr(AnnotationService, "list_queues", _unexpected_queue_query)
    monkeypatch.setattr(AnnotationService, "list_items", _unexpected_queue_query)
    monkeypatch.setattr(AnnotationService, "get_item_detail", _unexpected_context_read)
    async with _client_as(client, _member_as(ws, None)) as http:
        _assert_forbidden(await http.get(_BASE))
        _assert_forbidden(await http.get(f"{_BASE}/{queue.id}"))
        _assert_forbidden(await http.get(f"{_BASE}/{queue.id}/items"))
        _assert_forbidden(await http.get(f"{_BASE}/{queue.id}/items/{item.id}"))
    assert await _database_snapshot(db_session) == before


@pytest.mark.parametrize("role", [WorkspaceRole.VIEWER, WorkspaceRole.EDITOR, WorkspaceRole.ADMIN])
async def test_authorized_by_id_reads_return_visible_resources(
    client: AsyncClient, db_session: AsyncSession, role: WorkspaceRole
) -> None:
    """Viewer and above read a visible queue, its items, and an item detail."""
    ws, _ = await _workspace_with_role(db_session, "ws-viewer", WorkspaceRole.VIEWER)
    queue = await _seed_queue(db_session, ws.id)
    trace = await _seed_trace(db_session, ws.id)
    item = await _seed_item(db_session, queue, target_id=trace.id)
    before = await _database_snapshot(db_session)
    async with _client_as(client, _member_as(ws, role)) as http:
        queue_response = await http.get(f"{_BASE}/{queue.id}")
        assert queue_response.status_code == 200
        assert queue_response.json()["id"] == str(queue.id)
        items_response = await http.get(f"{_BASE}/{queue.id}/items")
        assert items_response.status_code == 200
        assert items_response.json()["total"] == 1
        detail_response = await http.get(f"{_BASE}/{queue.id}/items/{item.id}")
        assert detail_response.status_code == 200, detail_response.text
        detail = detail_response.json()
        assert detail["item"]["id"] == str(item.id)
        assert detail["queue"]["id"] == str(queue.id)
        assert detail["trace"]["id"] == str(trace.id)
        assert set(detail) == {"item", "queue", "trace", "projection", "suggestions"}
    assert await _database_snapshot(db_session) == before


@pytest.mark.parametrize("identity", ["viewer", "roleless", "system"])
async def test_invisible_queue_resolved_before_role(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, identity: str
) -> None:
    """Missing, deleted, and foreign queues share one full 404 before any read."""
    from hecate.ops.evaluation.annotation.service import AnnotationService

    ws, viewer = await _workspace_with_role(db_session, "ws-one", WorkspaceRole.VIEWER)
    other, _ = await _workspace_with_role(db_session, "ws-two", WorkspaceRole.VIEWER)
    deleted = await _seed_queue(db_session, ws.id, deleted=True)
    foreign = await _seed_queue(db_session, other.id)
    before = await _database_snapshot(db_session)
    ctx = {"viewer": viewer, "roleless": _member_as(ws, None), "system": _system_context()}[identity]
    queue_ids = [uuid.uuid4(), deleted.id]
    if identity != "system":
        queue_ids.append(foreign.id)
    monkeypatch.setattr(AnnotationService, "list_queues", _unexpected_queue_query)
    monkeypatch.setattr(AnnotationService, "list_items", _unexpected_queue_query)
    async with _client_as(client, ctx) as http:
        for queue_id in queue_ids:
            detail = await http.get(f"{_BASE}/{queue_id}")
            _assert_queue_not_found(detail)
            assert str(queue_id) not in detail.text
            listing = await http.get(f"{_BASE}/{queue_id}/items")
            _assert_queue_not_found(listing)
            assert str(queue_id) not in listing.text
            assert await _database_snapshot(db_session) == before
        if identity == "system":
            # The contrast: explicit system scope reads the foreign queue by id.
            readable = await http.get(f"{_BASE}/{foreign.id}")
            assert readable.status_code == 200, readable.text
            assert readable.json()["workspace_id"] == str(other.id)


@pytest.mark.parametrize("identity", ["viewer", "roleless", "system"])
async def test_item_binding_and_visibility_resolved_before_context(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, identity: str
) -> None:
    """Wrong queue paths, missing/deleted/foreign items 404 before any context read."""
    from hecate.ops.evaluation.annotation.service import AnnotationService

    ws, viewer = await _workspace_with_role(db_session, "ws-one", WorkspaceRole.VIEWER)
    other, _ = await _workspace_with_role(db_session, "ws-two", WorkspaceRole.VIEWER)
    queue_a = await _seed_queue(db_session, ws.id)
    queue_b = await _seed_queue(db_session, ws.id)
    deleted_queue = await _seed_queue(db_session, ws.id, deleted=True)
    foreign_queue = await _seed_queue(db_session, other.id)
    item = await _seed_item(db_session, queue_b)
    deleted_item = await _seed_item(db_session, queue_a, deleted=True)
    foreign_item = await _seed_item(db_session, foreign_queue)
    stranded = await _seed_item(db_session, deleted_queue)
    before = await _database_snapshot(db_session)
    ctx = {"viewer": viewer, "roleless": _member_as(ws, None), "system": _system_context()}[identity]
    monkeypatch.setattr(AnnotationService, "get_item_detail", _unexpected_context_read)
    cases = [
        (queue_a.id, uuid.uuid4()),  # missing item
        (queue_a.id, deleted_item.id),  # soft-deleted item
        (queue_a.id, item.id),  # live item bound to a different queue
        (queue_a.id, stranded.id),  # item whose queue is soft-deleted
    ]
    if identity != "system":
        cases.append((queue_a.id, foreign_item.id))
    async with _client_as(client, ctx) as http:
        for queue_id, item_id in cases:
            response = await http.get(f"{_BASE}/{queue_id}/items/{item_id}")
            _assert_item_not_found(response)
            assert str(item_id) not in response.text
            assert await _database_snapshot(db_session) == before


async def test_system_reads_queue_and_items_in_real_workspace(client: AsyncClient, db_session: AsyncSession) -> None:
    """Explicit system scope reads through the resource's real workspace."""
    ws, _ = await _workspace_with_role(db_session, "ws-target", WorkspaceRole.VIEWER)
    other, _ = await _workspace_with_role(db_session, "ws-other", WorkspaceRole.VIEWER)
    queue = await _seed_queue(db_session, ws.id)
    trace = await _seed_trace(db_session, ws.id)
    item = await _seed_item(db_session, queue, target_id=trace.id)
    suggestion = await _seed_score(db_session, ws.id, trace.id, value=0.4)
    await _seed_score(db_session, other.id, trace.id, value=0.9)
    for active_workspace in (False, True):
        other_ctx = replace(_system_context(), workspace_id=other.id)
        ctx = other_ctx if active_workspace else _system_context()
        before = await _database_snapshot(db_session)
        async with _client_as(client, ctx) as http:
            queue_response = await http.get(f"{_BASE}/{queue.id}")
            assert queue_response.status_code == 200
            assert queue_response.json()["workspace_id"] == str(ws.id)
            items_response = await http.get(f"{_BASE}/{queue.id}/items")
            assert items_response.status_code == 200
            assert [row["id"] for row in items_response.json()["items"]] == [str(item.id)]
            detail_response = await http.get(f"{_BASE}/{queue.id}/items/{item.id}")
            assert detail_response.status_code == 200, detail_response.text
            detail = detail_response.json()
            assert detail["trace"]["id"] == str(trace.id)
            assert [row["score_id"] for row in detail["suggestions"]] == [str(suggestion.id)]
            assert await _database_snapshot(db_session) == before


async def test_suggestions_constrained_to_queue_workspace(client: AsyncClient, db_session: AsyncSession) -> None:
    """Only the queue's workspace live automated rows suggest, latest per metric."""
    ws, viewer = await _workspace_with_role(db_session, "ws-one", WorkspaceRole.VIEWER)
    other, _ = await _workspace_with_role(db_session, "ws-two", WorkspaceRole.VIEWER)
    queue = await _seed_queue(db_session, ws.id)
    trace = await _seed_trace(db_session, ws.id)
    item = await _seed_item(db_session, queue, target_id=trace.id)
    base = datetime(2025, 5, 1, tzinfo=UTC)
    await _seed_score(db_session, ws.id, trace.id, value=0.35, created_at=base)
    latest = await _seed_score(db_session, ws.id, trace.id, value=0.5, created_at=base + timedelta(days=2))
    factuality = await _seed_score(
        db_session,
        ws.id,
        trace.id,
        metric_name="factuality",
        value=0.8,
        created_at=base + timedelta(days=1),
    )
    await _seed_score(db_session, ws.id, trace.id, value=0.9, deleted=True, created_at=base + timedelta(days=3))
    await _seed_score(db_session, ws.id, trace.id, source="human", value=0.7)
    await _seed_score(db_session, other.id, trace.id, value=0.99, created_at=base + timedelta(days=4))
    before = await _database_snapshot(db_session)
    async with _client_as(client, viewer) as http:
        response = await http.get(f"{_BASE}/{queue.id}/items/{item.id}")
    assert response.status_code == 200, response.text
    suggestions = response.json()["suggestions"]
    assert [(row["metric_name"], row["value"]) for row in suggestions] == [
        ("factuality", 0.8),
        ("helpfulness", 0.5),
    ]
    by_metric = {row["metric_name"]: row for row in suggestions}
    assert by_metric["helpfulness"]["score_id"] == str(latest.id)
    assert by_metric["factuality"]["score_id"] == str(factuality.id)
    assert by_metric["helpfulness"]["source"] == "llm_judge"
    assert await _database_snapshot(db_session) == before


@pytest.mark.parametrize("invisible", ["foreign-workspace", "deleted", "deleted-session"])
async def test_invisible_trace_keeps_missing_context_and_skips_events(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    invisible: str,
) -> None:
    """A trace outside the tenant scope never surfaces content or event reads."""
    from hecate.ops.evaluation.annotation import service as annotation_service

    ws, viewer = await _workspace_with_role(db_session, "ws-one", WorkspaceRole.VIEWER)
    other, _ = await _workspace_with_role(db_session, "ws-two", WorkspaceRole.VIEWER)
    queue = await _seed_queue(db_session, ws.id)
    if invisible == "foreign-workspace":
        trace = await _seed_trace(db_session, other.id)
    else:
        trace = await _seed_trace(db_session, ws.id, deleted=invisible == "deleted")
        if invisible == "deleted-session":
            session = await db_session.get(SessionModel, trace.session_id)
            assert session is not None
            session.deleted = True
            session.deleted_at = datetime.now(UTC)
            await db_session.commit()
    item = await _seed_item(db_session, queue, target_id=trace.id)
    suggestion = await _seed_score(db_session, ws.id, trace.id, value=0.4)
    before = await _database_snapshot(db_session)
    monkeypatch.setattr(annotation_service, "build_eval_input", _unexpected_event_read)
    async with _client_as(client, viewer) as http:
        response = await http.get(f"{_BASE}/{queue.id}/items/{item.id}")
    assert response.status_code == 200, response.text
    detail = response.json()
    assert detail["trace"] is None
    assert detail["projection"] is None
    assert [row["score_id"] for row in detail["suggestions"]] == [str(suggestion.id)]
    assert await _database_snapshot(db_session) == before


async def test_missing_trace_keeps_missing_context(client: AsyncClient, db_session: AsyncSession) -> None:
    """A dangling target keeps the null semantics without failing the read."""
    ws, viewer = await _workspace_with_role(db_session, "ws-one", WorkspaceRole.VIEWER)
    queue = await _seed_queue(db_session, ws.id)
    item = await _seed_item(db_session, queue, target_id=uuid.uuid4())
    before = await _database_snapshot(db_session)
    async with _client_as(client, viewer) as http:
        response = await http.get(f"{_BASE}/{queue.id}/items/{item.id}")
    assert response.status_code == 200, response.text
    detail = response.json()
    assert detail["trace"] is None
    assert detail["projection"] is None
    assert detail["suggestions"] == []
    assert await _database_snapshot(db_session) == before


async def test_items_pagination_status_filter_and_empty_results(client: AsyncClient, db_session: AsyncSession) -> None:
    """Pagination, the status filter, and legitimate empties keep their shapes."""
    ws, viewer = await _workspace_with_role(db_session, "ws-viewer", WorkspaceRole.VIEWER)
    queue = await _seed_queue(db_session, ws.id)
    base = datetime(2025, 6, 1, tzinfo=UTC)
    first = await _seed_item(db_session, queue, status="pending", created_at=base)
    second = await _seed_item(db_session, queue, status="pending", created_at=base + timedelta(seconds=1))
    third = await _seed_item(db_session, queue, status="completed", created_at=base + timedelta(seconds=2))
    await _seed_item(db_session, queue, status="skipped", deleted=True, created_at=base + timedelta(seconds=3))
    before = await _database_snapshot(db_session)
    probes = [
        ({}, [first, second, third], 3),
        ({"status": "pending"}, [first, second], 2),
        ({"status": "completed"}, [third], 1),
        ({"status": "skipped"}, [], 0),
        ({"page_size": 2}, [first, second], 3),
        ({"page_size": 2, "page": 2}, [third], 3),
        ({"page_size": 2, "page": 3}, [], 3),
    ]
    async with _client_as(client, viewer) as http:
        for params, expected, total in probes:
            response = await http.get(f"{_BASE}/{queue.id}/items", params=params)
            assert response.status_code == 200, response.text
            assert response.json()["total"] == total
            assert [row["id"] for row in response.json()["items"]] == [str(row.id) for row in expected]
    assert await _database_snapshot(db_session) == before


async def test_zero_uuid_workspace_is_a_legal_scope(client: AsyncClient, db_session: AsyncSession) -> None:
    """The zero-UUID workspace stays a valid explicit scope for the reads."""
    zero_ws = uuid.UUID(int=0)
    queue = await _seed_queue(db_session, zero_ws)
    item = await _seed_item(db_session, queue)
    member = AuthContext(
        user_id=uuid.uuid4(),
        org_id=None,
        workspace_id=zero_ws,
        role=WorkspaceRole.VIEWER,
        auth_method="jwt",
        api_key_scope=None,
    )
    before = await _database_snapshot(db_session)
    async with _client_as(client, member) as http:
        listing = await http.get(_BASE)
        assert listing.status_code == 200
        assert [row["id"] for row in listing.json()["items"]] == [str(queue.id)]
        detail = await http.get(f"{_BASE}/{queue.id}")
        assert detail.status_code == 200
        items = await http.get(f"{_BASE}/{queue.id}/items")
        assert items.status_code == 200, items.text
        assert [row["id"] for row in items.json()["items"]] == [str(item.id)]
        item_detail = await http.get(f"{_BASE}/{queue.id}/items/{item.id}")
        assert item_detail.status_code == 200, item_detail.text
        assert item_detail.json()["item"]["workspace_id"] == str(zero_ws)
    assert await _database_snapshot(db_session) == before


@pytest.mark.parametrize(
    "params",
    [
        {"page": 0},
        {"page_size": 101},
        {"status": "invalid"},
    ],
)
async def test_parameter_validation_stays_422(client: AsyncClient, params: dict[str, Any]) -> None:
    """Authorization does not change the endpoints' existing validation errors."""
    response = await client.get(f"{_BASE}/{uuid.uuid4()}/items", params=params)
    assert response.status_code == 422, response.text


@pytest.mark.parametrize("identity", ["viewer", "roleless", "system"])
async def test_item_and_parent_workspace_must_match_before_context(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    identity: str,
) -> None:
    """A mismatched parent workspace is the same item 404 before role or context."""
    from hecate.ops.evaluation.annotation.service import AnnotationService

    ws, viewer = await _workspace_with_role(db_session, "item-owner", WorkspaceRole.VIEWER)
    foreign, _ = await _workspace_with_role(db_session, "queue-owner", WorkspaceRole.VIEWER)
    queue = await _seed_queue(db_session, foreign.id)
    item = await _seed_item(db_session, queue)
    item.workspace_id = ws.id
    await db_session.commit()
    ctx = {"viewer": viewer, "roleless": _member_as(ws, None), "system": _system_context()}[identity]
    before = await _database_snapshot(db_session)
    monkeypatch.setattr(AnnotationService, "get_item_detail", _unexpected_context_read)

    async with _client_as(client, ctx) as http:
        response = await http.get(f"{_BASE}/{queue.id}/items/{item.id}")
    _assert_item_not_found(response)
    assert await _database_snapshot(db_session) == before


@pytest.mark.parametrize("identity", ["viewer", "system"])
async def test_item_service_lookup_failure_keeps_identifier_free_404(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    identity: str,
) -> None:
    """An item disappearing after the precheck must not turn into a server error."""
    from hecate.ops.evaluation.annotation.service import AnnotationQueueItemNotFoundError, AnnotationService

    ws, viewer = await _workspace_with_role(db_session, "lookup-owner", WorkspaceRole.VIEWER)
    queue = await _seed_queue(db_session, ws.id)
    item = await _seed_item(db_session, queue)
    ctx = viewer if identity == "viewer" else _system_context()
    before = await _database_snapshot(db_session)

    async def disappeared(*args: Any, **kwargs: Any) -> None:
        raise AnnotationQueueItemNotFoundError(str(item.id))

    monkeypatch.setattr(AnnotationService, "get_item_detail", disappeared)
    async with _client_as(client, ctx) as http:
        response = await http.get(f"{_BASE}/{queue.id}/items/{item.id}")
    _assert_item_not_found(response)
    assert await _database_snapshot(db_session) == before


async def test_detail_service_rechecks_path_binding_before_context(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The service rejects a changed queue binding before loading review context."""
    from hecate.ops.evaluation.annotation.service import AnnotationQueueItemNotFoundError, AnnotationService

    ws, _ = await _workspace_with_role(db_session, "binding-owner", WorkspaceRole.VIEWER)
    requested = await _seed_queue(db_session, ws.id)
    actual = await _seed_queue(db_session, ws.id)
    item = await _seed_item(db_session, actual)
    before = await _database_snapshot(db_session)
    monkeypatch.setattr(AnnotationService, "_visible_trace", _unexpected_context_read)
    monkeypatch.setattr(AnnotationService, "_latest_automated_scores", _unexpected_context_read)

    with pytest.raises(AnnotationQueueItemNotFoundError):
        await AnnotationService(db_session).get_item_detail(item.id, workspace_id=ws.id, queue_id=requested.id)
    assert await _database_snapshot(db_session) == before


@pytest.mark.parametrize("identity", ["viewer", "system"])
async def test_rebound_item_is_rejected_after_api_precheck_without_context_reads(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    identity: str,
) -> None:
    """A changed binding cannot reuse the ORM object cached by the API precheck."""
    from hecate.ops.api import evaluation_annotations as api
    from hecate.ops.evaluation.annotation.service import AnnotationService

    ws, viewer = await _workspace_with_role(db_session, "rebound-owner", WorkspaceRole.VIEWER)
    requested = await _seed_queue(db_session, ws.id)
    alternate = await _seed_queue(db_session, ws.id)
    item = await _seed_item(db_session, requested)
    before = await _database_snapshot(db_session)
    original = api._get_item_for_read

    async def rebind_after_precheck(ctx: AuthContext, queue_id: uuid.UUID, item_id: uuid.UUID, db: AsyncSession):
        resolved = await original(ctx, queue_id, item_id, db)
        await db.execute(
            update(AnnotationQueueItemModel)
            .where(AnnotationQueueItemModel.id == item_id)
            .values(queue_id=alternate.id)
            .execution_options(synchronize_session=False)
        )
        return resolved

    monkeypatch.setattr(api, "_get_item_for_read", rebind_after_precheck)
    monkeypatch.setattr(AnnotationService, "_visible_trace", _unexpected_context_read)
    monkeypatch.setattr(AnnotationService, "_latest_automated_scores", _unexpected_context_read)
    ctx = viewer if identity == "viewer" else _system_context()
    async with _client_as(client, ctx) as http:
        response = await http.get(f"{_BASE}/{requested.id}/items/{item.id}")
    _assert_item_not_found(response)
    # The simulated change is rolled back by the failed request's transaction.
    assert await _database_snapshot(db_session) == before


@pytest.mark.parametrize("visibility", ["owned-agent", "foreign-agent", "deleted-agent", "orphan"])
async def test_agent_owned_and_orphan_traces_respect_workspace_scope(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    visibility: str,
) -> None:
    """Agent ownership can authorize a sessionless trace; foreign and orphan targets cannot."""
    from hecate.ops.evaluation.annotation import service as annotation_service

    ws, viewer = await _workspace_with_role(db_session, "trace-reader", WorkspaceRole.VIEWER)
    foreign, _ = await _workspace_with_role(db_session, "foreign-agent", WorkspaceRole.VIEWER)
    queue = await _seed_queue(db_session, ws.id)
    agent = None
    if visibility != "orphan":
        agent = AgentModel(
            name="trace-owner",
            workspace_id=foreign.id if visibility == "foreign-agent" else ws.id,
            deleted=visibility == "deleted-agent",
        )
        db_session.add(agent)
        await db_session.flush()
    now = datetime.now(UTC)
    trace = TraceModel(
        trace_id=uuid.uuid4(),
        type="trace",
        name="agent-owned-trace",
        session_id=None,
        agent_id=agent.id if agent is not None else None,
        status="completed",
        start_time=now,
        end_time=now + timedelta(seconds=5),
    )
    db_session.add(trace)
    await db_session.commit()
    item = await _seed_item(db_session, queue, target_id=trace.id)
    before = await _database_snapshot(db_session)
    monkeypatch.setattr(annotation_service, "build_eval_input", _unexpected_event_read)
    async with _client_as(client, viewer) as http:
        response = await http.get(f"{_BASE}/{queue.id}/items/{item.id}")
    assert response.status_code == 200, response.text
    detail = response.json()
    if visibility == "owned-agent":
        assert detail["trace"]["id"] == str(trace.id)
    else:
        assert detail["trace"] is None
    assert detail["projection"] is None
    assert await _database_snapshot(db_session) == before
