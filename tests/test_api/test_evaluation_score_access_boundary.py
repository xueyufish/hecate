"""Evaluation score read access boundary (step10).

Covers the shared read boundary on the score query endpoint:

- Workspace scope: a query without ``task_id`` returns only the
  caller's workspace scores; every identity without an active
  workspace — system included — is denied with 403 ``NO_WORKSPACE``
  before any query (the null workspace is never an equality condition).
- Task visibility: a ``task_id`` naming a missing, soft-deleted, or
  cross-workspace task yields the task endpoints' constant
  identifier-free 404 — never an empty successful list — and explicit
  system scope reads a visible task's scores constrained to the task's
  real workspace.
- Role floor: viewer suffices; membership without a role is 403.
- Semantics: manual annotations (``task_id`` NULL rows, ``source=human``),
  disabled-task reads, pagination, ``created_at`` ascending ordering,
  and the existing filters survive the boundary, and rejected requests
  are read-only, verified against table-direct full-column snapshots.
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
from hecate.models.evaluation import EvaluationTaskModel, EvaluationTaskScoreModel
from hecate.models.organization import OrganizationModel
from hecate.models.workspace import WorkspaceModel
from hecate.models.workspace_member import WorkspaceMemberModel, WorkspaceRole


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


async def _seed_task(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    status: str = "active",
    deleted: bool = False,
) -> EvaluationTaskModel:
    """Seed an online task directly via the ORM, without invoking evaluators."""
    task = EvaluationTaskModel(
        task_type="online",
        name=f"score-boundary-task-{uuid.uuid4().hex[:8]}",
        workspace_id=workspace_id,
        evaluator_configs=["stub"],
        status=status,
        config={"agent_id": str(uuid.uuid4()), "sampling_rate": 1.0},
        deleted=deleted,
        deleted_at=datetime.now(UTC) if deleted else None,
    )
    db.add(task)
    await db.commit()
    return task


async def _seed_score(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    task_id: uuid.UUID | None = None,
    metric_name: str = "relevancy",
    value: float = 0.9,
    source: str = "llm_judge",
    status: str = "completed",
    target_id: uuid.UUID | None = None,
    session_id: uuid.UUID | None = None,
    created_at: datetime | None = None,
    deleted: bool = False,
    overrides_score_id: uuid.UUID | None = None,
) -> EvaluationTaskScoreModel:
    """Seed a score with meaningful reasoning and optional human provenance."""
    score = EvaluationTaskScoreModel(
        task_id=task_id,
        target_id=target_id or uuid.uuid4(),
        session_id=session_id or uuid.uuid4(),
        agent_id=uuid.uuid4(),
        metric_name=metric_name,
        value=value,
        reasoning="Persisted evaluation evidence",
        source=source,
        status=status,
        workspace_id=workspace_id,
        deleted=deleted,
        deleted_at=datetime.now(UTC) if deleted else None,
        value_label="good" if source == "human" else None,
        annotator_id=uuid.uuid4() if source == "human" else None,
        overrides_score_id=overrides_score_id,
        reason_code="reviewed" if source == "human" else None,
    )
    if created_at is not None:
        score.created_at = created_at
    db.add(score)
    await db.commit()
    return score


async def _database_snapshot(db: AsyncSession) -> list[list[dict[str, Any]]]:
    """Read every task and score column from storage, bypassing the identity map."""
    snapshots = []
    for model in (EvaluationTaskModel, EvaluationTaskScoreModel):
        table = model.__table__
        rows = (await db.execute(select(table).order_by(table.c.id))).mappings().all()
        snapshots.append([dict(row) for row in rows])
    return snapshots


def _assert_task_not_found(resp: Any) -> None:
    assert resp.status_code == 404, resp.text
    assert resp.json() == {
        "detail": {"error": {"code": "NOT_FOUND", "message": "Evaluation task not found", "details": None}}
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


async def _unexpected_score_query(*args: Any, **kwargs: Any) -> None:
    raise AssertionError("Denied requests must not query scores")


async def _unexpected_database_query(*args: Any, **kwargs: Any) -> None:
    raise AssertionError("Missing workspace must be rejected before any database query")


@pytest.mark.parametrize("role", [WorkspaceRole.VIEWER, WorkspaceRole.EDITOR, WorkspaceRole.ADMIN, "system"])
@pytest.mark.parametrize("task_scoped", [False, True])
async def test_authorized_reads_scope_items_and_total(
    client: AsyncClient, db_session: AsyncSession, role: WorkspaceRole | str, task_scoped: bool
) -> None:
    """Include only permitted live scores, without modifying task or score state."""
    ws, member = await _workspace_with_role(db_session, "ws-one", WorkspaceRole.VIEWER)
    other, _ = await _workspace_with_role(db_session, "ws-two", WorkspaceRole.VIEWER)
    task = await _seed_task(db_session, ws.id)
    other_task = await _seed_task(db_session, ws.id)
    foreign_task = await _seed_task(db_session, other.id)
    own_score = await _seed_score(db_session, ws.id, task_id=task.id)
    other_score = await _seed_score(db_session, ws.id, task_id=other_task.id)
    await _seed_score(db_session, other.id, task_id=foreign_task.id)
    await _seed_score(db_session, other.id, task_id=task.id)
    await _seed_score(db_session, ws.id, task_id=task.id, deleted=True)
    before = await _database_snapshot(db_session)
    ctx = replace(_system_context(), workspace_id=ws.id) if role == "system" else replace(member, role=role)
    expected = {str(own_score.id)} if task_scoped else {str(own_score.id), str(other_score.id)}
    async with _client_as(client, ctx) as http:
        response = await http.get("/api/evaluation/scores", params={"task_id": str(task.id)} if task_scoped else {})
    assert response.status_code == 200, response.text
    assert set(response.json()) == {"items", "total"}
    assert response.json()["total"] == len(expected)
    assert {item["id"] for item in response.json()["items"]} == expected
    assert all(item["workspace_id"] == str(ws.id) for item in response.json()["items"])
    assert await _database_snapshot(db_session) == before


@pytest.mark.parametrize("identity", ["member", "system"])
@pytest.mark.parametrize("filter_name", [None, "session_id", "target_id"])
async def test_missing_workspace_denied_before_any_query(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    identity: str,
    filter_name: str | None,
) -> None:
    """Other filter IDs cannot replace an active workspace on workspace queries."""
    ws, _ = await _workspace_with_role(db_session, "ws-seeded", WorkspaceRole.VIEWER)
    task = await _seed_task(db_session, ws.id)
    await _seed_score(db_session, ws.id, task_id=task.id)
    before = await _database_snapshot(db_session)
    ctx = _system_context() if identity == "system" else _no_workspace_context()
    params = {filter_name: str(uuid.uuid4())} if filter_name else {}
    with monkeypatch.context() as patch:
        patch.setattr(AsyncSession, "execute", _unexpected_database_query)
        async with _client_as(client, ctx) as http:
            response = await http.get("/api/evaluation/scores", params=params)
            _assert_no_workspace(response)
            if identity == "member":
                response = await http.get("/api/evaluation/scores", params={**params, "task_id": str(task.id)})
                _assert_task_not_found(response)
    assert await _database_snapshot(db_session) == before


@pytest.mark.parametrize("task_scoped", [False, True])
async def test_roleless_member_denied_before_score_query(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, task_scoped: bool
) -> None:
    """Visible task and workspace queries require a viewer membership."""
    from hecate.ops.evaluation.tasks.service import EvaluationTaskService

    ws, _ = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.VIEWER)
    task = await _seed_task(db_session, ws.id)
    await _seed_score(db_session, ws.id, task_id=task.id)
    before = await _database_snapshot(db_session)
    monkeypatch.setattr(EvaluationTaskService, "list_scores", _unexpected_score_query)
    async with _client_as(client, _member_as(ws, None)) as http:
        response = await http.get("/api/evaluation/scores", params={"task_id": str(task.id)} if task_scoped else {})
    _assert_forbidden(response)
    assert await _database_snapshot(db_session) == before


@pytest.mark.parametrize("identity", ["viewer", "roleless", "system"])
async def test_invisible_task_resolved_before_role(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, identity: str
) -> None:
    """Missing, deleted, and foreign tasks share one full 404 before any score query."""
    from hecate.ops.evaluation.tasks.service import EvaluationTaskService

    ws, viewer = await _workspace_with_role(db_session, "ws-one", WorkspaceRole.VIEWER)
    other, _ = await _workspace_with_role(db_session, "ws-two", WorkspaceRole.VIEWER)
    deleted = await _seed_task(db_session, ws.id, deleted=True)
    foreign = await _seed_task(db_session, other.id)
    # Wrong-workspace rows must not bypass parent visibility.
    await _seed_score(db_session, ws.id, task_id=foreign.id)
    await _seed_score(db_session, ws.id, task_id=deleted.id)
    before = await _database_snapshot(db_session)
    ctx = {"viewer": viewer, "roleless": _member_as(ws, None), "system": _system_context()}[identity]
    task_ids = [uuid.uuid4(), deleted.id]
    if identity != "system":
        task_ids.append(foreign.id)
    monkeypatch.setattr(EvaluationTaskService, "list_scores", _unexpected_score_query)
    async with _client_as(client, ctx) as http:
        for task_id in task_ids:
            response = await http.get("/api/evaluation/scores", params={"task_id": str(task_id)})
            _assert_task_not_found(response)
            assert str(task_id) not in response.text
            assert await _database_snapshot(db_session) == before


@pytest.mark.parametrize("active_workspace", [False, True])
async def test_system_reads_task_real_workspace(
    client: AsyncClient, db_session: AsyncSession, active_workspace: bool
) -> None:
    """The task's owner overrides both null and unrelated system workspace context."""
    ws, _ = await _workspace_with_role(db_session, "ws-target", WorkspaceRole.VIEWER)
    other, _ = await _workspace_with_role(db_session, "ws-other", WorkspaceRole.VIEWER)
    task = await _seed_task(db_session, ws.id)
    expected = await _seed_score(db_session, ws.id, task_id=task.id)
    await _seed_score(db_session, other.id, task_id=task.id)
    await _seed_score(db_session, uuid.UUID(int=0), task_id=task.id)
    await _seed_score(db_session, ws.id, task_id=task.id, deleted=True)
    other_task = await _seed_task(db_session, ws.id)
    await _seed_score(db_session, ws.id, task_id=other_task.id)
    before = await _database_snapshot(db_session)
    ctx = replace(_system_context(), workspace_id=other.id) if active_workspace else _system_context()
    async with _client_as(client, ctx) as http:
        response = await http.get("/api/evaluation/scores", params={"task_id": str(task.id)})
    assert response.status_code == 200, response.text
    assert response.json()["total"] == 1
    assert [item["id"] for item in response.json()["items"]] == [str(expected.id)]
    assert await _database_snapshot(db_session) == before


async def test_human_annotation_and_disabled_task_compatibility(client: AsyncClient, db_session: AsyncSession) -> None:
    """Raw scores keep human provenance and disabled-task results without reconciliation."""
    ws, viewer = await _workspace_with_role(db_session, "ws-viewer", WorkspaceRole.VIEWER)
    task = await _seed_task(db_session, ws.id, status="disabled")
    automated = await _seed_score(db_session, ws.id, task_id=task.id)
    human = await _seed_score(db_session, ws.id, source="human", overrides_score_id=automated.id)
    before = await _database_snapshot(db_session)
    async with _client_as(client, viewer) as http:
        all_scores = await http.get("/api/evaluation/scores")
        assert all_scores.status_code == 200 and all_scores.json()["total"] == 2
        human_only = await http.get("/api/evaluation/scores", params={"source": "human"})
        assert human_only.status_code == 200 and human_only.json()["total"] == 1
        body = human_only.json()["items"][0]
        assert body["id"] == str(human.id) and body["task_id"] is None
        assert body["annotator_id"] == str(human.annotator_id)
        assert body["overrides_score_id"] == str(automated.id)
        assert body["reason_code"] == "reviewed" and body["value_label"] == "good"
        assert body["reasoning"] == "Persisted evaluation evidence"
        assert set(body) == {
            "id",
            "task_id",
            "target_type",
            "target_id",
            "session_id",
            "agent_id",
            "metric_name",
            "value",
            "value_label",
            "reasoning",
            "source",
            "status",
            "annotator_id",
            "overrides_score_id",
            "reason_code",
            "workspace_id",
            "created_at",
        }
        task_scores = await http.get("/api/evaluation/scores", params={"task_id": str(task.id)})
        assert task_scores.status_code == 200 and task_scores.json()["total"] == 1
        assert task_scores.json()["items"][0]["id"] == str(automated.id)
        no_human = await http.get("/api/evaluation/scores", params={"task_id": str(task.id), "source": "human"})
        assert no_human.status_code == 200 and no_human.json() == {"items": [], "total": 0}
    assert await _database_snapshot(db_session) == before


@pytest.mark.parametrize("system", [False, True])
async def test_filters_pagination_and_explicit_created_order(
    client: AsyncClient, db_session: AsyncSession, system: bool
) -> None:
    """Every filter and total use the same scope; ordering follows timestamps, not values."""
    ws, viewer = await _workspace_with_role(db_session, "ws-viewer", WorkspaceRole.VIEWER)
    task = await _seed_task(db_session, ws.id)
    base = datetime(2025, 1, 1, tzinfo=UTC)
    target_id, session_id = uuid.uuid4(), uuid.uuid4()
    # Insertion order and score values deliberately disagree with timestamp order.
    late = await _seed_score(db_session, ws.id, task_id=task.id, value=0.5, created_at=base + timedelta(days=2))
    early = await _seed_score(
        db_session, ws.id, task_id=task.id, value=0.9, created_at=base, target_id=target_id, session_id=session_id
    )
    middle = await _seed_score(db_session, ws.id, task_id=task.id, value=0.1, created_at=base + timedelta(days=1))
    error = await _seed_score(
        db_session, ws.id, task_id=task.id, status="error", value=-1.0, created_at=base + timedelta(days=3)
    )
    await _seed_score(db_session, ws.id, task_id=task.id, metric_name="factuality")
    human = await _seed_score(db_session, ws.id, source="human", metric_name="factuality")
    before = await _database_snapshot(db_session)
    ctx = _system_context() if system else viewer
    scope = {"task_id": str(task.id)} if system else {}
    async with _client_as(client, ctx) as http:
        probes = [
            ({"metric_name": "relevancy", "status": "completed"}, [early, middle, late], 3),
            ({"metric_name": "relevancy", "page_size": 2}, [early, middle], 4),
            ({"metric_name": "relevancy", "page_size": 2, "page": 2}, [late, error], 4),
            ({"metric_name": "relevancy", "page_size": 2, "page": 3}, [], 4),
            ({"status": "error", "source": "llm_judge"}, [error], 1),
            ({"target_id": str(target_id)}, [early], 1),
            ({"session_id": str(session_id)}, [early], 1),
            ({"target_id": str(target_id), "session_id": str(uuid.uuid4())}, [], 0),
            ({"source": "human"}, [] if system else [human], 0 if system else 1),
        ]
        for params, expected, total in probes:
            response = await http.get("/api/evaluation/scores", params={**scope, **params})
            assert response.status_code == 200, response.text
            assert response.json()["total"] == total
            assert [item["id"] for item in response.json()["items"]] == [str(row.id) for row in expected]
    assert await _database_snapshot(db_session) == before


@pytest.mark.parametrize("task_scoped", [False, True])
async def test_valid_empty_queries_remain_successful(
    client: AsyncClient, db_session: AsyncSession, task_scoped: bool
) -> None:
    """An empty visible scope remains distinct from the 404 for invisible tasks."""
    ws, viewer = await _workspace_with_role(db_session, "ws-empty", WorkspaceRole.VIEWER)
    task = await _seed_task(db_session, ws.id)
    async with _client_as(client, viewer) as http:
        response = await http.get("/api/evaluation/scores", params={"task_id": str(task.id)} if task_scoped else {})
    assert response.status_code == 200 and response.json() == {"items": [], "total": 0}


@pytest.mark.parametrize("params", [{"task_id": "bad-uuid"}, {"page": 0}, {"page_size": 101}, {"status": "invalid"}])
async def test_parameter_validation_stays_422(client: AsyncClient, params: dict[str, Any]) -> None:
    """Authorization does not change the endpoint's existing validation errors."""
    response = await client.get("/api/evaluation/scores", params=params)
    assert response.status_code == 422, response.text
