"""Evaluation task read access boundary (step10).

Covers the shared read boundary on the three evaluation task read
endpoints (listing, detail, run listing):

- Workspace ownership: listing items and ``total`` count only the
  caller's workspace tasks; detail and run listing surface a missing,
  soft-deleted, or cross-workspace task as the *same* 404 body
  ("Evaluation task not found") that echoes no identifier.
- Run integrity: the run listing includes only runs bound to the task
  AND carrying the task's real workspace; mismatched-workspace runs
  enter neither ``items`` nor ``total``.
- Role floor: viewer suffices for all three endpoints in the task's
  workspace; membership without a role is 403 after ownership.
- Identity rules: only explicit system scope crosses workspaces — its
  null workspace is never applied as an equality condition (the fix:
  system reads used to be permanently empty); missing-workspace
  non-system identities never degrade to an unscoped query.
- Semantics: pagination, type/status filters, and ordering survive the
  boundary, all reads leave persisted state unchanged, and the task
  write endpoints keep their shapes (their access rules now live in
  the task write boundary, including system scope's real-workspace
  mutations).
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
from hecate.models.evaluation import EvaluationRunModel, EvaluationTaskModel
from hecate.models.organization import OrganizationModel
from hecate.models.workspace import WorkspaceModel
from hecate.models.workspace_member import WorkspaceMemberModel, WorkspaceRole
from hecate.ops.evaluation.types import EvalInput, EvalOutput, Score
from tests.test_api.test_evaluation_tasks_api import _offline_payload, _online_payload


class _StubEvaluator:
    """Minimal evaluator standing in for a registered built-in."""

    @property
    def name(self) -> str:
        return "stub"

    @property
    def description(self) -> str:
        return "stub evaluator"

    async def evaluate(self, input: EvalInput) -> EvalOutput:  # noqa: A002 — ABC signature
        return EvalOutput(scores=[Score(metric_name="stub", value=0.5, source="deterministic")])


@pytest.fixture
def seeded_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make ``stub`` resolve through the service's evaluator lookup."""
    import hecate.ops.evaluation.tasks.service as service_mod

    monkeypatch.setattr(
        service_mod,
        "get_evaluator_class",
        lambda name: _StubEvaluator if name == "stub" else None,
    )


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
    name: str = "boundary-task",
    deleted: bool = False,
) -> EvaluationTaskModel:
    """Seed a task directly via the ORM."""
    task = EvaluationTaskModel(
        task_type="offline",
        name=f"{name}-{uuid.uuid4().hex[:8]}",
        workspace_id=workspace_id,
        evaluator_configs=["stub"],
        # The dataset reference lives in the type-specific config, not a
        # column; seed it so offline update validation passes.
        config={"dataset_id": str(uuid.uuid4()), "answer_source": "manual"},
    )
    task.deleted = deleted
    db.add(task)
    await db.commit()
    return task


async def _seed_task_run(
    db: AsyncSession,
    task_id: uuid.UUID,
    workspace_id: uuid.UUID,
    *,
    deleted: bool = False,
) -> EvaluationRunModel:
    """Seed a completed run bound to ``task_id`` with its own workspace."""
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
    run.deleted = deleted
    db.add(run)
    await db.commit()
    return run


async def _database_snapshot(db: AsyncSession) -> list[list[dict[str, Any]]]:
    """Read all persisted task/run columns without ORM identity-map caching."""
    snapshots = []
    for model in (EvaluationTaskModel, EvaluationRunModel):
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
    message = resp.json()["detail"]["error"]["message"]
    assert message in {"Viewer role required", "Workspace membership required"}
    assert resp.json() == {"detail": {"error": {"code": "FORBIDDEN", "message": message, "details": None}}}


async def _create_task_via_api(http: AsyncClient, name: str) -> dict[str, Any]:
    resp = await http.post("/api/evaluation/tasks", json=_offline_payload(name=name))
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# Listing scope and system-scope fix
# ---------------------------------------------------------------------------


async def test_listing_scoped_to_caller_workspace(
    client: AsyncClient,
    db_session: AsyncSession,
    seeded_registry: None,
) -> None:
    """Items and ``total`` count only the caller's workspace tasks."""
    ws1, ws1_ctx = await _workspace_with_role(db_session, "ws-one", WorkspaceRole.VIEWER)
    ws2, _ = await _workspace_with_role(db_session, "ws-two", WorkspaceRole.VIEWER)

    # Setup writes go through the task write boundary, so they need editor.
    async with _client_as(client, _member_as(ws1, WorkspaceRole.EDITOR)) as http:
        await _create_task_via_api(http, "w1-a")
        await _create_task_via_api(http, "w1-b")
    async with _client_as(client, _member_as(ws2, WorkspaceRole.ADMIN)) as http:
        await _create_task_via_api(http, "w2-a")

    async with _client_as(client, ws1_ctx) as http:
        resp = await http.get("/api/evaluation/tasks")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 2
    assert all(item["name"].startswith(("w1-a", "w1-b")) for item in body["items"])


async def test_system_scope_reads_across_workspaces(
    client: AsyncClient,
    db_session: AsyncSession,
    seeded_registry: None,
) -> None:
    """The null system workspace is never an equality condition (the fix)."""
    ws, _ = await _workspace_with_role(db_session, "ws-owned", WorkspaceRole.VIEWER)

    async with _client_as(client, _member_as(ws, WorkspaceRole.ADMIN)) as http:
        task = await _create_task_via_api(http, "owned-task")
        await _seed_task_run(db_session, uuid.UUID(task["id"]), ws.id)

    async with _client_as(client, _system_context()) as http:
        listing = await http.get("/api/evaluation/tasks")
        detail = await http.get(f"/api/evaluation/tasks/{task['id']}")
        runs = await http.get(f"/api/evaluation/tasks/{task['id']}/runs")
    assert listing.status_code == 200, listing.text
    assert listing.json()["total"] >= 1  # not permanently empty
    assert any(item["id"] == task["id"] for item in listing.json()["items"])
    assert detail.status_code == 200, detail.text
    assert runs.status_code == 200, runs.text
    assert runs.json()["total"] == 1


# ---------------------------------------------------------------------------
# Detail / runs ownership
# ---------------------------------------------------------------------------


async def test_detail_invisible_task_uniform_and_id_free(
    client: AsyncClient,
    db_session: AsyncSession,
    seeded_registry: None,
) -> None:
    """Missing, soft-deleted, and cross-workspace tasks 404 alike."""
    ws1, ws1_ctx = await _workspace_with_role(db_session, "ws-one", WorkspaceRole.VIEWER)
    ws2, _ = await _workspace_with_role(db_session, "ws-two", WorkspaceRole.VIEWER)
    foreign = await _seed_task(db_session, ws2.id)
    deleted = await _seed_task(db_session, ws1.id, deleted=True)

    async with _client_as(client, ws1_ctx) as http:
        probes = [
            await http.get(f"/api/evaluation/tasks/{uuid.uuid4()}"),
            await http.get(f"/api/evaluation/tasks/{deleted.id}"),
            await http.get(f"/api/evaluation/tasks/{foreign.id}"),
        ]
    for probe in probes:
        _assert_task_not_found(probe)
        assert probes[0].json()["detail"] == probe.json()["detail"]
        assert str(foreign.id) not in probe.text
        assert str(deleted.id) not in probe.text


async def test_runs_of_invisible_task_not_listable(client: AsyncClient, db_session: AsyncSession) -> None:
    """A cross-workspace task's run listing 404s like a missing task."""
    ws1, ws1_ctx = await _workspace_with_role(db_session, "ws-one", WorkspaceRole.VIEWER)
    ws2, _ = await _workspace_with_role(db_session, "ws-two", WorkspaceRole.VIEWER)
    foreign = await _seed_task(db_session, ws2.id)
    await _seed_task_run(db_session, foreign.id, ws2.id)

    async with _client_as(client, ws1_ctx) as http:
        cross = await http.get(f"/api/evaluation/tasks/{foreign.id}/runs")
        missing = await http.get(f"/api/evaluation/tasks/{uuid.uuid4()}/runs")
    _assert_task_not_found(cross)
    assert cross.json()["detail"] == missing.json()["detail"]


async def test_run_listing_constrained_to_task_workspace(client: AsyncClient, db_session: AsyncSession) -> None:
    """Mismatched-workspace runs enter neither ``items`` nor ``total``."""
    ws1, ws1_ctx = await _workspace_with_role(db_session, "ws-one", WorkspaceRole.VIEWER)
    ws2, _ = await _workspace_with_role(db_session, "ws-two", WorkspaceRole.VIEWER)
    task = await _seed_task(db_session, ws1.id)
    await _seed_task_run(db_session, task.id, ws1.id)  # correctly bound
    await _seed_task_run(db_session, task.id, ws2.id)  # wrong-workspace row
    await _seed_task_run(db_session, task.id, ws1.id, deleted=True)  # soft-deleted

    async with _client_as(client, ws1_ctx) as http:
        resp = await http.get(f"/api/evaluation/tasks/{task.id}/runs")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 1
    assert len(body["items"]) == 1


# ---------------------------------------------------------------------------
# Role floor and identity rules
# ---------------------------------------------------------------------------


async def test_ownership_precedes_role_for_cross_workspace_calls(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """A role-less caller from another workspace gets the task 404."""
    ws, _ = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.VIEWER)
    other_ws, _ = await _workspace_with_role(db_session, "ws-other", WorkspaceRole.VIEWER)
    task = await _seed_task(db_session, ws.id)
    cross_workspace = _member_as(other_ws, None)

    async with _client_as(client, cross_workspace) as http:
        listing = await http.get("/api/evaluation/tasks")
        detail = await http.get(f"/api/evaluation/tasks/{task.id}")
        runs = await http.get(f"/api/evaluation/tasks/{task.id}/runs")
    # The listing is role-gated, not task-scoped: a role-less identity is
    # 403 before any task visibility question arises.
    _assert_forbidden(listing)
    _assert_task_not_found(detail)
    _assert_task_not_found(runs)


@pytest.mark.parametrize("role", [WorkspaceRole.VIEWER, WorkspaceRole.EDITOR, WorkspaceRole.ADMIN])
async def test_roles_at_or_above_viewer_can_read(
    client: AsyncClient,
    db_session: AsyncSession,
    seeded_registry: None,
    role: WorkspaceRole,
) -> None:
    """Viewer and above receive the same successful task reads."""
    ws, ctx = await _workspace_with_role(db_session, "ws-role", role)
    ds = await _seed_task(db_session, ws.id)
    await _seed_task_run(db_session, ds.id, ws.id)

    async with _client_as(client, ctx) as http:
        listing = await http.get("/api/evaluation/tasks")
        detail = await http.get(f"/api/evaluation/tasks/{ds.id}")
        runs = await http.get(f"/api/evaluation/tasks/{ds.id}/runs")
    assert listing.status_code == 200, listing.text
    assert detail.status_code == 200, detail.text
    assert detail.json()["id"] == str(ds.id)
    assert runs.status_code == 200, runs.text
    assert runs.json()["total"] == 1


async def test_role_floor_inside_own_workspace(
    client: AsyncClient,
    db_session: AsyncSession,
    seeded_registry: None,
) -> None:
    """Membership without a role is 403 on all three task read endpoints."""
    ws, _ = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.EDITOR)
    ds = await _seed_task(db_session, ws.id)
    role_less = _member_as(ws, None)

    async with _client_as(client, role_less) as http:
        listing = await http.get("/api/evaluation/tasks")
        detail = await http.get(f"/api/evaluation/tasks/{ds.id}")
        runs = await http.get(f"/api/evaluation/tasks/{ds.id}/runs")
    _assert_forbidden(listing)
    _assert_forbidden(detail)
    _assert_forbidden(runs)


async def test_missing_workspace_identity_denied_without_unscoped_query(
    client: AsyncClient,
    db_session: AsyncSession,
    seeded_registry: None,
) -> None:
    """No active workspace: denied everywhere, no unscoped read fallback."""
    ws, _ = await _workspace_with_role(db_session, "ws-seeded", WorkspaceRole.VIEWER)
    task = await _seed_task(db_session, ws.id)
    before = await _database_snapshot(db_session)

    async with _client_as(client, _no_workspace_context()) as http:
        listing = await http.get("/api/evaluation/tasks")
        detail = await http.get(f"/api/evaluation/tasks/{task.id}")
        runs = await http.get(f"/api/evaluation/tasks/{task.id}/runs")
    _assert_forbidden(listing)
    _assert_task_not_found(detail)
    _assert_task_not_found(runs)
    assert await _database_snapshot(db_session) == before


# ---------------------------------------------------------------------------
# Semantics preservation
# ---------------------------------------------------------------------------


async def test_filters_and_pagination_preserved(
    client: AsyncClient,
    db_session: AsyncSession,
    seeded_registry: None,
) -> None:
    """Task type/status filters and pagination keep their existing behavior."""
    ws, admin_ctx = await _workspace_with_role(db_session, "ws-admin", WorkspaceRole.ADMIN)

    async with _client_as(client, admin_ctx) as http:
        await _create_task_via_api(http, "offline-a")
        online = await http.post("/api/evaluation/tasks", json=_online_payload(name="online-b"))
        assert online.status_code == 201, online.text
        offline_only = await http.get("/api/evaluation/tasks", params={"task_type": "offline"})
        assert offline_only.json()["total"] == 1

        paged = await http.get("/api/evaluation/tasks", params={"page": 1, "page_size": 1})
        assert paged.status_code == 200, paged.text
        assert paged.json()["total"] == 2
        assert len(paged.json()["items"]) == 1


async def test_write_paths_keep_their_behavior(
    client: AsyncClient,
    db_session: AsyncSession,
    seeded_registry: None,
) -> None:
    """Non-system writes work as before; system writes reach the target."""
    ws, admin_ctx = await _workspace_with_role(db_session, "ws-admin", WorkspaceRole.ADMIN)
    ds = await _seed_task(db_session, ws.id)

    async with _client_as(client, admin_ctx) as http:
        created = await _create_task_via_api(http, "writable")
        updated = await http.put(f"/api/evaluation/tasks/{created['id']}", json={"name": "renamed"})
        assert updated.status_code == 200, updated.text
        removed = await http.delete(f"/api/evaluation/tasks/{created['id']}")
        assert removed.status_code == 204, removed.text
        # The shared write path still resolves within the workspace: the
        # boundary does not loosen update/delete into cross-workspace writes.
        foreign_update = await http.put(f"/api/evaluation/tasks/{ds.id}", json={"name": "still-own"})
        assert foreign_update.status_code == 200, foreign_update.text

    # System-scope writes resolve through the task read boundary and now
    # operate on the task's real workspace (the pre-boundary None-equality
    # filter used to 404 every system write).
    async with _client_as(client, _system_context()) as http:
        system_update = await http.put(f"/api/evaluation/tasks/{ds.id}", json={"name": "system"})
        assert system_update.status_code == 200, system_update.text
        assert system_update.json()["workspace_id"] == str(ws.id)


async def test_task_disappearing_before_run_query_keeps_404(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A service lookup race still maps to the constant task 404 envelope."""
    from hecate.ops.evaluation.tasks.service import EvaluationTaskNotFoundError, EvaluationTaskService

    ws, ctx = await _workspace_with_role(db_session, "lookup-race", WorkspaceRole.VIEWER)
    task = await _seed_task(db_session, ws.id)

    async def disappeared(self: EvaluationTaskService, *args: Any, **kwargs: Any) -> Any:
        raise EvaluationTaskNotFoundError(str(task.id))

    monkeypatch.setattr(EvaluationTaskService, "list_task_runs", disappeared)
    async with _client_as(client, ctx) as http:
        response = await http.get(f"/api/evaluation/tasks/{task.id}/runs")
    _assert_task_not_found(response)
    assert str(task.id) not in response.text


@pytest.mark.parametrize("role", [None, WorkspaceRole.VIEWER])
@pytest.mark.parametrize("suffix", ["", "/runs"])
async def test_hidden_tasks_precede_role_and_keep_state(
    client: AsyncClient, db_session: AsyncSession, role: WorkspaceRole | None, suffix: str
) -> None:
    """All hidden task states return the same full envelope without mutations."""
    ws, _ = await _workspace_with_role(db_session, "hidden-own", WorkspaceRole.VIEWER)
    other, _ = await _workspace_with_role(db_session, "hidden-other", WorkspaceRole.VIEWER)
    deleted = await _seed_task(db_session, ws.id, deleted=True)
    foreign = await _seed_task(db_session, other.id)
    await _seed_task_run(db_session, deleted.id, ws.id)
    await _seed_task_run(db_session, foreign.id, other.id)
    before = await _database_snapshot(db_session)
    async with _client_as(client, _member_as(ws, role)) as http:
        for task_id in (uuid.uuid4(), deleted.id, foreign.id):
            response = await http.get(f"/api/evaluation/tasks/{task_id}{suffix}")
            _assert_task_not_found(response)
            assert await _database_snapshot(db_session) == before


@pytest.mark.parametrize("system", [False, True])
async def test_pagination_filters_ordering_and_successful_reads_keep_state(
    client: AsyncClient, db_session: AsyncSession, system: bool
) -> None:
    """Workspace filters apply before pagination and count for both identities."""
    ws, viewer = await _workspace_with_role(db_session, "paged-own", WorkspaceRole.VIEWER)
    other, _ = await _workspace_with_role(db_session, "paged-other", WorkspaceRole.VIEWER)
    task = await _seed_task(db_session, ws.id)
    online = await _seed_task(db_session, ws.id)
    disabled = await _seed_task(db_session, ws.id)
    foreign = await _seed_task(db_session, other.id)
    deleted = await _seed_task(db_session, ws.id, deleted=True)
    online.task_type = disabled.task_type = foreign.task_type = deleted.task_type = "online"
    disabled.status = "disabled"
    now = datetime.now(UTC)
    online.created_at = now
    foreign.created_at = now + timedelta(seconds=1)
    runs = [await _seed_task_run(db_session, task.id, ws.id) for _ in range(3)]
    for index, run in enumerate(runs):
        run.created_at = now + timedelta(seconds=index)
    runs[-1].summary = {"total_items": 3, "pass_rate": 1.0}
    await _seed_task_run(db_session, task.id, other.id)
    await _seed_task_run(db_session, online.id, ws.id)
    await _seed_task_run(db_session, task.id, ws.id, deleted=True)
    await db_session.commit()
    before = await _database_snapshot(db_session)
    ctx = _system_context() if system else viewer
    async with _client_as(client, ctx) as http:
        expected = [foreign, online] if system else [online]
        for page in range(1, len(expected) + 2):
            response = await http.get(
                "/api/evaluation/tasks",
                params={"task_type": "online", "status": "active", "page": page, "page_size": 1},
            )
            assert response.status_code == 200, response.text
            assert response.json()["total"] == len(expected)
            assert [entry["id"] for entry in response.json()["items"]] == [str(t.id) for t in expected[page - 1 : page]]
        response = await http.get("/api/evaluation/tasks", params={"status": "disabled"})
        assert response.status_code == 200, response.text
        assert response.json()["total"] == 1
        assert response.json()["items"][0]["id"] == str(disabled.id)
        for page in range(1, 5):
            response = await http.get(f"/api/evaluation/tasks/{task.id}/runs", params={"page": page, "page_size": 1})
            assert response.status_code == 200, response.text
            assert response.json()["total"] == 3
            assert [entry["id"] for entry in response.json()["items"]] == [
                str(r.id) for r in list(reversed(runs))[page - 1 : page]
            ]
            if page == 1:
                assert response.json()["items"][0]["summary"] == {"total_items": 3, "pass_rate": 1.0}
        response = await http.get(f"/api/evaluation/tasks/{task.id}")
        assert response.status_code == 200, response.text
    assert await _database_snapshot(db_session) == before
