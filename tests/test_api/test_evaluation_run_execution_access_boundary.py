"""Evaluation run execution access boundary (step10).

Covers the write/execution boundary on ``POST /api/evaluation/runs``:

- Workspace ownership: a missing, soft-deleted, or cross-workspace dataset
  yields the *same* constant 404 (no IDs echoed) and the check precedes
  the role floor, so a role denial never discloses dataset existence.
- Role floor: creating and executing a run requires editor in the
  dataset's workspace; viewer and role-less membership are 403, and the
  denial precedes evaluator-name validation (403 wins over 422).
- Identity rules: a non-system identity without an active workspace can
  never own the dataset and receives the constant dataset 404 before any
  query; only explicit system scope executes across workspaces, with the
  target workspace taken from the resolved dataset.
- Persistence ownership: the created run — and the scores the engine
  derives from it — lands in the dataset's real workspace (not the
  zero-UUID column default), so the run read boundary serves the result
  back to workspace viewers while other workspaces cannot see it.
- Zero execution on denial: rejected requests preserve persisted state;
  answer generation is stubbed explicitly and guarded against external calls.
  Success keeps 201 semantics (response shape, tags,
  empty-dataset runs, unknown-evaluator 422) and ``manual``/``auto``/
  ``pipeline`` success and error paths are tested without an external model.

Success cases seed the engine's class index with the *real* deterministic
evaluator classes — the engine, persistence, and read-back run end to
end; only the evaluator registration (an app-startup side effect that
ASGITransport does not trigger) is seeded.
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

import hecate.ops.api.evaluation as evaluation_api
import hecate.ops.evaluation.engine as evaluation_engine
from hecate.core.auth_context import AuthContext
from hecate.models.evaluation import (
    EvaluationDatasetModel,
    EvaluationItemModel,
    EvaluationRunModel,
    EvaluationScoreModel,
)
from hecate.models.organization import OrganizationModel
from hecate.models.workspace import WorkspaceModel
from hecate.models.workspace_member import WorkspaceMemberModel, WorkspaceRole
from hecate.ops.evaluation.format_evaluators import ContainsEvaluator, ExactMatchEvaluator

ZERO_WORKSPACE = uuid.UUID("00000000-0000-0000-0000-000000000000")


@pytest.fixture(autouse=True)
def _guard_answer_generation(monkeypatch: MonkeyPatch) -> Iterator[None]:
    """Fail even if the engine catches an unexpected answer-generation call."""
    calls = []

    async def unexpected(self: evaluation_engine.EvaluationEngine, *args: object, **kwargs: object) -> str:
        calls.append(args)
        raise AssertionError("This test must not invoke an unstubbed answer source")

    monkeypatch.setattr(evaluation_engine.EvaluationEngine, "_generate_answer_via_pipeline", unexpected)
    yield
    assert calls == []


async def _state_snapshot(db: AsyncSession) -> list[list[dict[str, Any]]]:
    """Read every dataset/item/run/score column directly from persisted storage."""
    snapshots = []
    for model in (EvaluationDatasetModel, EvaluationItemModel, EvaluationRunModel, EvaluationScoreModel):
        table = model.__table__
        rows = (await db.execute(select(table).order_by(table.c.id))).mappings().all()
        snapshots.append([dict(row) for row in rows])
    return snapshots


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
    execution still targets the dataset's workspace, not the active one.
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


def _seed_evaluator_registry(monkeypatch: MonkeyPatch) -> None:
    """Seed the engine class index with the real deterministic evaluators.

    Registration normally happens at app startup; ASGITransport skips the
    lifespan, so tests that pass name resolution install the same classes
    the startup path would — no stub evaluator, no engine mock.
    """
    monkeypatch.setattr(
        evaluation_engine,
        "_EVALUATOR_CLASS_REGISTRY",
        {"exact_match": ExactMatchEvaluator, "contains": ContainsEvaluator},
    )


async def _seed_dataset(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    name: str,
    *,
    items: list[dict[str, Any]],
    deleted: bool = False,
) -> EvaluationDatasetModel:
    """Create a dataset (and items) directly via the ORM.

    Items keep the model's zero-UUID workspace default — the historical
    pre-boundary shape — to pin that execution membership follows the
    parent dataset, never the item row's own workspace column.
    """
    ds = EvaluationDatasetModel(name=f"{name}-{uuid.uuid4().hex[:8]}", workspace_id=workspace_id)
    ds.deleted = deleted
    db.add(ds)
    await db.flush()
    for item in items:
        db.add(EvaluationItemModel(dataset_id=ds.id, **item))
    await db.commit()
    return ds


async def _run_count(db: AsyncSession) -> int:
    return len((await db.execute(select(EvaluationRunModel))).scalars().all())


async def _score_count(db: AsyncSession) -> int:
    return len((await db.execute(select(EvaluationScoreModel))).scalars().all())


def _assert_not_found(resp: Any) -> None:
    assert resp.status_code == 404, resp.text
    assert resp.json() == {"detail": {"error": {"code": "NOT_FOUND", "message": "Dataset not found", "details": None}}}


def _assert_forbidden(resp: Any, *, roleless: bool = False) -> None:
    assert resp.status_code == 403, resp.text
    message = "Workspace membership required" if roleless else "Editor role required"
    assert resp.json() == {"detail": {"error": {"code": "FORBIDDEN", "message": message, "details": None}}}


def _payload(dataset_id: uuid.UUID | str, **overrides: Any) -> dict[str, Any]:
    """Build a create-run request body; ``overrides`` replace default fields."""
    body: dict[str, Any] = {"dataset_id": str(dataset_id), "evaluators": ["exact_match"]}
    body.update(overrides)
    return body


def _run_payload(ds: EvaluationDatasetModel, **overrides: Any) -> dict[str, Any]:
    return _payload(overrides.pop("dataset_id", ds.id), **overrides)


# ---------------------------------------------------------------------------
# Workspace ownership: one constant 404, checked before the role floor
# ---------------------------------------------------------------------------


async def test_cross_workspace_dataset_matches_missing(client: AsyncClient, db_session: AsyncSession) -> None:
    """A W2 editor sees a W1 dataset exactly like a nonexistent one."""
    ws, _ = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.EDITOR)
    ds = await _seed_dataset(db_session, ws.id, "ds", items=[{"query": "q"}])
    _, outsider_ctx = await _workspace_with_role(db_session, "ws-outsider", WorkspaceRole.EDITOR)

    async with _client_as(client, outsider_ctx) as http:
        probe = await http.post("/api/evaluation/runs", json=_run_payload(ds))
        reference = await http.post("/api/evaluation/runs", json=_run_payload(ds, dataset_id=str(uuid.uuid4())))
        _assert_not_found(probe)
        assert probe.json()["detail"] == reference.json()["detail"]
        assert str(ds.id) not in probe.text


async def test_deleted_dataset_matches_missing(client: AsyncClient, db_session: AsyncSession) -> None:
    """A soft-deleted dataset in the caller's workspace shares the 404."""
    ws, owner_ctx = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.EDITOR)
    deleted_ds = await _seed_dataset(db_session, ws.id, "ds-deleted", items=[{"query": "q"}], deleted=True)

    async with _client_as(client, owner_ctx) as http:
        probe = await http.post("/api/evaluation/runs", json=_run_payload(deleted_ds))
        reference = await http.post("/api/evaluation/runs", json=_run_payload(deleted_ds, dataset_id=str(uuid.uuid4())))
        _assert_not_found(probe)
        assert probe.json()["detail"] == reference.json()["detail"]


@pytest.mark.parametrize("condition", ["missing", "deleted", "foreign"])
@pytest.mark.parametrize("role", [WorkspaceRole.VIEWER, None])
async def test_ownership_precedes_role(
    client: AsyncClient,
    db_session: AsyncSession,
    condition: str,
    role: WorkspaceRole | None,
) -> None:
    """An invisible dataset yields 404 even for identities without a role."""
    ws, viewer_ctx = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.VIEWER)
    foreign_ws, _ = await _workspace_with_role(db_session, "ws-foreign", WorkspaceRole.VIEWER)
    if condition == "missing":
        dataset_id = uuid.uuid4()
    else:
        invisible = await _seed_dataset(
            db_session,
            foreign_ws.id if condition == "foreign" else ws.id,
            f"ds-{condition}",
            items=[{"query": "q"}],
            deleted=condition == "deleted",
        )
        dataset_id = invisible.id
    ctx = viewer_ctx if role == WorkspaceRole.VIEWER else _no_role_context(ws)

    async with _client_as(client, ctx) as http:
        probe = await http.post("/api/evaluation/runs", json=_payload(dataset_id))
        reference = await http.post("/api/evaluation/runs", json=_payload(uuid.uuid4()))
        _assert_not_found(probe)
        assert probe.json()["detail"] == reference.json()["detail"]
        assert str(dataset_id) not in probe.text


async def test_missing_workspace_identity_gets_constant_dataset_404(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: MonkeyPatch,
) -> None:
    """No active workspace: the constant dataset 404, without querying it."""
    ws, _ = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.EDITOR)
    ds = await _seed_dataset(db_session, ws.id, "ds", items=[{"query": "q"}])
    ghost = _no_workspace_context()

    async def unexpected_query(*args: object, **kwargs: object) -> None:
        raise AssertionError("Missing workspace must be rejected before any resource query")

    before = await _state_snapshot(db_session)
    with monkeypatch.context() as patch:
        patch.setattr(AsyncSession, "execute", unexpected_query)
        async with _client_as(client, ghost) as http:
            for dataset_id in (ds.id, uuid.uuid4()):
                _assert_not_found(await http.post("/api/evaluation/runs", json=_payload(dataset_id)))
    assert await _state_snapshot(db_session) == before


# ---------------------------------------------------------------------------
# Role floor: editor required; denial precedes evaluator validation
# ---------------------------------------------------------------------------


async def test_viewer_is_forbidden_without_execution(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: MonkeyPatch,
) -> None:
    """A viewer of the dataset's workspace is 403 — even for unknown evaluators."""
    _seed_evaluator_registry(monkeypatch)
    ws, _ = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.EDITOR)
    ds = await _seed_dataset(db_session, ws.id, "ds", items=[{"query": "q"}])
    viewer_ctx = await _member_as(db_session, ws, WorkspaceRole.VIEWER)
    runs_before, scores_before = await _run_count(db_session), await _score_count(db_session)

    async with _client_as(client, viewer_ctx) as http:
        _assert_forbidden(await http.post("/api/evaluation/runs", json=_run_payload(ds)))
        # Authorization precedes evaluator-name validation: 403, not 422.
        _assert_forbidden(await http.post("/api/evaluation/runs", json=_run_payload(ds, evaluators=["nonexistent"])))

    assert await _run_count(db_session) == runs_before
    assert await _score_count(db_session) == scores_before


async def test_membership_without_role_is_forbidden(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """An active workspace without any membership role is 403."""
    ws, _ = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.EDITOR)
    ds = await _seed_dataset(db_session, ws.id, "ds", items=[{"query": "q"}])
    no_role = _no_role_context(ws)
    runs_before = await _run_count(db_session)

    async with _client_as(client, no_role) as http:
        _assert_forbidden(await http.post("/api/evaluation/runs", json=_run_payload(ds)), roleless=True)

    assert await _run_count(db_session) == runs_before


@pytest.mark.parametrize(
    ("identity", "condition"),
    [
        ("viewer", "visible"),
        ("roleless", "visible"),
        ("editor", "missing"),
        ("editor", "deleted"),
        ("editor", "foreign"),
        ("viewer", "foreign"),
        ("roleless", "foreign"),
        ("no_workspace", "visible"),
        ("system", "missing"),
        ("system", "deleted"),
    ],
)
async def test_denial_preserves_all_columns_before_evaluator_resolution(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: MonkeyPatch,
    identity: str,
    condition: str,
) -> None:
    """Rejected requests cannot mutate existing evidence or resolve evaluators."""
    ws, editor = await _workspace_with_role(db_session, "owner", WorkspaceRole.EDITOR)
    other, _ = await _workspace_with_role(db_session, "other", WorkspaceRole.EDITOR)
    dataset = await _seed_dataset(
        db_session,
        other.id if condition == "foreign" else ws.id,
        "protected",
        items=[{"query": "original", "generated_answer": "original", "metadata_": {"keep": True}}],
        deleted=condition == "deleted",
    )
    item = (await db_session.execute(select(EvaluationItemModel))).scalars().one()
    run = EvaluationRunModel(
        dataset_id=dataset.id,
        workspace_id=dataset.workspace_id,
        status="completed",
        evaluator_configs=["exact_match"],
        summary={"keep": True},
        dataset_snapshot={"hash": "unchanged", "items": [{"id": str(item.id)}]},
    )
    db_session.add(run)
    await db_session.flush()
    db_session.add(
        EvaluationScoreModel(
            run_id=run.id,
            item_id=item.id,
            workspace_id=dataset.workspace_id,
            metric_name="exact_match",
            value=0.75,
            reasoning="Existing evidence must remain unchanged",
            source="deterministic",
        )
    )
    await db_session.commit()
    contexts = {
        "editor": editor,
        "viewer": await _member_as(db_session, ws, WorkspaceRole.VIEWER),
        "roleless": _no_role_context(ws),
        "no_workspace": _no_workspace_context(),
        "system": _system_context(),
    }

    def unexpected_resolution(name: str) -> None:
        raise AssertionError("Authorization must precede evaluator resolution and execution")

    monkeypatch.setattr(evaluation_api, "get_evaluator_class", unexpected_resolution)
    dataset_id = uuid.uuid4() if condition == "missing" else dataset.id
    before = await _state_snapshot(db_session)
    async with _client_as(client, contexts[identity]) as http:
        for evaluator in ("exact_match", "nonexistent"):
            response = await http.post("/api/evaluation/runs", json=_payload(dataset_id, evaluators=[evaluator]))
            if condition == "visible" and identity in {"viewer", "roleless"}:
                _assert_forbidden(response, roleless=identity == "roleless")
            else:
                _assert_not_found(response)
            assert await _state_snapshot(db_session) == before


# ---------------------------------------------------------------------------
# Success: real evaluator execution, persistence ownership, read-back
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", [WorkspaceRole.EDITOR, WorkspaceRole.ADMIN])
async def test_editor_execution_persists_dataset_workspace_and_reads_back(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: MonkeyPatch,
    role: WorkspaceRole,
) -> None:
    """A W1 editor executes; run and scores land in W1; a W1 viewer reads them back."""
    _seed_evaluator_registry(monkeypatch)
    ws, editor_ctx = await _workspace_with_role(db_session, "ws-owner", role)
    _, outsider_ctx = await _workspace_with_role(db_session, "ws-outsider", WorkspaceRole.EDITOR)
    viewer_ctx = await _member_as(db_session, ws, WorkspaceRole.VIEWER)
    ds = await _seed_dataset(
        db_session,
        ws.id,
        "ds",
        items=[
            {"query": "q1", "expected_answer": "Paris", "generated_answer": "Paris"},
            {"query": "q2", "expected_answer": "42", "generated_answer": "41"},
        ],
    )

    async with _client_as(client, editor_ctx) as http:
        resp = await http.post("/api/evaluation/runs", json=_run_payload(ds))
        assert resp.status_code == 201, resp.text
        body = resp.json()
        expected_keys = {"run_id", "dataset_id", "total_items", "metric_averages", "total_duration_ms", "item_scores"}
        assert set(body) == expected_keys
        assert body["dataset_id"] == str(ds.id)
        assert body["total_items"] == 2
        assert body["metric_averages"]["exact_match"] == pytest.approx(0.5)
        assert len(body["item_scores"]) == 2
        run_id = body["run_id"]

    # Check the persisted run and score ownership after the request commits.
    run_stmt = select(EvaluationRunModel).where(EvaluationRunModel.id == uuid.UUID(run_id))
    run = (await db_session.execute(run_stmt)).scalar_one()
    assert run.workspace_id == ws.id
    assert run.dataset_id == ds.id
    assert run.task_id is None
    assert run.evaluator_configs == ["exact_match"]
    assert run.status == "completed"
    assert run.started_at is not None
    assert run.completed_at is not None

    scores = (
        (await db_session.execute(select(EvaluationScoreModel).where(EvaluationScoreModel.run_id == run.id)))
        .scalars()
        .all()
    )
    assert len(scores) == 2
    assert all(s.workspace_id == ws.id for s in scores)
    assert all(s.metric_name == "exact_match" for s in scores)
    assert all(s.source == "deterministic" for s in scores)
    assert sorted(s.value for s in scores) == [0.0, 1.0]

    # Same-workspace viewer reads the result back through the existing API.
    async with _client_as(client, viewer_ctx) as http:
        listing = await http.get("/api/evaluation/runs")
        assert listing.status_code == 200, listing.text
        assert listing.json()["total"] == 1
        assert listing.json()["items"][0]["id"] == run_id
        assert listing.json()["items"][0]["workspace_id"] == str(ws.id)

        detail = await http.get(f"/api/evaluation/runs/{run_id}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["total_scores"] == 2

        score_list = await http.get(f"/api/evaluation/runs/{run_id}/scores")
        assert score_list.status_code == 200, score_list.text
        assert score_list.json()["total"] == 2

    # Other workspaces still cannot observe the run.
    async with _client_as(client, outsider_ctx) as http:
        listing = await http.get("/api/evaluation/runs")
        assert listing.json()["total"] == 0
        hidden = await http.get(f"/api/evaluation/runs/{run_id}")
        assert hidden.status_code == 404
        assert hidden.json() == {
            "detail": {"error": {"code": "NOT_FOUND", "message": "Run not found", "details": None}}
        }
        hidden_scores = await http.get(f"/api/evaluation/runs/{run_id}/scores")
        assert hidden_scores.status_code == 404 and hidden_scores.json() == hidden.json()


async def test_legacy_zero_workspace_items_participate(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: MonkeyPatch,
) -> None:
    """Historical zero-UUID items follow their parent dataset, not their own column."""
    _seed_evaluator_registry(monkeypatch)
    ws, editor_ctx = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.EDITOR)
    ds = await _seed_dataset(
        db_session,
        ws.id,
        "ds-legacy",
        items=[
            {"query": "q1", "expected_answer": "a", "generated_answer": "a"},
            {"query": "q2", "expected_answer": "b", "generated_answer": "b"},
        ],
    )
    item_stmt = select(EvaluationItemModel).where(EvaluationItemModel.dataset_id == ds.id)
    items = (await db_session.execute(item_stmt)).scalars().all()
    assert all(it.workspace_id == ZERO_WORKSPACE for it in items)

    async with _client_as(client, editor_ctx) as http:
        resp = await http.post("/api/evaluation/runs", json=_run_payload(ds))
        assert resp.status_code == 201, resp.text
        assert resp.json()["total_items"] == 2

    scores = (await db_session.execute(select(EvaluationScoreModel))).scalars().all()
    assert len(scores) == 2
    assert all(s.workspace_id == ws.id for s in scores)


async def test_empty_dataset_run_completes(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: MonkeyPatch,
) -> None:
    """A legal empty dataset still yields a completed 201 run."""
    _seed_evaluator_registry(monkeypatch)
    ws, editor_ctx = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.EDITOR)
    ds = await _seed_dataset(db_session, ws.id, "ds-empty", items=[])

    async with _client_as(client, editor_ctx) as http:
        resp = await http.post("/api/evaluation/runs", json=_run_payload(ds))
        assert resp.status_code == 201, resp.text
        assert resp.json()["total_items"] == 0

    run = (await db_session.execute(select(EvaluationRunModel))).scalars().one()
    assert run.workspace_id == ws.id
    assert run.status == "completed"


async def test_unknown_evaluator_422_after_authorization_leaves_no_rows(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: MonkeyPatch,
) -> None:
    """An authorized editor keeps the 422 branch, with zero rows created."""
    _seed_evaluator_registry(monkeypatch)
    ws, editor_ctx = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.EDITOR)
    ds = await _seed_dataset(db_session, ws.id, "ds", items=[{"query": "q"}])
    before = await _state_snapshot(db_session)

    async with _client_as(client, editor_ctx) as http:
        resp = await http.post("/api/evaluation/runs", json=_run_payload(ds, evaluators=["nonexistent"]))
        assert resp.status_code == 422, resp.text
        assert resp.json()["detail"]["error"]["code"] == "INVALID_EVALUATOR"

    assert await _state_snapshot(db_session) == before


async def test_tag_filter_semantics_preserved(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: MonkeyPatch,
) -> None:
    """Tag filtering still narrows execution to the tagged items."""
    _seed_evaluator_registry(monkeypatch)
    ws, editor_ctx = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.EDITOR)
    ds = await _seed_dataset(
        db_session,
        ws.id,
        "ds-tags",
        items=[
            {"query": "q1", "expected_answer": "a", "generated_answer": "a", "tags": ["smoke"]},
            {"query": "q2", "expected_answer": "b", "generated_answer": "b", "tags": ["regression"]},
        ],
    )

    async with _client_as(client, editor_ctx) as http:
        resp = await http.post("/api/evaluation/runs", json=_run_payload(ds, tags=["smoke"]))
        assert resp.status_code == 201, resp.text
        assert resp.json()["total_items"] == 1

    assert await _score_count(db_session) == 1


@pytest.mark.parametrize("answer_source", ["auto", "pipeline"])
@pytest.mark.parametrize("generation_fails", [False, True])
async def test_generated_sources_complete_without_external_model(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: MonkeyPatch,
    answer_source: str,
    generation_fails: bool,
) -> None:
    """Real engine execution preserves generated-answer and error-score semantics."""
    _seed_evaluator_registry(monkeypatch)
    ws, editor_ctx = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.EDITOR)
    ds = await _seed_dataset(
        db_session,
        ws.id,
        f"ds-{answer_source}",
        items=[{"query": "q1", "expected_answer": "alpha centauri", "context": ["Alpha Centauri is nearby."]}],
    )
    calls: list[tuple[str, list[str]]] = []

    async def generate(self: evaluation_engine.EvaluationEngine, query: str, context: list[str]) -> str:
        calls.append((query, context))
        if generation_fails:
            raise RuntimeError("stub generation failed")
        return "alpha centauri"

    monkeypatch.setattr(evaluation_engine.EvaluationEngine, "_generate_answer_via_pipeline", generate)
    viewer = await _member_as(db_session, ws, WorkspaceRole.VIEWER)
    async with _client_as(client, editor_ctx) as http:
        response = await http.post("/api/evaluation/runs", json=_run_payload(ds, answer_source=answer_source))
    assert response.status_code == 201, response.text
    assert calls == [("q1", ["Alpha Centauri is nearby."])]
    run_table, score_table = EvaluationRunModel.__table__, EvaluationScoreModel.__table__
    run = (await db_session.execute(select(run_table))).mappings().one()
    score = (await db_session.execute(select(score_table))).mappings().one()
    assert run["workspace_id"] == ws.id and run["status"] == "completed"
    assert score["workspace_id"] == ws.id and score["run_id"] == run["id"]
    item_id, item_scores = next(iter(response.json()["item_scores"].items()))
    assert score["item_id"] == uuid.UUID(item_id)
    assert {key: score[key] for key in item_scores[0]} == item_scores[0]
    if generation_fails:
        assert score["value"] == -1.0 and score["source"] == "llm_judge"
        assert "stub generation failed" in score["reasoning"]
        assert response.json()["metric_averages"] == {}
    else:
        assert score["value"] == 1.0 and score["source"] == "deterministic"
        assert response.json()["metric_averages"] == {"exact_match": 1.0}

    async with _client_as(client, viewer) as http:
        detail = await http.get(f"/api/evaluation/runs/{run['id']}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["total_scores"] == 1
        assert detail.json()["metric_averages"] == response.json()["metric_averages"]
        scores = await http.get(f"/api/evaluation/runs/{run['id']}/scores")
        assert scores.status_code == 200, scores.text
        assert scores.json()["total"] == 1
        persisted = scores.json()["items"][0]
        assert persisted["workspace_id"] == str(ws.id)
        assert persisted["item_id"] == item_id
        assert {key: persisted[key] for key in item_scores[0]} == item_scores[0]


# ---------------------------------------------------------------------------
# System scope: cross-workspace execution targets the dataset's workspace
# ---------------------------------------------------------------------------


async def test_evaluator_error_score_retains_dataset_workspace(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: MonkeyPatch,
) -> None:
    """Evaluator failures still persist error scores owned by the target workspace."""
    _seed_evaluator_registry(monkeypatch)
    ws, editor = await _workspace_with_role(db_session, "error-owner", WorkspaceRole.EDITOR)
    dataset = await _seed_dataset(
        db_session, ws.id, "error-dataset", items=[{"query": "q", "generated_answer": "answer"}]
    )

    async def fail_evaluation(*args: object, **kwargs: object) -> None:
        raise RuntimeError("stub evaluator failed")

    monkeypatch.setattr(ExactMatchEvaluator, "evaluate", fail_evaluation)
    async with _client_as(client, editor) as http:
        response = await http.post("/api/evaluation/runs", json=_run_payload(dataset))
    assert response.status_code == 201, response.text
    score = (await db_session.execute(select(EvaluationScoreModel.__table__))).mappings().one()
    assert score["workspace_id"] == ws.id
    assert score["run_id"] == uuid.UUID(response.json()["run_id"])
    assert score["value"] == -1.0 and score["source"] == "llm_judge"
    assert "stub evaluator failed" in score["reasoning"]
    assert response.json()["metric_averages"] == {}


async def test_system_scope_executes_across_workspaces(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: MonkeyPatch,
) -> None:
    """System keys execute by dataset ID; the run lands in the dataset's workspace."""
    _seed_evaluator_registry(monkeypatch)
    ws, _ = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.EDITOR)
    other_ws, _ = await _workspace_with_role(db_session, "ws-other", WorkspaceRole.EDITOR)
    ds = await _seed_dataset(
        db_session,
        ws.id,
        "ds-system",
        items=[{"query": "q1", "expected_answer": "a", "generated_answer": "a"}],
    )

    # Without an active workspace...
    async with _client_as(client, _system_context()) as http:
        first = await http.post("/api/evaluation/runs", json=_run_payload(ds))
        assert first.status_code == 201, first.text
        first_run_id = first.json()["run_id"]

    # ...and with a different active workspace (system scope is unaffected).
    async with _client_as(client, _system_context(workspace_id=other_ws.id)) as http:
        second = await http.post("/api/evaluation/runs", json=_run_payload(ds))
        assert second.status_code == 201, second.text
        second_run_id = second.json()["run_id"]

    assert first_run_id != second_run_id
    run_ids = [uuid.UUID(first_run_id), uuid.UUID(second_run_id)]
    runs_stmt = select(EvaluationRunModel).where(EvaluationRunModel.id.in_(run_ids))
    runs = (await db_session.execute(runs_stmt)).scalars().all()
    assert len(runs) == 2
    assert all(run.workspace_id == ws.id for run in runs)
    scores = (await db_session.execute(select(EvaluationScoreModel))).scalars().all()
    assert len(scores) == 2
    assert all(score.workspace_id == ws.id and score.run_id in run_ids for score in scores)
