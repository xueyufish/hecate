"""Distributions report access boundary (step10).

Covers the access boundary on ``GET /api/evaluation/reports/distributions``:

- Parameter contract: the existing 400 messages for providing both or
  neither of ``run_id``/``task_id`` and the 422 for malformed UUIDs are
  unchanged.
- Ownership before role: missing, soft-deleted, and cross-workspace runs
  and tasks share one identifier-free 404 across both scopes for every identity;
  a role-less member of a visible resource's workspace receives 403.
- System scope: explicit system identities read the resource's real
  histograms via its real workspace (not an empty null-filtered result).
- Read-only: rejections leave run, task, and score state untouched, and
  histogram/reconciliation semantics survive the boundary unchanged.
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
from hecate.models.evaluation import (
    EvaluationRunModel,
    EvaluationScoreModel,
    EvaluationTaskModel,
    EvaluationTaskScoreModel,
)
from hecate.models.organization import OrganizationModel
from hecate.models.workspace import WorkspaceModel
from hecate.models.workspace_member import WorkspaceMemberModel, WorkspaceRole
from hecate.ops.evaluation.reports.service import EvaluationReportNotFoundError, EvaluationReportService

DEFAULT_WORKSPACE_ID = uuid.UUID(int=0)


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
    return AuthContext(
        user_id=uuid.uuid4(),
        org_id=None,
        workspace_id=None,
        role=None,
        auth_method="api_key",
        api_key_scope="system",
    )


def _no_workspace_context() -> AuthContext:
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


async def _seed_run(db: AsyncSession, workspace_id: uuid.UUID, *, deleted: bool = False) -> EvaluationRunModel:
    run = EvaluationRunModel(
        dataset_id=uuid.uuid4(),
        evaluator_configs=["stub"],
        status="completed",
        summary={"pass_rate": 0.9},
        workspace_id=workspace_id,
        created_at=_naive(5),
        completed_at=_naive(5, 13),
    )
    run.deleted = deleted
    db.add(run)
    await db.commit()
    return run


async def _seed_run_scores(db: AsyncSession, run_id: uuid.UUID, workspace_id: uuid.UUID, values: list[float]) -> None:
    for value in values:
        db.add(
            EvaluationScoreModel(
                run_id=run_id,
                item_id=uuid.uuid4(),
                metric_name="faithfulness",
                value=value,
                workspace_id=workspace_id,
                created_at=_naive(5),
            )
        )
    await db.commit()


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


async def _seed_task_score(db: AsyncSession, task_id: uuid.UUID, workspace_id: uuid.UUID, value: float) -> None:
    db.add(
        EvaluationTaskScoreModel(
            task_id=task_id,
            target_id=uuid.uuid4(),
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
    for model in (
        EvaluationRunModel,
        EvaluationTaskModel,
        EvaluationScoreModel,
        EvaluationTaskScoreModel,
    ):
        rows = await db.execute(select(model.__table__).order_by(model.id))
        states.append([dict(row) for row in rows.mappings()])
    return states


def _assert_scope_not_found(resp: Any) -> None:
    assert resp.status_code == 404, resp.text
    body = resp.json()["detail"]["error"]
    assert body["code"] == "NOT_FOUND"
    assert body == {"code": "NOT_FOUND", "message": "Report scope not found", "details": None}


@pytest.mark.parametrize("role", [WorkspaceRole.VIEWER, WorkspaceRole.EDITOR, WorkspaceRole.ADMIN])
async def test_viewer_and_above_read_both_scopes(
    client: AsyncClient,
    db_session: AsyncSession,
    role: WorkspaceRole,
) -> None:
    """A viewer member of the resource's workspace reads both scopes."""
    workspace = await _foreign_workspace(db_session)
    run = await _seed_run(db_session, workspace.id)
    await _seed_run_scores(db_session, run.id, workspace.id, [0.9, 0.8])
    task = await _seed_task(db_session, workspace.id)
    await _seed_task_score(db_session, task.id, workspace.id, 0.3)
    viewer = await _member_of(db_session, workspace.id, role)

    async with _client_as(client, viewer) as http:
        run_resp = await http.get("/api/evaluation/reports/distributions", params={"run_id": str(run.id)})
        task_resp = await http.get("/api/evaluation/reports/distributions", params={"task_id": str(task.id)})
    assert run_resp.status_code == 200, run_resp.text
    assert run_resp.json()["metrics"][0]["count"] == 2
    assert task_resp.status_code == 200, task_resp.text
    assert task_resp.json()["metrics"][0]["count"] == 1
    assert run_resp.json()["scope_id"] == str(run.id)
    assert task_resp.json()["scope_id"] == str(task.id)


async def test_role_less_member_of_visible_resource_denied(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Membership without a role is 403 on visible resources, both scopes."""
    run = await _seed_run(db_session, DEFAULT_WORKSPACE_ID)
    task = await _seed_task(db_session, DEFAULT_WORKSPACE_ID)
    role_less = await _member_of(db_session, DEFAULT_WORKSPACE_ID, None)

    async def unexpected_aggregation(*args, **kwargs):
        pytest.fail("Role-less callers must be denied before aggregating scores")

    monkeypatch.setattr(EvaluationReportService, "distributions", unexpected_aggregation)
    before = await _report_state_snapshot(db_session)
    async with _client_as(client, role_less) as http:
        run_resp = await http.get("/api/evaluation/reports/distributions", params={"run_id": str(run.id)})
        task_resp = await http.get("/api/evaluation/reports/distributions", params={"task_id": str(task.id)})
    assert run_resp.status_code == 403, run_resp.text
    assert run_resp.json()["detail"]["error"]["code"] == "FORBIDDEN"
    assert task_resp.status_code == 403, task_resp.text
    assert await _report_state_snapshot(db_session) == before


@pytest.mark.parametrize("role", [WorkspaceRole.VIEWER, WorkspaceRole.EDITOR, None])
async def test_invisible_resources_share_identifier_free_404(
    client: AsyncClient,
    db_session: AsyncSession,
    role: WorkspaceRole | None,
) -> None:
    """Missing, soft-deleted, and cross-workspace resources 404 alike across scopes."""
    foreign = await _foreign_workspace(db_session)
    deleted_run = await _seed_run(db_session, DEFAULT_WORKSPACE_ID, deleted=True)
    deleted_task = await _seed_task(db_session, DEFAULT_WORKSPACE_ID, deleted=True)
    foreign_run = await _seed_run(db_session, foreign.id)
    foreign_task = await _seed_task(db_session, foreign.id)
    prober = await _member_of(db_session, DEFAULT_WORKSPACE_ID, role)

    before = await _report_state_snapshot(db_session)
    async with _client_as(client, prober) as http:
        run_probes = [
            await http.get("/api/evaluation/reports/distributions", params={"run_id": str(uuid.uuid4())}),
            await http.get("/api/evaluation/reports/distributions", params={"run_id": str(deleted_run.id)}),
            await http.get("/api/evaluation/reports/distributions", params={"run_id": str(foreign_run.id)}),
        ]
        task_probes = [
            await http.get("/api/evaluation/reports/distributions", params={"task_id": str(uuid.uuid4())}),
            await http.get("/api/evaluation/reports/distributions", params={"task_id": str(deleted_task.id)}),
            await http.get("/api/evaluation/reports/distributions", params={"task_id": str(foreign_task.id)}),
        ]
    for probe in run_probes + task_probes:
        _assert_scope_not_found(probe)
        for identifier in (deleted_run.id, deleted_task.id, foreign_run.id, foreign_task.id, prober.user_id):
            assert str(identifier) not in probe.text
    assert await _report_state_snapshot(db_session) == before


async def test_ownership_precedes_role_for_cross_workspace_caller(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """A foreign-workspace role-less identity gets the identifier-free 404, not a 403."""
    foreign = await _foreign_workspace(db_session)
    run = await _seed_run(db_session, DEFAULT_WORKSPACE_ID)
    task = await _seed_task(db_session, DEFAULT_WORKSPACE_ID)
    foreign_caller = await _member_of(db_session, foreign.id, None)

    async with _client_as(client, foreign_caller) as http:
        run_resp = await http.get("/api/evaluation/reports/distributions", params={"run_id": str(run.id)})
        task_resp = await http.get("/api/evaluation/reports/distributions", params={"task_id": str(task.id)})
    _assert_scope_not_found(run_resp)
    _assert_scope_not_found(task_resp)


async def test_system_scope_reads_real_histograms_via_resource_workspace(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """System scope receives the resource's real histograms, not an empty result."""
    workspace = await _foreign_workspace(db_session)
    run = await _seed_run(db_session, workspace.id)
    await _seed_run_scores(db_session, run.id, workspace.id, [0.9, 0.8])
    task = await _seed_task(db_session, workspace.id)
    await _seed_task_score(db_session, task.id, workspace.id, 0.3)

    await _seed_run_scores(db_session, run.id, DEFAULT_WORKSPACE_ID, [0.1])
    await _seed_task_score(db_session, task.id, DEFAULT_WORKSPACE_ID, 0.1)
    before = await _report_state_snapshot(db_session)
    async with _client_as(client, _system_context()) as http:
        run_resp = await http.get("/api/evaluation/reports/distributions", params={"run_id": str(run.id)})
        task_resp = await http.get("/api/evaluation/reports/distributions", params={"task_id": str(task.id)})
    assert run_resp.status_code == 200, run_resp.text
    assert run_resp.json()["metrics"][0]["count"] == 2
    assert task_resp.status_code == 200, task_resp.text
    assert task_resp.json()["metrics"][0]["count"] == 1
    assert await _report_state_snapshot(db_session) == before


@pytest.mark.parametrize("scope_param", ["run_id", "task_id"])
async def test_missing_workspace_identity_denied(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    scope_param: str,
) -> None:
    """A non-system identity without a workspace is denied before any SELECT."""
    run = await _seed_run(db_session, DEFAULT_WORKSPACE_ID)
    task = await _seed_task(db_session, DEFAULT_WORKSPACE_ID)
    resource_id = run.id if scope_param == "run_id" else task.id

    async def unexpected_query(*args, **kwargs):
        pytest.fail("A missing-workspace identity must not query report resources")

    with monkeypatch.context() as patch:
        patch.setattr(AsyncSession, "execute", unexpected_query)
        async with _client_as(client, _no_workspace_context()) as http:
            response = await http.get("/api/evaluation/reports/distributions", params={scope_param: str(resource_id)})
    _assert_scope_not_found(response)


async def test_parameter_contract_preserved(client: AsyncClient) -> None:
    """Both/neither scopes keep the 400 messages; malformed UUIDs stay 422."""
    both = await client.get(
        "/api/evaluation/reports/distributions",
        params={"run_id": str(uuid.uuid4()), "task_id": str(uuid.uuid4())},
    )
    assert both.status_code == 400, both.text
    assert both.json()["detail"]["error"]["message"] == "only one of run_id or task_id may be provided"

    neither = await client.get("/api/evaluation/reports/distributions")
    assert neither.status_code == 400, neither.text
    assert neither.json()["detail"]["error"]["message"] == "exactly one of run_id or task_id is required"

    malformed = await client.get("/api/evaluation/reports/distributions", params={"run_id": "not-a-uuid"})
    assert malformed.status_code == 422, malformed.text


async def test_rejections_are_read_only(client: AsyncClient, db_session: AsyncSession) -> None:
    """Denied requests leave run, task, and score state untouched."""
    foreign = await _foreign_workspace(db_session)
    run = await _seed_run(db_session, DEFAULT_WORKSPACE_ID)
    await _seed_run_scores(db_session, run.id, DEFAULT_WORKSPACE_ID, [0.9])
    task = await _seed_task(db_session, DEFAULT_WORKSPACE_ID)
    await _seed_task_score(db_session, task.id, DEFAULT_WORKSPACE_ID, 0.3)

    before = await _report_state_snapshot(db_session)
    for ctx in (
        await _member_of(db_session, DEFAULT_WORKSPACE_ID, None),
        await _member_of(db_session, foreign.id, WorkspaceRole.VIEWER),
        _no_workspace_context(),
    ):
        async with _client_as(client, ctx) as http:
            run_resp = await http.get("/api/evaluation/reports/distributions", params={"run_id": str(run.id)})
            task_resp = await http.get("/api/evaluation/reports/distributions", params={"task_id": str(task.id)})
        assert run_resp.status_code in (403, 404), run_resp.text
        assert task_resp.status_code in (403, 404), task_resp.text
    assert await _report_state_snapshot(db_session) == before


@pytest.mark.parametrize("scope_param", ["run_id", "task_id"])
async def test_soft_deleted_scope_is_not_found(
    client: AsyncClient,
    db_session: AsyncSession,
    scope_param: str,
) -> None:
    """Soft-deleted runs and tasks are invisible to distributions."""
    run = await _seed_run(db_session, DEFAULT_WORKSPACE_ID, deleted=True)
    task = await _seed_task(db_session, DEFAULT_WORKSPACE_ID, deleted=True)
    resource_id = run.id if scope_param == "run_id" else task.id

    resp = await client.get("/api/evaluation/reports/distributions", params={scope_param: str(resource_id)})
    _assert_scope_not_found(resp)


@pytest.mark.parametrize("scope_param", ["run_id", "task_id"])
async def test_scope_disappearing_after_authorization_has_same_404(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    scope_param: str,
) -> None:
    """A service lookup failure uses the same body as the router precheck."""
    run = await _seed_run(db_session, DEFAULT_WORKSPACE_ID)
    task = await _seed_task(db_session, DEFAULT_WORKSPACE_ID)
    resource_id = run.id if scope_param == "run_id" else task.id
    missing = await client.get("/api/evaluation/reports/distributions", params={scope_param: str(uuid.uuid4())})
    before = await _report_state_snapshot(db_session)

    async def missing_scope(*args, **kwargs):
        raise EvaluationReportNotFoundError(str(resource_id))

    monkeypatch.setattr(EvaluationReportService, "distributions", missing_scope)
    response = await client.get("/api/evaluation/reports/distributions", params={scope_param: str(resource_id)})
    _assert_scope_not_found(response)
    assert response.json() == missing.json()
    assert str(resource_id) not in response.text
    assert await _report_state_snapshot(db_session) == before


@pytest.mark.parametrize("scope_param", ["run_id", "task_id"])
@pytest.mark.parametrize("deleted", [False, True])
async def test_system_cannot_read_missing_or_deleted_scope(
    client: AsyncClient,
    db_session: AsyncSession,
    scope_param: str,
    deleted: bool,
) -> None:
    """System permissions do not make missing or deleted report scopes visible."""
    run = await _seed_run(db_session, DEFAULT_WORKSPACE_ID, deleted=deleted)
    task = await _seed_task(db_session, DEFAULT_WORKSPACE_ID, deleted=deleted)
    resource_id = (run.id if scope_param == "run_id" else task.id) if deleted else uuid.uuid4()
    before = await _report_state_snapshot(db_session)
    async with _client_as(client, _system_context()) as http:
        response = await http.get("/api/evaluation/reports/distributions", params={scope_param: str(resource_id)})
    _assert_scope_not_found(response)
    assert str(resource_id) not in response.text
    assert await _report_state_snapshot(db_session) == before
