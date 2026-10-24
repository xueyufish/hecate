"""Calibration read access boundary (step10).

Covers ``GET /api/evaluation/calibration``:

- Every identity — system scope included — needs an active workspace; a
  missing workspace is 403 ``NO_WORKSPACE`` before any statistics, score,
  or queue query.
- Non-system callers need a viewer role floor; a role-less member gets
  403 ``FORBIDDEN`` before the statistics service runs, while viewer,
  editor, and admin read normally.
- System scope keeps its role exemption but never widens the aggregation:
  statistics stay inside the active workspace, and a foreign workspace's
  categorical queue declaration cannot change this workspace's metric
  mode. ``agent_id``/``task_id``/``metric_name`` only narrow within the
  workspace.
- Semantics survive the boundary: latest-automated-score pairing, window
  filtering on human rows, soft-delete exclusion, zero-UUID workspaces,
  legitimate empty results, and a structured 400 for inverted time
  windows (previously a 500 through the shared report window resolver).
- Successful and denied reads are read-only, verified against
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
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.auth_context import AuthContext
from hecate.models.evaluation import (
    AnnotationQueueItemModel,
    AnnotationQueueModel,
    EvaluationTaskScoreModel,
)
from hecate.models.organization import OrganizationModel
from hecate.models.workspace import WorkspaceModel
from hecate.models.workspace_member import WorkspaceMemberModel, WorkspaceRole

_BASE = "/api/evaluation/calibration"


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
    metric_defs: list[dict[str, Any]] | None = None,
    deleted: bool = False,
) -> AnnotationQueueModel:
    queue = AnnotationQueueModel(
        name=f"calibration-queue-{uuid.uuid4().hex[:8]}",
        instructions="Rate the output",
        metric_defs=metric_defs or [{"name": "helpfulness", "data_type": "numeric", "min": 0, "max": 1}],
        workspace_id=workspace_id,
        deleted=deleted,
        deleted_at=datetime.now(UTC) if deleted else None,
    )
    db.add(queue)
    await db.commit()
    return queue


async def _seed_score(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    target_id: uuid.UUID,
    *,
    metric_name: str = "helpfulness",
    value: float = 0.5,
    source: str = "llm_judge",
    agent_id: uuid.UUID | None = None,
    task_id: uuid.UUID | None = None,
    value_label: str | None = None,
    deleted: bool = False,
    created_at: datetime | None = None,
) -> EvaluationTaskScoreModel:
    row = EvaluationTaskScoreModel(
        task_id=task_id if source != "human" else None,
        target_id=target_id,
        metric_name=metric_name,
        value=value,
        value_label=value_label,
        reasoning="Persisted evaluation evidence",
        source=source,
        status="completed",
        annotator_id=uuid.uuid4() if source == "human" else None,
        agent_id=agent_id,
        workspace_id=workspace_id,
        deleted=deleted,
        deleted_at=datetime.now(UTC) if deleted else None,
        created_at=created_at if created_at is not None else datetime.now(UTC),
    )
    db.add(row)
    await db.commit()
    return row


async def _seed_pair(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    target_id: uuid.UUID,
    *,
    metric_name: str = "helpfulness",
    machine: float = 0.5,
    human: float = 0.5,
    agent_id: uuid.UUID | None = None,
    task_id: uuid.UUID | None = None,
) -> None:
    """One automated row plus one human row for the same target and metric."""
    await _seed_score(
        db,
        workspace_id,
        target_id,
        metric_name=metric_name,
        value=machine,
        agent_id=agent_id,
        task_id=task_id or uuid.uuid4(),
    )
    await _seed_score(
        db,
        workspace_id,
        target_id,
        metric_name=metric_name,
        value=human,
        source="human",
        agent_id=agent_id,
    )


async def _database_snapshot(db: AsyncSession) -> list[list[dict[str, Any]]]:
    """Read every relevant column from storage, bypassing the identity map."""
    snapshots = []
    for model in (AnnotationQueueModel, AnnotationQueueItemModel, EvaluationTaskScoreModel):
        table = model.__table__
        rows = (await db.execute(select(table).order_by(table.c.id))).mappings().all()
        snapshots.append([dict(row) for row in rows])
    return snapshots


def _assert_no_workspace(resp: Any) -> None:
    assert resp.status_code == 403, resp.text
    assert resp.json() == {
        "detail": {"error": {"code": "NO_WORKSPACE", "message": "An active workspace is required", "details": None}}
    }


def _assert_forbidden(resp: Any) -> None:
    assert resp.status_code == 403, resp.text
    assert resp.json() == {
        "detail": {"error": {"code": "FORBIDDEN", "message": "Workspace membership required", "details": None}}
    }


def _assert_invalid_request(resp: Any, message: str) -> None:
    assert resp.status_code == 400, resp.text
    assert resp.json() == {"detail": {"error": {"code": "INVALID_REQUEST", "message": message, "details": None}}}


async def _unexpected_query(*args: Any, **kwargs: Any) -> None:
    raise AssertionError("Denied requests must not query scores or queues")


async def _unexpected_statistics(*args: Any, **kwargs: Any) -> None:
    raise AssertionError("Denied requests must not run calibration statistics")


@pytest.mark.parametrize("role", [WorkspaceRole.VIEWER, WorkspaceRole.EDITOR, WorkspaceRole.ADMIN])
async def test_role_matrix_reads_scoped_statistics(
    client: AsyncClient, db_session: AsyncSession, role: WorkspaceRole
) -> None:
    """Viewer and above read their workspace's statistics; foreign rows never mix in."""
    ws, _ = await _workspace_with_role(db_session, "ws-one", WorkspaceRole.VIEWER)
    other, _ = await _workspace_with_role(db_session, "ws-two", WorkspaceRole.VIEWER)
    shared_target = uuid.uuid4()
    await _seed_pair(db_session, ws.id, shared_target, machine=0.5, human=0.5)
    machine_only_target = uuid.uuid4()
    await _seed_score(db_session, ws.id, machine_only_target, value=0.2)
    human_only_target = uuid.uuid4()
    await _seed_score(db_session, ws.id, human_only_target, value=0.8, source="human")
    # Same target+metric interference rows in the other workspace.
    await _seed_pair(db_session, other.id, shared_target, machine=0.99, human=0.01)
    await _seed_score(db_session, other.id, machine_only_target, value=0.9, source="human")
    await _seed_score(db_session, other.id, human_only_target, value=0.9)
    before = await _database_snapshot(db_session)
    async with _client_as(client, _member_as(ws, role)) as http:
        response = await http.get(_BASE)
    assert response.status_code == 200, response.text
    metrics = response.json()["metrics"]
    assert [m["metric_name"] for m in metrics] == ["helpfulness"]
    metric = metrics[0]
    assert metric["pair_count"] == 1
    assert metric["agreement_rate"] == 1.0
    assert metric["mae"] == 0.0
    assert metric["data_mode"] == "numeric"
    assert metric["machine_only_count"] == 1
    assert metric["human_only_count"] == 1
    assert metric["heatmap"] == [{"machine_bin": 5, "human_bin": 5, "count": 1}]
    assert await _database_snapshot(db_session) == before


@pytest.mark.parametrize("identity", ["member", "system"])
async def test_missing_workspace_denied_before_any_query(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    identity: str,
) -> None:
    """Workspace-less identities — system included — never reach a query."""
    ws, _ = await _workspace_with_role(db_session, "ws-seeded", WorkspaceRole.VIEWER)
    await _seed_pair(db_session, ws.id, uuid.uuid4())
    ctx = _system_context() if identity == "system" else _no_workspace_context()
    before = await _database_snapshot(db_session)
    with monkeypatch.context() as patch:
        patch.setattr(AsyncSession, "execute", _unexpected_query)
        async with _client_as(client, ctx) as http:
            response = await http.get(
                _BASE,
                params={
                    "agent_id": str(uuid.uuid4()),
                    "task_id": str(uuid.uuid4()),
                    "metric_name": "helpfulness",
                },
            )
    _assert_no_workspace(response)
    assert await _database_snapshot(db_session) == before


async def test_roleless_member_denied_before_statistics(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Membership without a role is 403 before the statistics service runs."""
    from hecate.ops.evaluation.annotation.calibration import CalibrationService

    ws, _ = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.VIEWER)
    await _seed_pair(db_session, ws.id, uuid.uuid4())
    before = await _database_snapshot(db_session)
    with monkeypatch.context() as patch:
        patch.setattr(AsyncSession, "execute", _unexpected_query)
        patch.setattr(CalibrationService, "calibration", _unexpected_statistics)
        async with _client_as(client, _member_as(ws, None)) as http:
            response = await http.get(_BASE)
    _assert_forbidden(response)
    assert await _database_snapshot(db_session) == before


async def test_system_with_workspace_stays_inside_it(client: AsyncClient, db_session: AsyncSession) -> None:
    """Explicit system scope reads its active workspace; a foreign categorical
    queue declaration cannot change this workspace's metric mode."""
    ws, _ = await _workspace_with_role(db_session, "ws-target", WorkspaceRole.VIEWER)
    other, _ = await _workspace_with_role(db_session, "ws-other", WorkspaceRole.VIEWER)
    shared_target = uuid.uuid4()
    await _seed_pair(db_session, ws.id, shared_target, machine=0.6, human=0.6)
    # The foreign workspace declares the shared metric categorical and adds
    # same-target interference rows.
    await _seed_queue(
        db_session,
        other.id,
        metric_defs=[{"name": "helpfulness", "data_type": "categorical", "options": ["good", "bad"]}],
    )
    await _seed_pair(db_session, other.id, shared_target, machine=0.99, human=0.01)
    # The active workspace declares a different metric categorical.
    await _seed_queue(
        db_session,
        ws.id,
        metric_defs=[{"name": "tone", "data_type": "categorical", "options": ["warm", "cold"]}],
    )
    tone_target = uuid.uuid4()
    await _seed_score(db_session, ws.id, tone_target, metric_name="tone", value=0.0, value_label="warm")
    await _seed_score(db_session, ws.id, tone_target, metric_name="tone", value=0.0, source="human", value_label="warm")
    before = await _database_snapshot(db_session)
    async with _client_as(client, replace(_system_context(), workspace_id=ws.id)) as http:
        response = await http.get(_BASE)
    assert response.status_code == 200, response.text
    metrics = {m["metric_name"]: m for m in response.json()["metrics"]}
    assert set(metrics) == {"helpfulness", "tone"}
    assert metrics["helpfulness"]["data_mode"] == "numeric"
    assert metrics["helpfulness"]["pair_count"] == 1
    assert metrics["helpfulness"]["agreement_rate"] == 1.0
    assert metrics["tone"]["data_mode"] == "categorical"
    # Labelled pair matches exactly: agreement 1.0, and a single-label
    # population makes Kappa degenerate to 1.0.
    assert metrics["tone"]["pair_count"] == 1
    assert metrics["tone"]["agreement_rate"] == 1.0
    assert metrics["tone"]["kappa"] == 1.0
    assert await _database_snapshot(db_session) == before


@pytest.mark.parametrize("identity", ["member", "system"])
async def test_agent_task_metric_filters_narrow_within_workspace(
    client: AsyncClient, db_session: AsyncSession, identity: str
) -> None:
    """Filters only narrow inside the workspace; foreign ids match nothing."""
    ws, viewer = await _workspace_with_role(db_session, "ws-filters", WorkspaceRole.VIEWER)
    other, _ = await _workspace_with_role(db_session, "ws-foreign", WorkspaceRole.VIEWER)
    agent_one = uuid.uuid4()
    agent_two = uuid.uuid4()
    task_one = uuid.uuid4()
    task_two = uuid.uuid4()
    target_a, target_b, target_c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await _seed_pair(db_session, ws.id, target_a, machine=0.5, human=0.5, agent_id=agent_one, task_id=task_one)
    await _seed_pair(db_session, ws.id, target_b, machine=0.9, human=0.1, agent_id=agent_two, task_id=task_two)
    await _seed_pair(
        db_session,
        ws.id,
        target_c,
        metric_name="factuality",
        machine=0.7,
        human=0.7,
        agent_id=agent_one,
        task_id=task_two,
    )
    # Foreign interference: same metric rows under another workspace's ids.
    foreign_agent = uuid.uuid4()
    foreign_task = uuid.uuid4()
    await _seed_pair(
        db_session,
        other.id,
        target_a,
        machine=0.9,
        human=0.9,
        agent_id=foreign_agent,
        task_id=foreign_task,
    )
    before = await _database_snapshot(db_session)
    probes = [
        ({"agent_id": str(agent_one)}, {"helpfulness": 1, "factuality": 1}),
        ({"agent_id": str(agent_two)}, {"helpfulness": 1}),
        ({"metric_name": "helpfulness"}, {"helpfulness": 2}),
        ({"task_id": str(task_one)}, {"helpfulness": 1}),
        ({"task_id": str(task_two)}, {"helpfulness": 1, "factuality": 1}),
        ({"agent_id": str(foreign_agent)}, {}),
        ({"task_id": str(foreign_task)}, {}),
        ({"metric_name": "missing"}, {}),
        ({"agent_id": str(agent_one), "task_id": str(task_one), "metric_name": "helpfulness"}, {"helpfulness": 1}),
        ({"agent_id": str(agent_two), "task_id": str(task_one)}, {}),
    ]
    ctx = replace(_system_context(), workspace_id=ws.id) if identity == "system" else viewer
    async with _client_as(client, ctx) as http:
        for params, expected in probes:
            response = await http.get(_BASE, params=params)
            assert response.status_code == 200, (params, response.text)
            pairs = {m["metric_name"]: m["pair_count"] for m in response.json()["metrics"]}
            assert pairs == expected, (params, pairs)
    assert await _database_snapshot(db_session) == before


async def test_soft_deleted_rows_and_queues_excluded(client: AsyncClient, db_session: AsyncSession) -> None:
    """Soft-deleted scores stay out of the statistics; a soft-deleted queue's
    categorical declaration stops shaping the metric mode."""
    ws, viewer = await _workspace_with_role(db_session, "ws-deleted", WorkspaceRole.VIEWER)
    await _seed_queue(
        db_session,
        ws.id,
        metric_defs=[{"name": "helpfulness", "data_type": "categorical", "options": ["good", "bad"]}],
        deleted=True,
    )
    deleted_target = uuid.uuid4()
    await _seed_pair(db_session, ws.id, deleted_target, machine=0.9, human=0.1)
    for row in (
        await db_session.execute(
            select(EvaluationTaskScoreModel).where(EvaluationTaskScoreModel.target_id == deleted_target)
        )
    ).scalars():
        row.deleted = True
        row.deleted_at = datetime.now(UTC)
    await db_session.commit()
    live_target = uuid.uuid4()
    await _seed_pair(db_session, ws.id, live_target, machine=0.5, human=0.5)
    before = await _database_snapshot(db_session)
    async with _client_as(client, viewer) as http:
        response = await http.get(_BASE)
    assert response.status_code == 200, response.text
    metrics = response.json()["metrics"]
    assert [m["metric_name"] for m in metrics] == ["helpfulness"]
    metric = metrics[0]
    # The deleted queue no longer declares the metric categorical.
    assert metric["data_mode"] == "numeric"
    assert metric["pair_count"] == 1
    assert metric["agreement_rate"] == 1.0
    assert metric["machine_only_count"] == 0
    assert metric["human_only_count"] == 0
    assert await _database_snapshot(db_session) == before


async def test_zero_uuid_workspace_is_a_legal_scope(client: AsyncClient, db_session: AsyncSession) -> None:
    """The zero-UUID workspace stays a valid explicit scope for the read."""
    zero_ws = uuid.UUID(int=0)
    target = uuid.uuid4()
    await _seed_pair(db_session, zero_ws, target, machine=0.4, human=0.5)
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
        response = await http.get(_BASE)
    assert response.status_code == 200, response.text
    metrics = response.json()["metrics"]
    assert [m["metric_name"] for m in metrics] == ["helpfulness"]
    assert metrics[0]["pair_count"] == 1
    assert metrics[0]["mae"] == 0.1
    assert await _database_snapshot(db_session) == before


async def test_empty_workspace_reports_empty_metrics(client: AsyncClient, db_session: AsyncSession) -> None:
    """A workspace with no rows keeps the legitimate empty-result shape."""
    ws, viewer = await _workspace_with_role(db_session, "ws-empty", WorkspaceRole.VIEWER)
    other, _ = await _workspace_with_role(db_session, "ws-full", WorkspaceRole.VIEWER)
    await _seed_pair(db_session, other.id, uuid.uuid4())
    before = await _database_snapshot(db_session)
    async with _client_as(client, viewer) as http:
        response = await http.get(_BASE)
    assert response.status_code == 200, response.text
    assert response.json() == {"metrics": []}
    assert await _database_snapshot(db_session) == before


async def test_inverted_window_is_structured_validation_error(client: AsyncClient, db_session: AsyncSession) -> None:
    """Window violations map to the structured 400, never a 500."""
    ws, viewer = await _workspace_with_role(db_session, "ws-window", WorkspaceRole.VIEWER)
    await _seed_pair(db_session, ws.id, uuid.uuid4())
    before = await _database_snapshot(db_session)
    async with _client_as(client, viewer) as http:
        inverted = await http.get(
            _BASE,
            params={"start_date": "2030-01-01T00:00:00Z", "end_date": "2020-01-01T00:00:00Z"},
        )
        _assert_invalid_request(inverted, "start_date must be before end_date")
        unparseable = await http.get(_BASE, params={"start_date": "not-a-date"})
        _assert_invalid_request(unparseable, "invalid start_date: 'not-a-date'")
        valid = await http.get(_BASE, params={"start_date": "2020-01-01T00:00:00Z"})
    assert valid.status_code == 200, valid.text
    assert await _database_snapshot(db_session) == before


@pytest.mark.parametrize("identity", ["member", "system"])
async def test_latest_valid_machine_score_pairs_with_only_in_window_human_rows(
    client: AsyncClient, db_session: AsyncSession, identity: str
) -> None:
    """Old automated evidence remains eligible; only human timestamps bound the window."""
    ws, viewer = await _workspace_with_role(db_session, "ws-pair-window", WorkspaceRole.VIEWER)
    target = uuid.uuid4()
    start = datetime(2026, 2, 1, tzinfo=UTC)
    for days_before, value, deleted in [(4, 0.1, False), (3, 0.8, False), (2, -1.0, False), (1, 0.2, True)]:
        await _seed_score(
            db_session,
            ws.id,
            target,
            value=value,
            deleted=deleted,
            created_at=start - timedelta(days=days_before),
        )
    for day_offset, value in [(-1, 0.1), (1, 0.8), (3, 0.1)]:
        await _seed_score(
            db_session,
            ws.id,
            target,
            value=value,
            source="human",
            created_at=start + timedelta(days=day_offset),
        )
    before = await _database_snapshot(db_session)
    ctx = replace(_system_context(), workspace_id=ws.id) if identity == "system" else viewer
    async with _client_as(client, ctx) as http:
        reports = []
        for start_date, end_date in [
            ("2026-02-01T00:00:00Z", "2026-02-03T00:00:00Z"),
            ("2026-02-01T08:00:00+08:00", "2026-02-03T08:00:00+08:00"),
        ]:
            response = await http.get(_BASE, params={"start_date": start_date, "end_date": end_date})
            assert response.status_code == 200, response.text
            reports.append(response.json())
    assert reports[0] == reports[1]
    assert reports[0] == {
        "metrics": [
            {
                "metric_name": "helpfulness",
                "pair_count": 1,
                "agreement_rate": 1.0,
                "mae": 0.0,
                "kappa": None,
                "data_mode": "numeric",
                "machine_only_count": 0,
                "human_only_count": 0,
                "heatmap": [{"machine_bin": 8, "human_bin": 8, "count": 1}],
            }
        ]
    }
    assert await _database_snapshot(db_session) == before
