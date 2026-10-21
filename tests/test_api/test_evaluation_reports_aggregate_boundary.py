"""Aggregate report access boundary (step10).

Covers the shared access boundary on the three workspace-aggregate
report endpoints (overview, trends, breakdowns):

- Role floor: viewer and above read their workspace's aggregates;
  membership without a role is 403 FORBIDDEN before any aggregation.
- Workspace requirement: a caller without an active workspace —
  including explicit system scope — receives 403 NO_WORKSPACE instead
  of the null-filtered empty reports the pre-boundary code produced;
  no global aggregation or workspace-selection capability is added.
- Data isolation: the service only ever receives the verified active
  workspace, so callers see their own workspace's aggregates.
- Read-only and semantics-preserving: denials leave all report state
  untouched, and successful responses keep reconciliation, raw
  per-source breakdowns, and empty-report behavior unchanged.
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
from hecate.ops.evaluation.reports.service import EvaluationReportService

DEFAULT_WORKSPACE_ID = uuid.UUID(int=0)
WINDOW = {"start_date": "2026-09-01T00:00:00Z", "end_date": "2026-09-11T00:00:00Z"}

AGGREGATE_PATHS = (
    "/api/evaluation/reports/overview",
    "/api/evaluation/reports/trends",
    "/api/evaluation/reports/breakdowns",
)


def _aggregate_params(path: str) -> dict[str, str]:
    """Use a fixed window containing the seeded data for every endpoint."""
    if path.endswith("trends"):
        return {**WINDOW, "dimension": "dataset"}
    if path.endswith("breakdowns"):
        return {**WINDOW, "group_by": "source"}
    return dict(WINDOW)


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
    """Deploy-time bootstrap identity without an active workspace."""
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


async def _seed_run(db: AsyncSession, workspace_id: uuid.UUID, *, pass_rate: float) -> EvaluationRunModel:
    run = EvaluationRunModel(
        dataset_id=uuid.uuid4(),
        evaluator_configs=["stub"],
        status="completed",
        summary={"pass_rate": pass_rate},
        workspace_id=workspace_id,
        created_at=_naive(5),
        completed_at=_naive(5, 13),
    )
    db.add(run)
    await db.flush()
    db.add(
        EvaluationScoreModel(
            run_id=run.id,
            item_id=uuid.uuid4(),
            metric_name="faithfulness",
            value=pass_rate,
            workspace_id=workspace_id,
            created_at=_naive(5),
        )
    )
    await db.commit()
    return run


async def _seed_online_score(db: AsyncSession, workspace_id: uuid.UUID, value: float) -> None:
    task = EvaluationTaskModel(
        task_type="online",
        name=f"task-{uuid.uuid4().hex[:8]}",
        evaluator_configs=["stub"],
        config={},
        workspace_id=workspace_id,
        created_at=_naive(1),
    )
    db.add(task)
    await db.flush()
    db.add(
        EvaluationTaskScoreModel(
            task_id=task.id,
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


async def _aggregate_state_snapshot(db: AsyncSession) -> list[list[dict[str, Any]]]:
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


@pytest.mark.parametrize("path", AGGREGATE_PATHS)
@pytest.mark.parametrize("role", [WorkspaceRole.VIEWER, WorkspaceRole.EDITOR, WorkspaceRole.ADMIN])
async def test_viewer_and_above_read_only_their_workspace(
    client: AsyncClient,
    db_session: AsyncSession,
    role: WorkspaceRole,
    path: str,
) -> None:
    """Every allowed role reads real, isolated data in zero and nonzero workspaces."""
    foreign = await _foreign_workspace(db_session)
    own_run = await _seed_run(db_session, DEFAULT_WORKSPACE_ID, pass_rate=0.9)
    foreign_run = await _seed_run(db_session, foreign.id, pass_rate=0.5)
    await _seed_online_score(db_session, DEFAULT_WORKSPACE_ID, 0.8)
    await _seed_online_score(db_session, foreign.id, 0.2)
    own_viewer = await _member_of(db_session, DEFAULT_WORKSPACE_ID, role)
    foreign_viewer = await _member_of(db_session, foreign.id, role)
    before = await _aggregate_state_snapshot(db_session)

    for ctx, run, pass_rate, online_score in (
        (own_viewer, own_run, 0.9, 0.8),
        (foreign_viewer, foreign_run, 0.5, 0.2),
    ):
        async with _client_as(client, ctx) as http:
            response = await http.get(path, params=_aggregate_params(path))
        assert response.status_code == 200, response.text
        data = response.json()
        if path.endswith("overview"):
            assert data["quality"]["offline_pass_rate"] == pass_rate
            assert data["quality"]["online_avg_score"] == online_score
            assert data["volume"]["completed_runs"] == 1
            assert data["volume"]["online_scored"] == 1
        elif path.endswith("trends"):
            assert data["dimension"] == "dataset"
            assert len(data["series"]) == 1
            series = data["series"][0]
            assert series["group_id"] == str(run.dataset_id)
            assert series["metric"] == "pass_rate"
            assert series["points"] == [{"bucket": "2026-09-05", "value": pass_rate, "count": 1}]
        else:
            assert data["group_by"] == "source"
            assert data["total"] == 1
            assert len(data["groups"]) == 1
            group = data["groups"][0]
            assert group["key"] == "llm_judge"
            assert group["metrics"] == [{"metric_name": "helpfulness", "avg": online_score, "count": 1}]
        assert await _aggregate_state_snapshot(db_session) == before


@pytest.mark.parametrize("path", AGGREGATE_PATHS)
@pytest.mark.parametrize("identity", ["role_less", "no_workspace", "system_no_workspace"])
async def test_rejections_precede_queries_and_aggregation_and_are_read_only(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    identity: str,
) -> None:
    """Denied identities cannot query or aggregate reports on any endpoint."""
    await _seed_run(db_session, DEFAULT_WORKSPACE_ID, pass_rate=0.9)
    await _seed_online_score(db_session, DEFAULT_WORKSPACE_ID, 0.8)
    if identity == "role_less":
        ctx = await _member_of(db_session, DEFAULT_WORKSPACE_ID, None)
    elif identity == "no_workspace":
        ctx = _no_workspace_context()
    else:
        ctx = _system_context()
    before = await _aggregate_state_snapshot(db_session)

    async def unexpected_query(*args, **kwargs):
        pytest.fail("Denied identities must not query report resources")

    async def unexpected_aggregation(*args, **kwargs):
        pytest.fail("Denied identities must not call the aggregation service")

    with monkeypatch.context() as patch:
        # Requests use their own session, so guard the class rather than the seeding session.
        patch.setattr(AsyncSession, "execute", unexpected_query)
        for method in ("overview", "trends", "breakdowns"):
            patch.setattr(EvaluationReportService, method, unexpected_aggregation)
        async with _client_as(client, ctx) as http:
            response = await http.get(path, params=_aggregate_params(path))
    assert response.status_code == 403, response.text
    error = response.json()["detail"]["error"]
    if identity == "role_less":
        assert error["code"] == "FORBIDDEN"
    else:
        assert error == {"code": "NO_WORKSPACE", "message": "request has no workspace scope", "details": None}
    assert await _aggregate_state_snapshot(db_session) == before


async def test_empty_workspace_keeps_normal_empty_reports(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """A valid workspace with no data returns the existing empty reports."""
    fresh = await _foreign_workspace(db_session)
    viewer = await _member_of(db_session, fresh.id, WorkspaceRole.VIEWER)
    before = await _aggregate_state_snapshot(db_session)

    async with _client_as(client, viewer) as http:
        overview = await http.get("/api/evaluation/reports/overview")
        trends = await http.get("/api/evaluation/reports/trends", params={"dimension": "dataset"})
        breakdowns = await http.get("/api/evaluation/reports/breakdowns", params={"group_by": "source"})
    assert overview.status_code == 200, overview.text
    assert overview.json()["volume"]["completed_runs"] == 0
    assert trends.status_code == 200, trends.text
    assert trends.json()["series"] == []
    assert breakdowns.status_code == 200, breakdowns.text
    assert breakdowns.json()["groups"] == []

    assert await _aggregate_state_snapshot(db_session) == before
