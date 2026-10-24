"""Annotation intake write access boundary (step10).

Covers the two item intake endpoints:

- Manual intake (``POST .../items``): the queue resolves through the
  read boundary first (existence, soft-delete state, ownership before
  the editor role check); invisible queues surface as the same
  identifier-free 404, a workspace-less member is denied before any
  query, and non-system callers need an editor floor before the intake
  service or trace validation run.
- From-task intake (``POST .../items/from-task``): the queue AND the
  source task must both be live with the task in the queue's real
  workspace, all resolved before the editor floor; any invisible piece
  returns the combined identifier-free 404.
- Provenance: only workspace-visible (session- or agent-owned),
  undeleted, completed root traces qualify. A score row's workspace or
  target id never substitutes for trace validation; unqualified
  targets are filtered before dedup, the limit, and ``candidates``.
- Duplicates: live and retained soft-deleted rows are no-ops
  (``already_present`` / ``skipped_existing``) without revival, a
  second row, or a unique-constraint 500.
- Semantics preserved: per-item ``rejected``, filters, bulk cap 422,
  default limit, pending state, ``added_by`` attribution, read-back
  through the existing item listing.
- Denied intakes never modify data (table-direct full-column
  snapshots); successful intakes only create the expected items.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.auth_context import AuthContext
from hecate.models.agent import AgentModel
from hecate.models.evaluation import (
    AnnotationQueueItemModel,
    AnnotationQueueModel,
    EvaluationTaskModel,
    EvaluationTaskScoreModel,
    TaskType,
)
from hecate.models.organization import OrganizationModel
from hecate.models.session import SessionModel
from hecate.models.trace import TraceModel
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


async def _seed_queue(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    deleted: bool = False,
) -> AnnotationQueueModel:
    queue = AnnotationQueueModel(
        name=f"intake-queue-{uuid.uuid4().hex[:8]}",
        instructions="Rate the output",
        metric_defs=[dict(d) for d in _METRIC_DEFS],
        assigned_user_ids=[],
        workspace_id=workspace_id,
        deleted=deleted,
        deleted_at=datetime.now(UTC) if deleted else None,
    )
    db.add(queue)
    await db.commit()
    return queue


async def _seed_trace(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    deleted: bool = False,
    status: str = "completed",
    trace_type: str = "trace",
    owner: str = "session",
    deleted_session: bool = False,
    created_at: datetime | None = None,
) -> TraceModel:
    """A root trace owned by a session (default) or an agent of the workspace."""
    now = datetime.now(UTC)
    agent = None
    if owner == "agent":
        agent = AgentModel(name=f"owner-{uuid.uuid4().hex[:8]}", workspace_id=workspace_id)
        db.add(agent)
        await db.flush()
    session_row = None
    if owner == "session":
        session_row = SessionModel(conversation_id=uuid.uuid4(), agent_id=uuid.uuid4(), workspace_id=workspace_id)
        db.add(session_row)
        await db.flush()
    trace = TraceModel(
        trace_id=uuid.uuid4(),
        type=trace_type,
        name="intake-target",
        session_id=session_row.id if session_row is not None else None,
        agent_id=agent.id if agent is not None else None,
        status=status,
        start_time=created_at or now,
        end_time=(created_at or now) + timedelta(seconds=5),
        deleted=deleted,
        deleted_at=datetime.now(UTC) if deleted else None,
    )
    db.add(trace)
    if session_row is not None and deleted_session:
        session_row.deleted = True
        session_row.deleted_at = datetime.now(UTC)
    await db.commit()
    return trace


async def _seed_task(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    deleted: bool = False,
    task_type: str = TaskType.ONLINE.value,
) -> EvaluationTaskModel:
    task = EvaluationTaskModel(
        task_type=task_type,
        name=f"prod-scoring-{uuid.uuid4().hex[:8]}",
        evaluator_configs=["stub"],
        config={"agent_id": str(uuid.uuid4()), "sampling_rate": 1.0},
        workspace_id=workspace_id,
        deleted=deleted,
        deleted_at=datetime.now(UTC) if deleted else None,
    )
    db.add(task)
    await db.commit()
    return task


async def _seed_score(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    task: EvaluationTaskModel,
    target_id: uuid.UUID,
    *,
    metric_name: str = "helpfulness",
    value: float = 0.5,
    error: bool = False,
    deleted: bool = False,
    target_type: str = "trace",
    created_at: datetime | None = None,
    source: str = "llm_judge",
    status: str = "completed",
) -> EvaluationTaskScoreModel:
    row = EvaluationTaskScoreModel(
        task_id=task.id,
        target_type=target_type,
        target_id=target_id,
        metric_name=metric_name,
        value=-1.0 if error else value,
        reasoning="Persisted evaluation evidence",
        source=source,
        status=status,
        workspace_id=workspace_id,
        deleted=deleted,
        deleted_at=datetime.now(UTC) if deleted else None,
        created_at=created_at or datetime.now(UTC),
    )
    db.add(row)
    await db.commit()
    return row


async def _seed_item(
    db: AsyncSession,
    queue: AnnotationQueueModel,
    target_id: uuid.UUID,
    *,
    deleted: bool = False,
) -> AnnotationQueueItemModel:
    item = AnnotationQueueItemModel(
        queue_id=queue.id,
        target_type="trace",
        target_id=target_id,
        status="pending",
        added_by=uuid.uuid4(),
        workspace_id=queue.workspace_id,
        deleted=deleted,
        deleted_at=datetime.now(UTC) if deleted else None,
    )
    db.add(item)
    await db.commit()
    return item


async def _database_snapshot(db: AsyncSession) -> list[list[dict[str, Any]]]:
    """Read every relevant column from storage, bypassing the identity map."""
    snapshots = []
    for model in (
        AnnotationQueueModel,
        AnnotationQueueItemModel,
        EvaluationTaskModel,
        EvaluationTaskScoreModel,
        TraceModel,
        SessionModel,
        AgentModel,
    ):
        table = model.__table__
        rows = (await db.execute(select(table).order_by(table.c.id))).mappings().all()
        snapshots.append([dict(row) for row in rows])
    return snapshots


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


def _assert_queue_or_task_not_found(resp: Any) -> None:
    assert resp.status_code == 404, resp.text
    assert resp.json() == {
        "detail": {"error": {"code": "NOT_FOUND", "message": "Annotation queue or task not found", "details": None}}
    }


async def _unexpected_write(*args: Any, **kwargs: Any) -> None:
    raise AssertionError("Denied requests must not run intake writes")


async def _unexpected_query(*args: Any, **kwargs: Any) -> None:
    raise AssertionError("Denied requests must not query queues, tasks, or traces")


@pytest.mark.parametrize("role", [WorkspaceRole.EDITOR, WorkspaceRole.ADMIN])
async def test_manual_intake_creates_pending_items_with_attribution(
    client: AsyncClient, db_session: AsyncSession, role: WorkspaceRole
) -> None:
    """Editor and above enqueue a visible trace; the listing reads it back."""
    ws, _ = await _workspace_with_role(db_session, "ws-manual", WorkspaceRole.VIEWER)
    queue = await _seed_queue(db_session, ws.id)
    trace = await _seed_trace(db_session, ws.id)
    caller = _member_as(ws, role)
    before = await _database_snapshot(db_session)
    async with _client_as(client, caller) as http:
        response = await http.post(f"{_BASE}/{queue.id}/items", json={"target_ids": [str(trace.id)]})
    assert response.status_code == 200, response.text
    assert response.json() == {"created": 1, "already_present": [], "rejected": []}
    snapshot = await _database_snapshot(db_session)
    assert len(snapshot[1]) == 1
    item_row = snapshot[1][0]
    assert item_row["queue_id"] == queue.id
    assert item_row["target_id"] == trace.id
    assert item_row["status"] == "pending"
    assert item_row["deleted"] == 0
    assert item_row["added_by"] == caller.user_id
    assert snapshot[2:] == before[2:]  # tasks, scores, traces, sessions, agents untouched
    async with _client_as(client, _member_as(ws, WorkspaceRole.VIEWER)) as http:
        listing = await http.get(f"{_BASE}/{queue.id}/items")
    assert listing.status_code == 200, listing.text
    assert [row["target_id"] for row in listing.json()["items"]] == [str(trace.id)]


async def test_manual_intake_rejects_invisible_targets_per_item(client: AsyncClient, db_session: AsyncSession) -> None:
    """Mixed batches keep per-item rejection; only visible traces enqueue."""
    ws, _ = await _workspace_with_role(db_session, "ws-mixed", WorkspaceRole.VIEWER)
    other, _ = await _workspace_with_role(db_session, "ws-other", WorkspaceRole.VIEWER)
    queue = await _seed_queue(db_session, ws.id)
    valid = await _seed_trace(db_session, ws.id)
    agent_owned = await _seed_trace(db_session, ws.id, owner="agent")
    foreign = await _seed_trace(db_session, other.id)
    foreign_agent = await _seed_trace(db_session, other.id, owner="agent")
    deleted = await _seed_trace(db_session, ws.id, deleted=True)
    running = await _seed_trace(db_session, ws.id, status="running")
    span = await _seed_trace(db_session, ws.id, trace_type="span")
    session_deleted = await _seed_trace(db_session, ws.id, deleted_session=True)
    before = await _database_snapshot(db_session)
    target_ids = [
        valid.id,
        agent_owned.id,
        uuid.uuid4(),  # orphan: no trace row at all
        foreign.id,
        foreign_agent.id,
        deleted.id,
        running.id,
        span.id,
        session_deleted.id,
    ]
    async with _client_as(client, _member_as(ws, WorkspaceRole.EDITOR)) as http:
        response = await http.post(f"{_BASE}/{queue.id}/items", json={"target_ids": [str(t) for t in target_ids]})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["created"] == 2
    assert body["already_present"] == []
    rejected_ids = {row["target_id"] for row in body["rejected"]}
    assert rejected_ids == {str(t) for t in target_ids[2:]}
    assert all(row["reason"] for row in body["rejected"])
    snapshot = await _database_snapshot(db_session)
    created = {row["target_id"] for row in snapshot[1]}
    assert created == {valid.id, agent_owned.id}
    assert snapshot[2:] == before[2:]


@pytest.mark.parametrize("role", [WorkspaceRole.VIEWER, None])
async def test_manual_intake_below_editor_denied_before_service(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    role: WorkspaceRole | None,
) -> None:
    """Viewers and role-less members never reach the service or validation."""
    from hecate.ops.evaluation.annotation.service import AnnotationService

    ws, _ = await _workspace_with_role(db_session, "ws-low", WorkspaceRole.VIEWER)
    queue = await _seed_queue(db_session, ws.id)
    trace = await _seed_trace(db_session, ws.id)
    before = await _database_snapshot(db_session)
    monkeypatch.setattr(AnnotationService, "add_items", _unexpected_write)
    async with _client_as(client, _member_as(ws, role)) as http:
        denied = await http.post(f"{_BASE}/{queue.id}/items", json={"target_ids": [str(trace.id)]})
        # Authorization precedes the bulk-cap business validation too.
        oversized = await http.post(f"{_BASE}/{queue.id}/items", json={"target_ids": [str(uuid.uuid4())] * 101})
    if role is None:
        _assert_forbidden(denied)
    else:
        _assert_forbidden_viewer(denied)
    assert oversized.status_code == 422, oversized.text  # schema cap preserved
    assert await _database_snapshot(db_session) == before


@pytest.mark.parametrize("identity", ["viewer", "roleless", "editor", "admin", "system"])
async def test_manual_intake_invisible_queue_404_before_role(
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
    monkeypatch.setattr(AnnotationService, "add_items", _unexpected_write)
    queue_ids = [uuid.uuid4(), deleted.id]
    if identity != "system":
        queue_ids.append(foreign.id)
    async with _client_as(client, ctx) as http:
        for queue_id in queue_ids:
            response = await http.post(f"{_BASE}/{queue_id}/items", json={"target_ids": [str(uuid.uuid4())]})
            _assert_queue_not_found(response)
            assert str(queue_id) not in response.text
            assert await _database_snapshot(db_session) == before


@pytest.mark.parametrize("endpoint", ["items", "from-task"])
async def test_member_without_workspace_denied_before_query(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    endpoint: str,
) -> None:
    """A workspace-less member gets the queue 404 before any database query."""
    ws, _ = await _workspace_with_role(db_session, "ws-seeded", WorkspaceRole.VIEWER)
    queue = await _seed_queue(db_session, ws.id)
    task = await _seed_task(db_session, ws.id)
    before = await _database_snapshot(db_session)
    with monkeypatch.context() as patch:
        patch.setattr(AsyncSession, "execute", _unexpected_query)
        async with _client_as(client, _no_workspace_context()) as http:
            if endpoint == "items":
                response = await http.post(f"{_BASE}/{queue.id}/items", json={"target_ids": [str(uuid.uuid4())]})
                _assert_queue_not_found(response)
            else:
                response = await http.post(f"{_BASE}/{queue.id}/items/from-task", json={"task_id": str(task.id)})
                _assert_queue_or_task_not_found(response)
    assert await _database_snapshot(db_session) == before


@pytest.mark.parametrize("role", [WorkspaceRole.EDITOR, WorkspaceRole.ADMIN])
async def test_from_task_intake_enqueues_qualified_targets(
    client: AsyncClient, db_session: AsyncSession, role: WorkspaceRole
) -> None:
    """From-task enqueues both ownership kinds and reports preserved fields."""
    ws, _ = await _workspace_with_role(db_session, "ws-task", WorkspaceRole.VIEWER)
    other, _ = await _workspace_with_role(db_session, "ws-foreign", WorkspaceRole.VIEWER)
    queue = await _seed_queue(db_session, ws.id)
    task = await _seed_task(db_session, ws.id)
    base = datetime(2026, 2, 1, tzinfo=UTC)
    session_owned = await _seed_trace(db_session, ws.id, created_at=base)
    agent_owned = await _seed_trace(db_session, ws.id, owner="agent", created_at=base + timedelta(seconds=1))
    await _seed_score(db_session, ws.id, task, session_owned.id, value=0.2, created_at=base + timedelta(seconds=10))
    await _seed_score(db_session, ws.id, task, agent_owned.id, value=0.8, created_at=base + timedelta(seconds=11))
    before = await _database_snapshot(db_session)
    async with _client_as(client, _member_as(ws, role)) as http:
        response = await http.post(f"{_BASE}/{queue.id}/items/from-task", json={"task_id": str(task.id)})
    assert response.status_code == 200, response.text
    assert response.json() == {"created": 2, "skipped_existing": 0, "candidates": 2, "limit": 50}
    snapshot = await _database_snapshot(db_session)
    created = {row["target_id"] for row in snapshot[1]}
    assert created == {session_owned.id, agent_owned.id}
    assert all(row["status"] == "pending" for row in snapshot[1])
    assert snapshot[2:] == before[2:]


async def test_from_task_filters_foreign_deleted_and_unqualified_targets(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Score provenance never substitutes for trace validation."""
    ws, _ = await _workspace_with_role(db_session, "ws-prov", WorkspaceRole.VIEWER)
    other, _ = await _workspace_with_role(db_session, "ws-other", WorkspaceRole.VIEWER)
    queue = await _seed_queue(db_session, ws.id)
    task = await _seed_task(db_session, ws.id)
    base = datetime(2026, 3, 1, tzinfo=UTC)
    valid = await _seed_trace(db_session, ws.id, created_at=base)
    foreign = await _seed_trace(db_session, other.id, created_at=base)
    deleted_trace = await _seed_trace(db_session, ws.id, deleted=True, created_at=base)
    running = await _seed_trace(db_session, ws.id, status="running", created_at=base)
    span = await _seed_trace(db_session, ws.id, trace_type="span", created_at=base)
    # Interference scores in another workspace for the SAME valid target.
    foreign_task = await _seed_task(db_session, other.id)
    await _seed_score(db_session, other.id, foreign_task, valid.id, value=0.99, created_at=base)
    # In-workspace rows: only the valid trace qualifies.
    for index, target in enumerate((valid, foreign, deleted_trace, running, span)):
        await _seed_score(db_session, ws.id, task, target.id, value=0.4, created_at=base + timedelta(seconds=index))
    await _seed_score(db_session, ws.id, task, uuid.uuid4(), value=0.4, created_at=base + timedelta(seconds=10))
    # Error sentinel, deleted score, and non-trace target type never qualify.
    error_trace = await _seed_trace(db_session, ws.id, created_at=base)
    await _seed_score(db_session, ws.id, task, error_trace.id, error=True, created_at=base + timedelta(seconds=11))
    deleted_score_trace = await _seed_trace(db_session, ws.id, created_at=base)
    await _seed_score(
        db_session, ws.id, task, deleted_score_trace.id, deleted=True, created_at=base + timedelta(seconds=12)
    )
    session_typed = await _seed_trace(db_session, ws.id, created_at=base)
    await _seed_score(
        db_session,
        ws.id,
        task,
        session_typed.id,
        value=0.4,
        target_type="session",
        created_at=base + timedelta(seconds=13),
    )
    before = await _database_snapshot(db_session)
    async with _client_as(client, _member_as(ws, WorkspaceRole.EDITOR)) as http:
        response = await http.post(f"{_BASE}/{queue.id}/items/from-task", json={"task_id": str(task.id)})
    assert response.status_code == 200, response.text
    assert response.json() == {"created": 1, "skipped_existing": 0, "candidates": 1, "limit": 50}
    snapshot = await _database_snapshot(db_session)
    assert [row["target_id"] for row in snapshot[1]] == [valid.id]
    assert snapshot[2:] == before[2:]


async def test_from_task_limit_order_and_filters(client: AsyncClient, db_session: AsyncSession) -> None:
    """Oldest-first order, filters, and the limit hold; invalid targets
    never consume the limit or the candidates count."""
    ws, _ = await _workspace_with_role(db_session, "ws-limit", WorkspaceRole.VIEWER)
    queue = await _seed_queue(db_session, ws.id)
    task = await _seed_task(db_session, ws.id)
    other_metric_task_score_base = datetime(2026, 4, 1, tzinfo=UTC)
    traces = [
        await _seed_trace(db_session, ws.id, created_at=other_metric_task_score_base + timedelta(seconds=i))
        for i in range(5)
    ]
    invalid = await _seed_trace(db_session, ws.id, status="running")
    for index, trace in enumerate(traces):
        await _seed_score(
            db_session,
            ws.id,
            task,
            trace.id,
            metric_name="helpfulness",
            value=[0.1, 0.2, 0.3, 0.4, 0.5][index],
            created_at=other_metric_task_score_base + timedelta(seconds=100 + index),
        )
    await _seed_score(
        db_session,
        ws.id,
        task,
        invalid.id,
        metric_name="helpfulness",
        value=0.05,
        created_at=other_metric_task_score_base + timedelta(seconds=99),
    )
    # A second score row for the same target (different metric, later
    # timestamp) collapses to one candidate under dedup.
    await _seed_score(
        db_session,
        ws.id,
        task,
        traces[0].id,
        metric_name="factuality",
        value=0.9,
        created_at=other_metric_task_score_base + timedelta(seconds=200),
    )
    # A factuality-only score for another target never matches the
    # helpfulness filter below.
    await _seed_score(
        db_session,
        ws.id,
        task,
        traces[4].id,
        metric_name="factuality",
        value=0.9,
        created_at=other_metric_task_score_base + timedelta(seconds=300),
    )
    editor = _member_as(ws, WorkspaceRole.EDITOR)
    async with _client_as(client, editor) as http:
        limited = await http.post(
            f"{_BASE}/{queue.id}/items/from-task",
            json={"task_id": str(task.id), "limit": 3},
        )
        assert limited.status_code == 200, limited.text
        assert limited.json() == {"created": 3, "skipped_existing": 0, "candidates": 5, "limit": 3}
        # The bounded selection has three qualified candidates (0.1/0.2/0.3);
        # the invalid 0.05 row never becomes a candidate, and all three
        # targets are already enqueued by the limited call.
        bounded = await http.post(
            f"{_BASE}/{queue.id}/items/from-task",
            json={"task_id": str(task.id), "max_score": 0.3, "metric_name": "helpfulness"},
        )
        assert bounded.status_code == 200, bounded.text
        assert bounded.json() == {"created": 0, "skipped_existing": 3, "candidates": 3, "limit": 50}
    snapshot = await _database_snapshot(db_session)
    created = {row["target_id"] for row in snapshot[1]}
    assert created == {trace.id for trace in traces[:3]}  # oldest-first under the limit


async def test_duplicate_and_soft_deleted_rows_stay_no_ops(client: AsyncClient, db_session: AsyncSession) -> None:
    """Live and soft-deleted rows are duplicates: no second row, no revival."""
    ws, _ = await _workspace_with_role(db_session, "ws-dup", WorkspaceRole.VIEWER)
    queue = await _seed_queue(db_session, ws.id)
    task = await _seed_task(db_session, ws.id)
    live_target = await _seed_trace(db_session, ws.id)
    deleted_target = await _seed_trace(db_session, ws.id)
    fresh_target = await _seed_trace(db_session, ws.id)
    await _seed_item(db_session, queue, live_target.id)
    await _seed_item(db_session, queue, deleted_target.id, deleted=True)
    await _seed_score(db_session, ws.id, task, live_target.id)
    await _seed_score(db_session, ws.id, task, deleted_target.id)
    await _seed_score(db_session, ws.id, task, fresh_target.id)
    before = await _database_snapshot(db_session)
    async with _client_as(client, _member_as(ws, WorkspaceRole.EDITOR)) as http:
        manual = await http.post(
            f"{_BASE}/{queue.id}/items",
            json={"target_ids": [str(live_target.id), str(deleted_target.id)]},
        )
        assert manual.status_code == 200, manual.text
        assert manual.json() == {
            "created": 0,
            "already_present": [str(live_target.id), str(deleted_target.id)],
            "rejected": [],
        }
        from_task = await http.post(f"{_BASE}/{queue.id}/items/from-task", json={"task_id": str(task.id)})
        assert from_task.status_code == 200, from_task.text
        assert from_task.json() == {"created": 1, "skipped_existing": 2, "candidates": 3, "limit": 50}
    snapshot = await _database_snapshot(db_session)
    rows = {row["target_id"]: row for row in snapshot[1]}
    assert rows[deleted_target.id]["deleted"] == 1  # not revived, not duplicated
    assert rows[live_target.id]["deleted"] == 0
    assert len(snapshot[1]) == 3  # two seeded + one fresh
    assert rows[fresh_target.id]["deleted"] == 0
    assert snapshot[2:] == before[2:]


@pytest.mark.parametrize("role", [WorkspaceRole.VIEWER, None])
async def test_from_task_below_editor_denied_before_service(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    role: WorkspaceRole | None,
) -> None:
    """Viewers and role-less members never reach the intake service."""
    from hecate.ops.evaluation.annotation.service import AnnotationService

    ws, _ = await _workspace_with_role(db_session, "ws-lowtask", WorkspaceRole.VIEWER)
    queue = await _seed_queue(db_session, ws.id)
    task = await _seed_task(db_session, ws.id)
    before = await _database_snapshot(db_session)
    monkeypatch.setattr(AnnotationService, "add_items_from_task", _unexpected_write)
    async with _client_as(client, _member_as(ws, role)) as http:
        response = await http.post(f"{_BASE}/{queue.id}/items/from-task", json={"task_id": str(task.id)})
    if role is None:
        _assert_forbidden(response)
    else:
        _assert_forbidden_viewer(response)
    assert await _database_snapshot(db_session) == before


@pytest.mark.parametrize("identity", ["viewer", "roleless", "editor", "admin", "system"])
async def test_from_task_invisible_queue_or_task_404_before_role(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    identity: str,
) -> None:
    """Invisible queue or task pairs share the combined 404 before writes."""
    from hecate.ops.evaluation.annotation.service import AnnotationService

    ws, viewer = await _workspace_with_role(db_session, "ws-pair", WorkspaceRole.VIEWER)
    other, _ = await _workspace_with_role(db_session, "ws-pair-other", WorkspaceRole.VIEWER)
    live_queue = await _seed_queue(db_session, ws.id)
    deleted_queue = await _seed_queue(db_session, ws.id, deleted=True)
    foreign_queue = await _seed_queue(db_session, other.id)
    live_task = await _seed_task(db_session, ws.id)
    deleted_task = await _seed_task(db_session, ws.id, deleted=True)
    foreign_task = await _seed_task(db_session, other.id)
    ctx = {
        "viewer": viewer,
        "roleless": _member_as(ws, None),
        "editor": _member_as(ws, WorkspaceRole.EDITOR),
        "admin": _member_as(ws, WorkspaceRole.ADMIN),
        "system": _system_context(),
    }[identity]
    before = await _database_snapshot(db_session)
    monkeypatch.setattr(AnnotationService, "add_items_from_task", _unexpected_write)
    cases = [
        (uuid.uuid4(), live_task.id),  # missing queue
        (deleted_queue.id, live_task.id),  # deleted queue
        (live_queue.id, uuid.uuid4()),  # missing task
        (live_queue.id, deleted_task.id),  # deleted task
        (live_queue.id, foreign_task.id),  # task outside the queue's workspace
        (foreign_queue.id, live_task.id),  # queue/task workspaces differ
    ]
    if identity != "system":
        cases.append((foreign_queue.id, foreign_task.id))  # both foreign: members cannot see either
    async with _client_as(client, ctx) as http:
        for queue_id, task_id in cases:
            response = await http.post(f"{_BASE}/{queue_id}/items/from-task", json={"task_id": str(task_id)})
            _assert_queue_or_task_not_found(response)
            assert str(queue_id) not in response.text
            assert str(task_id) not in response.text
            assert await _database_snapshot(db_session) == before


async def test_system_intake_without_workspace_uses_real_workspace(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """System scope intakes by explicit queue id; foreign tasks never enter."""
    ws, _ = await _workspace_with_role(db_session, "ws-target", WorkspaceRole.VIEWER)
    other, _ = await _workspace_with_role(db_session, "ws-other", WorkspaceRole.VIEWER)
    queue = await _seed_queue(db_session, ws.id)
    task = await _seed_task(db_session, ws.id)
    trace = await _seed_trace(db_session, ws.id)
    await _seed_score(db_session, ws.id, task, trace.id)
    foreign_trace = await _seed_trace(db_session, other.id)
    foreign_task = await _seed_task(db_session, other.id)
    before = await _database_snapshot(db_session)
    system = _system_context()
    async with _client_as(client, system) as http:
        manual = await http.post(
            f"{_BASE}/{queue.id}/items",
            json={"target_ids": [str(trace.id), str(foreign_trace.id)]},
        )
        assert manual.status_code == 200, manual.text
        assert manual.json()["created"] == 1
        assert [row["target_id"] for row in manual.json()["rejected"]] == [str(foreign_trace.id)]
        from_task = await http.post(f"{_BASE}/{queue.id}/items/from-task", json={"task_id": str(task.id)})
        assert from_task.status_code == 200, from_task.text
        assert from_task.json() == {"created": 0, "skipped_existing": 1, "candidates": 1, "limit": 50}
        foreign = await http.post(f"{_BASE}/{queue.id}/items/from-task", json={"task_id": str(foreign_task.id)})
        _assert_queue_or_task_not_found(foreign)
    snapshot = await _database_snapshot(db_session)
    assert [row["target_id"] for row in snapshot[1]] == [trace.id]
    assert all(row["workspace_id"] == ws.id for row in snapshot[1])
    assert snapshot[2:] == before[2:]


async def test_zero_uuid_workspace_is_a_legal_scope_for_intake(client: AsyncClient, db_session: AsyncSession) -> None:
    """The zero-UUID workspace stays a valid explicit scope for intake."""
    zero_ws = uuid.UUID(int=0)
    queue = await _seed_queue(db_session, zero_ws)
    task = await _seed_task(db_session, zero_ws)
    trace = await _seed_trace(db_session, zero_ws)
    await _seed_score(db_session, zero_ws, task, trace.id)
    editor = AuthContext(
        user_id=uuid.uuid4(),
        org_id=None,
        workspace_id=zero_ws,
        role=WorkspaceRole.EDITOR,
        auth_method="jwt",
        api_key_scope=None,
    )
    async with _client_as(client, editor) as http:
        manual = await http.post(f"{_BASE}/{queue.id}/items", json={"target_ids": [str(trace.id)]})
        assert manual.status_code == 200, manual.text
        assert manual.json()["created"] == 1
        from_task = await http.post(f"{_BASE}/{queue.id}/items/from-task", json={"task_id": str(task.id)})
        assert from_task.status_code == 200, from_task.text
        assert from_task.json() == {"created": 0, "skipped_existing": 1, "candidates": 1, "limit": 50}


@pytest.mark.parametrize("payload", [{"target_ids": []}, {"target_ids": ["not-a-uuid"]}, {"unknown": 1}])
async def test_request_schema_422_preserved(
    client: AsyncClient, db_session: AsyncSession, payload: dict[str, Any]
) -> None:
    """Schema-shape violations keep their 422 behavior."""
    ws, _ = await _workspace_with_role(db_session, "ws-schema", WorkspaceRole.VIEWER)
    queue = await _seed_queue(db_session, ws.id)
    async with _client_as(client, _member_as(ws, WorkspaceRole.EDITOR)) as http:
        response = await http.post(f"{_BASE}/{queue.id}/items", json=payload)
    assert response.status_code == 422, response.text


@pytest.mark.parametrize("invalid_provenance", ["human", "error"])
async def test_from_task_excludes_non_automated_or_error_score_rows(
    client: AsyncClient, db_session: AsyncSession, invalid_provenance: str
) -> None:
    """A task id or non-sentinel value cannot make an ineligible score a candidate."""
    ws, _ = await _workspace_with_role(db_session, "ws-ineligible-score", WorkspaceRole.VIEWER)
    queue = await _seed_queue(db_session, ws.id)
    task = await _seed_task(db_session, ws.id)
    trace = await _seed_trace(db_session, ws.id)
    await _seed_score(
        db_session,
        ws.id,
        task,
        trace.id,
        source="human" if invalid_provenance == "human" else "llm_judge",
        status="error" if invalid_provenance == "error" else "completed",
    )
    before = await _database_snapshot(db_session)
    async with _client_as(client, _member_as(ws, WorkspaceRole.EDITOR)) as http:
        response = await http.post(f"{_BASE}/{queue.id}/items/from-task", json={"task_id": str(task.id)})
    assert response.status_code == 200, response.text
    assert response.json() == {"created": 0, "skipped_existing": 0, "candidates": 0, "limit": 50}
    assert await _database_snapshot(db_session) == before


@pytest.mark.parametrize("identity", ["viewer", "roleless", "editor", "admin", "system"])
async def test_offline_source_task_is_validated_only_after_authorization(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, identity: str
) -> None:
    """Only online tasks supply intake; the business error must not precede role denial."""
    from hecate.ops.evaluation.annotation.service import AnnotationService

    ws, viewer = await _workspace_with_role(db_session, "ws-offline-source", WorkspaceRole.VIEWER)
    queue = await _seed_queue(db_session, ws.id)
    task = await _seed_task(db_session, ws.id, task_type=TaskType.OFFLINE.value)
    trace = await _seed_trace(db_session, ws.id)
    await _seed_score(db_session, ws.id, task, trace.id)
    ctx = {
        "viewer": viewer,
        "roleless": _member_as(ws, None),
        "editor": _member_as(ws, WorkspaceRole.EDITOR),
        "admin": _member_as(ws, WorkspaceRole.ADMIN),
        "system": _system_context(),
    }[identity]
    before = await _database_snapshot(db_session)
    monkeypatch.setattr(AnnotationService, "_workspace_trace", _unexpected_query)
    async with _client_as(client, ctx) as http:
        response = await http.post(f"{_BASE}/{queue.id}/items/from-task", json={"task_id": str(task.id)})
    if identity == "viewer":
        _assert_forbidden_viewer(response)
    elif identity == "roleless":
        _assert_forbidden(response)
    else:
        assert response.status_code == 400, response.text
        assert response.json()["detail"]["error"]["code"] == "INVALID_REQUEST"
    assert await _database_snapshot(db_session) == before


@pytest.mark.parametrize("endpoint", ["manual", "from-task"])
@pytest.mark.parametrize("deleted", [False, True])
async def test_other_target_type_does_not_shadow_trace_intake(
    client: AsyncClient, db_session: AsyncSession, endpoint: str, deleted: bool
) -> None:
    """Deduplication must use the target type as well as the target id, like the unique key."""
    ws, _ = await _workspace_with_role(db_session, "ws-typed-dedup", WorkspaceRole.VIEWER)
    queue = await _seed_queue(db_session, ws.id)
    task = await _seed_task(db_session, ws.id)
    trace = await _seed_trace(db_session, ws.id)
    other_item = await _seed_item(db_session, queue, trace.id, deleted=deleted)
    other_item.target_type = "session"
    await db_session.commit()
    await _seed_score(db_session, ws.id, task, trace.id)
    before = await _database_snapshot(db_session)
    async with _client_as(client, _member_as(ws, WorkspaceRole.EDITOR)) as http:
        if endpoint == "manual":
            response = await http.post(f"{_BASE}/{queue.id}/items", json={"target_ids": [str(trace.id)]})
            assert response.status_code == 200, response.text
            assert response.json() == {"created": 1, "already_present": [], "rejected": []}
        else:
            response = await http.post(f"{_BASE}/{queue.id}/items/from-task", json={"task_id": str(task.id)})
            assert response.status_code == 200, response.text
            assert response.json() == {"created": 1, "skipped_existing": 0, "candidates": 1, "limit": 50}
    after = await _database_snapshot(db_session)
    assert next(row for row in after[1] if row["id"] == other_item.id) == before[1][0]
    assert len(after[1]) == 2
    assert [row["target_id"] for row in after[1] if row["target_type"] == "trace"] == [trace.id]
    assert after[0] == before[0]
    assert after[2:] == before[2:]
