"""Sessions report access boundary (step10).

Covers the two-mode access boundary on
``GET /api/evaluation/reports/sessions``:

- Workspace mode (no ``task_id``): the aggregate gate applies — a caller
  without an active workspace (system included) is 403 NO_WORKSPACE
  before any query, and a member below viewer (or without a role) is
  403 FORBIDDEN.
- Task mode (with ``task_id``): a non-system caller without an active
  workspace is 404 before any query; a missing, soft-deleted, or
  cross-workspace task is one constant identifier-free 404; the viewer
  floor applies after ownership; and system scope reads the task's real
  rollup via its real workspace (not a null-filtered empty result).
- Read-only: denials leave task and score state untouched.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import AsyncIterator
from datetime import datetime
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
from hecate.ops.evaluation.reports.service import EvaluationReportService

DEFAULT_WORKSPACE_ID = uuid.UUID(int=0)

# Fixed window containing every seeded score (the service defaults to the
# trailing 30 days from "now", which drifts past fixed seed dates).
WINDOW = {"start_date": "2026-09-01T00:00:00Z", "end_date": "2026-09-11T00:00:00Z"}

SESSIONS_PATH = "/api/evaluation/reports/sessions"


def _naive(day: int, hour: int = 12) -> datetime:
    return datetime(2026, 9, day, hour)


async def _member_of(db: AsyncSession, workspace_id: uuid.UUID, role: WorkspaceRole | None) -> AuthContext:
    """A fresh identity whose active workspace is ``workspace_id``."""
    user_id = uuid.uuid4()
    if role is not None:
        db.add(WorkspaceMemberModel(user_id=user_id, workspace_id=workspace_id, role=role))
        await db.commit()
    return AuthContext(
        user_id=user_id,
        org_id=None,
        workspace_id=workspace_id,
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
    """Authenticated non-system identity with no active workspace."""
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


async def _foreign_workspace(db: AsyncSession) -> WorkspaceModel:
    """A workspace outside the default one, with its own organization."""
    name = f"foreign-{uuid.uuid4().hex[:8]}"
    org = OrganizationModel(name=f"Org {name}", slug=f"org-{name}", owner_id=uuid.uuid4())
    db.add(org)
    await db.flush()
    ws = WorkspaceModel(org_id=org.id, name=name, slug=name)
    db.add(ws)
    await db.commit()
    return ws


async def _seed_task(db: AsyncSession, workspace_id: uuid.UUID, *, deleted: bool = False) -> EvaluationTaskModel:
    task = EvaluationTaskModel(
        task_type="online",
        name=f"task-{uuid.uuid4().hex[:8]}",
        evaluator_configs=["stub"],
        config={},
        workspace_id=workspace_id,
        created_at=_naive(1),
    )
    task.deleted = deleted
    db.add(task)
    await db.commit()
    return task


async def _seed_task_score(
    db: AsyncSession,
    task_id: uuid.UUID,
    workspace_id: uuid.UUID,
    value: float,
    *,
    session_id: uuid.UUID | None = None,
) -> None:
    db.add(
        EvaluationTaskScoreModel(
            task_id=task_id,
            target_id=uuid.uuid4(),
            session_id=session_id,
            metric_name="helpfulness",
            value=value,
            source="llm_judge",
            status="completed",
            workspace_id=workspace_id,
            created_at=_naive(5),
        )
    )
    await db.commit()


async def _report_state_snapshot(db: AsyncSession) -> list[list[dict[str, Any]]]:
    states = []
    for model in (EvaluationTaskModel, EvaluationTaskScoreModel):
        rows = await db.execute(select(model.__table__).order_by(model.id))
        states.append([dict(row) for row in rows.mappings()])
    return states


def _assert_scope_not_found(resp: Any) -> None:
    assert resp.status_code == 404, resp.text
    body = resp.json()["detail"]["error"]
    assert body == {"code": "NOT_FOUND", "message": "Report scope not found", "details": None}


def _assert_sessions(response: Any, expected: dict[uuid.UUID, tuple[float, int]]) -> None:
    assert response.status_code == 200, response.text
    data = response.json()
    assert set(data) == {"items", "total"}
    assert data["total"] == len(expected)
    assert len(data["items"]) == len(expected)
    by_session = {item["session_id"]: item for item in data["items"]}
    assert set(by_session) == {str(session_id) for session_id in expected}
    for session_id, (avg, count) in expected.items():
        item = by_session[str(session_id)]
        assert set(item) == {"session_id", "agent_id", "trace_count", "last_scored_at", "metrics"}
        assert item["trace_count"] == count
        assert item["metrics"] == [{"metric_name": "helpfulness", "avg": avg, "count": count}]


@pytest.mark.parametrize("task_scoped", [False, True])
@pytest.mark.parametrize("role", [WorkspaceRole.VIEWER, WorkspaceRole.EDITOR, WorkspaceRole.ADMIN])
async def test_viewer_and_above_read_isolated_rollups_without_mutation(
    client: AsyncClient,
    db_session: AsyncSession,
    task_scoped: bool,
    role: WorkspaceRole,
) -> None:
    """Both modes isolate real data by workspace and, when supplied, by task."""
    foreign = await _foreign_workspace(db_session)
    cases = []
    for workspace_id, values, other_value in (
        (DEFAULT_WORKSPACE_ID, (0.2, 0.8), 0.9),
        (foreign.id, (0.1, 0.3), 0.7),
    ):
        task = await _seed_task(db_session, workspace_id)
        other_task = await _seed_task(db_session, workspace_id)
        session, other_session = uuid.uuid4(), uuid.uuid4()
        for value in values:
            await _seed_task_score(db_session, task.id, workspace_id, value, session_id=session)
        await _seed_task_score(db_session, other_task.id, workspace_id, other_value, session_id=other_session)
        ctx = await _member_of(db_session, workspace_id, role)
        expected = {session: (round(sum(values) / len(values), 4), len(values))}
        if not task_scoped:
            expected[other_session] = (other_value, 1)
        cases.append((ctx, task.id, expected))
    before = await _report_state_snapshot(db_session)

    for ctx, task_id, expected in cases:
        params = {**WINDOW, **({"task_id": str(task_id)} if task_scoped else {})}
        async with _client_as(client, ctx) as http:
            response = await http.get(SESSIONS_PATH, params=params)
        _assert_sessions(response, expected)
        assert await _report_state_snapshot(db_session) == before


@pytest.mark.parametrize("identity", ["role_less", "no_workspace", "system"])
async def test_workspace_rejections_precede_queries_and_aggregation(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    identity: str,
) -> None:
    """Workspace-mode denials happen before any report query or service call."""
    task = await _seed_task(db_session, DEFAULT_WORKSPACE_ID)
    await _seed_task_score(db_session, task.id, DEFAULT_WORKSPACE_ID, 0.6, session_id=uuid.uuid4())
    if identity == "role_less":
        ctx = await _member_of(db_session, DEFAULT_WORKSPACE_ID, None)
    elif identity == "no_workspace":
        ctx = _no_workspace_context()
    else:
        ctx = _system_context()
    before = await _report_state_snapshot(db_session)

    async def unexpected_query(*args, **kwargs):
        pytest.fail("Denied identities must not query report resources")

    async def unexpected_aggregation(*args, **kwargs):
        pytest.fail("Denied identities must not call the session rollup service")

    with monkeypatch.context() as patch:
        # HTTP requests use a separate session from the seeding fixture.
        patch.setattr(AsyncSession, "execute", unexpected_query)
        patch.setattr(EvaluationReportService, "session_rollup", unexpected_aggregation)
        async with _client_as(client, ctx) as http:
            response = await http.get(SESSIONS_PATH, params=WINDOW)
    assert response.status_code == 403, response.text
    error = response.json()["detail"]["error"]
    if identity == "role_less":
        assert error["code"] == "FORBIDDEN"
    else:
        assert error == {"code": "NO_WORKSPACE", "message": "request has no workspace scope", "details": None}
    assert await _report_state_snapshot(db_session) == before


async def test_task_scoped_role_less_identity_is_denied_before_aggregation(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A visible task is resolved before its role-less caller receives 403."""
    task = await _seed_task(db_session, DEFAULT_WORKSPACE_ID)
    await _seed_task_score(db_session, task.id, DEFAULT_WORKSPACE_ID, 0.6, session_id=uuid.uuid4())
    role_less = await _member_of(db_session, DEFAULT_WORKSPACE_ID, None)
    before = await _report_state_snapshot(db_session)

    async def unexpected_aggregation(*args, **kwargs):
        pytest.fail("Role-less identities must not call the session rollup service")

    monkeypatch.setattr(EvaluationReportService, "session_rollup", unexpected_aggregation)
    async with _client_as(client, role_less) as http:
        response = await http.get(SESSIONS_PATH, params={**WINDOW, "task_id": str(task.id)})
    assert response.status_code == 403, response.text
    assert response.json()["detail"]["error"]["code"] == "FORBIDDEN"
    assert await _report_state_snapshot(db_session) == before


@pytest.mark.parametrize("role", [WorkspaceRole.VIEWER, WorkspaceRole.EDITOR, WorkspaceRole.ADMIN, None])
@pytest.mark.parametrize("visibility", ["missing", "deleted", "foreign"])
async def test_invisible_tasks_share_identifier_free_404_before_role_checks(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    role: WorkspaceRole | None,
    visibility: str,
) -> None:
    """Even role-less foreign callers receive the same 404, never a role 403."""
    foreign = await _foreign_workspace(db_session)
    deleted_task = await _seed_task(db_session, DEFAULT_WORKSPACE_ID, deleted=True)
    foreign_task = await _seed_task(db_session, foreign.id)
    for task in (deleted_task, foreign_task):
        await _seed_task_score(db_session, task.id, task.workspace_id, 0.6, session_id=uuid.uuid4())
    ctx = await _member_of(db_session, DEFAULT_WORKSPACE_ID, role)
    task_id = {"missing": uuid.uuid4(), "deleted": deleted_task.id, "foreign": foreign_task.id}[visibility]
    before = await _report_state_snapshot(db_session)

    async def unexpected_aggregation(*args, **kwargs):
        pytest.fail("Invisible tasks must not reach the session rollup service")

    monkeypatch.setattr(EvaluationReportService, "session_rollup", unexpected_aggregation)
    async with _client_as(client, ctx) as http:
        response = await http.get(SESSIONS_PATH, params={**WINDOW, "task_id": str(task_id)})
    _assert_scope_not_found(response)
    for identifier in (task_id, deleted_task.id, foreign_task.id, ctx.user_id, foreign.id):
        assert str(identifier) not in response.text
    assert await _report_state_snapshot(db_session) == before


async def test_missing_workspace_with_task_id_denied_before_query(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-system identity without a workspace gets 404 before any query."""
    task = await _seed_task(db_session, DEFAULT_WORKSPACE_ID)
    await _seed_task_score(db_session, task.id, DEFAULT_WORKSPACE_ID, 0.6, session_id=uuid.uuid4())
    before = await _report_state_snapshot(db_session)

    async def unexpected_query(*args, **kwargs):
        pytest.fail("A missing-workspace identity must not query report resources")

    async def unexpected_aggregation(*args, **kwargs):
        pytest.fail("A missing-workspace identity must not call the rollup service")

    with monkeypatch.context() as patch:
        patch.setattr(AsyncSession, "execute", unexpected_query)
        patch.setattr(EvaluationReportService, "session_rollup", unexpected_aggregation)
        async with _client_as(client, _no_workspace_context()) as http:
            for task_id in (task.id, uuid.uuid4()):
                response = await http.get(SESSIONS_PATH, params={**WINDOW, "task_id": str(task_id)})
                _assert_scope_not_found(response)
    assert await _report_state_snapshot(db_session) == before


async def test_system_scope_uses_nonzero_task_workspace_and_task_filter(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """System reads one named task's real data, without a null or zero fallback."""
    foreign = await _foreign_workspace(db_session)
    task = await _seed_task(db_session, foreign.id)
    other_task = await _seed_task(db_session, foreign.id)
    session = uuid.uuid4()
    await _seed_task_score(db_session, task.id, foreign.id, 0.6, session_id=session)
    await _seed_task_score(db_session, other_task.id, foreign.id, 0.1, session_id=uuid.uuid4())
    await _seed_task_score(db_session, task.id, DEFAULT_WORKSPACE_ID, 0.9, session_id=uuid.uuid4())
    before = await _report_state_snapshot(db_session)
    async with _client_as(client, _system_context()) as http:
        response = await http.get(SESSIONS_PATH, params={**WINDOW, "task_id": str(task.id)})
    _assert_sessions(response, {session: (0.6, 1)})
    assert await _report_state_snapshot(db_session) == before


@pytest.mark.parametrize("deleted", [False, True])
async def test_system_cannot_read_missing_or_deleted_task(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    deleted: bool,
) -> None:
    """System permissions do not expose deleted tasks, even with residual scores."""
    foreign = await _foreign_workspace(db_session)
    task = await _seed_task(db_session, foreign.id, deleted=deleted)
    await _seed_task_score(db_session, task.id, foreign.id, 0.6, session_id=uuid.uuid4())
    task_id = task.id if deleted else uuid.uuid4()
    before = await _report_state_snapshot(db_session)

    async def unexpected_aggregation(*args, **kwargs):
        pytest.fail("System must not aggregate scores for invisible tasks")

    monkeypatch.setattr(EvaluationReportService, "session_rollup", unexpected_aggregation)
    async with _client_as(client, _system_context()) as http:
        response = await http.get(SESSIONS_PATH, params={**WINDOW, "task_id": str(task_id)})
    _assert_scope_not_found(response)
    assert str(task_id) not in response.text
    assert await _report_state_snapshot(db_session) == before


@pytest.mark.parametrize("task_scoped", [False, True])
async def test_valid_empty_workspace_or_task_keeps_normal_empty_result(
    client: AsyncClient,
    db_session: AsyncSession,
    task_scoped: bool,
) -> None:
    """An existing task or workspace without scored sessions still returns 200."""
    foreign = await _foreign_workspace(db_session)
    populated = await _seed_task(db_session, DEFAULT_WORKSPACE_ID)
    await _seed_task_score(db_session, populated.id, DEFAULT_WORKSPACE_ID, 0.6, session_id=uuid.uuid4())
    empty_task = await _seed_task(db_session, foreign.id)
    viewer = await _member_of(db_session, foreign.id, WorkspaceRole.VIEWER)
    before = await _report_state_snapshot(db_session)
    params = {**WINDOW, **({"task_id": str(empty_task.id)} if task_scoped else {})}
    async with _client_as(client, viewer) as http:
        response = await http.get(SESSIONS_PATH, params=params)
    _assert_sessions(response, {})
    assert await _report_state_snapshot(db_session) == before


@pytest.mark.parametrize("task_scoped", [False, True])
async def test_parameter_validation_contract_preserved(
    client: AsyncClient,
    db_session: AsyncSession,
    task_scoped: bool,
) -> None:
    """Malformed UUIDs, pagination, and date windows keep their validation errors."""
    task = await _seed_task(db_session, DEFAULT_WORKSPACE_ID)
    params = {**WINDOW, **({"task_id": str(task.id)} if task_scoped else {})}
    before = await _report_state_snapshot(db_session)
    for invalid in ({"task_id": "not-a-uuid"}, {"page": "0"}, {"page_size": "101"}, {"start_date": "invalid"}):
        response = await client.get(SESSIONS_PATH, params={**params, **invalid})
        assert response.status_code == 422, response.text
    reversed_window = await client.get(
        SESSIONS_PATH,
        params={**params, "start_date": WINDOW["end_date"], "end_date": WINDOW["start_date"]},
    )
    assert reversed_window.status_code == 400, reversed_window.text
    assert reversed_window.json()["detail"]["error"]["code"] == "INVALID_REQUEST"
    assert await _report_state_snapshot(db_session) == before
