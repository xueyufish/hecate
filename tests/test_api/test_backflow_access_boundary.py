"""Automated backflow rule access boundary and source isolation (step10).

Covers the six backflow-rule REST endpoints:

- Create and list require an active workspace for every identity — system
  scope included (403 ``NO_WORKSPACE``) — and non-system callers need an
  editor floor to create or a viewer floor to list; system scope lists
  only its explicit workspace and has no global listing.
- By-id reads, updates, deletes, and runs resolve the rule first
  (existence, soft-delete state, workspace ownership before the role
  check); a workspace-less member is denied before any query, system
  scope keeps its role exemption and operates in the rule's real
  workspace.
- References resolve like resources: create validates the requested
  task/dataset, update the merged-patch effective pair, run the rule's
  currently bound pair — each live in the target workspace before the
  role check and business validation, system scope included. Invisible
  rules or references on create/update/run share the combined
  identifier-free 404; detail and delete keep the rule-only message, and
  deletion never depends on reference liveness.
- The service rechecks rule and references after the API precheck;
  rejected requests change no rule, last_run, dataset, sample, or score
  and read no candidate scores or events (spies + table-direct
  full-column snapshots).
- Source isolation: candidate and provenance qualification is same
  workspace, same task, trace-typed, completed, undeleted, non-human,
  non-sentinel; the trace itself must be a live completed root trace
  visible under the session-or-agent workspace rule — ineligible traces
  are skipped before any event read, and materialized samples carry the
  rule's real workspace explicitly.
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
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.auth_context import AuthContext
from hecate.models.evaluation import (
    EvaluationBackflowRuleModel,
    EvaluationDatasetModel,
    EvaluationItemModel,
    EvaluationTaskModel,
    EvaluationTaskScoreModel,
)
from hecate.models.organization import OrganizationModel
from hecate.models.session import SessionModel
from hecate.models.trace import TraceModel
from hecate.models.workspace import WorkspaceModel
from hecate.models.workspace_member import WorkspaceMemberModel, WorkspaceRole
from hecate.ops.evaluation.annotation import AnnotationService
from hecate.ops.evaluation.backflow import BackflowService
from hecate.runtime.eventstore import Event, EventType, InMemoryEventStore

_BASE = "/api/evaluation/backflow-rules"

_NOW = datetime.now(UTC).replace(microsecond=0)

_COMBINED_404 = "Backflow rule or task or dataset not found"
_RULE_404 = "Backflow rule not found"


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


async def _seed_task(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    task_type: str = "online",
    deleted: bool = False,
) -> EvaluationTaskModel:
    task = EvaluationTaskModel(
        task_type=task_type,
        name=f"boundary-task-{uuid.uuid4().hex[:8]}",
        evaluator_configs=["stub"],
        config={"agent_id": str(uuid.uuid4()), "sampling_rate": 1.0},
        workspace_id=workspace_id,
        deleted=deleted,
        deleted_at=datetime.now(UTC) if deleted else None,
    )
    db.add(task)
    await db.commit()
    return task


async def _seed_dataset(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    deleted: bool = False,
) -> EvaluationDatasetModel:
    dataset = EvaluationDatasetModel(
        name=f"boundary-corpus-{uuid.uuid4().hex[:8]}",
        workspace_id=workspace_id,
        deleted=deleted,
        deleted_at=datetime.now(UTC) if deleted else None,
    )
    db.add(dataset)
    await db.commit()
    return dataset


async def _seed_rule(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    task: EvaluationTaskModel,
    dataset: EvaluationDatasetModel,
    *,
    name: str | None = None,
    deleted: bool = False,
) -> EvaluationBackflowRuleModel:
    rule = EvaluationBackflowRuleModel(
        name=name or f"boundary-rule-{uuid.uuid4().hex[:8]}",
        task_id=task.id,
        dataset_id=dataset.id,
        filters=[{"metric_name": "correctness", "max_score": 0.5}],
        limit=500,
        workspace_id=workspace_id,
        deleted=deleted,
        deleted_at=datetime.now(UTC) if deleted else None,
    )
    db.add(rule)
    await db.commit()
    return rule


async def _seed_trace(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    status: str = "completed",
    trace_type: str = "trace",
) -> TraceModel:
    agent_id = uuid.uuid4()
    session_row = SessionModel(conversation_id=uuid.uuid4(), agent_id=agent_id, workspace_id=workspace_id)
    db.add(session_row)
    await db.flush()
    trace = TraceModel(
        trace_id=uuid.uuid4(),
        type=trace_type,
        name=f"session:{session_row.id}",
        session_id=session_row.id,
        agent_id=agent_id,
        status=status,
        start_time=_NOW,
        end_time=_NOW + timedelta(seconds=10),
        created_at=_NOW,
    )
    db.add(trace)
    await db.commit()
    return trace


async def _seed_score(
    db: AsyncSession,
    task: EvaluationTaskModel,
    target_id: uuid.UUID,
    *,
    value: float = 0.3,
    workspace_id: uuid.UUID | None = None,
    source: str = "llm_judge",
    status: str = "completed",
    target_type: str = "trace",
    metric_name: str = "correctness",
    created_at: datetime | None = None,
    deleted: bool = False,
) -> EvaluationTaskScoreModel:
    row = EvaluationTaskScoreModel(
        task_id=task.id,
        target_id=target_id,
        metric_name=metric_name,
        value=value,
        source=source,
        status=status,
        target_type=target_type,
        workspace_id=workspace_id if workspace_id is not None else task.workspace_id,
        created_at=created_at or _NOW,
        deleted=deleted,
    )
    db.add(row)
    await db.commit()
    return row


async def _install_event_store(monkeypatch: pytest.MonkeyPatch, traces: list[TraceModel]) -> InMemoryEventStore:
    """Back the service's default event store with one exchange per trace."""
    store = InMemoryEventStore()
    for trace in traces:
        await store.append(
            Event(
                session_id=trace.session_id,
                superstep=1,
                event_type=EventType.LLM_REQUEST,
                timestamp=trace.start_time + timedelta(seconds=1),
                payload={"messages": [{"role": "user", "content": "what is X?"}]},
            )
        )
        await store.append(
            Event(
                session_id=trace.session_id,
                superstep=2,
                event_type=EventType.LLM_REQUEST,
                timestamp=trace.start_time + timedelta(seconds=2),
                payload={"messages": [{"role": "assistant", "content": "X is a thing"}]},
            )
        )

    async def _stub_event_store(self: BackflowService) -> Any:
        return store

    monkeypatch.setattr(BackflowService, "_get_event_store", _stub_event_store)
    return store


def _rule_payload(task_id: uuid.UUID, dataset_id: uuid.UUID, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": f"boundary-rule-{uuid.uuid4().hex[:8]}",
        "task_id": str(task_id),
        "dataset_id": str(dataset_id),
        "filters": [{"metric_name": "correctness", "max_score": 0.5}],
    }
    payload.update(overrides)
    return payload


async def _database_snapshot(db: AsyncSession) -> list[list[dict[str, Any]]]:
    """Read every relevant column from storage, bypassing the identity map."""
    snapshots = []
    for model in (
        EvaluationBackflowRuleModel,
        EvaluationTaskModel,
        EvaluationDatasetModel,
        EvaluationItemModel,
        EvaluationTaskScoreModel,
    ):
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


def _assert_forbidden_viewer(resp: Any) -> None:
    assert resp.status_code == 403, resp.text
    assert resp.json() == {
        "detail": {"error": {"code": "FORBIDDEN", "message": "Editor role required", "details": None}}
    }


def _assert_rule_not_found(resp: Any) -> None:
    assert resp.status_code == 404, resp.text
    assert resp.json() == {"detail": {"error": {"code": "NOT_FOUND", "message": _RULE_404, "details": None}}}


def _assert_combined_not_found(resp: Any) -> None:
    assert resp.status_code == 404, resp.text
    assert resp.json() == {"detail": {"error": {"code": "NOT_FOUND", "message": _COMBINED_404, "details": None}}}


async def _unexpected_query(*args: Any, **kwargs: Any) -> None:
    raise AssertionError("Denied requests must not query backflow data")


async def _unexpected_write(*args: Any, **kwargs: Any) -> None:
    raise AssertionError("Denied requests must not run backflow writes")


async def _unexpected_events(*args: Any, **kwargs: Any) -> None:
    raise AssertionError("Rejected requests must not read trace events")


# ---------------------------------------------------------------------------
# Create + list: active workspace, role floors, reference visibility
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("identity", ["member", "system"])
async def test_create_and_list_without_workspace_denied_before_query(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    identity: str,
) -> None:
    """Every identity — system included — needs an active workspace here."""
    from hecate.ops.evaluation.backflow.service import BackflowService

    ctx = _system_context() if identity == "system" else _no_workspace_context()
    before = await _database_snapshot(db_session)
    with monkeypatch.context() as patch:
        patch.setattr(BackflowService, "create_rule", _unexpected_write)
        patch.setattr(BackflowService, "list_rules", _unexpected_write)
        patch.setattr(AsyncSession, "execute", _unexpected_query)
        async with _client_as(client, ctx) as http:
            denied_create = await http.post(_BASE, json=_rule_payload(uuid.uuid4(), uuid.uuid4()))
            denied_list = await http.get(_BASE)
    _assert_no_workspace(denied_create)
    _assert_no_workspace(denied_list)
    assert await _database_snapshot(db_session) == before


@pytest.mark.parametrize("role", [WorkspaceRole.VIEWER, None])
async def test_create_below_editor_denied_before_business_validation(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    role: WorkspaceRole | None,
) -> None:
    """Viewers and role-less members are denied before service or validation."""
    from hecate.ops.evaluation.backflow.service import BackflowService

    ws, _ = await _workspace_with_role(db_session, "ws-low", WorkspaceRole.VIEWER)
    task = await _seed_task(db_session, ws.id)
    dataset = await _seed_dataset(db_session, ws.id)
    before = await _database_snapshot(db_session)
    with monkeypatch.context() as patch:
        patch.setattr(BackflowService, "create_rule", _unexpected_write)
        async with _client_as(client, _member_as(ws, role)) as http:
            denied = await http.post(_BASE, json=_rule_payload(task.id, dataset.id))
            # Authorization precedes business validation: a schema-valid
            # payload with an inverted band still gets the role denial,
            # while schema-shape violations keep their 422.
            business_invalid = await http.post(
                _BASE,
                json=_rule_payload(
                    task.id, dataset.id, filters=[{"metric_name": "correctness", "min_score": 0.8, "max_score": 0.3}]
                ),
            )
            schema_invalid = await http.post(_BASE, json=_rule_payload(task.id, dataset.id, filters=[]))
    if role is None:
        _assert_forbidden(denied)
        _assert_forbidden(business_invalid)
    else:
        _assert_forbidden_viewer(denied)
        _assert_forbidden_viewer(business_invalid)
    assert schema_invalid.status_code == 422, schema_invalid.text
    assert await _database_snapshot(db_session) == before


@pytest.mark.parametrize("identity", ["viewer", "roleless", "editor", "admin", "system"])
@pytest.mark.parametrize("reference", ["missing_task", "deleted_task", "missing_dataset", "deleted_dataset"])
async def test_create_invisible_reference_combined_404_before_role(
    client: AsyncClient,
    db_session: AsyncSession,
    identity: str,
    reference: str,
) -> None:
    """Invisible task/dataset references share one 404 before the role check."""
    ws, viewer = await _workspace_with_role(db_session, "ws-refs", WorkspaceRole.VIEWER)
    other, _ = await _workspace_with_role(db_session, "ws-refs-other", WorkspaceRole.VIEWER)
    live_task = await _seed_task(db_session, ws.id)
    live_dataset = await _seed_dataset(db_session, ws.id)
    deleted_task = await _seed_task(db_session, ws.id, deleted=True)
    deleted_dataset = await _seed_dataset(db_session, ws.id, deleted=True)
    foreign_task = await _seed_task(db_session, other.id)
    foreign_dataset = await _seed_dataset(db_session, other.id)
    task_id, dataset_id = {
        "missing_task": (uuid.uuid4(), live_dataset.id),
        "deleted_task": (deleted_task.id, live_dataset.id),
        "missing_dataset": (live_task.id, uuid.uuid4()),
        "deleted_dataset": (live_task.id, deleted_dataset.id),
    }[reference]
    ctx = {
        "viewer": viewer,
        "roleless": _member_as(ws, None),
        "editor": _member_as(ws, WorkspaceRole.EDITOR),
        "admin": _member_as(ws, WorkspaceRole.ADMIN),
        # Create requires an active workspace for system too, and its
        # references resolve inside that workspace — foreign ones stay
        # invisible, so system cannot stitch cross-workspace rules.
        "system": replace(_system_context(), workspace_id=ws.id),
    }[identity]
    before = await _database_snapshot(db_session)
    async with _client_as(client, ctx) as http:
        response = await http.post(_BASE, json=_rule_payload(task_id, dataset_id))
        _assert_combined_not_found(response)
        # An invisible reference outranks business validation too.
        with_inverted_band = await http.post(
            _BASE,
            json=_rule_payload(
                task_id, dataset_id, filters=[{"metric_name": "correctness", "min_score": 0.8, "max_score": 0.3}]
            ),
        )
        _assert_combined_not_found(with_inverted_band)
        foreign = await http.post(_BASE, json=_rule_payload(foreign_task.id, foreign_dataset.id))
        _assert_combined_not_found(foreign)
    assert await _database_snapshot(db_session) == before


@pytest.mark.parametrize("role", [WorkspaceRole.EDITOR, WorkspaceRole.ADMIN])
async def test_editor_creates_and_lists_own_workspace(
    client: AsyncClient,
    db_session: AsyncSession,
    role: WorkspaceRole,
) -> None:
    """Editor and above create rules; the workspace list shows them once."""
    ws, _ = await _workspace_with_role(db_session, "ws-create", WorkspaceRole.VIEWER)
    other, _ = await _workspace_with_role(db_session, "ws-create-other", WorkspaceRole.VIEWER)
    task = await _seed_task(db_session, ws.id)
    dataset = await _seed_dataset(db_session, ws.id)
    other_task = await _seed_task(db_session, other.id)
    other_dataset = await _seed_dataset(db_session, other.id)
    await _seed_rule(db_session, other.id, other_task, other_dataset)
    async with _client_as(client, _member_as(ws, role)) as http:
        created = await http.post(_BASE, json=_rule_payload(task.id, dataset.id))
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["workspace_id"] == str(ws.id)
        listing = await http.get(_BASE)
        assert listing.status_code == 200, listing.text
        assert listing.json()["total"] == 1
        assert [item["id"] for item in listing.json()["items"]] == [body["id"]]


async def test_system_list_scopes_to_active_workspace_only(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """System scope has no global listing; it lists only its own workspace."""
    ws, _ = await _workspace_with_role(db_session, "ws-sys-list", WorkspaceRole.VIEWER)
    other, _ = await _workspace_with_role(db_session, "ws-sys-list-other", WorkspaceRole.VIEWER)
    task, dataset = await _seed_task(db_session, ws.id), await _seed_dataset(db_session, ws.id)
    rule = await _seed_rule(db_session, ws.id, task, dataset)
    async with _client_as(client, _system_context()) as http:
        denied = await http.get(_BASE)
        _assert_no_workspace(denied)
    async with _client_as(client, replace(_system_context(), workspace_id=other.id)) as http:
        empty = await http.get(_BASE)
        assert empty.status_code == 200 and empty.json()["total"] == 0, empty.text
    async with _client_as(client, replace(_system_context(), workspace_id=ws.id)) as http:
        scoped = await http.get(_BASE)
        assert scoped.status_code == 200 and scoped.json()["total"] == 1, scoped.text
        assert scoped.json()["items"][0]["id"] == str(rule.id)


# ---------------------------------------------------------------------------
# By-id endpoints: rule visibility, role floors, message split
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("identity", ["viewer", "roleless", "editor", "admin", "system"])
async def test_invisible_rule_denied_before_role(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    identity: str,
) -> None:
    """Missing/deleted/foreign rules share one 404; the message splits by endpoint."""
    from hecate.ops.evaluation.backflow.service import BackflowService

    ws, viewer = await _workspace_with_role(db_session, "ws-rule", WorkspaceRole.VIEWER)
    other, _ = await _workspace_with_role(db_session, "ws-rule-other", WorkspaceRole.VIEWER)
    task, dataset = await _seed_task(db_session, ws.id), await _seed_dataset(db_session, ws.id)
    deleted = await _seed_rule(db_session, ws.id, task, dataset, deleted=True)
    foreign_task, foreign_dataset = await _seed_task(db_session, other.id), await _seed_dataset(db_session, other.id)
    foreign = await _seed_rule(db_session, other.id, foreign_task, foreign_dataset)
    ctx = {
        "viewer": viewer,
        "roleless": _member_as(ws, None),
        "editor": _member_as(ws, WorkspaceRole.EDITOR),
        "admin": _member_as(ws, WorkspaceRole.ADMIN),
        "system": _system_context(),
    }[identity]
    before = await _database_snapshot(db_session)
    with monkeypatch.context() as patch:
        patch.setattr(BackflowService, "update_rule", _unexpected_write)
        patch.setattr(BackflowService, "delete_rule", _unexpected_write)
        patch.setattr(BackflowService, "run_rule", _unexpected_write)
        rule_ids = [uuid.uuid4(), deleted.id]
        if identity != "system":
            rule_ids.append(foreign.id)
        async with _client_as(client, ctx) as http:
            for target in rule_ids:
                fetched = await http.get(f"{_BASE}/{target}")
                _assert_rule_not_found(fetched)
                updated = await http.put(f"{_BASE}/{target}", json={"name": "rewritten"})
                _assert_combined_not_found(updated)
                deleted_resp = await http.delete(f"{_BASE}/{target}")
                _assert_rule_not_found(deleted_resp)
                ran = await http.post(f"{_BASE}/{target}/run")
                _assert_combined_not_found(ran)
                for resp in (fetched, updated, deleted_resp, ran):
                    assert str(target) not in resp.text
                assert await _database_snapshot(db_session) == before


async def test_get_rule_viewers_read_editors_manage(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Viewer floor on reads, editor floor on updates: roleless always 403."""
    from hecate.ops.evaluation.backflow.service import BackflowService

    ws, viewer = await _workspace_with_role(db_session, "ws-floors", WorkspaceRole.VIEWER)
    task, dataset = await _seed_task(db_session, ws.id), await _seed_dataset(db_session, ws.id)
    rule = await _seed_rule(db_session, ws.id, task, dataset)
    before = await _database_snapshot(db_session)
    async with _client_as(client, viewer) as http:
        fetched = await http.get(f"{_BASE}/{rule.id}")
        assert fetched.status_code == 200 and fetched.json()["id"] == str(rule.id), fetched.text
        listed = await http.get(_BASE)
        assert listed.status_code == 200 and listed.json()["total"] == 1, listed.text
    with monkeypatch.context() as patch:
        patch.setattr(BackflowService, "update_rule", _unexpected_write)
        patch.setattr(BackflowService, "delete_rule", _unexpected_write)
        async with _client_as(client, _member_as(ws, None)) as http:
            roleless_get = await http.get(f"{_BASE}/{rule.id}")
            _assert_forbidden(roleless_get)
            roleless_put = await http.put(f"{_BASE}/{rule.id}", json={"name": "nope"})
            _assert_forbidden(roleless_put)
            # Authorization precedes business validation on writes too.
            roleless_inverted = await http.put(
                f"{_BASE}/{rule.id}", json={"filters": [{"metric_name": "c", "min_score": 0.9, "max_score": 0.1}]}
            )
            _assert_forbidden(roleless_inverted)
    assert await _database_snapshot(db_session) == before


async def test_system_manages_rule_in_real_workspace(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Explicit system scope reads, updates, and deletes via the real workspace."""
    ws, _ = await _workspace_with_role(db_session, "ws-sys-rule", WorkspaceRole.VIEWER)
    task, dataset = await _seed_task(db_session, ws.id), await _seed_dataset(db_session, ws.id)
    rule = await _seed_rule(db_session, ws.id, task, dataset)
    async with _client_as(client, _system_context()) as http:
        fetched = await http.get(f"{_BASE}/{rule.id}")
        assert fetched.status_code == 200 and fetched.json()["workspace_id"] == str(ws.id), fetched.text
        updated = await http.put(f"{_BASE}/{rule.id}", json={"name": "system-renamed"})
        assert updated.status_code == 200 and updated.json()["name"] == "system-renamed", updated.text
        deleted = await http.delete(f"{_BASE}/{rule.id}")
        assert deleted.status_code == 204, deleted.text
    snapshot = await _database_snapshot(db_session)
    stored = {row["id"]: row for row in snapshot[0]}
    assert stored[rule.id]["name"] == "system-renamed"
    assert stored[rule.id]["deleted"] == 1


async def test_by_id_endpoints_without_workspace_denied_before_query(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A workspace-less member gets the fixed 404s before any database query."""
    ws, _ = await _workspace_with_role(db_session, "ws-seeded", WorkspaceRole.VIEWER)
    task, dataset = await _seed_task(db_session, ws.id), await _seed_dataset(db_session, ws.id)
    rule = await _seed_rule(db_session, ws.id, task, dataset)
    before = await _database_snapshot(db_session)
    with monkeypatch.context() as patch:
        patch.setattr(AsyncSession, "execute", _unexpected_query)
        async with _client_as(client, _no_workspace_context()) as http:
            fetched = await http.get(f"{_BASE}/{rule.id}")
            updated = await http.put(f"{_BASE}/{rule.id}", json={"name": "nope"})
            deleted = await http.delete(f"{_BASE}/{rule.id}")
            ran = await http.post(f"{_BASE}/{rule.id}/run")
    _assert_rule_not_found(fetched)
    _assert_combined_not_found(updated)
    _assert_rule_not_found(deleted)
    _assert_combined_not_found(ran)
    assert await _database_snapshot(db_session) == before


# ---------------------------------------------------------------------------
# Reference visibility on update/run + service rechecks
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("identity", ["viewer", "editor", "system"])
async def test_update_invisible_effective_reference_combined_404(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    identity: str,
) -> None:
    """The merged-patch effective task/dataset must be live, before roles."""
    from hecate.ops.evaluation.backflow.service import BackflowService

    ws, viewer = await _workspace_with_role(db_session, "ws-eff", WorkspaceRole.VIEWER)
    other, _ = await _workspace_with_role(db_session, "ws-eff-other", WorkspaceRole.VIEWER)
    task, dataset = await _seed_task(db_session, ws.id), await _seed_dataset(db_session, ws.id)
    rule = await _seed_rule(db_session, ws.id, task, dataset)
    # The rule's unchanged dataset reference expires.
    await db_session.execute(
        update(EvaluationDatasetModel).where(EvaluationDatasetModel.id == dataset.id).values(deleted=True)
    )
    await db_session.commit()
    foreign_task = await _seed_task(db_session, other.id)
    ctx = {"viewer": viewer, "editor": _member_as(ws, WorkspaceRole.EDITOR), "system": _system_context()}[identity]
    before = await _database_snapshot(db_session)
    with monkeypatch.context() as patch:
        patch.setattr(BackflowService, "update_rule", _unexpected_write)
        async with _client_as(client, ctx) as http:
            name_only = await http.put(f"{_BASE}/{rule.id}", json={"name": "renamed"})
            _assert_combined_not_found(name_only)
            rebind = await http.put(f"{_BASE}/{rule.id}", json={"task_id": str(foreign_task.id)})
            _assert_combined_not_found(rebind)
    assert await _database_snapshot(db_session) == before


@pytest.mark.parametrize("change", ["delete_task", "delete_dataset", "rebind_dataset"])
async def test_run_rechecks_references_after_precheck(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    """The service rejects on stale bindings before samples or last_run."""
    from hecate.ops.evaluation.backflow.service import BackflowService

    ws, _ = await _workspace_with_role(db_session, "ws-race", WorkspaceRole.VIEWER)
    other, _ = await _workspace_with_role(db_session, "ws-race-other", WorkspaceRole.VIEWER)
    task, dataset = await _seed_task(db_session, ws.id), await _seed_dataset(db_session, ws.id)
    foreign_dataset = await _seed_dataset(db_session, other.id)
    rule = await _seed_rule(db_session, ws.id, task, dataset)
    trace = await _seed_trace(db_session, ws.id)
    await _seed_score(db_session, task, trace.id, value=0.2)
    await _install_event_store(monkeypatch, [trace])
    before = await _database_snapshot(db_session)
    original = BackflowService.run_rule

    async def precheck_then_change(self: BackflowService, rule_id: uuid.UUID, workspace_id: uuid.UUID, **kwargs: Any):
        if change == "delete_task":
            await self.db.execute(
                update(EvaluationTaskModel)
                .where(EvaluationTaskModel.id == task.id)
                .values(deleted=True)
                .execution_options(synchronize_session=False)
            )
        elif change == "delete_dataset":
            await self.db.execute(
                update(EvaluationDatasetModel)
                .where(EvaluationDatasetModel.id == dataset.id)
                .values(deleted=True)
                .execution_options(synchronize_session=False)
            )
        else:
            await self.db.execute(
                update(EvaluationBackflowRuleModel)
                .where(EvaluationBackflowRuleModel.id == rule.id)
                .values(dataset_id=foreign_dataset.id)
                .execution_options(synchronize_session=False)
            )
        return await original(self, rule_id, workspace_id, **kwargs)

    monkeypatch.setattr(BackflowService, "run_rule", precheck_then_change)
    async with _client_as(client, _member_as(ws, WorkspaceRole.EDITOR)) as http:
        response = await http.post(f"{_BASE}/{rule.id}/run")
    _assert_combined_not_found(response)
    assert await _database_snapshot(db_session) == before


async def test_run_rejects_rule_deleted_after_precheck(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rule soft-deleted between the precheck and the service never runs."""
    from hecate.ops.api import evaluation_backflow

    ws, _ = await _workspace_with_role(db_session, "ws-rule-race", WorkspaceRole.VIEWER)
    task, dataset = await _seed_task(db_session, ws.id), await _seed_dataset(db_session, ws.id)
    rule = await _seed_rule(db_session, ws.id, task, dataset)
    trace = await _seed_trace(db_session, ws.id)
    await _seed_score(db_session, task, trace.id, value=0.2)
    await _install_event_store(monkeypatch, [trace])
    before = await _database_snapshot(db_session)

    original = evaluation_backflow._get_rule_for_read

    async def precheck_then_delete(ctx: AuthContext, target: uuid.UUID, db: AsyncSession):
        found = await original(ctx, target, db)
        # Another writer soft-deletes the rule; the identity map keeps the
        # stale live object, so the service must recheck.
        await db.execute(
            update(EvaluationBackflowRuleModel)
            .where(EvaluationBackflowRuleModel.id == rule.id)
            .values(deleted=True, deleted_at=datetime.now(UTC))
        )
        await db.commit()
        return found

    monkeypatch.setattr(evaluation_backflow, "_get_rule_for_read", precheck_then_delete)
    async with _client_as(client, _member_as(ws, WorkspaceRole.EDITOR)) as http:
        response = await http.post(f"{_BASE}/{rule.id}/run")
    _assert_combined_not_found(response)
    after = await _database_snapshot(db_session)
    # The deletion itself persisted; everything the run would have touched
    # (tasks, datasets, samples, scores) and the last_run summary did not.
    assert after[1:] == before[1:]
    stored_rule = next(row for row in after[0] if row["id"] == rule.id)
    assert stored_rule["deleted"] == 1
    assert "last_run" not in (stored_rule["metadata"] or {})


async def test_delete_survives_dead_references_and_keeps_samples(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deleting a rule never depends on its references and keeps samples."""
    ws, _ = await _workspace_with_role(db_session, "ws-cleanup", WorkspaceRole.VIEWER)
    task, dataset = await _seed_task(db_session, ws.id), await _seed_dataset(db_session, ws.id)
    rule = await _seed_rule(db_session, ws.id, task, dataset)
    trace = await _seed_trace(db_session, ws.id)
    await _seed_score(db_session, task, trace.id, value=0.2)
    await _install_event_store(monkeypatch, [trace])
    async with _client_as(client, _member_as(ws, WorkspaceRole.EDITOR)) as http:
        ran = await http.post(f"{_BASE}/{rule.id}/run")
        assert ran.status_code == 200 and ran.json()["created"] == 1, ran.text
    # Both references disappear; the rule stays deletable for cleanup.
    await db_session.execute(update(EvaluationTaskModel).where(EvaluationTaskModel.id == task.id).values(deleted=True))
    await db_session.execute(
        update(EvaluationDatasetModel).where(EvaluationDatasetModel.id == dataset.id).values(deleted=True)
    )
    await db_session.commit()
    async with _client_as(client, _member_as(ws, WorkspaceRole.EDITOR)) as http:
        deleted = await http.delete(f"{_BASE}/{rule.id}")
        assert deleted.status_code == 204, deleted.text
        gone = await http.get(f"{_BASE}/{rule.id}")
        _assert_rule_not_found(gone)
    snapshot = await _database_snapshot(db_session)
    assert [row for row in snapshot[3] if row["dataset_id"] == dataset.id], "samples must be retained"


@pytest.mark.parametrize("action", ["update", "run"])
@pytest.mark.parametrize("reference", ["task", "dataset"])
@pytest.mark.parametrize("identity", ["editor", "system"])
async def test_valid_rebinding_after_precheck_rejected(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    action: str,
    reference: str,
    identity: str,
) -> None:
    """Even live replacement references cannot replace those admitted by the API."""
    ws, _ = await _workspace_with_role(db_session, "ws-local-rebinding", WorkspaceRole.VIEWER)
    task, dataset = await _seed_task(db_session, ws.id), await _seed_dataset(db_session, ws.id)
    new_task, new_dataset = await _seed_task(db_session, ws.id), await _seed_dataset(db_session, ws.id)
    rule = await _seed_rule(db_session, ws.id, task, dataset)
    trace = await _seed_trace(db_session, ws.id)
    await _seed_score(db_session, task, trace.id)
    await _install_event_store(monkeypatch, [trace])
    ctx = _system_context() if identity == "system" else _member_as(ws, WorkspaceRole.EDITOR)
    before = await _database_snapshot(db_session)
    original = getattr(BackflowService, f"{action}_rule")

    async def replace_binding(self: BackflowService, *args: Any, **kwargs: Any):
        values = {"task_id": new_task.id} if reference == "task" else {"dataset_id": new_dataset.id}
        await self.db.execute(
            update(EvaluationBackflowRuleModel)
            .where(EvaluationBackflowRuleModel.id == rule.id)
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(BackflowService, f"{action}_rule", replace_binding)
    async with _client_as(client, ctx) as http:
        if action == "run":
            response = await http.post(f"{_BASE}/{rule.id}/run")
        else:
            response = await http.put(f"{_BASE}/{rule.id}", json={"name": "must-not-rename"})
    _assert_combined_not_found(response)
    assert await _database_snapshot(db_session) == before


# ---------------------------------------------------------------------------
# Run: source qualification, trace ownership, attribution
# ---------------------------------------------------------------------------


async def test_interference_scores_never_qualify_or_enter_provenance(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Foreign, human, error, deleted, and non-trace scores stay out."""
    ws, _ = await _workspace_with_role(db_session, "ws-qualify", WorkspaceRole.VIEWER)
    other, _ = await _workspace_with_role(db_session, "ws-qualify-other", WorkspaceRole.VIEWER)
    task, dataset = await _seed_task(db_session, ws.id), await _seed_dataset(db_session, ws.id)
    rule = await _seed_rule(db_session, ws.id, task, dataset)

    good = await _seed_trace(db_session, ws.id)
    await _seed_score(db_session, task, good.id, value=0.2)
    # Human-sourced row on the same task: never a candidate or provenance.
    human_target = await _seed_trace(db_session, ws.id)
    await _seed_score(db_session, task, human_target.id, value=0.1, source="human")
    # Error-status row inside the band: no score, no candidate.
    error_target = await _seed_trace(db_session, ws.id)
    await _seed_score(db_session, task, error_target.id, value=0.15, status="error")
    # Foreign-workspace row for its own trace.
    foreign_target = await _seed_trace(db_session, ws.id)
    await _seed_score(db_session, task, foreign_target.id, value=0.2, workspace_id=other.id)
    # Soft-deleted row.
    deleted_target = await _seed_trace(db_session, ws.id)
    await _seed_score(db_session, task, deleted_target.id, value=0.2, deleted=True)
    # Non-trace target type.
    session_target = await _seed_trace(db_session, ws.id)
    await _seed_score(db_session, task, session_target.id, value=0.2, target_type="session")
    await _install_event_store(monkeypatch, [good])

    async with _client_as(client, _member_as(ws, WorkspaceRole.EDITOR)) as http:
        response = await http.post(f"{_BASE}/{rule.id}/run")
    assert response.status_code == 200, response.text
    assert response.json()["created"] == 1 and response.json()["skipped"] == 0, response.text

    snapshot = await _database_snapshot(db_session)
    items = [row for row in snapshot[3] if row["dataset_id"] == dataset.id]
    assert [row["metadata"]["backflow"]["trace_id"] for row in items] == [str(good.id)]
    assert items[0]["workspace_id"] == ws.id
    provenance = items[0]["metadata"]["backflow"]["scores"]
    assert provenance == [
        {"metric_name": "correctness", "value": 0.2, "source": "llm_judge", "scored_at": provenance[0]["scored_at"]}
    ]


@pytest.mark.parametrize("invalid", ["foreign", "deleted", "non_root", "running"])
async def test_ineligible_traces_skip_before_event_reads(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    invalid: str,
) -> None:
    """Scores alone never qualify a trace: ownership is re-derived first."""
    ws, _ = await _workspace_with_role(db_session, "ws-trace", WorkspaceRole.VIEWER)
    other, _ = await _workspace_with_role(db_session, "ws-trace-other", WorkspaceRole.VIEWER)
    task, dataset = await _seed_task(db_session, ws.id), await _seed_dataset(db_session, ws.id)
    rule = await _seed_rule(db_session, ws.id, task, dataset)
    if invalid == "foreign":
        trace = await _seed_trace(db_session, other.id)
    elif invalid == "deleted":
        trace = await _seed_trace(db_session, ws.id)
        trace.deleted = 1
        await db_session.commit()
    elif invalid == "non_root":
        trace = await _seed_trace(db_session, ws.id, trace_type="span")
    else:
        trace = await _seed_trace(db_session, ws.id, status="started")
    await _seed_score(db_session, task, trace.id, value=0.2)
    await _install_event_store(monkeypatch, [])
    with monkeypatch.context() as patch:
        patch.setattr(InMemoryEventStore, "get_events", _unexpected_events)
        async with _client_as(client, _member_as(ws, WorkspaceRole.EDITOR)) as http:
            response = await http.post(f"{_BASE}/{rule.id}/run")
    assert response.status_code == 200, response.text
    assert response.json()["created"] == 0 and response.json()["skipped"] == 1, response.text
    snapshot = await _database_snapshot(db_session)
    assert [row for row in snapshot[3] if row["dataset_id"] == dataset.id] == []


async def test_run_orders_stably_and_limits_materializations(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Oldest-first with stable ties; cheap skips never consume the limit."""
    ws, _ = await _workspace_with_role(db_session, "ws-order", WorkspaceRole.VIEWER)
    task, dataset = await _seed_task(db_session, ws.id), await _seed_dataset(db_session, ws.id)
    rule = await _seed_rule(db_session, ws.id, task, dataset, name="ordered-harvest")
    traces = []
    for index in range(3):
        trace = await _seed_trace(db_session, ws.id)
        traces.append(trace)
        await _seed_score(db_session, task, trace.id, value=0.2, created_at=_NOW + timedelta(seconds=index))
    await _install_event_store(monkeypatch, traces)
    async with _client_as(client, _member_as(ws, WorkspaceRole.EDITOR)) as http:
        capped = await http.put(f"{_BASE}/{rule.id}", json={"limit": 2})
        assert capped.status_code == 200, capped.text
        first = await http.post(f"{_BASE}/{rule.id}/run")
        assert first.status_code == 200 and first.json()["created"] == 2, first.text
        after_first = await _database_snapshot(db_session)
        second = await http.post(f"{_BASE}/{rule.id}/run")
        assert second.status_code == 200, second.text
        assert second.json()["created"] == 1 and second.json()["skipped"] == 2, second.text
    snapshot = await _database_snapshot(db_session)
    # The limit-2 run must pick the two oldest hits; the cheap skip of the
    # already-materialized pair must not consume the cap, leaving the third
    # reachable for the second run. (Server-side created_at is second-
    # precision, so per-run ordering is asserted via membership.)
    first_ids = {row["metadata"]["backflow"]["trace_id"] for row in after_first[3] if row["dataset_id"] == dataset.id}
    assert first_ids == {str(traces[0].id), str(traces[1].id)}
    all_ids = {row["metadata"]["backflow"]["trace_id"] for row in snapshot[3] if row["dataset_id"] == dataset.id}
    assert all_ids == {str(t.id) for t in traces}
    assert all(row["workspace_id"] == ws.id for row in snapshot[3] if row["dataset_id"] == dataset.id)
    stored_rule = next(row for row in snapshot[0] if row["id"] == rule.id)
    assert stored_rule["metadata"]["last_run"]["created"] == 1 and stored_rule["metadata"]["last_run"]["skipped"] == 2


async def test_zero_uuid_workspace_rules_still_work(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The zero-UUID workspace stays a valid explicit scope for backflow."""
    zero_ws = uuid.UUID(int=0)
    task, dataset = await _seed_task(db_session, zero_ws), await _seed_dataset(db_session, zero_ws)
    rule = await _seed_rule(db_session, zero_ws, task, dataset)
    trace = await _seed_trace(db_session, zero_ws)
    await _seed_score(db_session, task, trace.id, value=0.2)
    await _install_event_store(monkeypatch, [trace])
    editor = AuthContext(
        user_id=uuid.uuid4(),
        org_id=None,
        workspace_id=zero_ws,
        role=WorkspaceRole.EDITOR,
        auth_method="jwt",
        api_key_scope=None,
    )
    async with _client_as(client, editor) as http:
        created = await http.post(_BASE, json=_rule_payload(task.id, dataset.id))
        assert created.status_code == 201 and created.json()["workspace_id"] == str(zero_ws), created.text
        ran = await http.post(f"{_BASE}/{rule.id}/run")
        assert ran.status_code == 200 and ran.json()["created"] == 1, ran.text
    snapshot = await _database_snapshot(db_session)
    items = [row for row in snapshot[3] if row["dataset_id"] == dataset.id]
    assert all(row["workspace_id"] == zero_ws for row in items)


@pytest.mark.parametrize("first", ["annotation", "backflow"])
async def test_real_cross_path_runs_dedupe_in_both_orders(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    first: str,
) -> None:
    """Human backflow and rule runs interleave without duplicating samples."""
    from hecate.models.evaluation import AnnotationQueueItemModel, AnnotationQueueModel

    ws, _ = await _workspace_with_role(db_session, "ws-cross", WorkspaceRole.VIEWER)
    task, dataset = await _seed_task(db_session, ws.id), await _seed_dataset(db_session, ws.id)
    rule = await _seed_rule(db_session, ws.id, task, dataset)
    trace = await _seed_trace(db_session, ws.id)
    await _seed_score(db_session, task, trace.id, value=0.2)
    queue = AnnotationQueueModel(
        name=f"cross-queue-{uuid.uuid4().hex[:8]}",
        metric_defs=[{"name": "helpfulness", "data_type": "numeric", "min": 0, "max": 1}],
        assigned_user_ids=[],
        workspace_id=ws.id,
    )
    db_session.add(queue)
    await db_session.flush()
    db_session.add(
        AnnotationQueueItemModel(
            queue_id=queue.id,
            target_type="trace",
            target_id=trace.id,
            status="completed",
            added_by=uuid.uuid4(),
            workspace_id=ws.id,
        )
    )
    # The automated qualifying score stays; a human ledger row gives the
    # annotation path labels to materialize.
    db_session.add(
        EvaluationTaskScoreModel(
            task_id=None,
            target_id=trace.id,
            metric_name="helpfulness",
            value=0.9,
            source="human",
            status="completed",
            annotator_id=uuid.uuid4(),
            workspace_id=ws.id,
            created_at=_NOW,
        )
    )
    await db_session.commit()
    store = await _install_event_store(monkeypatch, [trace])
    annotation_svc = AnnotationService(db_session, event_store=store)

    async with _client_as(client, _member_as(ws, WorkspaceRole.EDITOR)) as http:
        if first == "annotation":
            human = await annotation_svc.push_to_dataset(
                queue.id, workspace_id=ws.id, dataset_id=dataset.id, dataset_name=None
            )
            await db_session.commit()
            assert human["created"] == 1, human
        automatic = await http.post(f"{_BASE}/{rule.id}/run")
        assert automatic.status_code == 200, automatic.text
        if first == "backflow":
            human = await annotation_svc.push_to_dataset(
                queue.id, workspace_id=ws.id, dataset_id=dataset.id, dataset_name=None
            )
            await db_session.commit()
            assert human["created"] == 0 and human["skipped"] == 1, human
    assert automatic.json()["created"] == (0 if first == "annotation" else 1), automatic.text
    assert automatic.json()["skipped"] == (1 if first == "annotation" else 0), automatic.text
    snapshot = await _database_snapshot(db_session)
    items = [row for row in snapshot[3] if row["dataset_id"] == dataset.id]
    assert len(items) == 1
    assert items[0]["metadata"][first]["trace_id"] == str(trace.id)


@pytest.mark.parametrize("identity", ["editor", "system"])
async def test_requested_reference_update_is_allowed(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    identity: str,
) -> None:
    """Explicitly requested reference changes still work when the stored binding is stable."""
    ws, _ = await _workspace_with_role(db_session, "ws-requested-rebind", WorkspaceRole.VIEWER)
    task, dataset = await _seed_task(db_session, ws.id), await _seed_dataset(db_session, ws.id)
    rule = await _seed_rule(db_session, ws.id, task, dataset)
    new_task, new_dataset = await _seed_task(db_session, ws.id), await _seed_dataset(db_session, ws.id)
    trace = await _seed_trace(db_session, ws.id)
    await _seed_score(db_session, new_task, trace.id)
    await _install_event_store(monkeypatch, [trace])
    ctx = _system_context() if identity == "system" else _member_as(ws, WorkspaceRole.EDITOR)
    async with _client_as(client, ctx) as http:
        updated = await http.put(
            f"{_BASE}/{rule.id}", json={"task_id": str(new_task.id), "dataset_id": str(new_dataset.id)}
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["task_id"] == str(new_task.id)
        assert updated.json()["dataset_id"] == str(new_dataset.id)
        ran = await http.post(f"{_BASE}/{rule.id}/run")
        assert ran.status_code == 200 and ran.json()["created"] == 1, ran.text
        assert ran.json()["dataset_id"] == str(new_dataset.id)
    rows = (await _database_snapshot(db_session))[3]
    assert len(rows) == 1 and rows[0]["dataset_id"] == new_dataset.id


@pytest.mark.parametrize("action", ["update", "run"])
@pytest.mark.parametrize("identity", ["viewer", "roleless", "editor", "admin", "system"])
async def test_effective_offline_task_rejected_after_role(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    action: str,
    identity: str,
) -> None:
    """A live legacy offline binding cannot be retained or materialized by a rule."""
    ws, viewer = await _workspace_with_role(db_session, "ws-offline-bound", WorkspaceRole.VIEWER)
    task = await _seed_task(db_session, ws.id, task_type="offline")
    dataset = await _seed_dataset(db_session, ws.id)
    rule = await _seed_rule(db_session, ws.id, task, dataset)
    trace = await _seed_trace(db_session, ws.id)
    await _seed_score(db_session, task, trace.id)
    await _install_event_store(monkeypatch, [trace])
    ctx = {
        "viewer": viewer,
        "roleless": _member_as(ws, None),
        "editor": _member_as(ws, WorkspaceRole.EDITOR),
        "admin": _member_as(ws, WorkspaceRole.ADMIN),
        "system": _system_context(),
    }[identity]
    before = await _database_snapshot(db_session)
    async with _client_as(client, ctx) as http:
        if action == "run":
            response = await http.post(f"{_BASE}/{rule.id}/run")
        else:
            response = await http.put(f"{_BASE}/{rule.id}", json={"name": "must-not-rename"})
    if identity == "viewer":
        _assert_forbidden_viewer(response)
    elif identity == "roleless":
        _assert_forbidden(response)
    else:
        assert response.status_code == 400, response.text
        assert response.json()["detail"]["error"]["code"] == "INVALID_REQUEST"
        assert "online" in response.json()["detail"]["error"]["message"]
    assert await _database_snapshot(db_session) == before


@pytest.mark.parametrize("action", ["update", "run"])
async def test_task_type_rechecked_after_api_precheck(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    action: str,
) -> None:
    """The service rejects a source that becomes offline after the API precheck."""
    ws, _ = await _workspace_with_role(db_session, "ws-task-type-race", WorkspaceRole.VIEWER)
    task = await _seed_task(db_session, ws.id)
    dataset = await _seed_dataset(db_session, ws.id)
    rule = await _seed_rule(db_session, ws.id, task, dataset)
    trace = await _seed_trace(db_session, ws.id)
    await _seed_score(db_session, task, trace.id)
    await _install_event_store(monkeypatch, [trace])
    before = await _database_snapshot(db_session)
    original = getattr(BackflowService, f"{action}_rule")

    async def change_type(self: BackflowService, *args: Any, **kwargs: Any):
        cached_task = await self._task(task.id, kwargs["workspace_id"])
        assert cached_task is not None and cached_task.task_type == "online"
        await self.db.execute(
            update(EvaluationTaskModel)
            .where(EvaluationTaskModel.id == task.id)
            .values(task_type="offline")
            .execution_options(synchronize_session=False)
        )
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(BackflowService, f"{action}_rule", change_type)
    async with _client_as(client, _member_as(ws, WorkspaceRole.EDITOR)) as http:
        if action == "run":
            response = await http.post(f"{_BASE}/{rule.id}/run")
        else:
            response = await http.put(f"{_BASE}/{rule.id}", json={"name": "must-not-rename"})
    assert response.status_code == 400, response.text
    assert await _database_snapshot(db_session) == before


@pytest.mark.parametrize("identity", ["editor", "system"])
async def test_disabled_online_task_still_allows_explicit_run(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    identity: str,
) -> None:
    """Disabling online sampling does not disable explicit backflow operations."""
    ws, _ = await _workspace_with_role(db_session, "ws-disabled-online", WorkspaceRole.VIEWER)
    task = await _seed_task(db_session, ws.id)
    task.status = "disabled"
    await db_session.commit()
    dataset = await _seed_dataset(db_session, ws.id)
    rule = await _seed_rule(db_session, ws.id, task, dataset)
    trace = await _seed_trace(db_session, ws.id)
    await _seed_score(db_session, task, trace.id)
    await _install_event_store(monkeypatch, [trace])
    ctx = _system_context() if identity == "system" else _member_as(ws, WorkspaceRole.EDITOR)
    async with _client_as(client, ctx) as http:
        updated = await http.put(f"{_BASE}/{rule.id}", json={"limit": 1})
        assert updated.status_code == 200, updated.text
        ran = await http.post(f"{_BASE}/{rule.id}/run")
        assert ran.status_code == 200 and ran.json()["created"] == 1, ran.text
