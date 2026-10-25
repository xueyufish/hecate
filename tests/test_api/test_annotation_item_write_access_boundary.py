"""Annotation item workflow write access boundary (step10).

Covers the four item workflow write endpoints:

- Claim / skip / submit / delete: the item and its parent queue resolve
  through the item read boundary first (existence, soft-delete state,
  workspace ownership, URL queue binding, parent-child workspace
  consistency — all before the role check), then non-system callers need
  an editor floor, and only then do state, assigned-list, and score
  business validations run.
- Invisible resources share one identifier-free item 404 for every
  identity; a workspace-less non-system member is denied before any
  query; explicit system scope keeps its role exemption, operates by
  explicit id in the item's real workspace, and still cannot bypass the
  path binding, soft deletes, parent-child consistency, or business
  rules.
- The service rechecks the path binding (and fresh row state) before
  writing, so a binding or row that changed after the API precheck is
  rejected instead of written through the cached ORM object.
- Human-score upserts stay inside the item's workspace: a same
  annotator/target/metric row in another workspace is never reused or
  updated, while same-workspace cross-queue resubmission keeps its
  upsert semantics and override targets are validated against the real
  workspace.
- Preserved semantics: success responses, 204 deletes, 409 state
  conflicts, 400 business validation, 422 schema shape, assigned-list
  claim rule, attribution fields, and soft deletes.
- Denied writes never modify data (service spies plus table-direct
  full-column snapshots).
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
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
_METRIC_DEFS = [
    {"name": "helpfulness", "data_type": "numeric", "min": 0, "max": 1},
    {"name": "tone", "data_type": "categorical", "categories": ["good", "neutral", "bad"]},
    {"name": "verified", "data_type": "boolean"},
]
_ACTIONS = ("claim", "skip", "submit", "delete")


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


def _member_as(ws: WorkspaceModel, role: WorkspaceRole | None, user_id: uuid.UUID | None = None) -> AuthContext:
    """A fresh identity whose active workspace is ``ws`` (role optional)."""
    return AuthContext(
        user_id=user_id or uuid.uuid4(),
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
    assigned_user_ids: list[uuid.UUID] | None = None,
) -> AnnotationQueueModel:
    queue = AnnotationQueueModel(
        name=f"workflow-queue-{uuid.uuid4().hex[:8]}",
        instructions="Rate the output",
        metric_defs=[dict(d) for d in _METRIC_DEFS],
        assigned_user_ids=[str(u) for u in (assigned_user_ids or [])],
        workspace_id=workspace_id,
        deleted=deleted,
        deleted_at=datetime.now(UTC) if deleted else None,
    )
    db.add(queue)
    await db.commit()
    return queue


async def _seed_trace(db: AsyncSession, workspace_id: uuid.UUID) -> TraceModel:
    session_row = SessionModel(conversation_id=uuid.uuid4(), agent_id=uuid.uuid4(), workspace_id=workspace_id)
    db.add(session_row)
    await db.flush()
    trace = TraceModel(
        trace_id=uuid.uuid4(),
        type="trace",
        name="workflow-target",
        session_id=session_row.id,
        status="completed",
        start_time=datetime.now(UTC),
        end_time=datetime.now(UTC),
    )
    db.add(trace)
    await db.commit()
    return trace


async def _seed_item(
    db: AsyncSession,
    queue: AnnotationQueueModel,
    *,
    status: str = "pending",
    deleted: bool = False,
    workspace_id: uuid.UUID | None = None,
    target_id: uuid.UUID | None = None,
) -> AnnotationQueueItemModel:
    """Seed one item; within a queue each item needs its own trace (unique key)."""
    if target_id is None:
        target_id = (await _seed_trace(db, workspace_id or queue.workspace_id)).id
    item = AnnotationQueueItemModel(
        queue_id=queue.id,
        target_type="trace",
        target_id=target_id,
        status=status,
        added_by=uuid.uuid4(),
        workspace_id=workspace_id or queue.workspace_id,
        deleted=deleted,
        deleted_at=datetime.now(UTC) if deleted else None,
    )
    if status == "claimed":
        item.claimed_by = item.added_by
        item.claimed_at = datetime.now(UTC)
    if status in ("completed", "skipped"):
        item.completed_by = item.added_by
        item.completed_at = datetime.now(UTC)
    db.add(item)
    await db.commit()
    return item


async def _seed_human_score(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    target_id: uuid.UUID,
    annotator_id: uuid.UUID,
    *,
    metric_name: str = "helpfulness",
    value: float = 0.1,
) -> EvaluationTaskScoreModel:
    row = EvaluationTaskScoreModel(
        task_id=None,
        target_type="trace",
        target_id=target_id,
        metric_name=metric_name,
        value=value,
        source="human",
        status="completed",
        annotator_id=annotator_id,
        workspace_id=workspace_id,
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


def _assert_item_not_found(resp: Any) -> None:
    assert resp.status_code == 404, resp.text
    assert resp.json() == {
        "detail": {"error": {"code": "NOT_FOUND", "message": "Annotation queue item not found", "details": None}}
    }


async def _unexpected_write(*args: Any, **kwargs: Any) -> None:
    raise AssertionError("Denied requests must not run item workflow writes")


async def _unexpected_query(*args: Any, **kwargs: Any) -> None:
    raise AssertionError("Denied requests must not query items or queues")


async def _request(http: AsyncClient, action: str, queue_id: uuid.UUID, item_id: uuid.UUID) -> Any:
    """One workflow request against the standard path shape per action."""
    base = f"{_BASE}/{queue_id}/items/{item_id}"
    if action == "delete":
        return await http.delete(base)
    if action == "submit":
        return await http.post(
            f"{base}/submit",
            json={"annotations": [{"metric_name": "helpfulness", "value": 0.5}]},
        )
    return await http.post(f"{base}/{action}")


def _spy_service(monkeypatch: pytest.MonkeyPatch, *actions: str) -> None:
    """Make the named workflow service methods fail if called."""
    from hecate.ops.evaluation.annotation.service import AnnotationService

    for action in actions:
        monkeypatch.setattr(AnnotationService, f"{action}_item", _unexpected_write)


# ---------------------------------------------------------------------------
# Successful workflow writes keep their semantics
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", [WorkspaceRole.EDITOR, WorkspaceRole.ADMIN])
async def test_claim_submit_lifecycle_with_attribution(
    client: AsyncClient, db_session: AsyncSession, role: WorkspaceRole
) -> None:
    """Claim then submit completes the item and persists attributed human scores."""
    ws, _ = await _workspace_with_role(db_session, "ws-lifecycle", WorkspaceRole.VIEWER)
    queue = await _seed_queue(db_session, ws.id)
    trace = await _seed_trace(db_session, ws.id)
    item = await _seed_item(db_session, queue, target_id=trace.id)
    caller = _member_as(ws, role)
    async with _client_as(client, caller) as http:
        claimed = await http.post(f"{_BASE}/{queue.id}/items/{item.id}/claim")
        assert claimed.status_code == 200, claimed.text
        assert claimed.json()["status"] == "claimed"

        submitted = await http.post(
            f"{_BASE}/{queue.id}/items/{item.id}/submit",
            json={
                "annotations": [
                    {"metric_name": "helpfulness", "value": 0.8},
                    {"metric_name": "tone", "value_label": "good"},
                    {"metric_name": "verified", "value_label": "true"},
                ]
            },
        )
        assert submitted.status_code == 200, submitted.text
        assert submitted.json()["status"] == "completed"
    snapshot = await _database_snapshot(db_session)
    item_row = next(row for row in snapshot[1] if row["id"] == item.id)
    assert item_row["status"] == "completed"
    assert item_row["claimed_by"] == caller.user_id
    assert item_row["claimed_at"] is not None
    assert item_row["completed_by"] == caller.user_id
    assert item_row["completed_at"] is not None
    assert item_row["deleted"] == 0
    scores = {row["metric_name"]: row for row in snapshot[2]}
    assert set(scores) == {"helpfulness", "tone", "verified"}
    for row in scores.values():
        assert row["source"] == "human"
        assert row["task_id"] is None
        assert row["annotator_id"] == caller.user_id
        assert row["workspace_id"] == ws.id
        assert row["status"] == "completed"
    assert scores["helpfulness"]["value"] == 0.8
    assert scores["tone"]["value"] == 0.0  # category index of "good"
    assert scores["tone"]["value_label"] == "good"
    assert scores["verified"]["value"] == 1.0


async def test_skip_marks_item_without_scores(client: AsyncClient, db_session: AsyncSession) -> None:
    """Skip moves a claimed item to skipped with attribution and no score rows."""
    ws, _ = await _workspace_with_role(db_session, "ws-skip", WorkspaceRole.VIEWER)
    queue = await _seed_queue(db_session, ws.id)
    trace = await _seed_trace(db_session, ws.id)
    item = await _seed_item(db_session, queue, target_id=trace.id)
    caller = _member_as(ws, WorkspaceRole.EDITOR)
    async with _client_as(client, caller) as http:
        response = await http.post(f"{_BASE}/{queue.id}/items/{item.id}/skip")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "skipped"
    snapshot = await _database_snapshot(db_session)
    item_row = snapshot[1][0]
    assert item_row["status"] == "skipped"
    assert item_row["completed_by"] == caller.user_id
    assert item_row["completed_at"] is not None
    assert snapshot[2] == []  # no human rows from a skip


async def test_delete_soft_deletes_pending_item(client: AsyncClient, db_session: AsyncSession) -> None:
    """Delete returns 204 and soft-deletes only pending or skipped items."""
    ws, _ = await _workspace_with_role(db_session, "ws-delete", WorkspaceRole.VIEWER)
    queue = await _seed_queue(db_session, ws.id)
    trace = await _seed_trace(db_session, ws.id)
    item = await _seed_item(db_session, queue, target_id=trace.id)
    async with _client_as(client, _member_as(ws, WorkspaceRole.EDITOR)) as http:
        response = await http.delete(f"{_BASE}/{queue.id}/items/{item.id}")
    assert response.status_code == 204, response.text
    assert response.content == b""
    snapshot = await _database_snapshot(db_session)
    assert snapshot[1][0]["deleted"] == 1
    assert snapshot[1][0]["deleted_at"] is not None


async def test_assigned_list_claim_rule_preserved(client: AsyncClient, db_session: AsyncSession) -> None:
    """Assigned queues keep their 400 rule for unassigned editors after the role floor."""
    ws, _ = await _workspace_with_role(db_session, "ws-assigned", WorkspaceRole.VIEWER)
    assignee = uuid.uuid4()
    queue = await _seed_queue(db_session, ws.id, assigned_user_ids=[assignee])
    trace = await _seed_trace(db_session, ws.id)
    item = await _seed_item(db_session, queue, target_id=trace.id)
    before = await _database_snapshot(db_session)
    async with _client_as(client, _member_as(ws, WorkspaceRole.EDITOR)) as http:
        denied = await http.post(f"{_BASE}/{queue.id}/items/{item.id}/claim")
    assert denied.status_code == 400, denied.text
    assert denied.json()["detail"]["error"]["code"] == "INVALID_REQUEST"
    assert await _database_snapshot(db_session) == before
    async with _client_as(client, _member_as(ws, WorkspaceRole.EDITOR, user_id=assignee)) as http:
        allowed = await http.post(f"{_BASE}/{queue.id}/items/{item.id}/claim")
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["status"] == "claimed"


@pytest.mark.parametrize(
    ("seed_status", "action", "expected_code"),
    [
        ("claimed", "claim", 409),
        ("completed", "claim", 409),
        ("skipped", "claim", 409),
        ("completed", "skip", 409),
        ("completed", "submit", 409),
        ("skipped", "submit", 409),
        ("claimed", "delete", 409),
        ("completed", "delete", 409),
    ],
)
async def test_state_conflicts_preserved(
    client: AsyncClient, db_session: AsyncSession, seed_status: str, action: str, expected_code: int
) -> None:
    """Out-of-state transitions keep their 409 semantics for authorized callers."""
    ws, _ = await _workspace_with_role(db_session, "ws-conflict", WorkspaceRole.VIEWER)
    queue = await _seed_queue(db_session, ws.id)
    item = await _seed_item(db_session, queue, status=seed_status)
    before = await _database_snapshot(db_session)
    async with _client_as(client, _member_as(ws, WorkspaceRole.EDITOR)) as http:
        response = await _request(http, action, queue.id, item.id)
    assert response.status_code == expected_code, response.text
    assert response.json()["detail"]["error"]["code"] == "CONFLICT"
    assert await _database_snapshot(db_session) == before


@pytest.mark.parametrize(
    "annotations",
    [
        [{"metric_name": "helpfulness", "value": 1.5}],  # outside numeric range
        [{"metric_name": "nope", "value": 0.5}],  # unknown metric
        [{"metric_name": "tone", "value_label": "great"}],  # unknown category
        [{"metric_name": "verified", "value": 1.0}],  # boolean requires value_label
        [
            {"metric_name": "helpfulness", "value": 0.5},
            {"metric_name": "helpfulness", "value": 0.6},
        ],  # duplicate metric
    ],
)
async def test_business_validation_400_preserved(
    client: AsyncClient, db_session: AsyncSession, annotations: list[dict[str, Any]]
) -> None:
    """Value/metric validation keeps its 400 semantics after the role floor."""
    ws, _ = await _workspace_with_role(db_session, "ws-validate", WorkspaceRole.VIEWER)
    queue = await _seed_queue(db_session, ws.id)
    trace = await _seed_trace(db_session, ws.id)
    item = await _seed_item(db_session, queue, target_id=trace.id)
    before = await _database_snapshot(db_session)
    async with _client_as(client, _member_as(ws, WorkspaceRole.EDITOR)) as http:
        response = await http.post(f"{_BASE}/{queue.id}/items/{item.id}/submit", json={"annotations": annotations})
    assert response.status_code == 400, response.text
    assert response.json()["detail"]["error"]["code"] == "INVALID_REQUEST"
    assert await _database_snapshot(db_session) == before


async def test_override_rules_preserved(client: AsyncClient, db_session: AsyncSession) -> None:
    """Override entries keep their reason/justification and target-matching rules."""
    ws, _ = await _workspace_with_role(db_session, "ws-override", WorkspaceRole.VIEWER)
    queue = await _seed_queue(db_session, ws.id)
    trace = await _seed_trace(db_session, ws.id)
    item = await _seed_item(db_session, queue, target_id=trace.id)
    automated = EvaluationTaskScoreModel(
        task_id=None,
        target_type="trace",
        target_id=trace.id,
        metric_name="helpfulness",
        value=0.2,
        source="llm_judge",
        status="completed",
        workspace_id=ws.id,
    )
    db_session.add(automated)
    await db_session.commit()
    base = f"{_BASE}/{queue.id}/items/{item.id}/submit"
    async with _client_as(client, _member_as(ws, WorkspaceRole.EDITOR)) as http:
        missing_reason = await http.post(
            f"{_BASE}/{queue.id}/items/{item.id}/submit",
            json={
                "annotations": [{"metric_name": "helpfulness", "value": 0.9, "overrides_score_id": str(uuid.uuid4())}]
            },
        )
        assert missing_reason.status_code == 400, missing_reason.text
        wrong_target = await http.post(
            base,
            json={
                "annotations": [
                    {
                        "metric_name": "helpfulness",
                        "value": 0.9,
                        "overrides_score_id": str(uuid.uuid4()),
                        "reason_code": "judge_wrong_fact",
                        "justification": "the cited passage does support the claim",
                    }
                ]
            },
        )
        assert wrong_target.status_code == 400, wrong_target.text
        valid = await http.post(
            base,
            json={
                "annotations": [
                    {
                        "metric_name": "helpfulness",
                        "value": 0.9,
                        "overrides_score_id": str(automated.id),
                        "reason_code": "judge_wrong_fact",
                        "justification": "the cited passage does support the claim",
                    }
                ]
            },
        )
        assert valid.status_code == 200, valid.text
    snapshot = await _database_snapshot(db_session)
    human_row = next(row for row in snapshot[2] if row["source"] == "human")
    assert human_row["overrides_score_id"] == automated.id
    automated_row = next(row for row in snapshot[2] if row["id"] == automated.id)
    assert automated_row["value"] == 0.2  # automated row untouched


async def test_override_target_validated_in_real_workspace(client: AsyncClient, db_session: AsyncSession) -> None:
    """A foreign-workspace automated row cannot serve as an override target."""
    ws, _ = await _workspace_with_role(db_session, "ws-ov-real", WorkspaceRole.VIEWER)
    other, _ = await _workspace_with_role(db_session, "ws-ov-foreign", WorkspaceRole.VIEWER)
    queue = await _seed_queue(db_session, ws.id)
    trace = await _seed_trace(db_session, ws.id)
    item = await _seed_item(db_session, queue, target_id=trace.id)
    foreign_row = EvaluationTaskScoreModel(
        task_id=None,
        target_type="trace",
        target_id=trace.id,  # same target id, but owned by another workspace
        metric_name="helpfulness",
        value=0.4,
        source="llm_judge",
        status="completed",
        workspace_id=other.id,
    )
    db_session.add(foreign_row)
    await db_session.commit()
    before = await _database_snapshot(db_session)
    async with _client_as(client, _member_as(ws, WorkspaceRole.EDITOR)) as http:
        response = await http.post(
            f"{_BASE}/{queue.id}/items/{item.id}/submit",
            json={
                "annotations": [
                    {
                        "metric_name": "helpfulness",
                        "value": 0.9,
                        "overrides_score_id": str(foreign_row.id),
                        "reason_code": "judge_wrong_fact",
                        "justification": "foreign row is not an eligible target",
                    }
                ]
            },
        )
    assert response.status_code == 400, response.text
    assert await _database_snapshot(db_session) == before


# ---------------------------------------------------------------------------
# Role floor: editor for non-system callers, after visibility
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", [WorkspaceRole.VIEWER, None])
@pytest.mark.parametrize("action", _ACTIONS)
async def test_below_editor_denied_before_service(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    role: WorkspaceRole | None,
    action: str,
) -> None:
    """Viewers and role-less members never reach the workflow service."""
    ws, _ = await _workspace_with_role(db_session, "ws-lowrole", WorkspaceRole.VIEWER)
    queue = await _seed_queue(db_session, ws.id)
    trace = await _seed_trace(db_session, ws.id)
    item = await _seed_item(db_session, queue, target_id=trace.id)
    before = await _database_snapshot(db_session)
    _spy_service(monkeypatch, "claim", "skip", "submit", "delete")
    async with _client_as(client, _member_as(ws, role)) as http:
        response = await _request(http, action, queue.id, item.id)
        # Schema shape violations keep their 422 regardless of the role floor.
        schema = await http.post(f"{_BASE}/{queue.id}/items/{item.id}/submit", json={"annotations": []})
    if role is None:
        _assert_forbidden(response)
    else:
        _assert_forbidden_viewer(response)
    assert schema.status_code == 422, schema.text
    assert await _database_snapshot(db_session) == before


async def test_role_precedes_state_and_score_validation(client: AsyncClient, db_session: AsyncSession) -> None:
    """A viewer on an out-of-state item gets 403, not the 409/400 business error."""
    ws, _ = await _workspace_with_role(db_session, "ws-order", WorkspaceRole.VIEWER)
    queue = await _seed_queue(db_session, ws.id)
    completed = await _seed_item(db_session, queue, status="completed")
    pending = await _seed_item(db_session, queue)
    before = await _database_snapshot(db_session)
    async with _client_as(client, _member_as(ws, WorkspaceRole.VIEWER)) as http:
        claim_completed = await http.post(f"{_BASE}/{queue.id}/items/{completed.id}/claim")
        submit_bad_value = await http.post(
            f"{_BASE}/{queue.id}/items/{pending.id}/submit",
            json={"annotations": [{"metric_name": "helpfulness", "value": 9.9}]},
        )
    _assert_forbidden_viewer(claim_completed)
    _assert_forbidden_viewer(submit_bad_value)
    assert await _database_snapshot(db_session) == before


# ---------------------------------------------------------------------------
# Visibility before role: one identifier-free 404
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("action", _ACTIONS)
@pytest.mark.parametrize("identity", ["viewer", "roleless", "editor", "admin", "system"])
async def test_invisible_item_404_before_role(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    action: str,
    identity: str,
) -> None:
    """Missing, deleted, foreign, unbound, and inconsistent items share one 404."""
    ws, viewer = await _workspace_with_role(db_session, "ws-invisible", WorkspaceRole.VIEWER)
    other, _ = await _workspace_with_role(db_session, "ws-invisible-other", WorkspaceRole.VIEWER)
    queue = await _seed_queue(db_session, ws.id)
    foreign_queue = await _seed_queue(db_session, other.id)
    trace = await _seed_trace(db_session, ws.id)
    await _seed_item(db_session, queue, target_id=trace.id)  # a live item exists too
    deleted_item = await _seed_item(db_session, queue, deleted=True)
    foreign_item = await _seed_item(db_session, foreign_queue, target_id=trace.id)
    wrong_queue = await _seed_queue(db_session, ws.id)  # same workspace, other queue
    rebound = await _seed_item(db_session, queue)
    deleted_queue = await _seed_queue(db_session, ws.id, deleted=True)
    orphan = await _seed_item(db_session, deleted_queue, target_id=trace.id)
    mismatch = await _seed_item(db_session, queue, workspace_id=other.id)
    ctx = {
        "viewer": viewer,
        "roleless": _member_as(ws, None),
        "editor": _member_as(ws, WorkspaceRole.EDITOR),
        "admin": _member_as(ws, WorkspaceRole.ADMIN),
        "system": _system_context(),
    }[identity]
    before = await _database_snapshot(db_session)
    _spy_service(monkeypatch, "claim", "skip", "submit", "delete")
    cases = [
        (uuid.uuid4(), uuid.uuid4()),  # both missing
        (queue.id, uuid.uuid4()),  # missing item in a live queue
        (queue.id, deleted_item.id),  # soft-deleted item
        (wrong_queue.id, rebound.id),  # URL queue does not own the item
        (deleted_queue.id, orphan.id),  # parent queue soft-deleted
        (queue.id, mismatch.id),  # parent-child workspace mismatch
    ]
    if identity != "system":
        cases.append((foreign_queue.id, foreign_item.id))  # foreign workspace pair
    async with _client_as(client, ctx) as http:
        for case_queue, case_item in cases:
            response = await _request(http, action, case_queue, case_item)
            _assert_item_not_found(response)
            assert str(case_item) not in response.text
            assert str(case_queue) not in response.text
            assert await _database_snapshot(db_session) == before


@pytest.mark.parametrize("action", _ACTIONS)
async def test_member_without_workspace_denied_before_query(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    action: str,
) -> None:
    """A workspace-less member gets the item 404 before any database query."""
    ws, _ = await _workspace_with_role(db_session, "ws-nows", WorkspaceRole.VIEWER)
    queue = await _seed_queue(db_session, ws.id)
    trace = await _seed_trace(db_session, ws.id)
    item = await _seed_item(db_session, queue, target_id=trace.id)
    before = await _database_snapshot(db_session)
    with monkeypatch.context() as patch:
        patch.setattr(AsyncSession, "execute", _unexpected_query)
        async with _client_as(client, _no_workspace_context()) as http:
            response = await _request(http, action, queue.id, item.id)
    _assert_item_not_found(response)
    assert await _database_snapshot(db_session) == before


async def test_visibility_precedes_role_for_every_action(client: AsyncClient, db_session: AsyncSession) -> None:
    """A viewer naming an invisible item gets the 404, not the 403."""
    ws, _ = await _workspace_with_role(db_session, "ws-precedence", WorkspaceRole.VIEWER)
    queue = await _seed_queue(db_session, ws.id)
    item = await _seed_item(db_session, queue, deleted=True)
    before = await _database_snapshot(db_session)
    async with _client_as(client, _member_as(ws, WorkspaceRole.VIEWER)) as http:
        for action in _ACTIONS:
            response = await _request(http, action, queue.id, item.id)
            _assert_item_not_found(response)
    assert await _database_snapshot(db_session) == before


async def test_system_writes_through_real_workspace(client: AsyncClient, db_session: AsyncSession) -> None:
    """System scope operates by explicit id in the item's real workspace."""
    ws, _ = await _workspace_with_role(db_session, "ws-sysreal", WorkspaceRole.VIEWER)
    queue = await _seed_queue(db_session, ws.id)
    claim_target = await _seed_item(db_session, queue)
    skip_target = await _seed_item(db_session, queue)
    submit_target = await _seed_item(db_session, queue)
    delete_target = await _seed_item(db_session, queue)
    system = _system_context()
    async with _client_as(client, system) as http:
        claimed = await http.post(f"{_BASE}/{queue.id}/items/{claim_target.id}/claim")
        assert claimed.status_code == 200, claimed.text
        assert claimed.json()["status"] == "claimed"
        assert claimed.json()["claimed_by"] == str(system.user_id)

        skipped = await http.post(f"{_BASE}/{queue.id}/items/{skip_target.id}/skip")
        assert skipped.status_code == 200, skipped.text
        assert skipped.json()["status"] == "skipped"

        submitted = await http.post(
            f"{_BASE}/{queue.id}/items/{submit_target.id}/submit",
            json={"annotations": [{"metric_name": "helpfulness", "value": 0.6}]},
        )
        assert submitted.status_code == 200, submitted.text
        assert submitted.json()["status"] == "completed"

        deleted = await http.delete(f"{_BASE}/{queue.id}/items/{delete_target.id}")
        assert deleted.status_code == 204, deleted.text
    snapshot = await _database_snapshot(db_session)
    statuses = {row["id"]: row for row in snapshot[1]}
    assert statuses[claim_target.id]["status"] == "claimed"
    assert statuses[skip_target.id]["status"] == "skipped"
    assert statuses[submit_target.id]["status"] == "completed"
    assert statuses[delete_target.id]["deleted"] == 1
    assert len(snapshot[2]) == 1
    assert snapshot[2][0]["workspace_id"] == ws.id
    assert snapshot[2][0]["annotator_id"] == system.user_id


async def test_system_cannot_bypass_binding_or_business_rules(client: AsyncClient, db_session: AsyncSession) -> None:
    """System keeps the path binding, state, and assigned-list rules."""
    ws, _ = await _workspace_with_role(db_session, "ws-sysrules", WorkspaceRole.VIEWER)
    assignee = uuid.uuid4()
    queue = await _seed_queue(db_session, ws.id, assigned_user_ids=[assignee])
    other_queue = await _seed_queue(db_session, ws.id)
    item = await _seed_item(db_session, queue)
    completed = await _seed_item(db_session, queue, status="completed")
    before = await _database_snapshot(db_session)
    system = _system_context()
    async with _client_as(client, system) as http:
        wrong_path = await http.post(f"{_BASE}/{other_queue.id}/items/{item.id}/claim")
        _assert_item_not_found(wrong_path)
        unassigned = await http.post(f"{_BASE}/{queue.id}/items/{item.id}/claim")
        assert unassigned.status_code == 400, unassigned.text
        out_of_state = await http.post(f"{_BASE}/{queue.id}/items/{completed.id}/claim")
        assert out_of_state.status_code == 409, out_of_state.text
        submit_completed = await http.post(
            f"{_BASE}/{queue.id}/items/{completed.id}/submit",
            json={"annotations": [{"metric_name": "helpfulness", "value": 9.9}]},
        )
        assert submit_completed.status_code == 409, submit_completed.text
    assert await _database_snapshot(db_session) == before


# ---------------------------------------------------------------------------
# Service recheck: the API precheck cache cannot authorize a write
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("action", _ACTIONS)
async def test_rebound_item_rejected_after_api_precheck(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    action: str,
) -> None:
    """A changed queue binding cannot reuse the ORM object cached by the precheck."""
    from hecate.ops.api import evaluation_annotations as api

    ws, _ = await _workspace_with_role(db_session, "ws-rebind", WorkspaceRole.VIEWER)
    requested = await _seed_queue(db_session, ws.id)
    alternate = await _seed_queue(db_session, ws.id)
    trace = await _seed_trace(db_session, ws.id)
    item = await _seed_item(db_session, requested, target_id=trace.id)
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
    async with _client_as(client, _member_as(ws, WorkspaceRole.EDITOR)) as http:
        response = await _request(http, action, requested.id, item.id)
    _assert_item_not_found(response)
    # The simulated change is rolled back by the failed request's transaction.
    assert await _database_snapshot(db_session) == before


@pytest.mark.parametrize("action", _ACTIONS)
async def test_item_deleted_after_api_precheck_rejected(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    action: str,
) -> None:
    """A row soft-deleted after the precheck fails the service re-read."""
    from hecate.ops.api import evaluation_annotations as api

    ws, _ = await _workspace_with_role(db_session, "ws-late-delete", WorkspaceRole.VIEWER)
    queue = await _seed_queue(db_session, ws.id)
    trace = await _seed_trace(db_session, ws.id)
    item = await _seed_item(db_session, queue, target_id=trace.id)
    before = await _database_snapshot(db_session)
    original = api._get_item_for_read

    async def delete_after_precheck(ctx: AuthContext, queue_id: uuid.UUID, item_id: uuid.UUID, db: AsyncSession):
        resolved = await original(ctx, queue_id, item_id, db)
        await db.execute(
            update(AnnotationQueueItemModel)
            .where(AnnotationQueueItemModel.id == item_id)
            .values(deleted=True)
            .execution_options(synchronize_session=False)
        )
        return resolved

    monkeypatch.setattr(api, "_get_item_for_read", delete_after_precheck)
    async with _client_as(client, _member_as(ws, WorkspaceRole.EDITOR)) as http:
        response = await _request(http, action, queue.id, item.id)
    _assert_item_not_found(response)
    assert await _database_snapshot(db_session) == before


@pytest.mark.parametrize("action", _ACTIONS)
async def test_parent_deleted_after_api_precheck_rejected(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    action: str,
) -> None:
    """A parent soft-deleted after the precheck cannot authorize an item write."""
    from hecate.ops.api import evaluation_annotations as api

    ws, _ = await _workspace_with_role(db_session, "ws-parent-delete", WorkspaceRole.VIEWER)
    queue = await _seed_queue(db_session, ws.id)
    item = await _seed_item(db_session, queue)
    before = await _database_snapshot(db_session)
    original = api._get_item_for_read

    async def delete_parent_after_precheck(ctx: AuthContext, queue_id: uuid.UUID, item_id: uuid.UUID, db: AsyncSession):
        resolved = await original(ctx, queue_id, item_id, db)
        await db.execute(
            update(AnnotationQueueModel)
            .where(AnnotationQueueModel.id == queue_id)
            .values(deleted=True)
            .execution_options(synchronize_session=False)
        )
        return resolved

    monkeypatch.setattr(api, "_get_item_for_read", delete_parent_after_precheck)
    async with _client_as(client, _member_as(ws, WorkspaceRole.EDITOR)) as http:
        response = await _request(http, action, queue.id, item.id)
    _assert_item_not_found(response)
    assert await _database_snapshot(db_session) == before


@pytest.mark.parametrize("identity", ["editor", "system"])
@pytest.mark.parametrize("action", ["claim", "submit"])
async def test_queue_rules_reloaded_after_api_precheck(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    identity: str,
    action: str,
) -> None:
    """Writes validate current queue rules instead of the precheck's cached rules."""
    from hecate.ops.api import evaluation_annotations as api

    ws, _ = await _workspace_with_role(db_session, "ws-rules-refresh", WorkspaceRole.VIEWER)
    caller = _system_context() if identity == "system" else _member_as(ws, WorkspaceRole.EDITOR)
    queue = await _seed_queue(db_session, ws.id, assigned_user_ids=[caller.user_id])
    item = await _seed_item(db_session, queue)
    before = await _database_snapshot(db_session)
    original = api._get_item_for_read

    async def change_rules_after_precheck(ctx: AuthContext, queue_id: uuid.UUID, item_id: uuid.UUID, db: AsyncSession):
        resolved = await original(ctx, queue_id, item_id, db)
        if action == "claim":
            values = {"assigned_user_ids": [str(uuid.uuid4())]}
        else:
            metric_defs = [dict(metric) for metric in _METRIC_DEFS]
            metric_defs[0]["max"] = 0.1
            values = {"metric_defs": metric_defs}
        await db.execute(
            update(AnnotationQueueModel)
            .where(AnnotationQueueModel.id == queue_id)
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        return resolved

    monkeypatch.setattr(api, "_get_item_for_read", change_rules_after_precheck)
    async with _client_as(client, caller) as http:
        response = await _request(http, action, queue.id, item.id)
    assert response.status_code == 400, response.text
    assert response.json()["detail"]["error"]["code"] == "INVALID_REQUEST"
    assert await _database_snapshot(db_session) == before


# ---------------------------------------------------------------------------
# Human-score upsert stays inside the item's workspace
# ---------------------------------------------------------------------------


async def test_upsert_never_reuses_other_workspace_rows(client: AsyncClient, db_session: AsyncSession) -> None:
    """A foreign same-annotator/target/metric row is not updated by this submit."""
    ws, _ = await _workspace_with_role(db_session, "ws-upsert-real", WorkspaceRole.VIEWER)
    other, _ = await _workspace_with_role(db_session, "ws-upsert-foreign", WorkspaceRole.VIEWER)
    queue = await _seed_queue(db_session, ws.id)
    trace = await _seed_trace(db_session, ws.id)
    item = await _seed_item(db_session, queue, target_id=trace.id)
    caller = _member_as(ws, WorkspaceRole.EDITOR)
    interference = await _seed_human_score(db_session, other.id, trace.id, caller.user_id, value=0.1)
    async with _client_as(client, caller) as http:
        response = await http.post(
            f"{_BASE}/{queue.id}/items/{item.id}/submit",
            json={"annotations": [{"metric_name": "helpfulness", "value": 0.7}]},
        )
    assert response.status_code == 200, response.text
    snapshot = await _database_snapshot(db_session)
    foreign_row = next(row for row in snapshot[2] if row["id"] == interference.id)
    assert foreign_row["value"] == 0.1  # untouched
    assert foreign_row["workspace_id"] == other.id
    local_rows = [row for row in snapshot[2] if row["workspace_id"] == ws.id and row["source"] == "human"]
    assert len(local_rows) == 1
    assert local_rows[0]["value"] == 0.7
    assert local_rows[0]["annotator_id"] == caller.user_id


async def test_same_workspace_cross_queue_upsert_preserved(client: AsyncClient, db_session: AsyncSession) -> None:
    """Same-workspace resubmission via another queue upserts, not duplicates."""
    ws, _ = await _workspace_with_role(db_session, "ws-upsert-same", WorkspaceRole.VIEWER)
    first_queue = await _seed_queue(db_session, ws.id)
    second_queue = await _seed_queue(db_session, ws.id)
    trace = await _seed_trace(db_session, ws.id)
    first_item = await _seed_item(db_session, first_queue, target_id=trace.id)
    second_item = await _seed_item(db_session, second_queue, target_id=trace.id)
    caller = _member_as(ws, WorkspaceRole.EDITOR)
    async with _client_as(client, caller) as http:
        first = await http.post(
            f"{_BASE}/{first_queue.id}/items/{first_item.id}/submit",
            json={"annotations": [{"metric_name": "helpfulness", "value": 0.4}]},
        )
        assert first.status_code == 200, first.text
        second = await http.post(
            f"{_BASE}/{second_queue.id}/items/{second_item.id}/submit",
            json={"annotations": [{"metric_name": "helpfulness", "value": 0.6}]},
        )
        assert second.status_code == 200, second.text
    snapshot = await _database_snapshot(db_session)
    human_rows = [row for row in snapshot[2] if row["source"] == "human"]
    assert len(human_rows) == 1  # upserted, not duplicated
    assert human_rows[0]["value"] == 0.6
    statuses = {row["id"]: row["status"] for row in snapshot[1]}
    assert statuses[first_item.id] == "completed"
    assert statuses[second_item.id] == "completed"


async def test_zero_uuid_workspace_is_a_legal_scope_for_workflow(client: AsyncClient, db_session: AsyncSession) -> None:
    """The zero-UUID workspace stays a valid explicit scope for item writes."""
    zero_ws = uuid.UUID(int=0)
    queue = await _seed_queue(db_session, zero_ws)
    trace = await _seed_trace(db_session, zero_ws)
    item = await _seed_item(db_session, queue, target_id=trace.id)
    editor = AuthContext(
        user_id=uuid.uuid4(),
        org_id=None,
        workspace_id=zero_ws,
        role=WorkspaceRole.EDITOR,
        auth_method="jwt",
        api_key_scope=None,
    )
    async with _client_as(client, editor) as http:
        claimed = await http.post(f"{_BASE}/{queue.id}/items/{item.id}/claim")
        assert claimed.status_code == 200, claimed.text
        assert claimed.json()["status"] == "claimed"
