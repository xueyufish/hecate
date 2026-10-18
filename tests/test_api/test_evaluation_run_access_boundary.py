"""Evaluation run read access boundary (step10).

Covers the shared read boundary on the four evaluation run endpoints:

- Workspace ownership: detail, scores, and compare surface a missing,
  soft-deleted, or cross-workspace run as the *same* 404 body (existence
  is never disclosed — scores used to answer 200 with an empty list, and
  compare used to distinguish missing from cross-workspace via the 404
  detail, both of which leaked existence); listing is scoped with an
  accurate ``total`` and cross-workspace filter values cannot widen it.
- Role floor: viewer suffices for all four endpoints in the caller's
  workspace; membership without a role is 403 (after ownership, so
  cross-workspace stays 404).
- Identity rules: missing-workspace non-system identities never degrade
  to a global query; only explicit system scope crosses workspaces.
- Read-only: rejected requests leave run and score state untouched, and
  the compare algorithm's 422 semantics survive the boundary unchanged.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.auth_context import AuthContext
from hecate.models.evaluation import EvaluationRunModel, EvaluationScoreModel, EvaluationTaskModel
from hecate.models.organization import OrganizationModel
from hecate.models.workspace import WorkspaceModel
from hecate.models.workspace_member import WorkspaceMemberModel, WorkspaceRole


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


async def _member_as(db: AsyncSession, ws: WorkspaceModel, role: WorkspaceRole) -> AuthContext:
    """Add a new member with ``role`` to an existing workspace."""
    user_id = uuid.uuid4()
    db.add(WorkspaceMemberModel(user_id=user_id, workspace_id=ws.id, role=role))
    await db.commit()
    return AuthContext(
        user_id=user_id,
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


def _no_role_context(ws: WorkspaceModel) -> AuthContext:
    """Active workspace without any membership role."""
    return AuthContext(
        user_id=uuid.uuid4(),
        org_id=ws.org_id,
        workspace_id=ws.id,
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


async def _seed_run(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    dataset_id: uuid.UUID | None = None,
    n_scores: int = 1,
    metric: str = "accuracy",
    deleted: bool = False,
) -> EvaluationRunModel:
    """Seed a completed run (with scores) directly via the ORM."""
    now = datetime.now(UTC)
    run = EvaluationRunModel(
        dataset_id=dataset_id or uuid.uuid4(),
        workspace_id=workspace_id,
        evaluator_configs=[metric],
        status="completed",
        started_at=now,
        completed_at=now,
    )
    run.deleted = deleted
    db.add(run)
    await db.flush()
    for _ in range(n_scores):
        db.add(
            EvaluationScoreModel(
                run_id=run.id,
                item_id=uuid.uuid4(),
                metric_name=metric,
                value=0.9,
                source="deterministic",
                workspace_id=workspace_id,
            )
        )
    await db.commit()
    return run


async def _run_count(db: AsyncSession) -> int:
    return len((await db.execute(select(EvaluationRunModel))).scalars().all())


async def _score_count(db: AsyncSession) -> int:
    return len((await db.execute(select(EvaluationScoreModel))).scalars().all())


def _assert_not_found(resp: Any) -> None:
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"]["error"]["code"] == "NOT_FOUND"


def _assert_forbidden(resp: Any) -> None:
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"]["error"]["code"] == "FORBIDDEN"


# ---------------------------------------------------------------------------
# Workspace ownership: detail / scores / compare hide other workspaces
# ---------------------------------------------------------------------------


async def test_detail_cross_workspace_matches_missing(client: AsyncClient, db_session: AsyncSession) -> None:
    """A W2 member sees a W1 run exactly like a nonexistent one."""
    ws, _ = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.EDITOR)
    _, outsider_ctx = await _workspace_with_role(db_session, "ws-outsider", WorkspaceRole.EDITOR)
    run = await _seed_run(db_session, ws.id)

    async with _client_as(client, outsider_ctx) as http:
        probe = await http.get(f"/api/evaluation/runs/{run.id}")
        reference = await http.get(f"/api/evaluation/runs/{uuid.uuid4()}")
        assert probe.status_code == 404, probe.text
        assert probe.json()["detail"] == reference.json()["detail"]
        assert probe.json()["detail"]["error"]["code"] == "NOT_FOUND"
        assert str(run.id) not in probe.text


async def test_scores_cross_workspace_is_not_found(client: AsyncClient, db_session: AsyncSession) -> None:
    """Scores for another workspace's run 404 instead of an empty list."""
    ws, _ = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.EDITOR)
    _, outsider_ctx = await _workspace_with_role(db_session, "ws-outsider", WorkspaceRole.EDITOR)
    run = await _seed_run(db_session, ws.id, n_scores=2)

    async with _client_as(client, outsider_ctx) as http:
        resp = await http.get(f"/api/evaluation/runs/{run.id}/scores")
        assert resp.status_code == 404, resp.text
        assert resp.json()["detail"]["error"]["message"] == "Run not found"


async def test_scores_missing_and_deleted_match_cross_workspace(client: AsyncClient, db_session: AsyncSession) -> None:
    """Missing and soft-deleted runs share the boundary's one 404 body."""
    ws, owner_ctx = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.EDITOR)
    deleted_run = await _seed_run(db_session, ws.id, deleted=True)

    async with _client_as(client, owner_ctx) as http:
        probe_deleted = await http.get(f"/api/evaluation/runs/{deleted_run.id}/scores")
        reference_missing = await http.get(f"/api/evaluation/runs/{uuid.uuid4()}/scores")
        assert probe_deleted.status_code == 404, probe_deleted.text
        assert probe_deleted.json()["detail"] == reference_missing.json()["detail"]


async def test_compare_invisible_runs_return_uniform_404_without_ids(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Compare 404s are identical across conditions and echo no run ID."""
    ws, owner_ctx = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.EDITOR)
    _, outsider_ctx = await _workspace_with_role(db_session, "ws-outsider", WorkspaceRole.EDITOR)
    run = await _seed_run(db_session, ws.id)
    deleted_run = await _seed_run(db_session, ws.id, deleted=True)
    other = await _seed_run(db_session, ws.id)

    async with _client_as(client, owner_ctx) as http:
        deleted_probe = await http.post(
            "/api/evaluation/runs/compare",
            json={"baseline_run_id": str(deleted_run.id), "candidate_run_id": str(other.id)},
        )
        missing_probe = await http.post(
            "/api/evaluation/runs/compare",
            json={"baseline_run_id": str(uuid.uuid4()), "candidate_run_id": str(other.id)},
        )
        assert deleted_probe.status_code == 404, deleted_probe.text
        assert missing_probe.status_code == 404, missing_probe.text
        assert deleted_probe.json()["detail"] == missing_probe.json()["detail"]
        assert str(deleted_run.id) not in deleted_probe.text
        assert str(other.id) not in deleted_probe.text

    async with _client_as(client, outsider_ctx) as http:
        cross_probe = await http.post(
            "/api/evaluation/runs/compare",
            json={"baseline_run_id": str(run.id), "candidate_run_id": str(other.id)},
        )
        assert cross_probe.status_code == 404, cross_probe.text
        assert cross_probe.json()["detail"] == deleted_probe.json()["detail"]
        assert str(run.id) not in cross_probe.text


async def test_listing_is_scoped_with_accurate_total(client: AsyncClient, db_session: AsyncSession) -> None:
    """Each workspace sees only its runs; total counts within scope."""
    ws_a, ctx_a = await _workspace_with_role(db_session, "ws-a", WorkspaceRole.EDITOR)
    ws_b, ctx_b = await _workspace_with_role(db_session, "ws-b", WorkspaceRole.EDITOR)
    await _seed_run(db_session, ws_a.id)
    await _seed_run(db_session, ws_a.id)
    await _seed_run(db_session, ws_b.id)

    async with _client_as(client, ctx_a) as http:
        resp = await http.get("/api/evaluation/runs")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 2
        assert all(item["workspace_id"] == str(ws_a.id) for item in body["items"])

    async with _client_as(client, ctx_b) as http:
        resp = await http.get("/api/evaluation/runs")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 1
        assert [item["workspace_id"] for item in body["items"]] == [str(ws_b.id)]


async def test_cross_workspace_filter_values_do_not_escalate(client: AsyncClient, db_session: AsyncSession) -> None:
    """Filtering by another workspace's dataset/task yields an empty page."""
    ws_a, ctx_a = await _workspace_with_role(db_session, "ws-a", WorkspaceRole.EDITOR)
    ws_b, _ = await _workspace_with_role(db_session, "ws-b", WorkspaceRole.EDITOR)
    foreign_run = await _seed_run(db_session, ws_b.id)
    foreign_task = EvaluationTaskModel(name="Foreign task", task_type="offline", workspace_id=ws_b.id)
    db_session.add(foreign_task)
    await db_session.flush()
    foreign_run.task_id = foreign_task.id
    await db_session.commit()

    async with _client_as(client, ctx_a) as http:
        by_dataset = await http.get(f"/api/evaluation/runs?dataset_id={foreign_run.dataset_id}")
        assert by_dataset.status_code == 200, by_dataset.text
        assert by_dataset.json() == {"items": [], "total": 0}

        by_task = await http.get(f"/api/evaluation/runs?task_id={foreign_task.id}")
        assert by_task.status_code == 200, by_task.text
        assert by_task.json() == {"items": [], "total": 0}

    async with _client_as(client, _system_context()) as http:
        by_task = await http.get(f"/api/evaluation/runs?task_id={foreign_task.id}")
        assert by_task.status_code == 200, by_task.text
        assert by_task.json()["total"] == 1
        assert [item["id"] for item in by_task.json()["items"]] == [str(foreign_run.id)]


# ---------------------------------------------------------------------------
# Role floor: viewer reads; membership without role is denied
# ---------------------------------------------------------------------------


async def test_viewer_reads_all_four_endpoints(client: AsyncClient, db_session: AsyncSession) -> None:
    """A viewer of the run's workspace reads listing, detail, scores, compare."""
    ws, _ = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.EDITOR)
    viewer_ctx = await _member_as(db_session, ws, WorkspaceRole.VIEWER)
    dataset = uuid.uuid4()
    baseline = await _seed_run(db_session, ws.id, dataset_id=dataset)
    candidate = await _seed_run(db_session, ws.id, dataset_id=dataset)

    async with _client_as(client, viewer_ctx) as http:
        listing = await http.get("/api/evaluation/runs")
        assert listing.status_code == 200, listing.text
        assert listing.json()["total"] == 2

        detail = await http.get(f"/api/evaluation/runs/{baseline.id}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["total_scores"] == 1

        scores = await http.get(f"/api/evaluation/runs/{baseline.id}/scores")
        assert scores.status_code == 200, scores.text
        assert scores.json()["total"] == 1

        compare = await http.post(
            "/api/evaluation/runs/compare",
            json={"baseline_run_id": str(baseline.id), "candidate_run_id": str(candidate.id)},
        )
        assert compare.status_code == 200, compare.text
        assert compare.json()["overall_regressed"] is False


async def test_membership_without_role_is_forbidden(client: AsyncClient, db_session: AsyncSession) -> None:
    """Same-workspace identity without a role is 403 on all four reads."""
    ws, _ = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.EDITOR)
    run = await _seed_run(db_session, ws.id)
    other = await _seed_run(db_session, ws.id)
    no_role = _no_role_context(ws)

    async with _client_as(client, no_role) as http:
        _assert_forbidden(await http.get("/api/evaluation/runs"))
        _assert_forbidden(await http.get(f"/api/evaluation/runs/{run.id}"))
        _assert_forbidden(await http.get(f"/api/evaluation/runs/{run.id}/scores"))
        _assert_forbidden(
            await http.post(
                "/api/evaluation/runs/compare",
                json={"baseline_run_id": str(run.id), "candidate_run_id": str(other.id)},
            )
        )


# ---------------------------------------------------------------------------
# Identity rules: missing workspace never degrades; system scope crosses
# ---------------------------------------------------------------------------


async def test_missing_workspace_identity_never_degrades_to_global(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """No active workspace: denied everywhere, zero rows leak."""
    ws, _ = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.EDITOR)
    run = await _seed_run(db_session, ws.id)
    other = await _seed_run(db_session, ws.id)
    ghost = _no_workspace_context()

    async with _client_as(client, ghost) as http:
        _assert_forbidden(await http.get("/api/evaluation/runs"))
        _assert_not_found(await http.get(f"/api/evaluation/runs/{run.id}"))
        _assert_not_found(await http.get(f"/api/evaluation/runs/{run.id}/scores"))
        _assert_not_found(
            await http.post(
                "/api/evaluation/runs/compare",
                json={"baseline_run_id": str(run.id), "candidate_run_id": str(other.id)},
            )
        )

        leaked = await http.get("/api/evaluation/runs")
        assert leaked.status_code == 403, leaked.text


async def test_system_scope_reads_across_workspaces(client: AsyncClient, db_session: AsyncSession) -> None:
    """Only the explicit deploy-time bootstrap key crosses workspaces."""
    ws_a, _ = await _workspace_with_role(db_session, "ws-a", WorkspaceRole.EDITOR)
    ws_b, _ = await _workspace_with_role(db_session, "ws-b", WorkspaceRole.EDITOR)
    dataset_id = uuid.uuid4()
    run_a = await _seed_run(db_session, ws_a.id, dataset_id=dataset_id)
    run_b = await _seed_run(db_session, ws_b.id, dataset_id=dataset_id)

    async with _client_as(client, _system_context()) as http:
        listing = await http.get("/api/evaluation/runs")
        assert listing.status_code == 200, listing.text
        assert listing.json()["total"] == 2

        detail = await http.get(f"/api/evaluation/runs/{run_a.id}")
        assert detail.status_code == 200, detail.text

        scores = await http.get(f"/api/evaluation/runs/{run_b.id}/scores")
        assert scores.status_code == 200, scores.text
        assert scores.json()["total"] == 1

        compare = await http.post(
            "/api/evaluation/runs/compare",
            json={"baseline_run_id": str(run_a.id), "candidate_run_id": str(run_b.id)},
        )
        assert compare.status_code == 200, compare.text
        assert compare.json()["baseline_run_id"] == str(run_a.id)
        assert compare.json()["candidate_run_id"] == str(run_b.id)


@pytest.mark.parametrize("invisible_side", ["baseline_run_id", "candidate_run_id"])
@pytest.mark.parametrize("condition", ["missing", "deleted", "foreign"])
@pytest.mark.parametrize("role", [WorkspaceRole.VIEWER, None])
async def test_compare_checks_both_ownerships_before_role(
    client: AsyncClient,
    db_session: AsyncSession,
    invisible_side: str,
    condition: str,
    role: WorkspaceRole | None,
) -> None:
    """Either invisible input yields the same 404 before any role denial."""
    ws, viewer_ctx = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.VIEWER)
    foreign_ws, _ = await _workspace_with_role(db_session, "ws-foreign", WorkspaceRole.VIEWER)
    visible = await _seed_run(db_session, ws.id)
    if condition == "missing":
        invisible_id = uuid.uuid4()
    else:
        invisible = await _seed_run(
            db_session,
            foreign_ws.id if condition == "foreign" else ws.id,
            deleted=condition == "deleted",
        )
        invisible_id = invisible.id
    ctx = viewer_ctx if role is not None else _no_role_context(ws)
    payload = {"baseline_run_id": str(visible.id), "candidate_run_id": str(visible.id)}
    payload[invisible_side] = str(invisible_id)

    async with _client_as(client, ctx) as http:
        response = await http.post("/api/evaluation/runs/compare", json=payload)
        reference = await http.get(f"/api/evaluation/runs/{uuid.uuid4()}")
        _assert_not_found(response)
        assert response.json() == reference.json()
        assert str(visible.id) not in response.text
        assert str(invisible_id) not in response.text


# ---------------------------------------------------------------------------
# Read-only guarantee, preserved compare semantics, empty-score contract
# ---------------------------------------------------------------------------


async def test_rejected_requests_leave_state_untouched(client: AsyncClient, db_session: AsyncSession) -> None:
    """Boundary rejections on all four endpoints mutate nothing."""
    ws, _ = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.EDITOR)
    _, outsider_ctx = await _workspace_with_role(db_session, "ws-outsider", WorkspaceRole.EDITOR)
    run = await _seed_run(db_session, ws.id)
    other = await _seed_run(db_session, ws.id)
    runs_before, scores_before = await _run_count(db_session), await _score_count(db_session)

    async with _client_as(client, outsider_ctx) as http:
        await http.get(f"/api/evaluation/runs/{run.id}")
        await http.get(f"/api/evaluation/runs/{run.id}/scores")
        await http.post(
            "/api/evaluation/runs/compare",
            json={"baseline_run_id": str(run.id), "candidate_run_id": str(other.id)},
        )
        await http.get("/api/evaluation/runs")

    assert await _run_count(db_session) == runs_before
    assert await _score_count(db_session) == scores_before


async def test_compare_422_semantics_survive_boundary(client: AsyncClient, db_session: AsyncSession) -> None:
    """Same-workspace compare keeps its dataset-mismatch 422 branch."""
    ws, _ = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.EDITOR)
    viewer_ctx = await _member_as(db_session, ws, WorkspaceRole.VIEWER)
    baseline = await _seed_run(db_session, ws.id, dataset_id=uuid.uuid4())
    candidate = await _seed_run(db_session, ws.id, dataset_id=uuid.uuid4())

    async with _client_as(client, viewer_ctx) as http:
        resp = await http.post(
            "/api/evaluation/runs/compare",
            json={"baseline_run_id": str(baseline.id), "candidate_run_id": str(candidate.id)},
        )
        assert resp.status_code == 422, resp.text
        assert "different datasets" in resp.text


async def test_visible_run_with_no_scores_returns_empty_page(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """A visible run without scores keeps the 200 empty-list response."""
    ws, viewer_ctx = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.VIEWER)
    run = await _seed_run(db_session, ws.id, n_scores=0)

    async with _client_as(client, viewer_ctx) as http:
        resp = await http.get(f"/api/evaluation/runs/{run.id}/scores")
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"items": [], "total": 0}
