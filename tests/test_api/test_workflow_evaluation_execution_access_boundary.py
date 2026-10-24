"""Workflow evaluation shortcut trigger access boundary (step10).

Covers the resource-anchored write/trigger boundary on
``POST /api/evaluation/workflow-evaluations/{workflow_version}/runs``:

- Resource resolution: the workflow resolves first and defines the target
  workspace; the dataset, optional dataset version, and optional baseline
  run must each be visible there. Missing, soft-deleted, and wrong-
  workspace resources share one constant 404 (no IDs echoed) for system
  and non-system identities alike — system scope cannot stitch resources
  across workspaces. A non-system identity without an active workspace
  gets that 404 before any resource query.
- Role floor: editor is required after resolution and before config
  validation, item counting, the cost guardrail, task/run creation, the
  commit, and the background handoff; viewer/role-less denials fire even
  when the request would otherwise fail validation.
- Validation preserved: visible-but-mismatched bindings (version↔dataset,
  baseline↔dataset) return 400 after the role floor; non-positive or
  exceeding ``workflow_version``, unknown evaluator names (previously a
  500 via ``validate_task``), and cost-guardrail violations keep their
  400 semantics without creating rows.
- Success semantics: 202 with the five response fields; the ad-hoc task
  and pending run persist bound to the server-resolved workspace with
  workflow/version, dataset (and named version), evaluator configs, and
  execution limits; live-path item counting follows the parent dataset
  (historical zero-UUID items participate) while version-bound counting
  uses the frozen items; the handoff happens only after a successful
  commit and is verified through an independent session; a same-workspace
  viewer reads both rows back while other workspaces cannot observe them.

The AttributeError regression is implicit in every success case: the old
payload omitted ``agent_id``, so any authorized request crashed inside
``build_task_config`` with a 500.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from pytest import MonkeyPatch
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import hecate.ops.api.evaluation_tasks as evaluation_api
import hecate.ops.evaluation.engine as evaluation_engine
from hecate.core.auth_context import AuthContext
from hecate.models.evaluation import (
    EvaluationDatasetModel,
    EvaluationDatasetVersionModel,
    EvaluationItemModel,
    EvaluationRunModel,
    EvaluationScoreModel,
    EvaluationTaskModel,
)
from hecate.models.organization import OrganizationModel
from hecate.models.workflow import WorkflowModel
from hecate.models.workspace import WorkspaceModel
from hecate.models.workspace_member import WorkspaceMemberModel, WorkspaceRole
from hecate.ops.evaluation.format_evaluators import ExactMatchEvaluator
from hecate.ops.evaluation.tasks.runner import OfflineTaskRunner


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


def _system_context(workspace_id: uuid.UUID | None = None) -> AuthContext:
    """Deploy-time bootstrap identity: the only cross-workspace bypass."""
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
    """Yield a client authenticating as ``ctx``; restore the app override."""
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
    """Seed the engine class index with a real deterministic evaluator."""
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
        self.persisted: list[dict[str, Any]] = []


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
            run_row = (
                (
                    await background_db.execute(
                        select(EvaluationRunModel.__table__).where(EvaluationRunModel.__table__.c.id == run_id)
                    )
                )
                .mappings()
                .one()
            )
            task_row = (
                (
                    await background_db.execute(
                        select(EvaluationTaskModel.__table__).where(EvaluationTaskModel.__table__.c.id == task_id)
                    )
                )
                .mappings()
                .one()
            )
            assert run_row["status"] == "pending" and run_row["task_id"] == task_id
            assert task_row["workspace_id"] == run_row["workspace_id"]
            recorder.persisted.append({"run": dict(run_row), "task": dict(task_row)})

    monkeypatch.setattr(AsyncSession, "commit", record_commit)
    monkeypatch.setattr(OfflineTaskRunner, "run_in_background", handoff)
    return recorder


async def _seed_workflow(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    current_version: int = 3,
    deleted: bool = False,
) -> WorkflowModel:
    """Create a workflow directly via the ORM."""
    wf = WorkflowModel(
        name=f"wf-{uuid.uuid4().hex[:8]}",
        workspace_id=workspace_id,
        current_version=current_version,
    )
    wf.deleted = deleted
    db.add(wf)
    await db.commit()
    return wf


async def _seed_dataset(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    items: list[dict[str, Any]] | None = None,
    deleted: bool = False,
) -> EvaluationDatasetModel:
    """Create a dataset with items; items keep the zero-UUID workspace default."""
    ds = EvaluationDatasetModel(name=f"ds-{uuid.uuid4().hex[:8]}", workspace_id=workspace_id)
    ds.deleted = deleted
    db.add(ds)
    await db.flush()
    for item in items or []:
        db.add(EvaluationItemModel(dataset_id=ds.id, **item))
    await db.commit()
    return ds


async def _seed_dataset_version(
    db: AsyncSession,
    dataset: EvaluationDatasetModel,
    *,
    n_items: int = 2,
    other_dataset_id: uuid.UUID | None = None,
    deleted: bool = False,
) -> EvaluationDatasetVersionModel:
    """Create a frozen dataset version bound to ``dataset`` by default."""
    version = EvaluationDatasetVersionModel(
        dataset_id=other_dataset_id or dataset.id,
        name=f"v-{uuid.uuid4().hex[:8]}",
        content_hash=uuid.uuid4().hex,
        items=[{"id": str(uuid.uuid4()), "query": f"q{i}"} for i in range(n_items)],
        workspace_id=dataset.workspace_id,
    )
    version.deleted = deleted
    db.add(version)
    await db.commit()
    return version


async def _seed_run(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    dataset_id: uuid.UUID | None = None,
    deleted: bool = False,
) -> EvaluationRunModel:
    """Seed a completed baseline run directly via the ORM."""
    run = EvaluationRunModel(
        dataset_id=dataset_id or uuid.uuid4(),
        workspace_id=workspace_id,
        evaluator_configs=["exact_match"],
        status="completed",
    )
    run.deleted = deleted
    db.add(run)
    await db.commit()
    return run


async def _state_snapshot(db: AsyncSession) -> list[list[dict[str, Any]]]:
    """Read all resource and evaluation-state columns directly from storage."""
    snapshots = []
    for model in (
        EvaluationTaskModel,
        EvaluationRunModel,
        EvaluationScoreModel,
        WorkflowModel,
        EvaluationDatasetModel,
        EvaluationDatasetVersionModel,
        EvaluationItemModel,
    ):
        table = model.__table__
        rows = (await db.execute(select(table).order_by(table.c.id))).mappings().all()
        snapshots.append([dict(row) for row in rows])
    return snapshots


@contextlib.contextmanager
def _guard_authorization_denial(monkeypatch: MonkeyPatch, *, no_queries: bool = False) -> Iterator[None]:
    original_execute = AsyncSession.execute
    original_add = AsyncSession.add

    async def execute(self: AsyncSession, statement: Any, *args: Any, **kwargs: Any) -> Any:
        assert not no_queries, "A missing workspace must be rejected before resource queries"
        tables = {table.name for table in statement.get_final_froms()}
        assert EvaluationItemModel.__tablename__ not in tables, "Denied requests must not count items"
        return await original_execute(self, statement, *args, **kwargs)

    def add(self: AsyncSession, instance: object, *args: Any, **kwargs: Any) -> None:
        assert not isinstance(instance, (EvaluationTaskModel, EvaluationRunModel, EvaluationScoreModel))
        original_add(self, instance, *args, **kwargs)

    def unexpected_validation(*args: object, **kwargs: object) -> None:
        raise AssertionError("Authorization must precede configuration/evaluator validation")

    async def unexpected_side_effect(*args: object, **kwargs: object) -> None:
        raise AssertionError("Denied requests must not commit or schedule background execution")

    with monkeypatch.context() as patch:
        patch.setattr(AsyncSession, "execute", execute)
        patch.setattr(AsyncSession, "add", add)
        patch.setattr(AsyncSession, "commit", unexpected_side_effect)
        patch.setattr(evaluation_api, "build_task_config", unexpected_validation)
        patch.setattr(evaluation_api, "validate_task", unexpected_validation)
        patch.setattr(OfflineTaskRunner, "run_in_background", unexpected_side_effect)
        yield


def _assert_not_found(resp: Any) -> None:
    assert resp.status_code == 404, resp.text
    expected = {"detail": {"error": {"code": "NOT_FOUND", "message": "Evaluation task not found", "details": None}}}
    assert resp.json() == expected


def _assert_forbidden(resp: Any, *, roleless: bool = False) -> None:
    assert resp.status_code == 403, resp.text
    message = "Workspace membership required" if roleless else "Editor role required"
    assert resp.json() == {"detail": {"error": {"code": "FORBIDDEN", "message": message, "details": None}}}


def _assert_invalid_request(resp: Any, fragment: str) -> None:
    assert resp.status_code == 400, resp.text
    body = resp.json()["detail"]
    assert isinstance(body, dict), resp.text
    assert body["error"]["code"] == "INVALID_REQUEST"
    assert fragment in body["error"]["message"]


def _payload(
    workflow: WorkflowModel,
    dataset: EvaluationDatasetModel,
    *,
    workflow_version: int = 2,
    evaluators: list[str] | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "workflow_id": str(workflow.id),
        "dataset_id": str(dataset.id),
        "evaluators": evaluators if evaluators is not None else ["exact_match"],
    }
    body.update(overrides)
    return body


def _trigger(client: AsyncClient, workflow_version: int, body: dict[str, Any]) -> Any:
    return client.post(f"/api/evaluation/workflow-evaluations/{workflow_version}/runs", json=body)


# ---------------------------------------------------------------------------
# Resource resolution: one constant 404 across resources and conditions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "resource",
    ["workflow", "dataset", "dataset_version", "baseline_run"],
)
@pytest.mark.parametrize("condition", ["missing", "deleted", "foreign"])
@pytest.mark.parametrize("identity", ["editor", "viewer", "roleless", "system"])
async def test_invisible_resource_matches_missing(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: MonkeyPatch,
    resource: str,
    condition: str,
    identity: str,
) -> None:
    """Every invisible resource yields the same 404, independent of role."""
    recorder = _stub_handoff(monkeypatch)
    ws, editor_ctx = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.EDITOR)
    foreign_ws, _ = await _workspace_with_role(db_session, "ws-foreign", WorkspaceRole.EDITOR)
    wf = await _seed_workflow(db_session, ws.id)
    ds = await _seed_dataset(db_session, ws.id)
    version = await _seed_dataset_version(db_session, ds)
    baseline = await _seed_run(db_session, ws.id, dataset_id=ds.id)
    foreign_ds = await _seed_dataset(db_session, foreign_ws.id)
    foreign = {
        "workflow": await _seed_workflow(db_session, foreign_ws.id),
        "dataset": foreign_ds,
        "dataset_version": await _seed_dataset_version(db_session, foreign_ds),
        "baseline_run": await _seed_run(db_session, foreign_ws.id),
    }
    home = {"workflow": wf, "dataset": ds, "dataset_version": version, "baseline_run": baseline}
    key = {
        "workflow": "workflow_id",
        "dataset": "dataset_id",
        "dataset_version": "dataset_version_id",
        "baseline_run": "baseline_run_id",
    }[resource]

    body = _payload(wf, ds)
    if condition == "missing":
        body[key] = str(uuid.uuid4())
    elif condition == "foreign":
        body[key] = str(foreign[resource].id)
    else:  # deleted — the request references the home resource, now soft-deleted
        home[resource].deleted = True
        body[key] = str(home[resource].id)
        await db_session.commit()

    existing_task = EvaluationTaskModel(
        task_type="offline",
        name="existing-evidence",
        config={"dataset_id": str(ds.id)},
        evaluator_configs=["exact_match"],
        workspace_id=ws.id,
    )
    db_session.add(existing_task)
    await db_session.flush()
    baseline.task_id = existing_task.id
    baseline.summary = {"keep": True}
    db_session.add(
        EvaluationScoreModel(
            run_id=baseline.id,
            item_id=uuid.uuid4(),
            metric_name="exact_match",
            value=0.75,
            reasoning="Existing evidence must remain unchanged",
            source="deterministic",
            workspace_id=ws.id,
        )
    )
    await db_session.commit()
    ctx = {
        "editor": editor_ctx,
        "viewer": await _member_as(db_session, ws, WorkspaceRole.VIEWER),
        "roleless": _no_role_context(ws),
        "system": _system_context(),
    }[identity]
    before = await _state_snapshot(db_session)
    with _guard_authorization_denial(monkeypatch):
        async with _client_as(client, ctx) as http:
            probe = await _trigger(http, 2, body)
            reference = await _trigger(http, 2, _payload(wf, ds, workflow_id=str(uuid.uuid4())))
            _assert_not_found(probe)
            assert probe.json() == reference.json()
    assert recorder.calls == []
    assert await _state_snapshot(db_session) == before


async def test_missing_workspace_identity_never_queries(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """No active workspace: the constant 404, without any resource query."""
    ws, _ = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.EDITOR)
    wf = await _seed_workflow(db_session, ws.id)
    ds = await _seed_dataset(db_session, ws.id)
    ghost = _no_workspace_context()

    async def unexpected_query(*args: object, **kwargs: object) -> None:
        raise AssertionError("Missing workspace must be rejected before any resource query")

    before = await _state_snapshot(db_session)
    with MonkeyPatch.context() as patch:
        patch.setattr(AsyncSession, "execute", unexpected_query)
        async with _client_as(client, ghost) as http:
            _assert_not_found(await _trigger(http, 2, _payload(wf, ds)))
    assert await _state_snapshot(db_session) == before


# ---------------------------------------------------------------------------
# Role floor: editor required after resolution, before validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", [WorkspaceRole.VIEWER, None])
async def test_role_floor_precedes_validation(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: MonkeyPatch,
    role: WorkspaceRole | None,
) -> None:
    """Viewer/role-less denials fire even for a version that would 400."""
    recorder = _stub_handoff(monkeypatch)
    ws, _ = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.EDITOR)
    ctx = await _member_as(db_session, ws, role) if role is not None else _no_role_context(ws)
    wf = await _seed_workflow(db_session, ws.id, current_version=1)
    ds = await _seed_dataset(db_session, ws.id, items=[{"query": f"q{i}"} for i in range(3)])
    other = await _seed_dataset(db_session, ws.id)
    version = await _seed_dataset_version(db_session, other)
    baseline = await _seed_run(db_session, ws.id, dataset_id=other.id)
    before = await _state_snapshot(db_session)
    probes = [
        (99, {}),
        (2, {"dataset_version_id": str(version.id)}),
        (2, {"baseline_run_id": str(baseline.id)}),
        (2, {"evaluators": ["nonexistent"]}),
        (2, {"max_total_executions": 1}),
    ]
    for requested_version, overrides in probes:
        with _guard_authorization_denial(monkeypatch):
            async with _client_as(client, ctx) as http:
                response = await _trigger(http, requested_version, _payload(wf, ds, **overrides))
                _assert_forbidden(response, roleless=role is None)
        assert await _state_snapshot(db_session) == before

    assert recorder.calls == []


@pytest.mark.parametrize("workflow_version", [0, -1])
@pytest.mark.parametrize("identity", ["no_workspace", "viewer", "roleless", "foreign", "missing"])
async def test_nonpositive_version_does_not_bypass_authorization(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: MonkeyPatch,
    workflow_version: int,
    identity: str,
) -> None:
    """Version-domain validation follows resource visibility and the editor floor."""
    recorder = _stub_handoff(monkeypatch)
    ws, editor = await _workspace_with_role(db_session, "owner", WorkspaceRole.EDITOR)
    _, outsider = await _workspace_with_role(db_session, "outsider", WorkspaceRole.EDITOR)
    workflow = await _seed_workflow(db_session, ws.id)
    dataset = await _seed_dataset(db_session, ws.id)
    contexts = {
        "no_workspace": _no_workspace_context(),
        "viewer": await _member_as(db_session, ws, WorkspaceRole.VIEWER),
        "roleless": _no_role_context(ws),
        "foreign": outsider,
        "missing": editor,
    }
    body = _payload(workflow, dataset)
    if identity == "missing":
        body["workflow_id"] = str(uuid.uuid4())
    before = await _state_snapshot(db_session)
    with _guard_authorization_denial(monkeypatch, no_queries=identity == "no_workspace"):
        async with _client_as(client, contexts[identity]) as http:
            response = await _trigger(http, workflow_version, body)
    if identity in {"viewer", "roleless"}:
        _assert_forbidden(response, roleless=identity == "roleless")
    else:
        _assert_not_found(response)
    assert recorder.calls == []
    assert await _state_snapshot(db_session) == before


# ---------------------------------------------------------------------------
# Authorized 400 branches: bindings, version bounds, evaluators, guardrail
# ---------------------------------------------------------------------------


async def test_version_bound_to_other_dataset_rejected(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: MonkeyPatch,
) -> None:
    """A visible version bound to another dataset is a post-role 400."""
    _seed_evaluator_registry(monkeypatch)
    recorder = _stub_handoff(monkeypatch)
    ws, editor_ctx = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.EDITOR)
    wf = await _seed_workflow(db_session, ws.id)
    ds = await _seed_dataset(db_session, ws.id)
    other_ds = await _seed_dataset(db_session, ws.id)
    version = await _seed_dataset_version(db_session, ds, other_dataset_id=other_ds.id)

    async with _client_as(client, editor_ctx) as http:
        resp = await _trigger(http, 2, _payload(wf, ds, dataset_version_id=str(version.id)))
        _assert_invalid_request(resp, "does not belong to the request's dataset")

    assert recorder.calls == []
    snapshot = await _state_snapshot(db_session)
    assert snapshot[0] == []


async def test_baseline_referencing_other_dataset_rejected(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: MonkeyPatch,
) -> None:
    """A visible baseline run referencing another dataset is a post-role 400."""
    _seed_evaluator_registry(monkeypatch)
    recorder = _stub_handoff(monkeypatch)
    ws, editor_ctx = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.EDITOR)
    wf = await _seed_workflow(db_session, ws.id)
    ds = await _seed_dataset(db_session, ws.id)
    other_ds = await _seed_dataset(db_session, ws.id)
    baseline = await _seed_run(db_session, ws.id, dataset_id=other_ds.id)

    async with _client_as(client, editor_ctx) as http:
        resp = await _trigger(http, 2, _payload(wf, ds, baseline_run_id=str(baseline.id)))
        _assert_invalid_request(resp, "must reference the request's dataset")

    assert recorder.calls == []
    snapshot = await _state_snapshot(db_session)
    assert snapshot[0] == []


async def test_version_bounds_and_unknown_evaluator_rejected_400(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: MonkeyPatch,
) -> None:
    """Non-positive/exceeding versions and unknown evaluators keep their 400s."""
    recorder = _stub_handoff(monkeypatch)
    ws, editor_ctx = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.EDITOR)
    wf = await _seed_workflow(db_session, ws.id, current_version=3)
    ds = await _seed_dataset(db_session, ws.id, items=[{"query": "q"}])

    async with _client_as(client, editor_ctx) as http:
        _assert_invalid_request(await _trigger(http, 0, _payload(wf, ds)), "workflow_version must be >= 1")
        _assert_invalid_request(await _trigger(http, 4, _payload(wf, ds)), "exceeds current_version=3")
        # Unknown evaluator names previously escaped as a 500 via
        # validate_task; they are a 400 now, without creating rows.
        resp = await _trigger(http, 2, _payload(wf, ds, evaluators=["nonexistent"]))
        _assert_invalid_request(resp, "Unknown evaluator")

    assert recorder.calls == []
    snapshot = await _state_snapshot(db_session)
    assert snapshot[0] == []


async def test_cost_guardrail_semantics_preserved(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: MonkeyPatch,
) -> None:
    """The cost guardrail keeps its 400 with the offending total."""
    _seed_evaluator_registry(monkeypatch)
    recorder = _stub_handoff(monkeypatch)
    ws, editor_ctx = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.EDITOR)
    wf = await _seed_workflow(db_session, ws.id)
    ds = await _seed_dataset(db_session, ws.id, items=[{"query": f"q{i}"} for i in range(3)])

    async with _client_as(client, editor_ctx) as http:
        resp = await _trigger(http, 2, _payload(wf, ds, max_total_executions=2))
        assert resp.status_code == 400, resp.text
        assert "cost guardrail" in resp.text
        assert "3 items x 1 repetitions = 3" in resp.text

    assert recorder.calls == []
    snapshot = await _state_snapshot(db_session)
    assert snapshot[0] == []


# ---------------------------------------------------------------------------
# Success: 202, target-workspace persistence, handoff after commit, read-back
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", [WorkspaceRole.EDITOR, WorkspaceRole.ADMIN])
async def test_editor_trigger_persists_and_hands_off(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: MonkeyPatch,
    role: WorkspaceRole,
) -> None:
    """202 + task/run bound to the workflow's workspace; viewer reads back."""
    _seed_evaluator_registry(monkeypatch)
    recorder = _stub_handoff(monkeypatch)
    ws, editor_ctx = await _workspace_with_role(db_session, "ws-owner", role)
    viewer_ctx = await _member_as(db_session, ws, WorkspaceRole.VIEWER)
    _, outsider_ctx = await _workspace_with_role(db_session, "ws-outsider", WorkspaceRole.EDITOR)
    wf = await _seed_workflow(db_session, ws.id, current_version=3)
    ds = await _seed_dataset(
        db_session,
        ws.id,
        items=[{"query": "q1", "expected_answer": "a", "generated_answer": "a"}],
    )
    version = await _seed_dataset_version(db_session, ds, n_items=1)
    baseline = await _seed_run(db_session, ws.id, dataset_id=ds.id)

    async with _client_as(client, editor_ctx) as http:
        resp = await _trigger(
            http,
            2,
            _payload(
                wf,
                ds,
                dataset_version_id=str(version.id),
                baseline_run_id=str(baseline.id),
                threshold=0.75,
                regression_threshold=0.1,
                repetitions=2,
                max_total_executions=4,
                max_in_flight=3,
            ),
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert set(body) == {"run_id", "task_id", "workflow_id", "workflow_version", "status"}
        assert body["status"] == "pending"
        assert body["workflow_id"] == str(wf.id)
        assert body["workflow_version"] == 2
        run_id, task_id = uuid.UUID(body["run_id"]), uuid.UUID(body["task_id"])

    assert recorder.calls == [(run_id, task_id)]
    handed = recorder.persisted[0]
    assert handed["task"]["workspace_id"] == ws.id
    assert handed["task"]["task_type"] == "offline"
    assert handed["task"]["evaluator_configs"] == ["exact_match"]
    assert handed["task"]["config"]["workflow_id"] == str(wf.id)
    assert handed["task"]["config"]["workflow_version"] == 2
    assert handed["task"]["config"]["dataset_id"] == str(ds.id)
    assert handed["task"]["config"]["answer_source"] == "workflow"
    assert handed["task"]["config"]["baseline_run_id"] == str(baseline.id)
    assert handed["task"]["config"]["repetitions"] == 2
    assert handed["task"]["config"]["max_total_executions"] == 4
    assert handed["task"]["config"]["max_in_flight"] == 3
    assert handed["task"]["config"]["threshold"] == 0.75
    assert handed["task"]["config"]["regression_threshold"] == 0.1
    assert handed["run"]["workspace_id"] == ws.id
    assert handed["run"]["workflow_id"] == wf.id
    assert handed["run"]["workflow_version"] == 2
    assert handed["run"]["dataset_id"] == ds.id
    assert handed["run"]["dataset_version_id"] == version.id
    assert handed["run"]["repetitions"] == 2
    assert handed["run"]["evaluator_configs"] == ["exact_match"]
    assert handed["run"]["status"] == "pending"

    # Same-workspace viewer reads both rows back through existing APIs.
    async with _client_as(client, viewer_ctx) as http:
        task_detail = await http.get(f"/api/evaluation/tasks/{task_id}")
        assert task_detail.status_code == 200, task_detail.text
        assert task_detail.json()["workspace_id"] == str(ws.id)
        assert task_detail.json()["config"] == handed["task"]["config"]
        runs = await http.get(f"/api/evaluation/tasks/{task_id}/runs")
        assert runs.status_code == 200, runs.text
        assert runs.json()["total"] == 1
        assert runs.json()["items"][0]["id"] == str(run_id)

        detail = await http.get(f"/api/evaluation/runs/{run_id}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["task_id"] == str(task_id)
        assert detail.json()["workspace_id"] == str(ws.id)

    async with _client_as(client, outsider_ctx) as http:
        _assert_not_found(await http.get(f"/api/evaluation/tasks/{task_id}"))
        # Each read endpoint keeps its own uniform 404 body — the point is
        # invisibility, not one shared message across endpoints.
        run_detail = await http.get(f"/api/evaluation/runs/{run_id}")
        assert run_detail.status_code == 404, run_detail.text
        assert run_detail.json()["detail"]["error"]["message"] == "Run not found"
        task_runs = await http.get(f"/api/evaluation/tasks/{task_id}/runs")
        _assert_not_found(task_runs)


async def test_live_path_counts_historical_zero_workspace_items(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: MonkeyPatch,
) -> None:
    """Live item counting follows the parent dataset, not item workspace columns."""
    _seed_evaluator_registry(monkeypatch)
    recorder = _stub_handoff(monkeypatch)
    ws, editor_ctx = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.EDITOR)
    wf = await _seed_workflow(db_session, ws.id)
    ds = await _seed_dataset(
        db_session,
        ws.id,
        items=[{"query": "q1"}, {"query": "q2"}, {"query": "deleted", "deleted": True}],
    )
    await _seed_dataset(db_session, ws.id, items=[{"query": "unrelated"}])
    stmt = select(EvaluationItemModel).where(EvaluationItemModel.dataset_id == ds.id, ~EvaluationItemModel.deleted)
    items = (await db_session.execute(stmt)).scalars().all()
    assert len(items) == 2 and all(item.workspace_id == uuid.UUID(int=0) for item in items)

    before = await _state_snapshot(db_session)
    async with _client_as(client, editor_ctx) as http:
        rejected = await _trigger(http, 2, _payload(wf, ds, max_total_executions=1))
        assert rejected.status_code == 400, rejected.text
        assert rejected.json() == {"detail": "cost guardrail: 2 items x 1 repetitions = 2 > max_total_executions=1"}
        assert await _state_snapshot(db_session) == before
        resp = await _trigger(http, 2, _payload(wf, ds, max_total_executions=2))
        assert resp.status_code == 202, resp.text

    assert len(recorder.calls) == 1


@pytest.mark.parametrize(("live_count", "frozen_count"), [(3, 1), (1, 3)])
async def test_version_path_counts_frozen_items(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: MonkeyPatch,
    live_count: int,
    frozen_count: int,
) -> None:
    """Version-bound guardrail counts the frozen items, not the live set."""
    _seed_evaluator_registry(monkeypatch)
    recorder = _stub_handoff(monkeypatch)
    ws, editor_ctx = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.EDITOR)
    wf = await _seed_workflow(db_session, ws.id)
    ds = await _seed_dataset(db_session, ws.id, items=[{"query": f"q{i}"} for i in range(live_count)])
    version = await _seed_dataset_version(db_session, ds, n_items=frozen_count)

    async with _client_as(client, editor_ctx) as http:
        resp = await _trigger(http, 2, _payload(wf, ds, dataset_version_id=str(version.id), max_total_executions=2))
        assert resp.status_code == (202 if frozen_count <= 2 else 400), resp.text

        live = await _trigger(http, 2, _payload(wf, ds, max_total_executions=2))
        assert live.status_code == (202 if live_count <= 2 else 400), live.text
        rejected = live if live_count > 2 else resp
        assert rejected.json() == {"detail": "cost guardrail: 3 items x 1 repetitions = 3 > max_total_executions=2"}

    assert len(recorder.calls) == 1


@pytest.mark.parametrize("failure_stage", ["run_flush", "commit"])
async def test_failed_persistence_rolls_back_task_and_run_without_handoff(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: MonkeyPatch,
    failure_stage: str,
) -> None:
    """Failures after the task insert or at commit leave neither partial rows nor handoff."""
    _seed_evaluator_registry(monkeypatch)
    recorder = _stub_handoff(monkeypatch)
    ws, editor_ctx = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.EDITOR)
    wf = await _seed_workflow(db_session, ws.id)
    ds = await _seed_dataset(db_session, ws.id, items=[{"query": "q"}])

    original_commit = AsyncSession.commit
    original_flush = AsyncSession.flush
    calls = {"commit": 0, "flush": 0}

    async def failing_first_commit(self: AsyncSession) -> None:
        calls["commit"] += 1
        if failure_stage == "commit":
            raise RuntimeError("stub commit failure")
        await original_commit(self)

    async def failing_run_flush(self: AsyncSession, *args: Any, **kwargs: Any) -> None:
        calls["flush"] += 1
        if failure_stage == "run_flush" and calls["flush"] == 2:
            raise RuntimeError("stub run flush failure")
        await original_flush(self, *args, **kwargs)

    before = await _state_snapshot(db_session)
    with monkeypatch.context() as patch:
        patch.setattr(AsyncSession, "commit", failing_first_commit)
        patch.setattr(AsyncSession, "flush", failing_run_flush)
        async with _client_as(client, editor_ctx, propagate_errors=False) as http:
            resp = await _trigger(http, 2, _payload(wf, ds))
            assert resp.status_code == 500, resp.text

    assert recorder.calls == []
    assert calls == {"commit": int(failure_stage == "commit"), "flush": 2}
    assert await _state_snapshot(db_session) == before


# ---------------------------------------------------------------------------
# System scope: anchored to the workflow's workspace
# ---------------------------------------------------------------------------


async def test_system_scope_anchors_to_workflow_workspace(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: MonkeyPatch,
) -> None:
    """System triggers across workspaces but cannot stitch foreign resources."""
    _seed_evaluator_registry(monkeypatch)
    recorder = _stub_handoff(monkeypatch)
    ws, _ = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.EDITOR)
    other_ws, _ = await _workspace_with_role(db_session, "ws-other", WorkspaceRole.EDITOR)
    wf = await _seed_workflow(db_session, ws.id)
    ds = await _seed_dataset(db_session, ws.id, items=[{"query": "q"}])
    foreign_ds = await _seed_dataset(db_session, other_ws.id)

    # Without an active workspace, and with a different active one.
    for system_ctx in (_system_context(), _system_context(workspace_id=other_ws.id)):
        async with _client_as(client, system_ctx) as http:
            ok = await _trigger(http, 2, _payload(wf, ds))
            assert ok.status_code == 202, ok.text
            run_id = uuid.UUID(ok.json()["run_id"])
            assert recorder.calls[-1][0] == run_id

            # A dataset from another workspace is still invisible to the
            # trigger even for system scope.
            _assert_not_found(await _trigger(http, 2, _payload(wf, foreign_ds)))

    assert len(recorder.calls) == 2
    runs = (await db_session.execute(select(EvaluationRunModel))).scalars().all()
    assert len(runs) == 2
    assert all(run.workspace_id == ws.id for run in runs)
