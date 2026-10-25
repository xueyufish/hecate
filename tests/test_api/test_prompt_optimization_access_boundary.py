"""Prompt optimization access boundary (step10).

Covers the eight REST entries of the prompt self-optimization surface
(run create/list/get/cancel, candidate list/get, approve/reject) behind
the evaluation access boundary:

- Every identity — system scope included — needs an active workspace
  (403 ``NO_WORKSPACE`` before any query); an explicit zero-UUID
  workspace stays a legal scope.
- Role floors: VIEWER to read, EDITOR to create/cancel/approve/reject —
  enforced from the server-resolved auth context only, after resource
  visibility and before any business query, evidence read, or mutation.
- By-id visibility: missing, foreign, and soft-deleted runs/candidates —
  including candidates whose parent run left the workspace — share one
  404 that leaks neither the reason nor any evidence.
- Create validates the full input binding: prompt, base version, agent,
  dataset, and pinned dataset version all live in the workspace and
  bound to each other.
- The review service re-verifies candidate→run→prompt inside the write
  transaction with fresh reads: a stale API precheck cannot publish or
  reject against another object, and a rejected binding changes nothing.
- The background runner re-verifies the run and every input reference
  on its fresh session before consuming templates, samples, agents, or
  model calls; invalid sources stop the run safely with zero rollouts.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.auth_context import AuthContext
from hecate.core.config import settings
from hecate.core.deps import get_db
from hecate.core.deps_workspace import get_auth_context
from hecate.models.evaluation import EvaluationDatasetVersionModel
from hecate.models.organization import OrganizationModel
from hecate.models.prompt import PromptModel, PromptVersionModel
from hecate.models.prompt_optimization import (
    PromptOptimizationCandidateModel,
    PromptOptimizationRunModel,
)
from hecate.models.workspace import WorkspaceModel
from hecate.models.workspace_member import WorkspaceMemberModel, WorkspaceRole
from hecate.ops.evaluation.engine import EvaluationEngine
from hecate.ops.prompt_optimization.review import CandidateReviewService
from hecate.ops.prompt_optimization.runner import PromptOptimizationRunner
from hecate.ops.prompt_optimization.service import PromptOptimizationService
from tests.conftest import test_session_factory as shared_session_factory
from tests.test_services.test_prompt_optimization.helpers import (
    dataset_items,
    seed_agent,
    seed_dataset_version,
    seed_prompt,
)

_BASE = "/api/prompt-optimization"

_CREATE_BODY_EXTRAS = {"evaluator_configs": ["stub"], "primary_metric": "stub"}


async def _workspace_with_role(db: AsyncSession, name: str, role: WorkspaceRole) -> WorkspaceModel:
    name = f"{name}-{uuid.uuid4().hex[:8]}"
    user_id = uuid.uuid4()
    org = OrganizationModel(name=f"Org {name}", slug=f"org-{name}", owner_id=user_id)
    db.add(org)
    await db.flush()
    ws = WorkspaceModel(org_id=org.id, name=name, slug=name)
    db.add(ws)
    await db.flush()
    db.add(WorkspaceMemberModel(user_id=user_id, workspace_id=ws.id, role=role))
    await db.commit()
    return ws


def _member_as(ws: WorkspaceModel, role: WorkspaceRole | None) -> AuthContext:
    return AuthContext(
        user_id=uuid.uuid4(),
        org_id=ws.org_id,
        workspace_id=ws.id,
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
async def _client_as(ctx: AuthContext) -> AsyncIterator[AsyncClient]:
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


async def _seed_candidate(
    db: AsyncSession,
    run: PromptOptimizationRunModel,
    *,
    status: str = "pending_review",
) -> PromptOptimizationCandidateModel:
    candidate = PromptOptimizationCandidateModel(
        run_id=run.id,
        round_no=1,
        template="Answer {{q}} carefully.",
        status=status,
        gate_report={"baseline_scores": {"stub": 0.5}, "candidate_scores": {"stub": 0.7}},
        workspace_id=run.workspace_id,
    )
    db.add(candidate)
    await db.commit()
    return candidate


async def _seed_run(
    db: AsyncSession,
    prompt: PromptModel,
    agent,
    dataset_version: EvaluationDatasetVersionModel,
    workspace_id: uuid.UUID,
    *,
    status: str = "awaiting_review",
) -> PromptOptimizationRunModel:
    run = PromptOptimizationRunModel(
        prompt_id=prompt.id,
        base_version=1,
        agent_id=agent.id,
        dataset_id=dataset_version.dataset_id,
        dataset_version_id=dataset_version.id,
        dataset_version_hash=dataset_version.content_hash,
        split_ratio=0.5,
        evaluator_configs=["stub"],
        primary_metric="stub",
        min_improvement=0.02,
        max_regression=0.05,
        budget_preset="light",
        max_rounds=2,
        mutation_call_limit=10,
        rollout_item_limit=100,
        strategy="reflective_mutation",
        reflection_model="test-model",
        status=status,
        workspace_id=workspace_id,
    )
    db.add(run)
    await db.commit()
    return run


@pytest.fixture
async def env(db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch):
    """App boundary with resolved auth contexts, shared sessions, and spies."""
    from tests.test_services.test_prompt_optimization.test_runner import StubEvaluator

    monkeypatch.setattr(settings, "PROMPT_OPTIMIZATION_ENABLED", True)
    monkeypatch.setattr(
        "hecate.ops.prompt_optimization.service.get_evaluator_class",
        lambda name: StubEvaluator if name == "stub" else None,
    )
    dispatched: list[str] = []

    class StubRunner:
        def __init__(self, db) -> None:
            pass

        async def run_in_background(self, run_id: uuid.UUID, *, workspace_id: uuid.UUID) -> None:
            dispatched.append(str(run_id))

    monkeypatch.setattr("hecate.ops.api.prompt_optimization.PromptOptimizationRunner", StubRunner)

    ws = await _workspace_with_role(db_session, "opt-boundary", WorkspaceRole.VIEWER)
    prompt = seed_prompt(db_session, ws.id, "Answer {{q}}.", ["q"])
    agent = seed_agent(db_session, ws.id)
    dataset, dataset_version = seed_dataset_version(db_session, ws.id, dataset_items(4))
    await db_session.commit()
    run = await _seed_run(db_session, prompt, agent, dataset_version, ws.id)
    candidate = await _seed_candidate(db_session, run)
    other_ws = await _workspace_with_role(db_session, "opt-other", WorkspaceRole.VIEWER)

    from hecate.main import app

    async def shared_db():
        yield db_session

    saved_db = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = shared_db
    try:
        yield {
            "db": db_session,
            "ws": ws,
            "other_ws": other_ws,
            "prompt": prompt,
            "agent": agent,
            "dataset": dataset,
            "dataset_version": dataset_version,
            "run": run,
            "candidate": candidate,
            "dispatched": dispatched,
        }
    finally:
        if saved_db is None:
            app.dependency_overrides.pop(get_db, None)
        else:
            app.dependency_overrides[get_db] = saved_db


def _create_body(env, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "prompt_id": str(env["prompt"].id),
        "agent_id": str(env["agent"].id),
        "dataset_id": str(env["dataset"].id),
        "dataset_version_id": str(env["dataset_version"].id),
        **_CREATE_BODY_EXTRAS,
    }
    payload.update(overrides)
    return payload


async def _snapshot(db: AsyncSession) -> dict[str, list[dict[str, Any]]]:
    from hecate.models.evaluation import EvaluationDatasetModel

    snapshot: dict[str, list[dict[str, Any]]] = {}
    for model in (
        PromptOptimizationRunModel,
        PromptOptimizationCandidateModel,
        PromptModel,
        PromptVersionModel,
        EvaluationDatasetModel,
        EvaluationDatasetVersionModel,
    ):
        table = model.__table__
        rows = (await db.execute(select(table).order_by(table.c.id))).mappings().all()
        snapshot[table.name] = [dict(row) for row in rows]
    return snapshot


def _assert_no_workspace(resp: Any) -> None:
    assert resp.status_code == 403, resp.text
    assert resp.json() == {
        "detail": {"error": {"code": "NO_WORKSPACE", "message": "An active workspace is required", "details": None}}
    }


def _assert_forbidden(resp: Any) -> None:
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"]["error"]["code"] == "FORBIDDEN"


def _assert_not_found(resp: Any) -> None:
    assert resp.status_code == 404, resp.text
    text = resp.text
    assert "gate_report" not in text and "per_item" not in text and "baseline_scores" not in text


# ---------------------------------------------------------------------------
# Workspace requirement: every identity, every endpoint, before any query
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("identity", ["member", "system"])
async def test_all_entries_require_workspace_before_any_query(
    env, monkeypatch: pytest.MonkeyPatch, identity: str
) -> None:
    """Workspace-less identities — system included — never reach a query."""
    from sqlalchemy.ext.asyncio import AsyncSession

    async def unexpected(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("denied requests must not query")

    ctx = _system_context() if identity == "system" else _no_workspace_context()
    before = await _snapshot(env["db"])
    with monkeypatch.context() as patch:
        patch.setattr(AsyncSession, "execute", unexpected)
        async with _client_as(ctx) as http:
            missing = await http.post(f"{_BASE}/runs", json=_create_body(env))
            listing = await http.get(f"{_BASE}/runs")
            detail = await http.get(f"{_BASE}/runs/{uuid.uuid4()}")
            cancel = await http.post(f"{_BASE}/runs/{uuid.uuid4()}/cancel")
            candidates = await http.get(f"{_BASE}/runs/{uuid.uuid4()}/candidates")
            evidence = await http.get(f"{_BASE}/candidates/{uuid.uuid4()}")
            approve = await http.post(f"{_BASE}/candidates/{uuid.uuid4()}/approve")
            reject = await http.post(f"{_BASE}/candidates/{uuid.uuid4()}/reject", json={"reason": "no"})
    for resp in (missing, listing, detail, cancel, candidates, evidence, approve, reject):
        _assert_no_workspace(resp)
    assert await _snapshot(env["db"]) == before


# ---------------------------------------------------------------------------
# Role floors after visibility
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", [WorkspaceRole.VIEWER, None])
async def test_create_below_editor_denied_before_business(env, role) -> None:
    """Viewers and role-less members are denied before validation/dispatch."""
    before = await _snapshot(env["db"])
    async with _client_as(_member_as(env["ws"], role)) as http:
        denied = await http.post(f"{_BASE}/runs", json=_create_body(env))
        business_invalid = await http.post(f"{_BASE}/runs", json=_create_body(env, evaluator_configs=["nope"]))
    _assert_forbidden(denied)
    _assert_forbidden(business_invalid)
    assert env["dispatched"] == []
    assert await _snapshot(env["db"]) == before


@pytest.mark.parametrize("role", [WorkspaceRole.EDITOR, WorkspaceRole.ADMIN])
async def test_editor_creates_and_dispatches(env, role) -> None:
    """Editor and above create the run; the background handoff fires once."""
    async with _client_as(_member_as(env["ws"], role)) as http:
        response = await http.post(f"{_BASE}/runs", json=_create_body(env))
    assert response.status_code == 202, response.text
    run_id = uuid.UUID(response.json()["id"])
    assert env["dispatched"] == [str(run_id)]
    row = await env["db"].get(PromptOptimizationRunModel, run_id)
    assert row is not None and row.workspace_id == env["ws"].id


async def test_viewer_reads_but_never_writes(env) -> None:
    """VIEWER reads everything; writes fall to 403 after visibility."""
    ws = env["ws"]
    viewer = _member_as(ws, WorkspaceRole.VIEWER)
    async with _client_as(viewer) as http:
        listing = await http.get(f"{_BASE}/runs")
        assert listing.status_code == 200 and listing.json()["total"] >= 1
        detail = await http.get(f"{_BASE}/runs/{env['run'].id}")
        assert detail.status_code == 200 and detail.json()["id"] == str(env["run"].id)
        candidates = await http.get(f"{_BASE}/runs/{env['run'].id}/candidates")
        assert candidates.status_code == 200 and candidates.json()["total"] == 1
        evidence = await http.get(f"{_BASE}/candidates/{env['candidate'].id}")
        assert evidence.status_code == 200 and evidence.json()["status"] == "pending_review"

        before = await _snapshot(env["db"])
        cancel = await http.post(f"{_BASE}/runs/{env['run'].id}/cancel")
        approve = await http.post(f"{_BASE}/candidates/{env['candidate'].id}/approve")
        reject = await http.post(f"{_BASE}/candidates/{env['candidate'].id}/reject", json={"reason": "not good"})
    _assert_forbidden(cancel)
    _assert_forbidden(approve)
    _assert_forbidden(reject)
    assert env["dispatched"] == []
    assert await _snapshot(env["db"]) == before


async def test_roleless_member_denied_on_reads_after_visibility(env) -> None:
    """A role-less member still resolves visibility first, then 403."""
    roleless = _member_as(env["ws"], None)
    async with _client_as(roleless) as http:
        visible_run = await http.get(f"{_BASE}/runs/{env['run'].id}")
        invisible_run = await http.get(f"{_BASE}/runs/{uuid.uuid4()}")
        visible_candidate = await http.get(f"{_BASE}/candidates/{env['candidate'].id}")
    _assert_forbidden(visible_run)
    _assert_forbidden(visible_candidate)
    assert invisible_run.status_code == 404, invisible_run.text


# ---------------------------------------------------------------------------
# Visibility: missing / foreign / soft-deleted / binding-inconsistent
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("identity", ["viewer", "roleless", "editor", "admin"])
async def test_invisible_resources_share_one_404(env, identity: str) -> None:
    """Missing, foreign, deleted, and parent-inconsistent records look alike."""
    ws = env["ws"]
    ctx = {
        "viewer": _member_as(ws, WorkspaceRole.VIEWER),
        "roleless": _member_as(ws, None),
        "editor": _member_as(ws, WorkspaceRole.EDITOR),
        "admin": _member_as(ws, WorkspaceRole.ADMIN),
    }[identity]
    # A run moved to another workspace, and a candidate whose parent run left.
    foreign_run = await _seed_run(env["db"], env["prompt"], env["agent"], env["dataset_version"], env["other_ws"].id)
    orphan_run = await _seed_run(env["db"], env["prompt"], env["agent"], env["dataset_version"], ws.id)
    orphan = await _seed_candidate(env["db"], orphan_run)
    orphan_run.workspace_id = env["other_ws"].id
    deleted_run = await _seed_run(env["db"], env["prompt"], env["agent"], env["dataset_version"], ws.id)
    deleted_run.deleted = True
    await env["db"].commit()

    before = await _snapshot(env["db"])
    async with _client_as(ctx) as http:
        for run_id in (uuid.uuid4(), foreign_run.id, deleted_run.id, orphan_run.id):
            detail = await http.get(f"{_BASE}/runs/{run_id}")
            assert detail.status_code == 404, detail.text
            cancel = await http.post(f"{_BASE}/runs/{run_id}/cancel")
            assert cancel.status_code == 404, cancel.text
            listing = await http.get(f"{_BASE}/runs/{run_id}/candidates")
            assert listing.status_code == 404, listing.text
            assert str(run_id) not in detail.text or True  # caller-supplied id echo is fine
        evidence = await http.get(f"{_BASE}/candidates/{orphan.id}")
        assert evidence.status_code == 404, evidence.text
        approve = await http.post(f"{_BASE}/candidates/{orphan.id}/approve")
        assert approve.status_code == 404, approve.text
        reject = await http.post(f"{_BASE}/candidates/{orphan.id}/reject", json={"reason": "x"})
        assert reject.status_code == 404, reject.text
        for resp in (evidence, approve, reject):
            assert "gate_report" not in resp.text
    assert await _snapshot(env["db"]) == before


async def test_candidate_listing_excludes_binding_inconsistency(env) -> None:
    """Direct service listing never mixes foreign-run candidates in."""
    ws = env["ws"]
    orphan_run = await _seed_run(env["db"], env["prompt"], env["agent"], env["dataset_version"], ws.id)
    await _seed_candidate(env["db"], orphan_run)
    orphan_run.workspace_id = env["other_ws"].id
    await env["db"].commit()
    with pytest.raises(LookupError):
        await CandidateReviewService(env["db"]).list_candidates(orphan_run.id, ws.id)
    rows = await CandidateReviewService(env["db"]).list_candidates(env["run"].id, ws.id)
    assert [row.id for row in rows] == [env["candidate"].id]


# ---------------------------------------------------------------------------
# Create: full input binding
# ---------------------------------------------------------------------------


async def _assert_create_404(env, http, **overrides: Any) -> None:
    before = await _snapshot(env["db"])
    response = await http.post(f"{_BASE}/runs", json=_create_body(env, **overrides))
    assert response.status_code == 404, response.text
    assert env["dispatched"] == []
    assert await _snapshot(env["db"]) == before


async def test_create_validates_full_input_binding(env) -> None:
    """Prompt, base version, agent, dataset, and version must bind."""
    ws, other = env["ws"], env["other_ws"]
    foreign_prompt = seed_prompt(env["db"], other.id, "Foreign {{q}}.", ["q"])
    foreign_agent = seed_agent(env["db"], other.id)
    foreign_dataset, foreign_version = seed_dataset_version(env["db"], other.id, dataset_items(4))
    # Same workspace version row bound to a foreign prompt of this workspace:
    second_prompt = seed_prompt(env["db"], ws.id, "Second {{q}}.", ["q"])
    await env["db"].commit()
    # A version row that exists, is live, but sits in another workspace.
    prompt = env["prompt"]
    foreign_ws_version = PromptVersionModel(
        prompt_id=prompt.id,
        version=7,
        template="Foreign-ws {{q}}.",
        variables=["q"],
        labels=[],
        workspace_id=other.id,
    )
    env["db"].add(foreign_ws_version)
    await env["db"].commit()

    editor = _member_as(ws, WorkspaceRole.EDITOR)
    async with _client_as(editor) as http:
        await _assert_create_404(env, http, prompt_id=str(foreign_prompt.id))
        await _assert_create_404(env, http, base_version=99)
        await _assert_create_404(env, http, agent_id=str(foreign_agent.id))
        await _assert_create_404(env, http, dataset_id=str(foreign_dataset.id))
        await _assert_create_404(env, http, dataset_version_id=str(foreign_version.id))
        await _assert_create_404(env, http, base_version=7)  # version lives in another workspace
        await _assert_create_404(env, http, dataset_id=str(second_prompt.id))  # not a dataset here

    # Soft-deleting the dataset parent invalidates the pinned version too.
    env["dataset"].deleted = True
    await env["db"].commit()
    async with _client_as(editor) as http:
        await _assert_create_404(env, http)


async def test_zero_uuid_workspace_is_a_legal_scope(env) -> None:
    """An explicit zero-UUID workspace keeps full function."""
    from tests.test_services.test_prompt_optimization.helpers import ZERO_WS

    db = env["db"]
    prompt = seed_prompt(db, ZERO_WS, "Answer {{q}}.", ["q"])
    agent = seed_agent(db, ZERO_WS)
    dataset, version = seed_dataset_version(db, ZERO_WS, dataset_items(4))
    await db.commit()
    zero_editor = AuthContext(
        user_id=uuid.uuid4(),
        org_id=None,
        workspace_id=ZERO_WS,
        role=WorkspaceRole.EDITOR,
        auth_method="jwt",
        api_key_scope=None,
    )
    body = {
        "prompt_id": str(prompt.id),
        "agent_id": str(agent.id),
        "dataset_id": str(dataset.id),
        "dataset_version_id": str(version.id),
        **_CREATE_BODY_EXTRAS,
    }
    async with _client_as(zero_editor) as http:
        created = await http.post(f"{_BASE}/runs", json=body)
        assert created.status_code == 202, created.text
        run_id = uuid.UUID(created.json()["id"])
        row = await db.get(PromptOptimizationRunModel, run_id)
        assert row.workspace_id == ZERO_WS
        listed = await http.get(f"{_BASE}/runs")
        assert listed.status_code == 200
        assert str(run_id) in [item["id"] for item in listed.json()["items"]]


# ---------------------------------------------------------------------------
# Write-path recheck: identity-map staleness cannot authorize a mutation
# ---------------------------------------------------------------------------


async def _interpose(monitor: dict, mutation) -> None:  # noqa: ANN001
    original = CandidateReviewService.get_candidate_binding

    async def wrapped(self, candidate_id, workspace_id):  # noqa: ANN001
        candidate = await original(self, candidate_id, workspace_id)
        if candidate is not None and not monitor.get("armed"):
            monitor["armed"] = True
            async with shared_session_factory() as session:
                await mutation(session)
                await session.commit()
        return candidate

    monitor["patch"] = wrapped


async def test_approve_rechecks_binding_after_precheck(env, monkeypatch: pytest.MonkeyPatch) -> None:
    """A parent run deleted after the API precheck blocks publication."""
    monitor: dict[str, Any] = {}

    async def delete_run(session: AsyncSession) -> None:
        run = await session.get(PromptOptimizationRunModel, env["run"].id)
        run.deleted = True

    await _interpose(monitor, delete_run)
    monkeypatch.setattr(CandidateReviewService, "get_candidate_binding", monitor["patch"])
    before = await _snapshot(env["db"])
    editor = _member_as(env["ws"], WorkspaceRole.EDITOR)
    async with _client_as(editor) as http:
        response = await http.post(f"{_BASE}/candidates/{env['candidate'].id}/approve")
    assert response.status_code == 404, response.text
    after = await _snapshot(env["db"])
    # No new prompt version beyond the baseline seed, no mutation, no reviewer.
    assert len(after["prompt_versions"]) == len(before["prompt_versions"])
    stored = next(row for row in after["prompt_optimization_candidates"] if row["id"] == env["candidate"].id)
    assert stored["status"] == "pending_review"
    assert stored["decided_by"] is None
    assert next(row for row in after["prompts"] if row["id"] == env["prompt"].id)["current_version"] == 1


async def test_reject_rechecks_binding_after_precheck(env, monkeypatch: pytest.MonkeyPatch) -> None:
    """A run moved to another workspace blocks rejection without changes."""
    monitor: dict[str, Any] = {}

    async def move_run(session: AsyncSession) -> None:
        run = await session.get(PromptOptimizationRunModel, env["run"].id)
        run.workspace_id = env["other_ws"].id

    await _interpose(monitor, move_run)
    monkeypatch.setattr(CandidateReviewService, "get_candidate_binding", monitor["patch"])
    editor = _member_as(env["ws"], WorkspaceRole.EDITOR)
    async with _client_as(editor) as http:
        response = await http.post(f"{_BASE}/candidates/{env['candidate'].id}/reject", json={"reason": "not good"})
    assert response.status_code == 404, response.text
    after = await _snapshot(env["db"])
    stored = next(row for row in after["prompt_optimization_candidates"] if row["id"] == env["candidate"].id)
    assert stored["status"] == "pending_review"
    assert stored["rejection_reason"] is None and stored["decided_by"] is None
    assert next(row for row in after["prompts"] if row["id"] == env["prompt"].id)["current_version"] == 1


async def test_cancel_rechecks_after_precheck(env, monkeypatch: pytest.MonkeyPatch) -> None:
    """A run deleted after the visibility precheck is never cancelled."""
    original = PromptOptimizationService.is_run_visible

    async def wrapped(self, run_id, workspace_id):  # noqa: ANN001
        visible = await original(self, run_id, workspace_id)
        if visible and run_id == env["run"].id:
            async with shared_session_factory() as session:
                row = await session.get(PromptOptimizationRunModel, run_id)
                row.deleted = True
                await session.commit()
        return visible

    monkeypatch.setattr(PromptOptimizationService, "is_run_visible", wrapped)
    editor = _member_as(env["ws"], WorkspaceRole.EDITOR)
    async with _client_as(editor) as http:
        response = await http.post(f"{_BASE}/runs/{env['run'].id}/cancel")
    assert response.status_code == 404, response.text
    row = await env["db"].get(PromptOptimizationRunModel, env["run"].id)
    await env["db"].refresh(row)
    assert row.status == "awaiting_review" and row.stop_reason is None


# ---------------------------------------------------------------------------
# Success semantics preserved
# ---------------------------------------------------------------------------


async def test_approve_publishes_unlabeled_version_and_concludes(env) -> None:
    """Approval keeps provenance, labels=[], reviewer attribution, converge."""
    editor = _member_as(env["ws"], WorkspaceRole.EDITOR)
    reviewer_id = editor.user_id
    async with _client_as(editor) as http:
        response = await http.post(f"{_BASE}/candidates/{env['candidate'].id}/approve")
    assert response.status_code == 200, response.text
    assert response.json()["published_prompt_version"] == 2
    version_row = (
        await env["db"].execute(
            select(PromptVersionModel).where(
                PromptVersionModel.prompt_id == env["prompt"].id, PromptVersionModel.version == 2
            )
        )
    ).scalar_one()
    assert version_row.labels == []
    assert version_row.metadata_["source"] == "prompt_optimization"
    assert version_row.metadata_["candidate_id"] == str(env["candidate"].id)
    await env["db"].refresh(env["candidate"])
    await env["db"].refresh(env["run"])
    assert env["candidate"].status == "published"
    assert env["candidate"].decided_by == reviewer_id
    assert env["run"].status == "concluded"

    # State conflicts keep 409 on every decided candidate.
    async with _client_as(editor) as http:
        again = await http.post(f"{_BASE}/candidates/{env['candidate'].id}/approve")
        rejected_now = await http.post(f"{_BASE}/candidates/{env['candidate'].id}/reject", json={"reason": "late"})
    assert again.status_code == 409, again.text
    assert rejected_now.status_code == 409, rejected_now.text


async def test_reject_persists_reason_and_concludes(env) -> None:
    """Rejection keeps the reason, reviewer, and convergence semantics."""
    editor = _member_as(env["ws"], WorkspaceRole.EDITOR)
    async with _client_as(editor) as http:
        response = await http.post(f"{_BASE}/candidates/{env['candidate'].id}/reject", json={"reason": "needs work"})
    assert response.status_code == 200, response.text
    await env["db"].refresh(env["candidate"])
    await env["db"].refresh(env["run"])
    assert env["candidate"].status == "rejected"
    assert env["candidate"].rejection_reason == "needs work"
    assert env["candidate"].decided_by == editor.user_id
    assert env["run"].status == "concluded"


async def test_active_run_conflict_keeps_409(env) -> None:
    """A running run for the same prompt keeps the 409 conflict."""
    env["run"].status = "running"
    await env["db"].commit()
    editor = _member_as(env["ws"], WorkspaceRole.EDITOR)
    async with _client_as(editor) as http:
        response = await http.post(f"{_BASE}/runs", json=_create_body(env))
    assert response.status_code == 409, response.text


async def test_flag_off_keeps_structured_404(env, monkeypatch: pytest.MonkeyPatch) -> None:
    """With the feature off, structurally valid requests still get 404."""
    monkeypatch.setattr(settings, "PROMPT_OPTIMIZATION_ENABLED", False)
    admin = _member_as(env["ws"], WorkspaceRole.ADMIN)
    async with _client_as(admin) as http:
        for method, path in (
            ("post", f"{_BASE}/runs"),
            ("get", f"{_BASE}/runs"),
            ("get", f"{_BASE}/runs/{env['run'].id}"),
            ("post", f"{_BASE}/runs/{env['run'].id}/cancel"),
            ("get", f"{_BASE}/runs/{env['run'].id}/candidates"),
            ("get", f"{_BASE}/candidates/{env['candidate'].id}"),
            ("post", f"{_BASE}/candidates/{env['candidate'].id}/approve"),
            ("post", f"{_BASE}/candidates/{env['candidate'].id}/reject"),
        ):
            if method == "post" and path.endswith("/runs"):
                resp = await http.post(path, json=_create_body(env))
            elif method == "post" and path.endswith("/reject"):
                resp = await http.post(path, json={"reason": "x"})
            elif method == "post":
                resp = await http.post(path)
            else:
                resp = await http.get(path)
            assert resp.status_code == 404, resp.text


# ---------------------------------------------------------------------------
# Background runner: source re-verification before any consumption
# ---------------------------------------------------------------------------


@pytest.fixture
def fresh_background_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the runner's fresh-session factory at the shared test DB."""
    monkeypatch.setattr("hecate.core.database.async_session_factory", shared_session_factory)


@pytest.fixture
def no_model_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Fail the test on any engine rollout attempt."""
    calls: list[str] = []

    async def spy(self, *args: Any, **kwargs: Any) -> None:
        calls.append("engine.run")
        raise AssertionError("model rollout must not run with invalid sources")

    monkeypatch.setattr(EvaluationEngine, "run", spy)
    return calls


async def _background_run_row(env, **overrides: Any) -> PromptOptimizationRunModel:
    run = await _seed_run(
        env["db"], env["prompt"], env["agent"], env["dataset_version"], env["ws"].id, status="created"
    )
    for key, value in overrides.items():
        setattr(run, key, value)
    await env["db"].commit()
    return run


@pytest.mark.parametrize(
    "damage",
    ["deleted_run", "deleted_prompt", "foreign_base_version", "deleted_dataset", "rebound_version", "deleted_agent"],
)
async def test_background_stops_safely_on_invalid_sources(
    env, fresh_background_session, no_model_calls, damage: str
) -> None:
    """Invalid sources stop the run with zero model calls and rollouts."""
    run = await _background_run_row(env)
    db = env["db"]
    if damage == "deleted_run":
        run.deleted = True
    elif damage == "deleted_prompt":
        env["prompt"].deleted = True
    elif damage == "foreign_base_version":
        foreign_version = (
            await db.execute(
                select(PromptVersionModel).where(
                    PromptVersionModel.prompt_id == env["prompt"].id, PromptVersionModel.version == 1
                )
            )
        ).scalar_one()
        foreign_version.workspace_id = env["other_ws"].id
    elif damage == "deleted_dataset":
        env["dataset"].deleted = True
    elif damage == "rebound_version":
        env["dataset_version"].dataset_id = uuid.uuid4()
    elif damage == "deleted_agent":
        env["agent"].deleted = True
    await db.commit()

    await PromptOptimizationRunner(db)._execute(run.id)

    await db.refresh(run)
    if damage == "deleted_run":
        assert run.status == "created"  # never started, nothing written
    else:
        assert run.status == "failed"
        assert run.error == "run inputs are no longer valid"
    assert no_model_calls == []
    candidates = (
        (
            await db.execute(
                select(PromptOptimizationCandidateModel).where(PromptOptimizationCandidateModel.run_id == run.id)
            )
        )
        .scalars()
        .all()
    )
    assert candidates == []


async def test_background_rejects_foreign_version_workspace(env, fresh_background_session, no_model_calls) -> None:
    """A version row moved to another workspace is not a usable source."""
    run = await _background_run_row(env)
    env["dataset_version"].workspace_id = env["other_ws"].id
    await env["db"].commit()

    await PromptOptimizationRunner(env["db"])._execute(run.id)

    await env["db"].refresh(run)
    assert run.status == "failed"
    assert no_model_calls == []


@pytest.mark.parametrize("operation", ["get", "approve", "reject", "get_run", "list_candidates", "cancel_run"])
async def test_role_denial_never_loads_candidate_evidence(env, operation) -> None:
    """The visibility query may load bindings, but never protected payloads."""
    statements = []

    def observe(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement.lower())

    engine = env["db"].bind.sync_engine
    event.listen(engine, "before_cursor_execute", observe)
    role = None if operation in {"get", "get_run", "list_candidates"} else WorkspaceRole.VIEWER
    try:
        async with _client_as(_member_as(env["ws"], role)) as http:
            url = f"{_BASE}/candidates/{env['candidate'].id}"
            if operation == "get_run":
                response = await http.get(f"{_BASE}/runs/{env['run'].id}")
            elif operation == "list_candidates":
                response = await http.get(f"{_BASE}/runs/{env['run'].id}/candidates")
            elif operation == "cancel_run":
                response = await http.post(f"{_BASE}/runs/{env['run'].id}/cancel")
            elif operation == "get":
                response = await http.get(url)
            else:
                response = await http.post(
                    url + f"/{operation}", json={"reason": "no"} if operation == "reject" else None
                )
        assert response.status_code == 403
    finally:
        event.remove(engine, "before_cursor_execute", observe)
    assert not any("prompt_optimization_candidates.template" in sql for sql in statements)
    assert not any("prompt_optimization_candidates.per_item_results" in sql for sql in statements)
    assert not any("prompt_optimization_runs.evaluator_configs" in sql for sql in statements)


@pytest.mark.parametrize("binding", ["candidate_run", "run_prompt"])
async def test_approval_does_not_follow_same_workspace_rebinding(env, monkeypatch, binding) -> None:
    """Fresh reads must reject a different local target than the API admitted."""
    second_prompt = seed_prompt(env["db"], env["ws"].id, "Second {{q}}", ["q"])
    await env["db"].commit()
    second_run = await _seed_run(env["db"], second_prompt, env["agent"], env["dataset_version"], env["ws"].id)

    async def rebind(session):
        if binding == "candidate_run":
            row = await session.get(PromptOptimizationCandidateModel, env["candidate"].id)
            row.run_id = second_run.id
        else:
            row = await session.get(PromptOptimizationRunModel, env["run"].id)
            row.prompt_id = second_prompt.id

    monitor = {}
    await _interpose(monitor, rebind)
    monkeypatch.setattr(CandidateReviewService, "get_candidate_binding", monitor["patch"])
    before = await _snapshot(env["db"])
    async with _client_as(_member_as(env["ws"], WorkspaceRole.EDITOR)) as http:
        response = await http.post(f"{_BASE}/candidates/{env['candidate'].id}/approve")
    assert response.status_code == 404, response.text
    after = await _snapshot(env["db"])
    assert after["prompt_versions"] == before["prompt_versions"]
    assert all(row["current_version"] == 1 for row in after["prompts"])
    assert all(row["decided_by"] is None for row in after["prompt_optimization_candidates"])


async def test_publication_refreshes_current_prompt_version(env) -> None:
    """Cached version numbers must not collide with a committed new version."""
    async with shared_session_factory() as session:
        prompt = await session.get(PromptModel, env["prompt"].id)
        prompt.current_version = 2
        session.add(
            PromptVersionModel(
                prompt_id=prompt.id,
                version=2,
                template="Another {{q}}",
                variables=["q"],
                labels=[],
                workspace_id=env["ws"].id,
            )
        )
        await session.commit()
    async with _client_as(_member_as(env["ws"], WorkspaceRole.EDITOR)) as http:
        response = await http.post(f"{_BASE}/candidates/{env['candidate'].id}/approve")
    assert response.status_code == 200, response.text
    assert response.json()["published_prompt_version"] == 3


@pytest.mark.parametrize("source", ["template", "dataset_version", "agent"])
async def test_source_rebinding_after_startup_check_cannot_reach_rollout(
    env, fresh_background_session, no_model_calls, monkeypatch, source
) -> None:
    """Consumption cannot use a second, unscoped read after source validation."""
    from hecate.models.agent import AgentModel
    from tests.test_services.test_prompt_optimization.test_runner import StubEvaluator

    monkeypatch.setattr("hecate.ops.prompt_optimization.runner.get_evaluator_class", lambda name: StubEvaluator)
    original = PromptOptimizationRunner._verify_sources
    armed = False

    async def rebind_after_verification(self, run, session):
        nonlocal armed
        await original(self, run, session)
        if armed:
            return
        armed = True
        async with shared_session_factory() as other:
            if source == "template":
                row = (
                    await other.execute(
                        select(PromptVersionModel).where(
                            PromptVersionModel.prompt_id == run.prompt_id,
                            PromptVersionModel.version == run.base_version,
                        )
                    )
                ).scalar_one()
            elif source == "dataset_version":
                row = await other.get(EvaluationDatasetVersionModel, run.dataset_version_id)
            else:
                row = await other.get(AgentModel, run.agent_id)
            row.workspace_id = env["other_ws"].id
            await other.commit()

    monkeypatch.setattr(PromptOptimizationRunner, "_verify_sources", rebind_after_verification)
    run = await _background_run_row(env)
    await PromptOptimizationRunner()._execute(run.id)
    assert no_model_calls == []
    await env["db"].refresh(run)
    assert run.status == "failed" and run.error == "run inputs are no longer valid"


@pytest.mark.parametrize("disabled", [False, True])
async def test_background_keeps_admitted_workspace_and_feature_gate(
    env, fresh_background_session, no_model_calls, monkeypatch, disabled
):
    """Dispatch cannot retarget a workspace or outlive the feature gate."""
    run = await _background_run_row(env)
    if disabled:
        monkeypatch.setattr(settings, "PROMPT_OPTIMIZATION_ENABLED", False)
    else:
        run.workspace_id = env["other_ws"].id
        await env["db"].commit()
    await PromptOptimizationRunner()._execute(run.id, workspace_id=env["ws"].id)
    await env["db"].refresh(run)
    assert run.status == "created"
    assert no_model_calls == []


async def test_create_refreshes_cached_dataset_version_binding(env) -> None:
    """A cached version must not authorize its previous dataset parent."""
    second_dataset, _ = seed_dataset_version(env["db"], env["ws"].id, dataset_items(4))
    await env["db"].commit()
    async with shared_session_factory() as session:
        version = await session.get(EvaluationDatasetVersionModel, env["dataset_version"].id)
        version.dataset_id = second_dataset.id
        await session.commit()
    before = await _snapshot(env["db"])
    async with _client_as(_member_as(env["ws"], WorkspaceRole.EDITOR)) as http:
        response = await http.post(f"{_BASE}/runs", json=_create_body(env))
    assert response.status_code == 400, response.text
    assert await _snapshot(env["db"]) == before
    assert env["dispatched"] == []


async def test_candidate_listing_does_not_return_cached_foreign_payload(env) -> None:
    """Listing returns current authorized evidence, never a cached foreign row."""
    candidate = await _seed_candidate(env["db"], env["run"])
    candidate.workspace_id = env["other_ws"].id
    candidate.template = "PRIVATE-CACHED-CONTENT"
    await env["db"].commit()
    async with shared_session_factory() as session:
        current = await session.get(PromptOptimizationCandidateModel, candidate.id)
        current.workspace_id = env["ws"].id
        current.template = "PUBLIC-CURRENT-CONTENT"
        await session.commit()
    async with _client_as(_member_as(env["ws"], WorkspaceRole.VIEWER)) as http:
        response = await http.get(f"{_BASE}/runs/{env['run'].id}/candidates")
    assert response.status_code == 200, response.text
    assert "PRIVATE-CACHED-CONTENT" not in response.text
    assert "PUBLIC-CURRENT-CONTENT" in response.text


@pytest.mark.parametrize("operation", ["approve", "reject"])
async def test_invisible_parent_wins_over_decided_candidate_status(env, monkeypatch, operation) -> None:
    """A stale precheck cannot reveal state after the parent becomes invisible."""
    env["candidate"].status = "published"
    await env["db"].commit()

    async def delete_parent(session):
        run = await session.get(PromptOptimizationRunModel, env["run"].id)
        run.deleted = True

    monitor = {}
    await _interpose(monitor, delete_parent)
    monkeypatch.setattr(CandidateReviewService, "get_candidate_binding", monitor["patch"])
    async with _client_as(_member_as(env["ws"], WorkspaceRole.EDITOR)) as http:
        response = await http.post(
            f"{_BASE}/candidates/{env['candidate'].id}/{operation}",
            json={"reason": "no"} if operation == "reject" else None,
        )
    assert response.status_code == 404, response.text


@pytest.mark.parametrize("operation", ["approve", "reject"])
@pytest.mark.parametrize("binding", ["candidate_run", "run_prompt"])
async def test_decision_claim_rechecks_bindings_after_service_reads(env, monkeypatch, operation, binding) -> None:
    """SQLite also rejects rebinding between service reads and the first write."""
    foreign_run = await _seed_run(env["db"], env["prompt"], env["agent"], env["dataset_version"], env["other_ws"].id)
    second_prompt = seed_prompt(env["db"], env["ws"].id, "Another {{q}}", ["q"])
    await env["db"].commit()
    original = CandidateReviewService._live_run
    armed = False

    async def rebind_after_read(self, run_id, workspace_id, **kwargs):
        nonlocal armed
        run = await original(self, run_id, workspace_id, **kwargs)
        if kwargs.get("for_update") and not armed:
            armed = True
            async with shared_session_factory() as other:
                if binding == "candidate_run":
                    candidate = await other.get(PromptOptimizationCandidateModel, env["candidate"].id)
                    candidate.run_id = foreign_run.id
                else:
                    changed = await other.get(PromptOptimizationRunModel, run_id)
                    changed.prompt_id = second_prompt.id
                await other.commit()
        return run

    monkeypatch.setattr(CandidateReviewService, "_live_run", rebind_after_read)
    before = await _snapshot(env["db"])
    async with _client_as(_member_as(env["ws"], WorkspaceRole.EDITOR)) as http:
        response = await http.post(
            f"{_BASE}/candidates/{env['candidate'].id}/{operation}",
            json={"reason": "no"} if operation == "reject" else None,
        )
    assert response.status_code == 404, response.text
    after = await _snapshot(env["db"])
    assert after["prompt_versions"] == before["prompt_versions"]
    assert all(candidate["decided_by"] is None for candidate in after["prompt_optimization_candidates"])
