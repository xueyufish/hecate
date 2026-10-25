"""Annotation dataset backflow write access boundary (step10).

Covers the ``POST /api/evaluation/annotation-queues/{queue_id}/push-dataset``
endpoint:

- The queue and explicit target dataset resolve before the role check;
  missing, deleted, or invisible resources share the combined identifier-free
  404. Even system cannot combine workspaces, and workspace-less non-system
  callers are denied before any query.
- An editor floor applies below the ownership check: viewers and role-less
  members get 403 before the backflow service runs — even when the payload
  would also fail dataset-selector validation — while schema-shape
  violations keep their 422.
- The backflow runs in the queue's real workspace: system scope keeps its
  role exemption but resolves and creates datasets, selects items, and
  draws labels and trace projections only from that workspace, and cannot
  bypass soft deletes or a missing queue.
- Source isolation inside the service: human labels embedded into dataset
  items come only from the queue's workspace's ledger rows, and only
  traces visible to that workspace materialize — foreign, deleted, or
  missing traces are skipped, and the queue's live state is rechecked
  after the API precheck.
- Denied pushes never touch queue, item, score, dataset, or dataset-item
  data, verified with service spies and table-direct full-column snapshots.
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
from hecate.models.evaluation import (
    AnnotationQueueItemModel,
    AnnotationQueueModel,
    EvaluationBackflowRuleCreateSchema,
    EvaluationDatasetModel,
    EvaluationItemModel,
    EvaluationTaskModel,
    EvaluationTaskScoreModel,
)
from hecate.models.organization import OrganizationModel
from hecate.models.session import SessionModel
from hecate.models.trace import TraceModel
from hecate.models.workspace import WorkspaceModel
from hecate.models.workspace_member import WorkspaceMemberModel, WorkspaceRole
from hecate.ops.evaluation.annotation import AnnotationService
from hecate.ops.evaluation.backflow import BackflowService
from hecate.runtime.eventstore import Event, EventType, InMemoryEventStore

_BASE = "/api/evaluation/annotation-queues"

_NOW = datetime.now(UTC).replace(microsecond=0)


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
        name=f"boundary-queue-{uuid.uuid4().hex[:8]}",
        instructions="Rate the output",
        metric_defs=[{"name": "helpfulness", "data_type": "numeric", "min": 0, "max": 1}],
        assigned_user_ids=[],
        workspace_id=workspace_id,
        deleted=deleted,
        deleted_at=datetime.now(UTC) if deleted else None,
    )
    db.add(queue)
    await db.commit()
    return queue


async def _seed_trace(db: AsyncSession, workspace_id: uuid.UUID) -> TraceModel:
    """A completed root trace tenanted through its session's workspace."""
    session_row = SessionModel(conversation_id=uuid.uuid4(), agent_id=uuid.uuid4(), workspace_id=workspace_id)
    db.add(session_row)
    await db.flush()
    trace = TraceModel(
        trace_id=uuid.uuid4(),
        type="trace",
        name=f"session:{session_row.id}",
        session_id=session_row.id,
        status="completed",
        start_time=_NOW,
        end_time=_NOW + timedelta(seconds=10),
        created_at=_NOW,
    )
    db.add(trace)
    await db.commit()
    return trace


async def _seed_completed_item(
    db: AsyncSession,
    queue: AnnotationQueueModel,
    trace: TraceModel,
) -> AnnotationQueueItemModel:
    item = AnnotationQueueItemModel(
        queue_id=queue.id,
        target_type="trace",
        target_id=trace.id,
        status="completed",
        added_by=uuid.uuid4(),
        completed_by=uuid.uuid4(),
        completed_at=datetime.now(UTC),
        workspace_id=queue.workspace_id,
    )
    db.add(item)
    await db.commit()
    return item


async def _seed_human_score(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    target_id: uuid.UUID,
    *,
    value: float = 0.9,
    annotator: uuid.UUID | None = None,
) -> EvaluationTaskScoreModel:
    row = EvaluationTaskScoreModel(
        task_id=None,
        target_id=target_id,
        metric_name="helpfulness",
        value=value,
        reasoning="Persisted evaluation evidence",
        source="human",
        status="completed",
        annotator_id=annotator or uuid.uuid4(),
        workspace_id=workspace_id,
        created_at=datetime.now(UTC),
    )
    db.add(row)
    await db.commit()
    return row


async def _seed_automated_score(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    target_id: uuid.UUID,
    *,
    value: float = 0.35,
) -> EvaluationTaskScoreModel:
    row = EvaluationTaskScoreModel(
        task_id=uuid.uuid4(),
        target_id=target_id,
        metric_name="helpfulness",
        value=value,
        reasoning="Machine judge output",
        source="llm_judge",
        status="completed",
        annotator_id=None,
        workspace_id=workspace_id,
        created_at=datetime.now(UTC),
    )
    db.add(row)
    await db.commit()
    return row


async def _seed_dataset(db: AsyncSession, workspace_id: uuid.UUID, name: str) -> EvaluationDatasetModel:
    dataset = EvaluationDatasetModel(name=name, workspace_id=workspace_id)
    db.add(dataset)
    await db.commit()
    return dataset


async def _install_event_store(monkeypatch: pytest.MonkeyPatch, traces: list[TraceModel]) -> None:
    """Back the service's default event store with one full exchange per trace.

    Events land inside each trace's window, so ``build_eval_input`` projects
    a complete user→assistant exchange for every visible trace.
    """
    store = InMemoryEventStore()
    for trace in traces:
        await store.append(
            Event(
                session_id=trace.session_id,
                superstep=1,
                event_type=EventType.LLM_REQUEST,
                timestamp=trace.start_time + timedelta(seconds=1),
                payload={"messages": [{"role": "user", "content": "what is X?"}]},
            )
        )
        await store.append(
            Event(
                session_id=trace.session_id,
                superstep=2,
                event_type=EventType.LLM_REQUEST,
                timestamp=trace.start_time + timedelta(seconds=2),
                payload={"messages": [{"role": "assistant", "content": "X is a thing"}]},
            )
        )

    async def _stub_event_store(self: AnnotationService) -> Any:
        return store

    monkeypatch.setattr(AnnotationService, "_get_event_store", _stub_event_store)


async def _database_snapshot(db: AsyncSession) -> list[list[dict[str, Any]]]:
    """Read every relevant column from storage, bypassing the identity map."""
    snapshots = []
    for model in (
        AnnotationQueueModel,
        AnnotationQueueItemModel,
        EvaluationTaskScoreModel,
        EvaluationDatasetModel,
        EvaluationItemModel,
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
        "detail": {"error": {"code": "NOT_FOUND", "message": "Annotation queue or dataset not found", "details": None}}
    }


async def _unexpected_query(*args: Any, **kwargs: Any) -> None:
    raise AssertionError("Denied requests must not query queues")


async def _unexpected_push(*args: Any, **kwargs: Any) -> None:
    raise AssertionError("Denied requests must not run the dataset backflow")


@pytest.mark.parametrize("role", [WorkspaceRole.EDITOR, WorkspaceRole.ADMIN])
async def test_editor_push_materializes_labels_and_attribution(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    role: WorkspaceRole,
) -> None:
    """Editor and above materialize completed items into their workspace."""
    ws, _ = await _workspace_with_role(db_session, "ws-push", WorkspaceRole.VIEWER)
    queue = await _seed_queue(db_session, ws.id)
    trace = await _seed_trace(db_session, ws.id)
    await _seed_completed_item(db_session, queue, trace)
    annotator = uuid.uuid4()
    await _seed_human_score(db_session, ws.id, trace.id, value=0.9, annotator=annotator)
    await _install_event_store(monkeypatch, [trace])

    async with _client_as(client, _member_as(ws, role)) as http:
        response = await http.post(f"{_BASE}/{queue.id}/push-dataset", json={"dataset_name": "human-golden-v1"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["created"] == 1 and body["skipped"] == 0

    snapshot = await _database_snapshot(db_session)
    dataset = next(row for row in snapshot[3] if row["name"] == "human-golden-v1")
    assert dataset["workspace_id"] == ws.id
    assert not dataset["deleted"]
    items = [row for row in snapshot[4] if row["dataset_id"] == dataset["id"]]
    assert len(items) == 1
    row = items[0]
    assert row["workspace_id"] == ws.id
    assert row["query"] == "what is X?" and row["generated_answer"] == "X is a thing"
    assert row["metadata"]["annotation"]["trace_id"] == str(trace.id)
    assert row["metadata"]["annotation"]["queue_id"] == str(queue.id)
    assert row["metadata"]["annotation"]["labels"] == [
        {
            "metric_name": "helpfulness",
            "value": 0.9,
            "value_label": None,
            "reason_code": None,
            "justification": "Persisted evaluation evidence",
            "annotator_id": str(annotator),
        }
    ]
    assert "human-annotation" in row["tags"] and queue.name in row["tags"]


@pytest.mark.parametrize("role", [WorkspaceRole.VIEWER, None])
async def test_push_below_editor_denied_without_materializing(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    role: WorkspaceRole | None,
) -> None:
    """Viewers and role-less members are denied before the backflow service."""
    ws, _ = await _workspace_with_role(db_session, "ws-low", WorkspaceRole.VIEWER)
    queue = await _seed_queue(db_session, ws.id)
    trace = await _seed_trace(db_session, ws.id)
    await _seed_completed_item(db_session, queue, trace)
    before = await _database_snapshot(db_session)
    with monkeypatch.context() as patch:
        patch.setattr(AnnotationService, "push_to_dataset", _unexpected_push)
        async with _client_as(client, _member_as(ws, role)) as http:
            denied = await http.post(f"{_BASE}/{queue.id}/push-dataset", json={"dataset_name": "never-created"})
            # Authorization precedes dataset-selector validation: a payload
            # that passes the request schema but fails service validation
            # still gets the role denial, while schema-shape violations
            # keep their 422.
            business_invalid = await http.post(f"{_BASE}/{queue.id}/push-dataset", json={})
            schema_invalid = await http.post(f"{_BASE}/{queue.id}/push-dataset", json={"unknown_field": 1})
    if role is None:
        _assert_forbidden(denied)
        _assert_forbidden(business_invalid)
    else:
        _assert_forbidden_viewer(denied)
        _assert_forbidden_viewer(business_invalid)
    assert schema_invalid.status_code == 422, schema_invalid.text
    assert await _database_snapshot(db_session) == before


async def test_push_without_workspace_denied_before_any_query(
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
            response = await http.post(f"{_BASE}/{queue.id}/push-dataset", json={"dataset_name": "never-created"})
    _assert_queue_not_found(response)
    assert await _database_snapshot(db_session) == before


@pytest.mark.parametrize("identity", ["viewer", "roleless", "editor", "admin", "system"])
async def test_invisible_queue_denied_before_role(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    identity: str,
) -> None:
    """Missing, soft-deleted, and foreign queues share one 404 before pushes."""
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
    monkeypatch.setattr(AnnotationService, "push_to_dataset", _unexpected_push)
    queue_ids = [uuid.uuid4(), deleted.id]
    if identity != "system":
        queue_ids.append(foreign.id)
    async with _client_as(client, ctx) as http:
        for queue_id in queue_ids:
            response = await http.post(f"{_BASE}/{queue_id}/push-dataset", json={"dataset_name": "never-created"})
            _assert_queue_not_found(response)
            assert str(queue_id) not in response.text
            assert await _database_snapshot(db_session) == before


@pytest.mark.parametrize("active_workspace", ["missing", "other"])
async def test_system_pushes_through_real_workspace(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    active_workspace: str,
) -> None:
    """Explicit system scope resolves datasets and labels in the real workspace."""
    ws, _ = await _workspace_with_role(db_session, "ws-target", WorkspaceRole.VIEWER)
    other, _ = await _workspace_with_role(db_session, "ws-other", WorkspaceRole.VIEWER)
    queue = await _seed_queue(db_session, ws.id)
    trace = await _seed_trace(db_session, ws.id)
    await _seed_completed_item(db_session, queue, trace)
    annotator = uuid.uuid4()
    await _seed_human_score(db_session, ws.id, trace.id, value=0.9, annotator=annotator)
    decoy = await _seed_dataset(db_session, other.id, "system-golden")
    await _install_event_store(monkeypatch, [trace])
    system = replace(_system_context(), workspace_id=other.id if active_workspace == "other" else None)

    async with _client_as(client, system) as http:
        # System scope cannot combine a queue with another workspace's dataset.
        foreign = await http.post(f"{_BASE}/{queue.id}/push-dataset", json={"dataset_id": str(decoy.id)})
        _assert_queue_not_found(foreign)
        # A new dataset name lands in the queue's real workspace, not the
        # system identity's active workspace.
        created = await http.post(f"{_BASE}/{queue.id}/push-dataset", json={"dataset_name": "system-golden"})
        assert created.status_code == 200, created.text
        assert created.json()["created"] == 1

    snapshot = await _database_snapshot(db_session)
    dataset = next(row for row in snapshot[3] if row["name"] == "system-golden" and row["workspace_id"] == ws.id)
    assert dataset["workspace_id"] == ws.id
    items = [row for row in snapshot[4] if row["dataset_id"] == dataset["id"]]
    assert len(items) == 1
    assert items[0]["workspace_id"] == ws.id
    assert items[0]["metadata"]["annotation"]["labels"] == [
        {
            "metric_name": "helpfulness",
            "value": 0.9,
            "value_label": None,
            "reason_code": None,
            "justification": "Persisted evaluation evidence",
            "annotator_id": str(annotator),
        }
    ]
    assert [row for row in snapshot[4] if row["dataset_id"] == decoy.id] == []


async def test_labels_stay_in_queue_workspace(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Foreign-workspace human rows and automated rows never become labels."""
    ws, _ = await _workspace_with_role(db_session, "ws-labels", WorkspaceRole.VIEWER)
    other, _ = await _workspace_with_role(db_session, "ws-foreign", WorkspaceRole.VIEWER)
    queue = await _seed_queue(db_session, ws.id)
    trace = await _seed_trace(db_session, ws.id)
    await _seed_completed_item(db_session, queue, trace)
    annotator = uuid.uuid4()
    own_row = await _seed_human_score(db_session, ws.id, trace.id, value=0.9, annotator=annotator)
    foreign_row = await _seed_human_score(db_session, other.id, trace.id, value=0.1)
    automated_row = await _seed_automated_score(db_session, ws.id, trace.id, value=0.35)
    wrong_type = await _seed_human_score(db_session, ws.id, trace.id, value=0.2)
    wrong_type.target_type = "session"
    failed = await _seed_human_score(db_session, ws.id, trace.id, value=0.3)
    failed.status = "error"
    deleted = await _seed_human_score(db_session, ws.id, trace.id, value=0.4)
    deleted.deleted = True
    await db_session.commit()
    await _install_event_store(monkeypatch, [trace])

    async with _client_as(client, _member_as(ws, WorkspaceRole.EDITOR)) as http:
        response = await http.post(f"{_BASE}/{queue.id}/push-dataset", json={"dataset_name": "isolated-golden"})
    assert response.status_code == 200, response.text
    assert response.json()["created"] == 1

    snapshot = await _database_snapshot(db_session)
    dataset = next(row for row in snapshot[3] if row["name"] == "isolated-golden")
    item = next(row for row in snapshot[4] if row["dataset_id"] == dataset["id"])
    labels = item["metadata"]["annotation"]["labels"]
    assert labels == [
        {
            "metric_name": "helpfulness",
            "value": 0.9,
            "value_label": None,
            "reason_code": None,
            "justification": "Persisted evaluation evidence",
            "annotator_id": str(annotator),
        }
    ]
    # The foreign human row and the automated row are untouched by the push.
    scores = {row["id"]: row for row in snapshot[2]}
    assert scores[foreign_row.id]["value"] == 0.1
    assert scores[automated_row.id]["value"] == 0.35
    assert scores[own_row.id]["value"] == 0.9


async def test_push_skips_traces_outside_the_workspace(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only traces visible in the queue's workspace materialize; rest skip."""
    ws, _ = await _workspace_with_role(db_session, "ws-visible", WorkspaceRole.VIEWER)
    other, _ = await _workspace_with_role(db_session, "ws-foreign", WorkspaceRole.VIEWER)
    queue = await _seed_queue(db_session, ws.id)
    visible = await _seed_trace(db_session, ws.id)
    foreign = await _seed_trace(db_session, other.id)
    deleted = await _seed_trace(db_session, ws.id)
    deleted.deleted = 1
    await db_session.commit()
    await _seed_completed_item(db_session, queue, visible)
    await _seed_human_score(db_session, ws.id, visible.id)
    await _seed_completed_item(db_session, queue, foreign)
    await _seed_completed_item(db_session, queue, deleted)
    missing_item = AnnotationQueueItemModel(
        queue_id=queue.id,
        target_type="trace",
        target_id=uuid.uuid4(),
        status="completed",
        added_by=uuid.uuid4(),
        workspace_id=queue.workspace_id,
    )
    db_session.add(missing_item)
    await db_session.commit()
    await _install_event_store(monkeypatch, [visible, foreign, deleted])

    async with _client_as(client, _member_as(ws, WorkspaceRole.EDITOR)) as http:
        response = await http.post(f"{_BASE}/{queue.id}/push-dataset", json={"dataset_name": "visible-only"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["created"] == 1 and body["skipped"] == 3

    snapshot = await _database_snapshot(db_session)
    dataset = next(row for row in snapshot[3] if row["name"] == "visible-only")
    items = [row for row in snapshot[4] if row["dataset_id"] == dataset["id"]]
    assert [row["metadata"]["annotation"]["trace_id"] for row in items] == [str(visible.id)]


async def test_item_ids_selector_cannot_reach_other_queues(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The optional item selector narrows within the queue's own items."""
    ws, _ = await _workspace_with_role(db_session, "ws-selector", WorkspaceRole.VIEWER)
    queue = await _seed_queue(db_session, ws.id)
    other_queue = await _seed_queue(db_session, ws.id)
    own_trace = await _seed_trace(db_session, ws.id)
    foreign_trace = await _seed_trace(db_session, ws.id)
    own_item = await _seed_completed_item(db_session, queue, own_trace)
    await _seed_human_score(db_session, ws.id, own_trace.id)
    other_item = await _seed_completed_item(db_session, other_queue, foreign_trace)
    await _install_event_store(monkeypatch, [own_trace, foreign_trace])

    async with _client_as(client, _member_as(ws, WorkspaceRole.EDITOR)) as http:
        # The dataset is created even when the selector matches nothing;
        # another queue's item is not a candidate, so nothing materializes.
        seeded = await http.post(
            f"{_BASE}/{queue.id}/push-dataset",
            json={"dataset_name": "selector-test", "item_ids": [str(other_item.id)]},
        )
        assert seeded.status_code == 200, seeded.text
        assert seeded.json()["created"] == 0 and seeded.json()["skipped"] == 0
        dataset_id = seeded.json()["dataset_id"]
        foreign_pick = await http.post(
            f"{_BASE}/{queue.id}/push-dataset", json={"dataset_id": dataset_id, "item_ids": [str(other_item.id)]}
        )
        assert foreign_pick.status_code == 200, foreign_pick.text
        assert foreign_pick.json()["created"] == 0 and foreign_pick.json()["skipped"] == 0
        own_pick = await http.post(
            f"{_BASE}/{queue.id}/push-dataset", json={"dataset_id": dataset_id, "item_ids": [str(own_item.id)]}
        )
        assert own_pick.status_code == 200, own_pick.text
        assert own_pick.json()["created"] == 1 and own_pick.json()["skipped"] == 0

    snapshot = await _database_snapshot(db_session)
    dataset = next(row for row in snapshot[3] if row["name"] == "selector-test")
    items = [row for row in snapshot[4] if row["dataset_id"] == dataset["id"]]
    assert [row["metadata"]["annotation"]["trace_id"] for row in items] == [str(own_trace.id)]


async def test_selector_validation_preserved(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exactly one of dataset_id / dataset_name stays a service rule."""
    ws, _ = await _workspace_with_role(db_session, "ws-validate", WorkspaceRole.VIEWER)
    queue = await _seed_queue(db_session, ws.id)
    dataset = await _seed_dataset(db_session, ws.id, "visible-target")
    await _install_event_store(monkeypatch, [])
    before = await _database_snapshot(db_session)
    async with _client_as(client, _member_as(ws, WorkspaceRole.EDITOR)) as http:
        neither = await http.post(f"{_BASE}/{queue.id}/push-dataset", json={})
        both = await http.post(
            f"{_BASE}/{queue.id}/push-dataset",
            json={"dataset_id": str(dataset.id), "dataset_name": "conflict"},
        )
    assert neither.status_code == 400, neither.text
    assert neither.json()["detail"]["error"]["code"] == "INVALID_REQUEST"
    assert "exactly one" in neither.json()["detail"]["error"]["message"]
    assert both.status_code == 400, both.text
    assert await _database_snapshot(db_session) == before


async def test_duplicate_push_is_idempotent(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Re-pushing the same queue adds nothing and reports skips."""
    ws, _ = await _workspace_with_role(db_session, "ws-idem", WorkspaceRole.VIEWER)
    queue = await _seed_queue(db_session, ws.id)
    trace = await _seed_trace(db_session, ws.id)
    await _seed_completed_item(db_session, queue, trace)
    await _seed_human_score(db_session, ws.id, trace.id)
    await _install_event_store(monkeypatch, [trace])

    async with _client_as(client, _member_as(ws, WorkspaceRole.EDITOR)) as http:
        first = await http.post(f"{_BASE}/{queue.id}/push-dataset", json={"dataset_name": "golden"})
        second = await http.post(f"{_BASE}/{queue.id}/push-dataset", json={"dataset_name": "golden"})
    assert first.status_code == 200 and first.json()["created"] == 1, first.text
    assert second.status_code == 200 and second.json()["created"] == 0 and second.json()["skipped"] == 1, second.text


async def test_queue_deleted_after_precheck_rejected(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The service rechecks queue liveness before materializing anything."""
    from hecate.ops.api import evaluation_annotations

    ws, _ = await _workspace_with_role(db_session, "ws-race", WorkspaceRole.VIEWER)
    queue = await _seed_queue(db_session, ws.id)
    trace = await _seed_trace(db_session, ws.id)
    await _seed_completed_item(db_session, queue, trace)
    await _install_event_store(monkeypatch, [trace])

    original = evaluation_annotations._get_queue_for_read

    async def precheck_then_delete(ctx: AuthContext, queue_id: uuid.UUID, db: AsyncSession):
        found = await original(ctx, queue_id, db)
        # Another writer soft-deletes the queue after the API precheck;
        # the identity map keeps the stale alive object.
        await db.execute(
            update(AnnotationQueueModel)
            .where(AnnotationQueueModel.id == queue_id)
            .values(deleted=True, deleted_at=datetime.now(UTC))
        )
        await db.commit()
        return found

    monkeypatch.setattr(evaluation_annotations, "_get_queue_for_read", precheck_then_delete)
    async with _client_as(client, _member_as(ws, WorkspaceRole.EDITOR)) as http:
        response = await http.post(f"{_BASE}/{queue.id}/push-dataset", json={"dataset_name": "should-not-exist"})
    _assert_queue_not_found(response)
    snapshot = await _database_snapshot(db_session)
    assert snapshot[3] == [] and snapshot[4] == []


async def test_zero_uuid_workspace_is_a_legal_scope_for_push(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The zero-UUID workspace stays a valid explicit scope for backflow."""
    zero_ws = uuid.UUID(int=0)
    queue = await _seed_queue(db_session, zero_ws)
    trace = await _seed_trace(db_session, zero_ws)
    await _seed_completed_item(db_session, queue, trace)
    await _seed_human_score(db_session, zero_ws, trace.id)
    await _install_event_store(monkeypatch, [trace])
    editor = AuthContext(
        user_id=uuid.uuid4(),
        org_id=None,
        workspace_id=zero_ws,
        role=WorkspaceRole.EDITOR,
        auth_method="jwt",
        api_key_scope=None,
    )
    async with _client_as(client, editor) as http:
        response = await http.post(f"{_BASE}/{queue.id}/push-dataset", json={"dataset_name": "zero-golden"})
    assert response.status_code == 200, response.text
    assert response.json()["created"] == 1
    snapshot = await _database_snapshot(db_session)
    dataset = next(row for row in snapshot[3] if row["name"] == "zero-golden")
    assert dataset["workspace_id"] == zero_ws


@pytest.mark.parametrize("identity", ["viewer", "roleless", "editor", "admin", "system"])
async def test_explicit_dataset_visibility_precedes_role(
    client: AsyncClient,
    db_session: AsyncSession,
    identity: str,
) -> None:
    """Invisible target datasets share a fixed 404 before the role or business checks."""
    ws, viewer = await _workspace_with_role(db_session, "ws-target-boundary", WorkspaceRole.VIEWER)
    other, _ = await _workspace_with_role(db_session, "ws-target-other", WorkspaceRole.VIEWER)
    queue = await _seed_queue(db_session, ws.id)
    foreign = await _seed_dataset(db_session, other.id, "foreign-target")
    deleted = await _seed_dataset(db_session, ws.id, "deleted-target")
    deleted.deleted = True
    await db_session.commit()
    ctx = {
        "viewer": viewer,
        "roleless": _member_as(ws, None),
        "editor": _member_as(ws, WorkspaceRole.EDITOR),
        "admin": _member_as(ws, WorkspaceRole.ADMIN),
        "system": _system_context(),
    }[identity]
    before = await _database_snapshot(db_session)
    async with _client_as(client, ctx) as http:
        for target in (uuid.uuid4(), foreign.id, deleted.id):
            for extra in ({}, {"dataset_name": "conflicting-selector"}):
                response = await http.post(
                    f"{_BASE}/{queue.id}/push-dataset", json={"dataset_id": str(target), **extra}
                )
                assert response.status_code == 404, response.text
                assert response.json() == {
                    "detail": {
                        "error": {
                            "code": "NOT_FOUND",
                            "message": "Annotation queue or dataset not found",
                            "details": None,
                        }
                    }
                }
                assert str(target) not in response.text
                assert await _database_snapshot(db_session) == before


@pytest.mark.parametrize("change", ["deleted", "workspace"])
async def test_explicit_dataset_rechecked_before_materializing(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    """The service hides a target that becomes invalid after the API precheck."""
    ws, _ = await _workspace_with_role(db_session, "ws-target-race", WorkspaceRole.VIEWER)
    other, _ = await _workspace_with_role(db_session, "ws-target-race-other", WorkspaceRole.VIEWER)
    queue = await _seed_queue(db_session, ws.id)
    dataset = await _seed_dataset(db_session, ws.id, "changing-target")
    before = await _database_snapshot(db_session)
    original = AnnotationService.push_to_dataset

    async def change_target(self: AnnotationService, *args: Any, **kwargs: Any):
        values = {"deleted": True} if change == "deleted" else {"workspace_id": other.id}
        await self.db.execute(
            update(EvaluationDatasetModel)
            .where(EvaluationDatasetModel.id == dataset.id)
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(AnnotationService, "push_to_dataset", change_target)
    async with _client_as(client, _member_as(ws, WorkspaceRole.EDITOR)) as http:
        response = await http.post(f"{_BASE}/{queue.id}/push-dataset", json={"dataset_id": str(dataset.id)})
    assert response.status_code == 404, response.text
    assert response.json()["detail"]["error"]["message"] == "Annotation queue or dataset not found"
    assert await _database_snapshot(db_session) == before


@pytest.mark.parametrize(
    "invalid",
    ["child", "running", "item_type", "no_labels", "wrong_label_type", "error_label", "deleted_label", "foreign_label"],
)
async def test_ineligible_sources_skipped_before_event_reads(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    invalid: str,
) -> None:
    """Malformed or unannotated completed items cannot read events or materialize."""
    ws, _ = await _workspace_with_role(db_session, "ws-source-boundary", WorkspaceRole.VIEWER)
    queue = await _seed_queue(db_session, ws.id)
    trace = await _seed_trace(db_session, ws.id)
    item = await _seed_completed_item(db_session, queue, trace)
    if invalid != "no_labels":
        score = await _seed_human_score(db_session, ws.id, trace.id)
        if invalid == "wrong_label_type":
            score.target_type = "session"
        elif invalid == "error_label":
            score.status = "error"
        elif invalid == "deleted_label":
            score.deleted = True
        elif invalid == "foreign_label":
            other, _ = await _workspace_with_role(db_session, "ws-label-other", WorkspaceRole.VIEWER)
            score.workspace_id = other.id
    if invalid == "child":
        trace.type = "span"
    elif invalid == "running":
        trace.status = "started"
    elif invalid == "item_type":
        item.target_type = "session"
    await db_session.commit()
    await _install_event_store(monkeypatch, [trace])

    async def unexpected_events(*args: Any, **kwargs: Any):
        raise AssertionError("Ineligible source must not read trace events")

    monkeypatch.setattr(InMemoryEventStore, "get_events", unexpected_events)
    async with _client_as(client, _member_as(ws, WorkspaceRole.EDITOR)) as http:
        response = await http.post(f"{_BASE}/{queue.id}/push-dataset", json={"dataset_name": "invalid-source"})
    assert response.status_code == 200, response.text
    assert response.json()["created"] == 0 and response.json()["skipped"] == 1
    assert (await _database_snapshot(db_session))[4] == []


@pytest.mark.parametrize("first", ["annotation", "backflow"])
async def test_real_cross_path_pushes_are_idempotent(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    first: str,
) -> None:
    """Both materializers run in either order without duplicating the dataset trace."""
    ws, _ = await _workspace_with_role(db_session, "ws-cross-path", WorkspaceRole.VIEWER)
    queue = await _seed_queue(db_session, ws.id)
    trace = await _seed_trace(db_session, ws.id)
    await _seed_completed_item(db_session, queue, trace)
    await _seed_human_score(db_session, ws.id, trace.id)
    dataset = await _seed_dataset(db_session, ws.id, "cross-path-dataset")
    task = EvaluationTaskModel(name="online-source", task_type="online", workspace_id=ws.id)
    db_session.add(task)
    await db_session.commit()
    score = await _seed_automated_score(db_session, ws.id, trace.id)
    score.task_id = task.id
    await db_session.commit()
    await _install_event_store(monkeypatch, [trace])
    service = BackflowService(db_session, event_store=await AnnotationService(db_session)._get_event_store())
    rule = await service.create_rule(
        EvaluationBackflowRuleCreateSchema(
            name="cross-path-rule",
            task_id=task.id,
            dataset_id=dataset.id,
            filters=[{"metric_name": "helpfulness", "max_score": 0.5}],
        ),
        workspace_id=ws.id,
    )
    await db_session.commit()

    async with _client_as(client, _member_as(ws, WorkspaceRole.EDITOR)) as http:
        if first == "backflow":
            automatic = await service.run_rule(rule.id, workspace_id=ws.id)
            await db_session.commit()
        response = await http.post(f"{_BASE}/{queue.id}/push-dataset", json={"dataset_id": str(dataset.id)})
        assert response.status_code == 200, response.text
        if first == "annotation":
            automatic = await service.run_rule(rule.id, workspace_id=ws.id)
            await db_session.commit()
    assert response.json()["created"] == (1 if first == "annotation" else 0)
    assert response.json()["skipped"] == (0 if first == "annotation" else 1)
    assert automatic["created"] == (1 if first == "backflow" else 0)
    assert automatic["skipped"] == (0 if first == "backflow" else 1)
    rows = (await _database_snapshot(db_session))[4]
    assert len(rows) == 1
    assert rows[0]["metadata"][first]["trace_id"] == str(trace.id)
