"""Evaluation task management write access boundary (step10).

Covers the shared write boundary on the five evaluation task
management endpoints (create, update, delete, enable, disable):

- Role floor: EDITOR and above mutate tasks in their workspace; VIEWER
  and role-less membership get 403 FORBIDDEN before the write service
  runs.
- Workspace requirement: creation requires an explicit active workspace
  — even for system scope — so the model's zero-UUID workspace default
  can never silently bind a row; workspace-less callers get 403
  NO_WORKSPACE.
- Ownership before role: a missing, soft-deleted, or cross-workspace
  task is the same identifier-free 404 for every non-system identity,
  including members holding EDITOR/ADMIN in their own workspace; a
  workspace-less non-system identity 404s before any query.
- System scope on existing tasks: operates on the resolved task's real
  workspace (the pre-boundary behavior was a constant 404 caused by the
  ``None`` equality filter), with the workspace role check waived.
- Read-only denials and preserved semantics: denied requests never call
  the write service nor change task/run/score state, while success
  paths keep config validation, the online-only enable/disable rule,
  and run/score retention after delete.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.auth_context import AuthContext
from hecate.models.evaluation import (
    EvaluationRunModel,
    EvaluationTaskModel,
    EvaluationTaskScoreModel,
)
from hecate.models.organization import OrganizationModel
from hecate.models.workspace import WorkspaceModel
from hecate.models.workspace_member import WorkspaceMemberModel, WorkspaceRole
from hecate.ops.evaluation.tasks.service import EvaluationTaskService
from tests.test_api.test_evaluation_tasks_api import _offline_payload, _online_payload

MUTATIONS = ("update", "delete", "enable", "disable")


@pytest.fixture
def seeded_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make ``stub`` resolve through the service's evaluator lookup."""
    import hecate.ops.evaluation.tasks.service as service_mod

    monkeypatch.setattr(service_mod, "get_evaluator_class", lambda name: object if name == "stub" else None)


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


async def _seed_task(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    task_type: str = "offline",
    status: str = "active",
    deleted: bool = False,
) -> EvaluationTaskModel:
    """Seed a task directly via the ORM with valid type-specific config."""
    if task_type == "online":
        config: dict[str, Any] = {"agent_id": str(uuid.uuid4()), "sampling_rate": 0.5}
    else:
        config = {"dataset_id": str(uuid.uuid4()), "answer_source": "manual"}
    task = EvaluationTaskModel(
        task_type=task_type,
        name=f"boundary-task-{uuid.uuid4().hex[:8]}",
        status=status,
        workspace_id=workspace_id,
        evaluator_configs=["stub"],
        config=config,
    )
    task.deleted = deleted
    db.add(task)
    await db.commit()
    return task


async def _seed_task_run(
    db: AsyncSession,
    task_id: uuid.UUID,
    workspace_id: uuid.UUID,
) -> EvaluationRunModel:
    """Seed a completed run bound to ``task_id`` with its own workspace."""
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    run = EvaluationRunModel(
        task_id=task_id,
        dataset_id=uuid.uuid4(),
        workspace_id=workspace_id,
        evaluator_configs=["stub"],
        status="completed",
        started_at=now,
        completed_at=now,
    )
    db.add(run)
    await db.commit()
    return run


async def _seed_task_score(
    db: AsyncSession,
    task_id: uuid.UUID,
    workspace_id: uuid.UUID,
) -> EvaluationTaskScoreModel:
    """Seed a completed online score bound to the task's workspace."""
    score = EvaluationTaskScoreModel(
        task_id=task_id,
        target_id=uuid.uuid4(),
        metric_name="helpfulness",
        value=0.8,
        source="llm_judge",
        status="completed",
        workspace_id=workspace_id,
    )
    db.add(score)
    await db.commit()
    return score


async def _seed_mutatable_task(db: AsyncSession, workspace_id: uuid.UUID, *, status: str) -> EvaluationTaskModel:
    """Seed an online task with a run and a score, ready for any mutation."""
    task = await _seed_task(db, workspace_id, task_type="online", status=status)
    await _seed_task_run(db, task.id, workspace_id)
    await _seed_task_score(db, task.id, workspace_id)
    return task


async def _database_snapshot(db: AsyncSession) -> list[list[dict[str, Any]]]:
    """Read all persisted task/run/score columns without identity-map caching."""
    snapshots = []
    for model in (EvaluationTaskModel, EvaluationRunModel, EvaluationTaskScoreModel):
        table = model.__table__
        rows = (await db.execute(select(table).order_by(table.c.id))).mappings().all()
        snapshots.append([dict(row) for row in rows])
    return snapshots


async def _task_row(db: AsyncSession, task_id: uuid.UUID) -> dict[str, Any]:
    """Read one task row without ORM identity-map caching."""
    table = EvaluationTaskModel.__table__
    row = (await db.execute(select(table).where(table.c.id == task_id))).mappings().one()
    return dict(row)


def _assert_not_found(resp: Any) -> None:
    assert resp.status_code == 404, resp.text
    assert resp.json() == {
        "detail": {"error": {"code": "NOT_FOUND", "message": "Evaluation task not found", "details": None}}
    }


def _assert_forbidden(resp: Any, message: str) -> None:
    assert resp.status_code == 403, resp.text
    assert resp.json() == {"detail": {"error": {"code": "FORBIDDEN", "message": message, "details": None}}}


def _assert_no_workspace(resp: Any) -> None:
    assert resp.status_code == 403, resp.text
    assert resp.json() == {
        "detail": {"error": {"code": "NO_WORKSPACE", "message": "An active workspace is required", "details": None}}
    }


async def _mutate(http: AsyncClient, op: str, task_id: uuid.UUID) -> httpx.Response:
    """Issue one task mutation request; ``update`` renames the task."""
    if op == "update":
        return await http.put(f"/api/evaluation/tasks/{task_id}", json={"name": f"renamed-{uuid.uuid4().hex[:6]}"})
    if op == "delete":
        return await http.delete(f"/api/evaluation/tasks/{task_id}")
    if op == "enable":
        return await http.post(f"/api/evaluation/tasks/{task_id}/enable")
    return await http.post(f"/api/evaluation/tasks/{task_id}/disable")


# ---------------------------------------------------------------------------
# Create: role floor, workspace requirement, isolation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("build_payload", [_offline_payload, _online_payload], ids=["offline", "online"])
@pytest.mark.parametrize("role", [WorkspaceRole.EDITOR, WorkspaceRole.ADMIN])
async def test_editor_and_above_create_in_their_workspace(
    client: AsyncClient,
    db_session: AsyncSession,
    seeded_registry: None,
    role: WorkspaceRole,
    build_payload: Callable[..., dict[str, Any]],
) -> None:
    """Allowed roles create tasks bound to their own workspace only."""
    ws, ctx = await _workspace_with_role(db_session, "create-own", role)
    async with _client_as(client, ctx) as http:
        response = await http.post("/api/evaluation/tasks", json=build_payload(name="authored"))
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["workspace_id"] == str(ws.id)
    assert body["status"] == "active"


@pytest.mark.parametrize("build_payload", [_offline_payload, _online_payload], ids=["offline", "online"])
@pytest.mark.parametrize("identity", ["viewer", "role_less", "no_workspace", "system"])
async def test_create_denials_precede_service_and_keep_state(
    client: AsyncClient,
    db_session: AsyncSession,
    seeded_registry: None,
    monkeypatch: pytest.MonkeyPatch,
    identity: str,
    build_payload: Callable[..., dict[str, Any]],
) -> None:
    """Denied identities never reach the write service or persist a row.

    The unchanged snapshot is the regression for the pre-boundary gap
    where a workspace-less create silently bound the row via the model's
    zero-UUID workspace default.
    """
    ws, _ = await _workspace_with_role(db_session, "create-deny", WorkspaceRole.EDITOR)
    if identity == "viewer":
        ctx = _member_as(ws, WorkspaceRole.VIEWER)
    elif identity == "role_less":
        ctx = _member_as(ws, None)
    elif identity == "no_workspace":
        ctx = _no_workspace_context()
    else:
        ctx = _system_context()
    before = await _database_snapshot(db_session)

    async def unexpected_create(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("Denied identities must not call the task write service")

    with monkeypatch.context() as patch:
        patch.setattr(EvaluationTaskService, "create_task", unexpected_create)
        async with _client_as(client, ctx) as http:
            response = await http.post("/api/evaluation/tasks", json=build_payload(name="denied"))
    if identity in {"viewer", "role_less"}:
        expected = "Editor role required" if identity == "viewer" else "Workspace membership required"
        _assert_forbidden(response, expected)
    else:
        _assert_no_workspace(response)
    assert await _database_snapshot(db_session) == before


async def test_create_keeps_configuration_validation(
    client: AsyncClient,
    db_session: AsyncSession,
    seeded_registry: None,
) -> None:
    """Configuration validation still rejects broken payloads after the gate."""
    ws, ctx = await _workspace_with_role(db_session, "create-validation", WorkspaceRole.EDITOR)
    offline_without_dataset = _offline_payload(name="broken-offline")
    offline_without_dataset.pop("dataset_id")
    unknown_evaluator = _online_payload(name="broken-online", evaluators=["missing-evaluator"])
    async with _client_as(client, ctx) as http:
        for payload in (offline_without_dataset, unknown_evaluator):
            response = await http.post("/api/evaluation/tasks", json=payload)
            assert response.status_code == 400, response.text
            assert response.json()["detail"]["error"]["code"] == "INVALID_REQUEST"


# ---------------------------------------------------------------------------
# Mutations of existing tasks: role floor, ownership, isolation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("op", MUTATIONS)
@pytest.mark.parametrize("role", [WorkspaceRole.EDITOR, WorkspaceRole.ADMIN])
async def test_editor_and_above_mutate_own_workspace_task(
    client: AsyncClient,
    db_session: AsyncSession,
    seeded_registry: None,
    op: str,
    role: WorkspaceRole,
) -> None:
    """Allowed roles complete every mutation on their workspace's task."""
    ws, ctx = await _workspace_with_role(db_session, "mutate-own", role)
    status = "disabled" if op == "enable" else "active"
    task = await _seed_mutatable_task(db_session, ws.id, status=status)
    async with _client_as(client, ctx) as http:
        response = await _mutate(http, op, task.id)
    if op == "delete":
        assert response.status_code == 204, response.text
        row = await _task_row(db_session, task.id)
        assert row["deleted"] and row["status"] == "disabled"
    else:
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["workspace_id"] == str(ws.id)
        if op == "update":
            assert body["name"].startswith("renamed-")
        else:
            assert body["status"] == ("active" if op == "enable" else "disabled")


@pytest.mark.parametrize("op", MUTATIONS)
@pytest.mark.parametrize("role", [None, WorkspaceRole.VIEWER])
async def test_role_floor_blocks_insufficient_members(
    client: AsyncClient,
    db_session: AsyncSession,
    seeded_registry: None,
    monkeypatch: pytest.MonkeyPatch,
    op: str,
    role: WorkspaceRole | None,
) -> None:
    """VIEWER and role-less membership are 403 before the write service."""
    ws, _ = await _workspace_with_role(db_session, "mutate-floor", WorkspaceRole.EDITOR)
    task = await _seed_mutatable_task(db_session, ws.id, status="active")
    ctx = _member_as(ws, role)
    before = await _database_snapshot(db_session)

    async def unexpected_mutation(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("Denied identities must not call the task write service")

    with monkeypatch.context() as patch:
        for method in ("update_task", "delete_task", "enable_task", "disable_task"):
            patch.setattr(EvaluationTaskService, method, unexpected_mutation)
        async with _client_as(client, ctx) as http:
            response = await _mutate(http, op, task.id)
    expected = "Editor role required" if role is WorkspaceRole.VIEWER else "Workspace membership required"
    _assert_forbidden(response, expected)
    assert await _database_snapshot(db_session) == before


@pytest.mark.parametrize("op", MUTATIONS)
@pytest.mark.parametrize("role", [None, WorkspaceRole.VIEWER, WorkspaceRole.EDITOR, WorkspaceRole.ADMIN])
async def test_cross_workspace_membership_gets_constant_404(
    client: AsyncClient,
    db_session: AsyncSession,
    seeded_registry: None,
    op: str,
    role: WorkspaceRole | None,
) -> None:
    """Ownership resolves before the role: other-workspace members 404.

    Even a caller holding EDITOR/ADMIN in their own workspace receives
    the missing-task 404 for a foreign task — never a role denial that
    would disclose the task's existence.
    """
    ws, _ = await _workspace_with_role(db_session, "mutate-owner", WorkspaceRole.EDITOR)
    other, _ = await _workspace_with_role(db_session, "mutate-other", WorkspaceRole.EDITOR)
    task = await _seed_mutatable_task(db_session, ws.id, status="active")
    before = await _database_snapshot(db_session)

    async with _client_as(client, _member_as(other, role)) as http:
        response = await _mutate(http, op, task.id)
        missing = await _mutate(http, op, uuid.uuid4())
    _assert_not_found(response)
    assert response.json() == missing.json()
    assert str(task.id) not in response.text
    assert await _database_snapshot(db_session) == before


@pytest.mark.parametrize("op", MUTATIONS)
async def test_no_workspace_identity_404s_before_any_query(
    client: AsyncClient,
    db_session: AsyncSession,
    seeded_registry: None,
    monkeypatch: pytest.MonkeyPatch,
    op: str,
) -> None:
    """Workspace-less non-system identities never reach the database."""
    ws, _ = await _workspace_with_role(db_session, "mutate-nowrap", WorkspaceRole.EDITOR)
    task = await _seed_mutatable_task(db_session, ws.id, status="active")

    async def unexpected_query(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("Workspace-less identities must be denied before any query")

    with monkeypatch.context() as patch:
        # Requests use their own session, so guard the class rather than the seeding session.
        patch.setattr(AsyncSession, "execute", unexpected_query)
        async with _client_as(client, _no_workspace_context()) as http:
            response = await _mutate(http, op, task.id)
    _assert_not_found(response)


# ---------------------------------------------------------------------------
# System scope: real-workspace mutations, identifier-free misses
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("op", MUTATIONS)
async def test_system_mutates_the_target_task_in_its_real_workspace(
    client: AsyncClient,
    db_session: AsyncSession,
    seeded_registry: None,
    op: str,
) -> None:
    """System scope completes mutations via the task's real workspace.

    The task lives in a non-default workspace, so success itself rules
    out both fallbacks: a ``None`` equality condition or the zero-UUID
    workspace would make the service's filter match nothing and 404.
    """
    ws, _ = await _workspace_with_role(db_session, "mutate-system", WorkspaceRole.EDITOR)
    status = "disabled" if op == "enable" else "active"
    task = await _seed_mutatable_task(db_session, ws.id, status=status)
    async with _client_as(client, _system_context()) as http:
        response = await _mutate(http, op, task.id)
    if op == "delete":
        assert response.status_code == 204, response.text
        row = await _task_row(db_session, task.id)
        assert row["deleted"] and row["workspace_id"] == ws.id
    else:
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["workspace_id"] == str(ws.id)
        if op == "update":
            assert body["name"].startswith("renamed-")
        else:
            assert body["status"] == ("active" if op == "enable" else "disabled")
        row = await _task_row(db_session, task.id)
        assert row["workspace_id"] == ws.id


@pytest.mark.parametrize("op", MUTATIONS)
async def test_system_on_missing_task_gets_constant_404(
    client: AsyncClient,
    db_session: AsyncSession,
    seeded_registry: None,
    op: str,
) -> None:
    """System scope shares the identifier-free 404 for invisible tasks."""
    async with _client_as(client, _system_context()) as http:
        response = await _mutate(http, op, uuid.uuid4())
    _assert_not_found(response)


@pytest.mark.parametrize("op", MUTATIONS)
@pytest.mark.parametrize("identity", ["role_less", "viewer", "editor", "admin", "system"])
async def test_soft_deleted_tasks_reject_mutations_before_write_service(
    client: AsyncClient,
    db_session: AsyncSession,
    seeded_registry: None,
    monkeypatch: pytest.MonkeyPatch,
    op: str,
    identity: str,
) -> None:
    """Deleted tasks stay invisible to every role and system despite retained results."""
    ws, _ = await _workspace_with_role(db_session, "mutate-deleted", WorkspaceRole.EDITOR)
    task = await _seed_task(db_session, ws.id, task_type="online", status="disabled", deleted=True)
    await _seed_task_run(db_session, task.id, ws.id)
    await _seed_task_score(db_session, task.id, ws.id)
    if identity == "system":
        ctx = _system_context()
    else:
        role = None if identity == "role_less" else WorkspaceRole(identity)
        ctx = _member_as(ws, role)
    before = await _database_snapshot(db_session)

    async def unexpected_mutation(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("Soft-deleted tasks must be rejected before the task write service")

    with monkeypatch.context() as patch:
        for method in ("update_task", "delete_task", "enable_task", "disable_task"):
            patch.setattr(EvaluationTaskService, method, unexpected_mutation)
        async with _client_as(client, ctx) as http:
            response = await _mutate(http, op, task.id)
            missing = await _mutate(http, op, uuid.uuid4())
    _assert_not_found(response)
    _assert_not_found(missing)
    assert response.json() == missing.json()
    assert str(task.id) not in response.text
    assert await _database_snapshot(db_session) == before


# ---------------------------------------------------------------------------
# Semantics preservation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("op", ["delete", "disable"])
@pytest.mark.parametrize("identity", ["editor", "admin", "system"])
async def test_delete_and_disable_retain_runs_and_scores(
    client: AsyncClient,
    db_session: AsyncSession,
    seeded_registry: None,
    op: str,
    identity: str,
) -> None:
    """Delete and disable preserve all produced results for every allowed identity."""
    role = WorkspaceRole.ADMIN if identity == "admin" else WorkspaceRole.EDITOR
    ws, member_ctx = await _workspace_with_role(db_session, "result-retain", role)
    ctx = _system_context() if identity == "system" else member_ctx
    task = await _seed_mutatable_task(db_session, ws.id, status="active")
    before = await _database_snapshot(db_session)
    async with _client_as(client, ctx) as http:
        response = await _mutate(http, op, task.id)
    assert response.status_code == (204 if op == "delete" else 200), response.text
    after = await _database_snapshot(db_session)
    assert after[1] == before[1]  # runs unchanged
    assert after[2] == before[2]  # scores unchanged
    row = await _task_row(db_session, task.id)
    assert row["status"] == "disabled"
    assert row["workspace_id"] == ws.id
    assert row["deleted"] is (op == "delete")
    if op == "delete":
        assert row["deleted_at"] is not None
    else:
        assert row["deleted_at"] is None


@pytest.mark.parametrize("op", ["enable", "disable"])
async def test_enable_disable_stay_online_only(
    client: AsyncClient,
    db_session: AsyncSession,
    seeded_registry: None,
    op: str,
) -> None:
    """The lifecycle rule survives the boundary for visible offline tasks."""
    ws, ctx = await _workspace_with_role(db_session, "lifecycle-offline", WorkspaceRole.EDITOR)
    task = await _seed_task(db_session, ws.id)
    async with _client_as(client, ctx) as http:
        response = await _mutate(http, op, task.id)
    assert response.status_code == 400, response.text
    assert response.json()["detail"]["error"]["code"] == "INVALID_REQUEST"


async def test_update_revalidation_survives_the_boundary(
    client: AsyncClient,
    db_session: AsyncSession,
    seeded_registry: None,
) -> None:
    """A visible task with an invalid merged configuration still 400s."""
    ws, ctx = await _workspace_with_role(db_session, "update-validation", WorkspaceRole.EDITOR)
    task = await _seed_task(db_session, ws.id, task_type="online")
    async with _client_as(client, ctx) as http:
        response = await http.put(f"/api/evaluation/tasks/{task.id}", json={"evaluators": ["missing-evaluator"]})
    assert response.status_code == 400, response.text
    assert response.json()["detail"]["error"]["code"] == "INVALID_REQUEST"
