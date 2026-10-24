"""Evaluation task run trigger access boundary (step10).

Covers the write/trigger boundary on ``POST /api/evaluation/tasks/{task_id}/runs``:

- Workspace ownership: a missing, soft-deleted, or cross-workspace task
  yields the *same* constant 404 (no IDs echoed), checked before the role
  floor so a role denial never discloses task existence; a non-system
  identity without an active workspace receives that 404 without any
  resource query.
- Role floor: triggering requires editor in the task's workspace; viewer
  and role-less membership are 403 even for tasks whose type/status/config
  would fail validation — authorization precedes task validation.
- Zero execution on denial: rejected requests leave task, run, and score
  rows byte-identical and never hand off to the background runner.
- System scope: explicit system keys trigger by task ID across workspaces
  (with or without an active workspace); the run lands in the resolved
  task's workspace.
- Success semantics: editor/admin keep 202 + ``run_id`` + ``status``
  "pending"; the persisted run binds the task, its dataset, evaluator
  configs, and the task's workspace; the background handoff happens only
  after the request-session commit, carries the exact run/task IDs, and is
  skipped when the commit fails. Authorized requests keep the 400
  INVALID_REQUEST branch for online, disabled, and invalid-evaluator
  tasks without creating a run. A same-workspace viewer reads the run back
  through the task-run listing and run detail endpoints; other workspaces
  cannot observe it.

Tests run the real trigger service (task validation + run persistence);
only ``OfflineTaskRunner.run_in_background`` — the background scheduling
boundary — is replaced by an observable stub. No real background task or
external model is started.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from pytest import MonkeyPatch
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import hecate.ops.evaluation.engine as evaluation_engine
from hecate.core.auth_context import AuthContext
from hecate.models.evaluation import (
    EvaluationRunModel,
    EvaluationScoreModel,
    EvaluationTaskModel,
)
from hecate.models.organization import OrganizationModel
from hecate.models.workspace import WorkspaceModel
from hecate.models.workspace_member import WorkspaceMemberModel, WorkspaceRole
from hecate.ops.evaluation.format_evaluators import ExactMatchEvaluator
from hecate.ops.evaluation.tasks.runner import OfflineTaskRunner
from hecate.ops.evaluation.tasks.service import EvaluationTaskService


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


def _system_context(workspace_id: uuid.UUID | None = None) -> AuthContext:
    """Deploy-time bootstrap identity: the only cross-workspace bypass.

    May carry an active workspace — system scope stays system scope, and
    the triggered run still targets the task's workspace, not the
    caller's active one.
    """
    return AuthContext(
        user_id=uuid.uuid4(),
        org_id=None,
        workspace_id=workspace_id,
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
async def _client_as(
    client: AsyncClient,
    ctx: AuthContext,
    *,
    propagate_errors: bool = True,
) -> AsyncIterator[AsyncClient]:
    """Yield a client authenticating as ``ctx``; restore the app override.

    ``propagate_errors=False`` swaps in an ASGITransport that converts
    unhandled server exceptions into 500 responses instead of raising,
    for fault-injection tests.
    """
    from hecate.core.deps_workspace import get_auth_context
    from hecate.main import app

    async def override_auth() -> AuthContext:
        return ctx

    saved = app.dependency_overrides.get(get_auth_context)
    app.dependency_overrides[get_auth_context] = override_auth
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=propagate_errors),
            base_url="http://test",
        ) as http:
            yield http
    finally:
        if saved is None:
            app.dependency_overrides.pop(get_auth_context, None)
        else:
            app.dependency_overrides[get_auth_context] = saved


def _seed_evaluator_registry(monkeypatch: MonkeyPatch) -> None:
    """Seed the engine class index with a real deterministic evaluator.

    Registration normally happens at app startup; ASGITransport skips the
    lifespan, so authorized-success tests install the same class the
    startup path would — ``validate_task`` resolves evaluator names
    through this index.
    """
    monkeypatch.setattr(
        evaluation_engine,
        "_EVALUATOR_CLASS_REGISTRY",
        {"exact_match": ExactMatchEvaluator},
    )


class _HandoffRecorder:
    """Record scheduling, successful commits, and rows visible at handoff."""

    def __init__(self) -> None:
        self.calls: list[tuple[uuid.UUID, uuid.UUID]] = []
        self.committed_sessions: list[AsyncSession] = []
        self.persisted_runs: list[dict[str, Any]] = []


def _stub_handoff(monkeypatch: MonkeyPatch) -> _HandoffRecorder:
    recorder = _HandoffRecorder()
    original_commit = AsyncSession.commit

    async def record_commit(self: AsyncSession) -> None:
        await original_commit(self)
        recorder.committed_sessions.append(self)

    async def handoff(self: OfflineTaskRunner, run_id: uuid.UUID, task_id: uuid.UUID) -> None:
        recorder.calls.append((run_id, task_id))
        assert self.db in recorder.committed_sessions, "The request session must commit before scheduling"
        assert not self.db.in_transaction()
        assert self.db.bind is not None
        # Reuse the fixture's database with a separate session, like the real runner.
        async with AsyncSession(bind=self.db.bind) as background_db:
            table = EvaluationRunModel.__table__
            row = (await background_db.execute(select(table).where(table.c.id == run_id))).mappings().one()
            assert row["task_id"] == task_id and row["status"] == "pending"
            recorder.persisted_runs.append(dict(row))

    monkeypatch.setattr(AsyncSession, "commit", record_commit)
    monkeypatch.setattr(OfflineTaskRunner, "run_in_background", handoff)
    return recorder


async def _seed_task(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    name: str,
    *,
    task_type: str = "offline",
    status: str = "active",
    evaluators: list[str] | None = None,
    config: dict[str, Any] | None = None,
    deleted: bool = False,
) -> EvaluationTaskModel:
    """Create an evaluation task directly via the ORM.

    The default offline config names a dataset by ID only — the trigger
    path validates config shape, not dataset existence.
    """
    task = EvaluationTaskModel(
        name=f"{name}-{uuid.uuid4().hex[:8]}",
        task_type=task_type,
        status=status,
        evaluator_configs=evaluators if evaluators is not None else ["exact_match"],
        config=config if config is not None else {"dataset_id": str(uuid.uuid4())},
        workspace_id=workspace_id,
    )
    task.deleted = deleted
    db.add(task)
    await db.commit()
    return task


async def _state_snapshot(db: AsyncSession) -> list[list[dict[str, Any]]]:
    """Read every task/run/score column directly from persisted storage."""
    snapshots = []
    for model in (EvaluationTaskModel, EvaluationRunModel, EvaluationScoreModel):
        table = model.__table__
        rows = (await db.execute(select(table).order_by(table.c.id))).mappings().all()
        snapshots.append([dict(row) for row in rows])
    return snapshots


async def _run_count(db: AsyncSession) -> int:
    return len((await db.execute(select(EvaluationRunModel))).scalars().all())


def _assert_not_found(resp: Any) -> None:
    assert resp.status_code == 404, resp.text
    expected = {"detail": {"error": {"code": "NOT_FOUND", "message": "Evaluation task not found", "details": None}}}
    assert resp.json() == expected


def _assert_forbidden(resp: Any, *, roleless: bool = False) -> None:
    assert resp.status_code == 403, resp.text
    message = "Workspace membership required" if roleless else "Editor role required"
    assert resp.json() == {"detail": {"error": {"code": "FORBIDDEN", "message": message, "details": None}}}


def _assert_invalid_request(resp: Any, message: str) -> None:
    assert resp.status_code == 400, resp.text
    assert resp.json() == {"detail": {"error": {"code": "INVALID_REQUEST", "message": message, "details": None}}}


# ---------------------------------------------------------------------------
# Workspace ownership: one constant 404, checked before the role floor
# ---------------------------------------------------------------------------


async def test_cross_workspace_task_matches_missing(client: AsyncClient, db_session: AsyncSession) -> None:
    """A W2 editor sees a W1 task exactly like a nonexistent one."""
    ws, _ = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.EDITOR)
    task = await _seed_task(db_session, ws.id, "task")
    _, outsider_ctx = await _workspace_with_role(db_session, "ws-outsider", WorkspaceRole.EDITOR)

    async with _client_as(client, outsider_ctx) as http:
        probe = await http.post(f"/api/evaluation/tasks/{task.id}/runs")
        reference = await http.post(f"/api/evaluation/tasks/{uuid.uuid4()}/runs")
        _assert_not_found(probe)
        assert probe.json()["detail"] == reference.json()["detail"]
        assert str(task.id) not in probe.text


async def test_deleted_task_matches_missing(client: AsyncClient, db_session: AsyncSession) -> None:
    """A soft-deleted task in the caller's workspace shares the 404."""
    ws, owner_ctx = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.EDITOR)
    deleted_task = await _seed_task(db_session, ws.id, "task-deleted", deleted=True)

    async with _client_as(client, owner_ctx) as http:
        probe = await http.post(f"/api/evaluation/tasks/{deleted_task.id}/runs")
        reference = await http.post(f"/api/evaluation/tasks/{uuid.uuid4()}/runs")
        _assert_not_found(probe)
        assert probe.json()["detail"] == reference.json()["detail"]


async def test_missing_workspace_identity_never_queries(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: MonkeyPatch,
) -> None:
    """No active workspace: the constant task 404, without any resource query."""
    ws, _ = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.EDITOR)
    task = await _seed_task(db_session, ws.id, "task")
    ghost = _no_workspace_context()

    async def unexpected_query(*args: object, **kwargs: object) -> None:
        raise AssertionError("Missing workspace must be rejected before any resource query")

    before = await _state_snapshot(db_session)
    with monkeypatch.context() as patch:
        patch.setattr(AsyncSession, "execute", unexpected_query)
        async with _client_as(client, ghost) as http:
            for task_id in (task.id, uuid.uuid4()):
                _assert_not_found(await http.post(f"/api/evaluation/tasks/{task_id}/runs"))
    assert await _state_snapshot(db_session) == before


@pytest.mark.parametrize("condition", ["missing", "deleted", "foreign"])
@pytest.mark.parametrize("role", [WorkspaceRole.VIEWER, None])
async def test_ownership_precedes_role(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: MonkeyPatch,
    condition: str,
    role: WorkspaceRole | None,
) -> None:
    """An invisible task yields 404 even for identities without editor rights."""
    recorder = _stub_handoff(monkeypatch)
    ws, viewer_ctx = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.VIEWER)
    foreign_ws, _ = await _workspace_with_role(db_session, "ws-foreign", WorkspaceRole.VIEWER)
    if condition == "missing":
        task_id = uuid.uuid4()
    else:
        invisible = await _seed_task(
            db_session,
            foreign_ws.id if condition == "foreign" else ws.id,
            f"task-{condition}",
            deleted=condition == "deleted",
        )
        task_id = invisible.id
    ctx = viewer_ctx if role == WorkspaceRole.VIEWER else _no_role_context(ws)

    async with _client_as(client, ctx) as http:
        probe = await http.post(f"/api/evaluation/tasks/{task_id}/runs")
        reference = await http.post(f"/api/evaluation/tasks/{uuid.uuid4()}/runs")
        _assert_not_found(probe)
        assert probe.json()["detail"] == reference.json()["detail"]
        assert str(task_id) not in probe.text
    assert recorder.calls == []


# ---------------------------------------------------------------------------
# Role floor: editor required; denial precedes task validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", [WorkspaceRole.VIEWER, None])
async def test_viewer_is_forbidden_before_task_validation(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: MonkeyPatch,
    role: WorkspaceRole | None,
) -> None:
    """A viewer of the task's workspace is 403 — even for a trigger-invalid task."""
    recorder = _stub_handoff(monkeypatch)
    ws, _ = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.EDITOR)
    viewer_ctx = await _member_as(db_session, ws, role) if role is not None else _no_role_context(ws)
    # Online + disabled: both would fail task validation with 400 — the
    # role floor must fire first.
    online = await _seed_task(
        db_session, ws.id, "task-online", task_type="online", config={"agent_id": str(uuid.uuid4())}
    )
    disabled = await _seed_task(db_session, ws.id, "task-disabled", status="disabled")
    unknown = await _seed_task(db_session, ws.id, "task-unknown", evaluators=["nonexistent"])
    invalid = await _seed_task(db_session, ws.id, "task-invalid", config={})

    async with _client_as(client, viewer_ctx) as http:
        for task in (online, disabled, unknown, invalid):
            _assert_forbidden(await http.post(f"/api/evaluation/tasks/{task.id}/runs"), roleless=role is None)

    assert recorder.calls == []
    assert await _run_count(db_session) == 0


async def test_membership_without_role_is_forbidden(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: MonkeyPatch,
) -> None:
    """An active workspace without any membership role is 403."""
    recorder = _stub_handoff(monkeypatch)
    ws, _ = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.EDITOR)
    task = await _seed_task(db_session, ws.id, "task")
    no_role = _no_role_context(ws)

    async with _client_as(client, no_role) as http:
        _assert_forbidden(await http.post(f"/api/evaluation/tasks/{task.id}/runs"), roleless=True)

    assert recorder.calls == []
    assert await _run_count(db_session) == 0


async def test_denial_preserves_all_persisted_state(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: MonkeyPatch,
) -> None:
    """Boundary rejections mutate no task, run, or score column."""
    recorder = _stub_handoff(monkeypatch)
    ws, editor = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.EDITOR)
    _, outsider = await _workspace_with_role(db_session, "ws-outsider", WorkspaceRole.EDITOR)
    viewer = await _member_as(db_session, ws, WorkspaceRole.VIEWER)
    task = await _seed_task(db_session, ws.id, "task")
    deleted_task = await _seed_task(db_session, ws.id, "task-deleted", deleted=True)
    run = EvaluationRunModel(
        dataset_id=uuid.uuid4(),
        task_id=task.id,
        workspace_id=ws.id,
        evaluator_configs=["exact_match"],
        status="completed",
    )
    db_session.add(run)
    await db_session.flush()
    db_session.add(
        EvaluationScoreModel(
            run_id=run.id,
            item_id=uuid.uuid4(),
            workspace_id=ws.id,
            metric_name="exact_match",
            value=0.5,
            source="deterministic",
        )
    )
    await db_session.commit()
    before = await _state_snapshot(db_session)

    async def unexpected_write(*args: object, **kwargs: object) -> None:
        raise AssertionError("Rejected triggers must not enter the service or commit")

    monkeypatch.setattr(EvaluationTaskService, "trigger_run", unexpected_write)
    monkeypatch.setattr(AsyncSession, "commit", unexpected_write)
    probes: list[tuple[AuthContext, uuid.UUID, int]] = [
        (editor, deleted_task.id, 404),
        (editor, uuid.uuid4(), 404),
        (viewer, task.id, 403),
        (_no_role_context(ws), task.id, 403),
        (outsider, task.id, 404),
        (_no_workspace_context(), task.id, 404),
        (viewer, deleted_task.id, 404),
        (_system_context(), deleted_task.id, 404),
        (_system_context(), uuid.uuid4(), 404),
    ]
    for ctx, target, expected_status in probes:
        async with _client_as(client, ctx) as http:
            resp = await http.post(f"/api/evaluation/tasks/{target}/runs")
            if expected_status == 404:
                _assert_not_found(resp)
            else:
                _assert_forbidden(resp, roleless=ctx.role is None)
        assert await _state_snapshot(db_session) == before

    assert recorder.calls == []


# ---------------------------------------------------------------------------
# Success: real trigger service, persisted pending run, handoff after commit
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", [WorkspaceRole.EDITOR, WorkspaceRole.ADMIN])
async def test_editor_trigger_persists_pending_run_and_hands_off(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: MonkeyPatch,
    role: WorkspaceRole,
) -> None:
    """202 + pending run bound to task/dataset/evaluators/workspace; viewer reads back."""
    _seed_evaluator_registry(monkeypatch)
    recorder = _stub_handoff(monkeypatch)
    ws, _ = await _workspace_with_role(db_session, "ws-owner", role)
    editor_ctx = await _member_as(db_session, ws, role)
    viewer_ctx = await _member_as(db_session, ws, WorkspaceRole.VIEWER)
    _, outsider_ctx = await _workspace_with_role(db_session, "ws-outsider", WorkspaceRole.EDITOR)
    dataset_id = uuid.uuid4()
    task = await _seed_task(db_session, ws.id, "task", config={"dataset_id": str(dataset_id)})

    async with _client_as(client, editor_ctx) as http:
        resp = await http.post(f"/api/evaluation/tasks/{task.id}/runs")
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert set(body) == {"run_id", "status"}
        assert body["status"] == "pending"
        run_id = body["run_id"]

    # The handoff carries exactly the persisted run and task IDs.
    assert recorder.calls == [(uuid.UUID(run_id), task.id)]
    handed_off = recorder.persisted_runs[0]
    assert handed_off["workspace_id"] == ws.id
    assert handed_off["dataset_id"] == dataset_id
    assert handed_off["evaluator_configs"] == ["exact_match"]

    # Check the persisted pending run after the request finishes.
    run_stmt = select(EvaluationRunModel).where(EvaluationRunModel.id == uuid.UUID(run_id))
    run = (await db_session.execute(run_stmt)).scalar_one()
    assert run.task_id == task.id
    assert run.dataset_id == dataset_id
    assert run.evaluator_configs == ["exact_match"]
    assert run.workspace_id == ws.id
    assert run.status == "pending"

    # Same-workspace viewer reads the run back through both read paths.
    async with _client_as(client, viewer_ctx) as http:
        listing = await http.get(f"/api/evaluation/tasks/{task.id}/runs")
        assert listing.status_code == 200, listing.text
        assert listing.json()["total"] == 1
        assert listing.json()["items"][0]["id"] == run_id
        assert listing.json()["items"][0]["status"] == "pending"

        detail = await http.get(f"/api/evaluation/runs/{run_id}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["task_id"] == str(task.id)
        assert detail.json()["workspace_id"] == str(ws.id)

    # Other workspaces still cannot observe the run.
    async with _client_as(client, outsider_ctx) as http:
        task_runs = await http.get(f"/api/evaluation/tasks/{task.id}/runs")
        _assert_not_found(task_runs)
        run_detail = await http.get(f"/api/evaluation/runs/{run_id}")
        assert run_detail.status_code == 404, run_detail.text


async def test_handoff_skipped_when_commit_fails(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: MonkeyPatch,
) -> None:
    """A failed request-session commit never reaches the background handoff."""
    _seed_evaluator_registry(monkeypatch)
    recorder = _stub_handoff(monkeypatch)
    ws, editor_ctx = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.EDITOR)
    task = await _seed_task(db_session, ws.id, "task")

    original_commit = AsyncSession.commit
    calls = 0
    before = await _state_snapshot(db_session)

    async def failing_first_commit(self: AsyncSession) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("stub commit failure")
        await original_commit(self)

    with monkeypatch.context() as patch:
        patch.setattr(AsyncSession, "commit", failing_first_commit)
        async with _client_as(client, editor_ctx, propagate_errors=False) as http:
            resp = await http.post(f"/api/evaluation/tasks/{task.id}/runs")
            assert resp.status_code == 500, resp.text

    assert recorder.calls == []
    assert calls == 1
    assert await _state_snapshot(db_session) == before


# ---------------------------------------------------------------------------
# Authorized 400 branch: task validation unchanged, no run, no handoff
# ---------------------------------------------------------------------------


async def test_authorized_online_task_rejected_400(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: MonkeyPatch,
) -> None:
    """An authorized editor keeps the online-task 400 without side effects."""
    _seed_evaluator_registry(monkeypatch)
    recorder = _stub_handoff(monkeypatch)
    ws, editor_ctx = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.EDITOR)
    online = await _seed_task(
        db_session,
        ws.id,
        "task-online",
        task_type="online",
        config={"agent_id": str(uuid.uuid4()), "sampling_rate": 0.5},
    )

    async with _client_as(client, editor_ctx) as http:
        resp = await http.post(f"/api/evaluation/tasks/{online.id}/runs")
        _assert_invalid_request(resp, "runs can only be triggered for offline tasks")

    assert recorder.calls == []
    assert await _run_count(db_session) == 0


async def test_authorized_disabled_task_rejected_400(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: MonkeyPatch,
) -> None:
    """An authorized editor keeps the disabled-task 400 without side effects."""
    _seed_evaluator_registry(monkeypatch)
    recorder = _stub_handoff(monkeypatch)
    ws, editor_ctx = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.EDITOR)
    disabled = await _seed_task(db_session, ws.id, "task-disabled", status="disabled")

    async with _client_as(client, editor_ctx) as http:
        resp = await http.post(f"/api/evaluation/tasks/{disabled.id}/runs")
        _assert_invalid_request(resp, "task is disabled")

    assert recorder.calls == []
    assert await _run_count(db_session) == 0


async def test_authorized_unknown_evaluator_rejected_400(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: MonkeyPatch,
) -> None:
    """An authorized editor keeps the unknown-evaluator 400 without side effects."""
    _seed_evaluator_registry(monkeypatch)
    recorder = _stub_handoff(monkeypatch)
    ws, editor_ctx = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.EDITOR)
    task = await _seed_task(db_session, ws.id, "task-unknown-evaluator", evaluators=["nonexistent"])

    async with _client_as(client, editor_ctx) as http:
        resp = await http.post(f"/api/evaluation/tasks/{task.id}/runs")
        _assert_invalid_request(resp, "Unknown evaluator: 'nonexistent'")

    assert recorder.calls == []
    assert await _run_count(db_session) == 0


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({}, "offline tasks require dataset_id"),
        (
            {"dataset_id": str(uuid.uuid4()), "answer_source": "workflow"},
            'answer_source="workflow" requires workflow_id',
        ),
    ],
)
async def test_authorized_invalid_config_rejected_without_commit(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: MonkeyPatch,
    config: dict[str, str],
    message: str,
) -> None:
    """Configuration validation retains its complete error envelope before writes."""
    _seed_evaluator_registry(monkeypatch)
    recorder = _stub_handoff(monkeypatch)
    ws, editor = await _workspace_with_role(db_session, "owner", WorkspaceRole.EDITOR)
    task = await _seed_task(db_session, ws.id, "invalid-config", config=config)
    before = await _state_snapshot(db_session)

    async def unexpected_commit(self: AsyncSession) -> None:
        raise AssertionError("Invalid configurations must not commit")

    monkeypatch.setattr(AsyncSession, "commit", unexpected_commit)
    async with _client_as(client, editor) as http:
        _assert_invalid_request(await http.post(f"/api/evaluation/tasks/{task.id}/runs"), message)
    assert recorder.calls == []
    assert await _state_snapshot(db_session) == before


async def test_task_missing_during_service_recheck_keeps_constant_404(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: MonkeyPatch,
) -> None:
    """A task disappearing after authorization does not create or hand off a run."""
    recorder = _stub_handoff(monkeypatch)
    ws, editor = await _workspace_with_role(db_session, "owner", WorkspaceRole.EDITOR)
    task = await _seed_task(db_session, ws.id, "disappearing-task")
    calls: list[tuple[uuid.UUID, uuid.UUID]] = []

    async def missing_task(
        self: EvaluationTaskService, task_id: uuid.UUID, workspace_id: uuid.UUID
    ) -> EvaluationTaskModel | None:
        calls.append((task_id, workspace_id))
        return None

    monkeypatch.setattr(EvaluationTaskService, "get_task", missing_task)
    before = await _state_snapshot(db_session)
    async with _client_as(client, editor) as http:
        _assert_not_found(await http.post(f"/api/evaluation/tasks/{task.id}/runs"))
    assert calls == [(task.id, ws.id)]
    assert recorder.calls == []
    assert await _state_snapshot(db_session) == before


# ---------------------------------------------------------------------------
# System scope: cross-workspace trigger targets the task's workspace
# ---------------------------------------------------------------------------


async def test_system_scope_triggers_across_workspaces(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: MonkeyPatch,
) -> None:
    """System keys trigger by task ID; runs land in the task's workspace."""
    _seed_evaluator_registry(monkeypatch)
    recorder = _stub_handoff(monkeypatch)
    ws, _ = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.EDITOR)
    other_ws, _ = await _workspace_with_role(db_session, "ws-other", WorkspaceRole.EDITOR)
    task = await _seed_task(db_session, ws.id, "task-system")

    # Without an active workspace...
    async with _client_as(client, _system_context()) as http:
        first = await http.post(f"/api/evaluation/tasks/{task.id}/runs")
        assert first.status_code == 202, first.text
        first_run_id = first.json()["run_id"]

    # ...and with a different active workspace (system scope is unaffected).
    async with _client_as(client, _system_context(workspace_id=other_ws.id)) as http:
        second = await http.post(f"/api/evaluation/tasks/{task.id}/runs")
        assert second.status_code == 202, second.text
        second_run_id = second.json()["run_id"]

    assert first_run_id != second_run_id
    run_ids = [uuid.UUID(first_run_id), uuid.UUID(second_run_id)]
    assert recorder.calls == [(run_ids[0], task.id), (run_ids[1], task.id)]
    runs_stmt = select(EvaluationRunModel).where(EvaluationRunModel.id.in_(run_ids))
    runs = (await db_session.execute(runs_stmt)).scalars().all()
    assert len(runs) == 2
    assert all(run.workspace_id == ws.id for run in runs)
    assert all(run.task_id == task.id for run in runs)
