"""Synthesis access boundary (step10).

Covers the shared boundary on the synthesis kickoff and job polling
endpoints:

- Target workspace resolution: non-system kickoffs are anchored to the
  caller's active workspace (a foreign request-body ``workspace_id`` is a
  constant 403 that never queries the named workspace); kickoffs without
  a resolvable target — including system scope without a live selection —
  are 403 ``NO_WORKSPACE`` and never produce zero-UUID-owned resources.
- Role floors: editor for kickoff, viewer for polling, both after
  ownership so denials never disclose existence.
- Seed gating: the seed dataset must exist, be live, and belong to the
  target workspace, checked before the role check; eligibility anchors
  to the parent dataset so historical item workspace values never
  disqualify a seed.
- Side effects: every rejection precedes any dataset, item, or job row,
  any background scheduling, and any model call, verified against
  full-column snapshots; successful async kickoffs land their job row in
  the validated target workspace.
"""

from __future__ import annotations

import contextlib
import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import replace
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.auth_context import AuthContext
from hecate.models.dataset_synthesis_job import DatasetSynthesisJobModel
from hecate.models.evaluation import EvaluationDatasetModel, EvaluationItemModel
from hecate.models.organization import OrganizationModel
from hecate.models.workspace import WorkspaceModel
from hecate.models.workspace_member import WorkspaceMemberModel, WorkspaceRole

_KICKOFF = "/api/evaluation/datasets/synthesize"


async def _workspace_with_role(db: AsyncSession, name: str, role: WorkspaceRole) -> tuple[WorkspaceModel, AuthContext]:
    """Create an isolated org, workspace, member, and authentication context."""
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


async def _seed_dataset(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    deleted: bool = False,
) -> EvaluationDatasetModel:
    ds = EvaluationDatasetModel(name=f"boundary-seed-{uuid.uuid4().hex[:8]}", workspace_id=workspace_id)
    ds.deleted = deleted
    db.add(ds)
    await db.commit()
    return ds


async def _seed_job(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    deleted: bool = False,
) -> DatasetSynthesisJobModel:
    job = DatasetSynthesisJobModel(
        strategy="generation",
        status="queued",
        config={"count": 50, "target_dataset_name": "boundary-job"},
        workspace_id=workspace_id,
    )
    job.deleted = deleted
    db.add(job)
    await db.commit()
    return job


async def _state_snapshot(db: AsyncSession) -> list[list[dict[str, Any]]]:
    """Read all dataset, item, and job columns, including deleted rows."""
    snapshots = []
    for model in (EvaluationDatasetModel, EvaluationItemModel, DatasetSynthesisJobModel):
        table = model.__table__
        rows = (await db.execute(select(table).order_by(table.c.id))).mappings().all()
        snapshots.append([dict(row) for row in rows])
    return snapshots


def _stub_llm_never_called(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """Patch the shared chat entry so any model call fails the test."""
    import hecate_llm.service

    calls = {"n": 0}

    async def _chat(*_a: object, **_kw: object) -> object:
        calls["n"] += 1
        raise AssertionError("model must not be called on a rejected kickoff")

    monkeypatch.setattr(hecate_llm.service.llm_service, "chat", _chat)
    return calls


@pytest.fixture(autouse=True)
def _reject_unexpected_background(monkeypatch: pytest.MonkeyPatch) -> None:
    """Require successful async tests to opt in to background scheduling explicitly."""
    from hecate.ops.evaluation.synthesis.job import DatasetSynthesisJobService

    async def unexpected_background(self: DatasetSynthesisJobService, job_id: uuid.UUID) -> None:
        raise AssertionError("Rejected requests and polling must never schedule a pipeline")

    monkeypatch.setattr(DatasetSynthesisJobService, "run_job_in_background", unexpected_background)


def _stub_background_noop(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """Keep successful async kickoffs from spawning the pipeline task."""
    from hecate.ops.evaluation.synthesis import job as job_mod

    calls = {"n": 0}

    async def _noop(self: object, job_id: object) -> None:
        calls["n"] += 1

    monkeypatch.setattr(job_mod.DatasetSynthesisJobService, "run_job_in_background", _noop)
    return calls


def _assert_no_workspace(resp: Any) -> None:
    assert resp.status_code == 403, resp.text
    assert resp.json() == {
        "detail": {"error": {"code": "NO_WORKSPACE", "message": "An active workspace is required", "details": None}}
    }


def _assert_forbidden_workspace(resp: Any) -> None:
    assert resp.status_code == 403, resp.text
    assert resp.json() == {
        "detail": {"error": {"code": "FORBIDDEN", "message": "Workspace access denied", "details": None}}
    }


def _assert_forbidden(resp: Any, role: WorkspaceRole | None, minimum: str = "Editor") -> None:
    assert resp.status_code == 403, resp.text
    message = f"{minimum} role required" if role is not None else "Workspace membership required"
    assert resp.json() == {"detail": {"error": {"code": "FORBIDDEN", "message": message, "details": None}}}


def _assert_dataset_not_found(resp: Any) -> None:
    assert resp.status_code == 404, resp.text
    assert resp.json() == {"detail": {"error": {"code": "NOT_FOUND", "message": "Dataset not found", "details": None}}}


def _assert_job_not_found(resp: Any) -> None:
    assert resp.status_code == 404, resp.text
    assert resp.json() == {
        "detail": {"error": {"code": "NOT_FOUND", "message": "Synthesis job not found", "details": None}}
    }


def _kickoff_payload(**extra: Any) -> dict[str, Any]:
    """An async-path kickoff (count>20) so no model call is needed to succeed."""
    payload: dict[str, Any] = {
        "strategy": "generation",
        "topic": "boundary topic",
        "count": 50,
        "target_dataset_name": "boundary-target",
    }
    payload.update(extra)
    return payload


@pytest.mark.parametrize("selection", ["omitted", "self", "foreign"])
async def test_kickoff_anchored_to_caller_workspace(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, selection: str
) -> None:
    """The server-side target is the caller's own workspace or a constant denial."""
    ws, editor = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.EDITOR)
    other, _ = await _workspace_with_role(db_session, "ws-other", WorkspaceRole.EDITOR)
    _stub_background_noop(monkeypatch)
    before = await _state_snapshot(db_session)
    ctx: AuthContext = editor
    if selection == "omitted":
        payload = _kickoff_payload()
    elif selection == "self":
        payload = _kickoff_payload(workspace_id=str(ws.id))
    else:
        payload = _kickoff_payload(workspace_id=str(other.id))

    async with _client_as(client, ctx) as http:
        resp = await http.post(_KICKOFF, json=payload)

    if selection == "foreign":
        _assert_forbidden_workspace(resp)
        assert str(other.id) not in resp.text
        assert await _state_snapshot(db_session) == before
        return
    assert resp.status_code == 202, resp.text
    assert set(resp.json()) == {"job_id", "status"} and resp.json()["status"] == "queued"
    jobs = await _state_snapshot(db_session)
    assert len(jobs[2]) == len(before[2]) + 1
    assert jobs[2][-1]["workspace_id"] == ws.id
    assert jobs[2][-1]["status"] == "queued"


@pytest.mark.parametrize(
    ("identity", "selection"),
    [
        ("no_workspace", "absent"),
        ("system", "absent"),
        ("system", "missing"),
        ("system", "deleted"),
    ],
)
async def test_kickoff_requires_resolvable_target_workspace(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    identity: str,
    selection: str,
) -> None:
    """No resolvable target — for any identity — is NO_WORKSPACE with zero side effects."""
    calls = _stub_llm_never_called(monkeypatch)
    selected: str | None = None
    if selection == "missing":
        selected = str(uuid.uuid4())
    elif selection == "deleted":
        gone, _ = await _workspace_with_role(db_session, "ws-gone", WorkspaceRole.EDITOR)
        wsrow = await db_session.get(WorkspaceModel, gone.id)
        assert wsrow is not None
        wsrow.deleted = True
        await db_session.commit()
        selected = str(gone.id)
    payload = _kickoff_payload() if selected is None else _kickoff_payload(workspace_id=selected)
    ctx = _system_context() if identity == "system" else _no_workspace_context()
    before = await _state_snapshot(db_session)

    async with _client_as(client, ctx) as http:
        resp = await http.post(_KICKOFF, json=payload)

    _assert_no_workspace(resp)
    assert await _state_snapshot(db_session) == before
    assert calls["n"] == 0


async def test_system_kickoff_lands_in_selected_workspace(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """System's explicit selection decides the persisted ownership."""
    ws1, _ = await _workspace_with_role(db_session, "ws-one", WorkspaceRole.EDITOR)
    ws2, _ = await _workspace_with_role(db_session, "ws-two", WorkspaceRole.EDITOR)
    _stub_background_noop(monkeypatch)

    async with _client_as(client, _system_context()) as http:
        resp = await http.post(_KICKOFF, json=_kickoff_payload(workspace_id=str(ws2.id)))

    assert resp.status_code == 202, resp.text
    jobs = (await _state_snapshot(db_session))[2]
    assert len(jobs) == 1
    assert jobs[0]["workspace_id"] == ws2.id and jobs[0]["workspace_id"] != ws1.id


@pytest.mark.parametrize("role", [WorkspaceRole.VIEWER, None])
async def test_editor_role_floor_applies_to_kickoff(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, role: WorkspaceRole | None
) -> None:
    """Visible target workspace still requires the editor floor, with zero side effects."""
    ws, _ = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.EDITOR)
    calls = _stub_llm_never_called(monkeypatch)
    before = await _state_snapshot(db_session)

    async with _client_as(client, _member_as(ws, role)) as http:
        resp = await http.post(_KICKOFF, json=_kickoff_payload())

    _assert_forbidden(resp, role, minimum="Editor")
    assert await _state_snapshot(db_session) == before
    assert calls["n"] == 0


@pytest.mark.parametrize("role", [WorkspaceRole.EDITOR, WorkspaceRole.VIEWER, None])
@pytest.mark.parametrize("kind", ["missing", "soft_deleted", "foreign_live", "foreign_deleted"])
async def test_seed_visibility_precedes_role(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    role: WorkspaceRole | None,
) -> None:
    """Every ineligible seed is the constant dataset 404, regardless of the caller role."""
    ws, _ = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.EDITOR)
    other, _ = await _workspace_with_role(db_session, "ws-other", WorkspaceRole.EDITOR)
    deleted_own = await _seed_dataset(db_session, ws.id, deleted=True)
    foreign_live = await _seed_dataset(db_session, other.id)
    foreign_deleted = await _seed_dataset(db_session, other.id, deleted=True)
    seed_id = {
        "missing": uuid.uuid4(),
        "soft_deleted": deleted_own.id,
        "foreign_live": foreign_live.id,
        "foreign_deleted": foreign_deleted.id,
    }[kind]
    calls = _stub_llm_never_called(monkeypatch)
    before = await _state_snapshot(db_session)

    async with _client_as(client, _member_as(ws, role)) as http:
        resp = await http.post(_KICKOFF, json=_kickoff_payload(seed_dataset_id=str(seed_id)))

    _assert_dataset_not_found(resp)
    assert str(seed_id) not in resp.text
    assert await _state_snapshot(db_session) == before
    assert calls["n"] == 0


async def test_visible_seed_with_insufficient_role_is_forbidden(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A visible seed passes the gate; the role check then denies with zero side effects."""
    ws, _ = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.EDITOR)
    seed = await _seed_dataset(db_session, ws.id)
    calls = _stub_llm_never_called(monkeypatch)
    before = await _state_snapshot(db_session)

    async with _client_as(client, _member_as(ws, WorkspaceRole.VIEWER)) as http:
        resp = await http.post(_KICKOFF, json=_kickoff_payload(seed_dataset_id=str(seed.id)))

    _assert_forbidden(resp, WorkspaceRole.VIEWER, minimum="Editor")
    assert await _state_snapshot(db_session) == before
    assert calls["n"] == 0


async def test_sync_kickoff_with_legacy_workspace_seed_items_succeeds(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A live same-workspace seed is usable: items are read regardless of their own workspace."""
    import hecate_llm.service

    from hecate.ops.dlp import service as dlp_mod

    ws, editor = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.EDITOR)
    seed = await _seed_dataset(db_session, ws.id)
    for item_ws in (uuid.UUID(int=0), ws.id, uuid.uuid4()):
        db_session.add(
            EvaluationItemModel(
                dataset_id=seed.id, workspace_id=item_ws, query="legacy seed query", expected_answer="answer"
            )
        )
    await db_session.commit()

    counter = {"n": 0}
    strategy_payload = json.dumps({"query": "derived", "expected_answer": "derived answer"})
    quality_payload = json.dumps({"self_containment": 1.0, "clarity": 1.0})

    async def _chat(*_a: object, **_kw: object) -> object:
        counter["n"] += 1

        class _R:
            content = strategy_payload if counter["n"] <= 3 else quality_payload

        return _R

    monkeypatch.setattr(hecate_llm.service.llm_service, "chat", _chat)

    class _StubDLP:
        def __init__(self, *_a: object, **_kw: object) -> None:
            pass

        async def dry_run_scan(self, *_a: object, **_kw: object) -> object:
            class _R:
                findings: list = []

            return _R()

    monkeypatch.setattr(dlp_mod, "DLPService", _StubDLP)

    async with _client_as(client, editor) as http:
        resp = await http.post(
            _KICKOFF,
            json={
                "strategy": "generation",
                "seed_dataset_id": str(seed.id),
                "count": 3,
                "target_dataset_name": "sync-seeded",
            },
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == {"target_dataset_id", "items_generated", "items_filtered", "duration_ms"}
    assert body["items_generated"] == 3
    table = EvaluationDatasetModel.__table__
    target_id = uuid.UUID(body["target_dataset_id"])
    row = (await db_session.execute(select(table).where(table.c.id == target_id))).mappings().one()
    assert dict(row)["workspace_id"] == ws.id
    items = (
        (
            await db_session.execute(
                select(EvaluationItemModel).where(
                    EvaluationItemModel.dataset_id == uuid.UUID(body["target_dataset_id"])
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(items) == 3
    for item in items:
        assert item.workspace_id == ws.id
        assert "synthetic" in (item.tags or [])


async def test_sync_seed_race_maps_to_constant_404(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A seed deleted between acceptance and pipeline run surfaces as the same 404."""
    from hecate.ops.evaluation.synthesis.service import DatasetSynthesisService, SeedDatasetUnavailableError

    ws, editor = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.EDITOR)
    seed = await _seed_dataset(db_session, ws.id)
    real_validate = DatasetSynthesisService.validate_seed
    pipeline_calls = {"n": 0}

    async def _flaky(self: Any, seed_dataset_id: uuid.UUID, workspace_id: uuid.UUID) -> Any:
        result = await real_validate(self, seed_dataset_id, workspace_id)
        pipeline_calls["n"] += 1
        if pipeline_calls["n"] == 2:
            raise SeedDatasetUnavailableError("raced delete")
        return result

    monkeypatch.setattr(DatasetSynthesisService, "validate_seed", _flaky)
    before = await _state_snapshot(db_session)

    async with _client_as(client, editor) as http:
        resp = await http.post(
            _KICKOFF,
            json={
                "strategy": "generation",
                "seed_dataset_id": str(seed.id),
                "count": 3,
                "target_dataset_name": "raced-target",
            },
        )

    _assert_dataset_not_found(resp)
    assert pipeline_calls["n"] == 2
    assert await _state_snapshot(db_session) == before


@pytest.mark.parametrize("role", [WorkspaceRole.ADMIN, None])
@pytest.mark.parametrize("kind", ["missing", "soft_deleted", "cross_workspace"])
async def test_job_polling_follows_visibility(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    role: WorkspaceRole | None,
) -> None:
    """Missing, soft-deleted, and cross-workspace jobs share one identifier-free 404."""
    ws, _ = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.EDITOR)
    other, _ = await _workspace_with_role(db_session, "ws-other", WorkspaceRole.EDITOR)
    own_deleted = await _seed_job(db_session, ws.id, deleted=True)
    foreign = await _seed_job(db_session, other.id)
    job_id = {
        "missing": uuid.uuid4(),
        "soft_deleted": own_deleted.id,
        "cross_workspace": foreign.id,
    }[kind]
    calls = _stub_llm_never_called(monkeypatch)

    before = await _state_snapshot(db_session)
    async with _client_as(client, _member_as(ws, role)) as http:
        resp = await http.get(f"/api/evaluation/synthesis-jobs/{job_id}")
    assert await _state_snapshot(db_session) == before

    _assert_job_not_found(resp)
    assert str(job_id) not in resp.text
    assert calls["n"] == 0


async def test_job_polling_without_workspace_is_denied_before_query(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A workspace-less non-system identity never reaches the job query."""
    ws, _ = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.EDITOR)
    job = await _seed_job(db_session, ws.id)
    calls = _stub_llm_never_called(monkeypatch)

    async def unexpected_query(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Missing workspace must be denied before the job query")

    before = await _state_snapshot(db_session)
    with monkeypatch.context() as patch:
        patch.setattr(AsyncSession, "execute", unexpected_query)
        async with _client_as(client, _no_workspace_context()) as http:
            resp = await http.get(f"/api/evaluation/synthesis-jobs/{job.id}")
    assert await _state_snapshot(db_session) == before
    _assert_job_not_found(resp)
    assert str(job.id) not in resp.text
    assert calls["n"] == 0


@pytest.mark.parametrize(("role", "ok"), [(WorkspaceRole.VIEWER, True), (None, False)])
async def test_job_polling_role_floor(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    role: WorkspaceRole | None,
    ok: bool,
) -> None:
    """A visible job requires the viewer floor; viewer passes, role-less is denied."""
    ws, _ = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.EDITOR)
    job = await _seed_job(db_session, ws.id)

    async with _client_as(client, _member_as(ws, role)) as http:
        resp = await http.get(f"/api/evaluation/synthesis-jobs/{job.id}")

    if ok:
        assert resp.status_code == 200, resp.text
        assert resp.json()["id"] == str(job.id) and resp.json()["status"] == "queued"
    else:
        _assert_forbidden(resp, role, minimum="Viewer")


@pytest.mark.parametrize("active_workspace", [False, True])
async def test_system_polls_job_across_workspaces(
    client: AsyncClient, db_session: AsyncSession, active_workspace: bool
) -> None:
    """System scope polls any job by ID and keeps the full response shape."""
    ws1, _ = await _workspace_with_role(db_session, "ws-one", WorkspaceRole.EDITOR)
    job = await _seed_job(db_session, ws1.id)

    other, _ = await _workspace_with_role(db_session, "ws-system-active", WorkspaceRole.EDITOR)
    ctx = replace(_system_context(), workspace_id=other.id) if active_workspace else _system_context()
    before = await _state_snapshot(db_session)
    async with _client_as(client, ctx) as http:
        resp = await http.get(f"/api/evaluation/synthesis-jobs/{job.id}")
    assert await _state_snapshot(db_session) == before

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == {
        "id",
        "target_dataset_id",
        "strategy",
        "status",
        "config",
        "metrics",
        "error_message",
        "started_at",
        "completed_at",
        "workspace_id",
        "created_at",
        "updated_at",
    }
    assert body["workspace_id"] == str(ws1.id)
    assert body["status"] == "queued"


async def test_malformed_count_keeps_422(client: AsyncClient, db_session: AsyncSession) -> None:
    """Schema validation runs before the boundary and is unchanged."""
    ws, editor = await _workspace_with_role(db_session, "ws-owner", WorkspaceRole.EDITOR)
    async with _client_as(client, editor) as http:
        resp = await http.post(
            _KICKOFF, json={"strategy": "generation", "topic": "t", "count": 0, "target_dataset_name": "bad"}
        )
    assert resp.status_code == 422


@pytest.mark.parametrize("system", [False, True])
async def test_async_job_completes_with_validated_ownership(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, system: bool
) -> None:
    """Kickoff, worker execution, and polling preserve one validated tenant end to end."""
    import hecate_llm.service

    import hecate.core.database as database_mod
    from hecate.ops.dlp import service as dlp_mod
    from hecate.ops.evaluation.synthesis.job import DatasetSynthesisJobService

    ws, editor = await _workspace_with_role(db_session, "ws-worker", WorkspaceRole.EDITOR)
    seed = await _seed_dataset(db_session, ws.id)
    seed_item = EvaluationItemModel(dataset_id=seed.id, query="seed input", expected_answer="seed answer")
    db_session.add(seed_item)
    await db_session.commit()
    count = 21
    calls = {"model": 0}
    strategy_payload = json.dumps({"query": "derived query", "expected_answer": "derived answer"})
    quality_payload = json.dumps({"self_containment": 1.0, "clarity": 1.0})

    async def chat(*args: object, **kwargs: object) -> object:
        calls["model"] += 1
        payload = strategy_payload if calls["model"] <= count else quality_payload

        class _Response:
            content = payload

        return _Response()

    class StubDLP:
        """Avoid external DLP dependencies while retaining the pipeline call boundary."""

        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def dry_run_scan(self, *args: object, **kwargs: object) -> object:
            """Return a clean deterministic scan result."""

            class _Result:
                findings: list = []

            return _Result()

    monkeypatch.setattr(hecate_llm.service.llm_service, "chat", chat)
    monkeypatch.setattr(dlp_mod, "DLPService", StubDLP)
    scheduled = _stub_background_noop(monkeypatch)
    ctx = _system_context() if system else editor
    payload = _kickoff_payload(count=count, seed_dataset_id=str(seed.id))
    if system:
        payload["workspace_id"] = str(ws.id)
    async with _client_as(client, ctx) as http:
        accepted = await http.post(_KICKOFF, json=payload)
        assert accepted.status_code == 202, accepted.text
        assert accepted.json()["status"] == "queued" and scheduled["n"] == 1
        assert calls["model"] == 0
        job_id = uuid.UUID(accepted.json()["job_id"])
        monkeypatch.setattr(database_mod, "async_session_factory", lambda: db_session)
        await DatasetSynthesisJobService(db_session)._execute_job(job_id)
        polled = await http.get(f"/api/evaluation/synthesis-jobs/{job_id}")
    assert polled.status_code == 200, polled.text
    body = polled.json()
    assert body["status"] == "completed", body
    assert body["workspace_id"] == str(ws.id) and body["error_message"] is None
    assert body["metrics"]["items_generated"] == count and body["metrics"]["items_filtered"] == 0
    assert body["started_at"] is not None and body["completed_at"] is not None
    target_id = uuid.UUID(body["target_dataset_id"])
    ds_table = EvaluationDatasetModel.__table__
    target = (await db_session.execute(select(ds_table).where(ds_table.c.id == target_id))).mappings().one()
    assert target["workspace_id"] == ws.id
    item_table = EvaluationItemModel.__table__
    rows = (await db_session.execute(select(item_table).where(item_table.c.dataset_id == target_id))).mappings().all()
    assert len(rows) == count
    assert all(row["workspace_id"] == ws.id and "synthetic" in row["tags"] for row in rows)
    assert calls["model"] == count * 2
