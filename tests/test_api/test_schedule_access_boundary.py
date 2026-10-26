"""Schedule management REST access boundary tests.

Every ``/api/schedules`` entry is exercised behind the workspace/role/
ownership gate matrix: the active-workspace requirement (system scope
included; an explicit zero-UUID workspace stays a legal value), the
VIEWER read floor vs EDITOR write floor, the uniform 404 for missing,
soft-deleted, cross-workspace (same-org and cross-org) and historical
NULL-workspace schedules, binding-liveness validation ahead of any
persistence or scheduler registration, execution_config reserved keys,
and zero business side effects on every denial — asserted with real
database row snapshots and a recording scheduler stub, never just HTTP
status codes.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.auth_context import AuthContext
from hecate.core.deps import get_db
from hecate.core.deps_workspace import get_auth_context
from hecate.models.agent import AgentModel
from hecate.models.organization import OrganizationModel
from hecate.models.scheduled_task import (
    ScheduledTaskExecutionModel,
    ScheduledTaskModel,
    ScheduleState,
)
from hecate.models.workflow import WorkflowModel
from hecate.models.workspace import WorkspaceModel
from hecate.models.workspace_member import WorkspaceRole
from hecate.ops.api.schedules import get_schedule_manager, router
from hecate.ops.scheduling.service import ScheduledTaskService, ScheduleTargetError
from tests.conftest import test_session_factory as shared_session_factory

# ---------------------------------------------------------------------
# Identity helpers
# ---------------------------------------------------------------------


def _ws_ctx(ws: WorkspaceModel, role: WorkspaceRole | None) -> AuthContext:
    """A workspace-scoped JWT identity with the given membership role."""
    return AuthContext(
        user_id=uuid.uuid4(),
        org_id=ws.org_id,
        workspace_id=ws.id,
        role=role,
        auth_method="jwt",
        api_key_scope=None,
    )


def _nows_ctx() -> AuthContext:
    """A valid JWT identity without any workspace attribution."""
    return AuthContext(
        user_id=uuid.uuid4(),
        org_id=None,
        workspace_id=None,
        role=None,
        auth_method="jwt",
        api_key_scope=None,
    )


def _system_ctx(ws: WorkspaceModel | None = None) -> AuthContext:
    """Deploy-time bootstrap identity, optionally bound to a workspace."""
    return AuthContext(
        user_id=uuid.uuid4(),
        org_id=ws.org_id if ws else None,
        workspace_id=ws.id if ws else None,
        role=None,
        auth_method="api_key",
        api_key_scope="system",
    )


# ---------------------------------------------------------------------
# Scheduler double
# ---------------------------------------------------------------------


class StubScheduleManager:
    """DI-injected scheduler double.

    Records external registrations (add/update/remove) so tests can
    assert that denials never touch the scheduler. Cron validation is
    self-contained (croniter is an optional extra): five fields of
    digits/``*``/``/``/'',''/``-`` accepted, anything else rejected.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.next_run = datetime(2030, 1, 1, 9, 0, tzinfo=UTC)

    def validate_cron(self, expression: str) -> bool:
        fields = expression.split()
        return len(fields) == 5 and all(re.fullmatch(r"[\d*/,\-]+", field) for field in fields)

    def calculate_next_run(self, cron_expression: str, timezone: str = "UTC") -> datetime:
        del cron_expression, timezone
        return self.next_run

    async def add_schedule(self, task: ScheduledTaskModel) -> None:
        self.calls.append(("add", str(task.id)))

    async def remove_schedule(self, task_id: uuid.UUID) -> None:
        self.calls.append(("remove", str(task_id)))

    async def update_schedule(self, task: ScheduledTaskModel) -> None:
        self.calls.append(("update", str(task.id)))


@asynccontextmanager
async def _client_for(ctx: AuthContext, scheduler: StubScheduleManager) -> AsyncGenerator[AsyncClient, None]:
    """Client wired to the schedules router with swappable auth/scheduler."""
    app = FastAPI()
    app.include_router(router, prefix="/api")

    async def per_request_db() -> AsyncGenerator[AsyncSession, None]:
        async with shared_session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def swapped_auth() -> AuthContext:
        return ctx

    app.dependency_overrides[get_db] = per_request_db
    app.dependency_overrides[get_auth_context] = swapped_auth
    app.dependency_overrides[get_schedule_manager] = lambda: scheduler
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client
    finally:
        app.dependency_overrides.clear()


async def _snapshot() -> dict[str, list[tuple]]:
    """Full durable picture of the scheduling tables (fresh session)."""
    async with shared_session_factory() as session:
        tasks = (await session.execute(select(ScheduledTaskModel).order_by(ScheduledTaskModel.id))).scalars().all()
        executions = (
            (await session.execute(select(ScheduledTaskExecutionModel).order_by(ScheduledTaskExecutionModel.id)))
            .scalars()
            .all()
        )
        return {
            "tasks": [
                (
                    t.id,
                    t.name,
                    t.cron_expression,
                    t.state,
                    t.deleted,
                    t.deleted_at,
                    t.next_run_at,
                    t.last_run_at,
                    t.updated_at,
                    t.execution_config,
                )
                for t in tasks
            ],
            "executions": [
                (e.id, e.task_id, e.status, e.triggered_by, e.started_at, e.result_summary) for e in executions
            ],
        }


def _create_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"name": "boundary schedule", "cron_expression": "0 9 * * *"}
    body.update(overrides)
    return body


# ---------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------


@pytest.fixture
async def env() -> AsyncGenerator[SimpleNamespace, None]:
    """Two orgs, three workspaces, bindings, schedule rows, executions.

    ws_a1/ws_a2 share org_a (same-org different-workspace case); ws_b1
    belongs to org_b (cross-org case). Scheduled rows cover the live,
    foreign, cross-org, soft-deleted, historical NULL-workspace, dead
    binding, and paused variants every gate needs.
    """
    user = uuid.uuid4()
    org_a = OrganizationModel(id=uuid.uuid4(), name="Org A", slug=f"org-a-{uuid.uuid4().hex[:8]}", owner_id=user)
    org_b = OrganizationModel(id=uuid.uuid4(), name="Org B", slug=f"org-b-{uuid.uuid4().hex[:8]}", owner_id=user)
    ws_a1 = WorkspaceModel(id=uuid.uuid4(), org_id=org_a.id, name="ws-a1", slug=f"ws-a1-{uuid.uuid4().hex[:8]}")
    ws_a2 = WorkspaceModel(id=uuid.uuid4(), org_id=org_a.id, name="ws-a2", slug=f"ws-a2-{uuid.uuid4().hex[:8]}")
    ws_b1 = WorkspaceModel(id=uuid.uuid4(), org_id=org_b.id, name="ws-b1", slug=f"ws-b1-{uuid.uuid4().hex[:8]}")

    agent_a1 = AgentModel(id=uuid.uuid4(), workspace_id=ws_a1.id, name="agent-a1")
    agent_a2 = AgentModel(id=uuid.uuid4(), workspace_id=ws_a2.id, name="agent-a2")
    agent_dead = AgentModel(
        id=uuid.uuid4(), workspace_id=ws_a1.id, name="agent-a1-dead", deleted=True, deleted_at=datetime.now(UTC)
    )
    wf_a1 = WorkflowModel(id=uuid.uuid4(), workspace_id=ws_a1.id, name="wf-a1")

    def _sched(ws: WorkspaceModel, **kwargs: Any) -> ScheduledTaskModel:
        base: dict[str, Any] = {
            "id": uuid.uuid4(),
            "org_id": ws.org_id,
            "workspace_id": ws.id,
            "name": f"schedule-{ws.slug}",
            "cron_expression": "0 9 * * *",
        }
        base.update(kwargs)
        return ScheduledTaskModel(**base)

    sched_live = _sched(ws_a1, name="live", agent_id=agent_a1.id)
    sched_paused = _sched(ws_a1, name="paused", agent_id=agent_a1.id, state=ScheduleState.PAUSED.value)
    sched_dead_binding = _sched(ws_a1, name="dead-binding", agent_id=agent_dead.id)
    sched_paused_dead = _sched(
        ws_a1, name="paused-dead-binding", agent_id=agent_dead.id, state=ScheduleState.PAUSED.value
    )
    sched_deleted = _sched(ws_a1, name="deleted", deleted=True, deleted_at=datetime.now(UTC))
    sched_foreign = _sched(ws_a2, name="foreign", agent_id=agent_a2.id)
    sched_b = _sched(ws_b1, name="cross-org")
    sched_legacy = ScheduledTaskModel(
        id=uuid.uuid4(),
        org_id=org_a.id,
        workspace_id=None,
        name="legacy-org-level",
        cron_expression="0 9 * * *",
    )

    exec_live_1 = ScheduledTaskExecutionModel(task_id=sched_live.id, status="success", triggered_by="cron")
    exec_live_2 = ScheduledTaskExecutionModel(task_id=sched_live.id, status="failed", triggered_by="cron")
    exec_foreign = ScheduledTaskExecutionModel(task_id=sched_foreign.id, status="success", triggered_by="cron")

    async with shared_session_factory() as session:
        session.add_all(
            [
                org_a,
                org_b,
                ws_a1,
                ws_a2,
                ws_b1,
                agent_a1,
                agent_a2,
                agent_dead,
                wf_a1,
                sched_live,
                sched_paused,
                sched_dead_binding,
                sched_paused_dead,
                sched_deleted,
                sched_foreign,
                sched_b,
                sched_legacy,
                exec_live_1,
                exec_live_2,
                exec_foreign,
            ]
        )
        await session.commit()

    yield SimpleNamespace(
        org_a=org_a,
        org_b=org_b,
        ws_a1=ws_a1,
        ws_a2=ws_a2,
        ws_b1=ws_b1,
        agent_a1=agent_a1,
        agent_a2=agent_a2,
        agent_dead=agent_dead,
        wf_a1=wf_a1,
        sched_live=sched_live,
        sched_paused=sched_paused,
        sched_dead_binding=sched_dead_binding,
        sched_paused_dead=sched_paused_dead,
        sched_deleted=sched_deleted,
        sched_foreign=sched_foreign,
        sched_b=sched_b,
        sched_legacy=sched_legacy,
    )


# ---------------------------------------------------------------------
# Active-workspace requirement
# ---------------------------------------------------------------------


class TestWorkspaceRequirement:
    @pytest.mark.parametrize(
        ("method", "path_builder"),
        [
            ("POST", lambda env: "/api/schedules"),
            ("GET", lambda env: "/api/schedules"),
            ("GET", lambda env: f"/api/schedules/{env.sched_live.id}"),
            ("PUT", lambda env: f"/api/schedules/{env.sched_live.id}"),
            ("DELETE", lambda env: f"/api/schedules/{env.sched_live.id}"),
            ("POST", lambda env: f"/api/schedules/{env.sched_live.id}/pause"),
            ("POST", lambda env: f"/api/schedules/{env.sched_live.id}/resume"),
            ("POST", lambda env: f"/api/schedules/{env.sched_live.id}/trigger"),
            ("GET", lambda env: f"/api/schedules/{env.sched_live.id}/executions"),
        ],
    )
    async def test_workspaceless_identity_denied_on_every_entry(
        self, env: SimpleNamespace, method: str, path_builder: Any
    ) -> None:
        """A workspaceless JWT gets 403 NO_WORKSPACE everywhere — before
        any visibility decision, storage access, or scheduler use."""
        scheduler = StubScheduleManager()
        before = await _snapshot()
        async with _client_for(_nows_ctx(), scheduler) as client:
            # PUT carries a body so request-schema validation (which runs
            # before any handler) cannot mask the workspace gate.
            body = _create_body() if method == "POST" else ({"name": "x"} if method == "PUT" else None)
            response = await client.request(method, path_builder(env), json=body)
        assert response.status_code == 403, response.text
        assert response.json()["detail"]["error"]["code"] == "NO_WORKSPACE"
        assert scheduler.calls == []
        assert await _snapshot() == before

    async def test_system_scope_without_workspace_denied(self, env: SimpleNamespace) -> None:
        """System scope is no escape: without an explicit workspace it
        gets the same 403 NO_WORKSPACE on reads and writes."""
        scheduler = StubScheduleManager()
        before = await _snapshot()
        async with _client_for(_system_ctx(), scheduler) as client:
            for method, path, body in [
                ("GET", "/api/schedules", None),
                ("POST", "/api/schedules", _create_body()),
                ("POST", f"/api/schedules/{env.sched_live.id}/trigger", None),
            ]:
                response = await client.request(method, path, json=body)
                assert response.status_code == 403, response.text
                assert response.json()["detail"]["error"]["code"] == "NO_WORKSPACE"
        assert scheduler.calls == []
        assert await _snapshot() == before

    async def test_explicit_zero_uuid_workspace_is_a_legal_value(self, env: SimpleNamespace) -> None:
        """An explicit zero-UUID workspace is a value, not a missing
        scope: listing succeeds (scoped, empty) and never falls back to
        a global query; creation cannot fabricate an org attribution."""
        scheduler = StubScheduleManager()
        before = await _snapshot()
        zero = uuid.UUID(int=0)
        ctx = AuthContext(
            user_id=uuid.uuid4(),
            org_id=None,
            workspace_id=zero,
            role=WorkspaceRole.EDITOR,
            auth_method="jwt",
            api_key_scope=None,
        )
        async with _client_for(ctx, scheduler) as client:
            listed = await client.get("/api/schedules")
            assert listed.status_code == 200, listed.text
            assert listed.json() == {"items": [], "total": 0}

            detail = await client.get(f"/api/schedules/{env.sched_live.id}")
            assert detail.status_code == 404

            # No workspace row exists for the zero UUID, so attribution
            # cannot be derived — and no zero-UUID org is fabricated.
            created = await client.post("/api/schedules", json=_create_body())
            assert created.status_code == 404, created.text
            assert created.json()["detail"]["error"]["message"] == "Workspace not found"
        assert scheduler.calls == []
        assert await _snapshot() == before


# ---------------------------------------------------------------------
# List boundary
# ---------------------------------------------------------------------


class TestListBoundary:
    async def test_list_scoped_to_workspace(self, env: SimpleNamespace) -> None:
        """A1 sees only its live rows: same-org A2, cross-org B1,
        soft-deleted and historical NULL-workspace rows never appear."""
        scheduler = StubScheduleManager()
        async with _client_for(_ws_ctx(env.ws_a1, WorkspaceRole.VIEWER), scheduler) as client:
            response = await client.get("/api/schedules")
        assert response.status_code == 200, response.text
        body = response.json()
        listed_ids = {item["id"] for item in body["items"]}
        expected = {
            str(env.sched_live.id),
            str(env.sched_paused.id),
            str(env.sched_dead_binding.id),
            str(env.sched_paused_dead.id),
        }
        assert listed_ids == expected
        assert body["total"] == len(expected)
        assert str(env.sched_foreign.id) not in listed_ids
        assert str(env.sched_deleted.id) not in listed_ids
        assert str(env.sched_legacy.id) not in listed_ids

    @pytest.mark.parametrize("role", [WorkspaceRole.EDITOR, WorkspaceRole.ADMIN])
    async def test_editor_and_admin_can_list(self, env: SimpleNamespace, role: WorkspaceRole) -> None:
        async with _client_for(_ws_ctx(env.ws_a1, role), StubScheduleManager()) as client:
            response = await client.get("/api/schedules")
        assert response.status_code == 200, response.text
        assert response.json()["total"] == 4

    async def test_membership_less_identity_denied(self, env: SimpleNamespace) -> None:
        """Workspace set but no role: 403 before any query."""
        scheduler = StubScheduleManager()
        before = await _snapshot()
        async with _client_for(_ws_ctx(env.ws_a1, None), scheduler) as client:
            response = await client.get("/api/schedules")
        assert response.status_code == 403, response.text
        assert response.json()["detail"]["error"]["code"] == "FORBIDDEN"
        assert scheduler.calls == []
        assert await _snapshot() == before

    async def test_system_scope_with_explicit_workspace_lists_scoped(self, env: SimpleNamespace) -> None:
        """System scope keeps its role exemption inside an explicit
        workspace — and gains no global reach from it."""
        scheduler = StubScheduleManager()
        async with _client_for(_system_ctx(env.ws_a1), scheduler) as client:
            listed = await client.get("/api/schedules")
            assert listed.status_code == 200, listed.text
            listed_ids = {item["id"] for item in listed.json()["items"]}
            assert str(env.sched_live.id) in listed_ids
            assert str(env.sched_foreign.id) not in listed_ids
            assert str(env.sched_legacy.id) not in listed_ids

            foreign = await client.get(f"/api/schedules/{env.sched_foreign.id}")
            assert foreign.status_code == 404


# ---------------------------------------------------------------------
# Detail boundary
# ---------------------------------------------------------------------


class TestDetailBoundary:
    async def test_detail_visible_to_viewer(self, env: SimpleNamespace) -> None:
        async with _client_for(_ws_ctx(env.ws_a1, WorkspaceRole.VIEWER), StubScheduleManager()) as client:
            response = await client.get(f"/api/schedules/{env.sched_live.id}")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["id"] == str(env.sched_live.id)
        assert body["workspace_id"] == str(env.ws_a1.id)
        assert body["org_id"] == str(env.org_a.id)
        assert body["agent_id"] == str(env.agent_a1.id)

    async def test_uniform_404_across_invisibility_reasons(self, env: SimpleNamespace) -> None:
        """Missing, soft-deleted, same-org foreign, cross-org and
        historical NULL-workspace schedules share one 404 body."""
        missing = uuid.uuid4()
        scheduler = StubScheduleManager()
        async with _client_for(_ws_ctx(env.ws_a1, WorkspaceRole.VIEWER), scheduler) as client:
            bodies = []
            for path in [
                f"/api/schedules/{missing}",
                f"/api/schedules/{env.sched_deleted.id}",
                f"/api/schedules/{env.sched_foreign.id}",
                f"/api/schedules/{env.sched_b.id}",
                f"/api/schedules/{env.sched_legacy.id}",
            ]:
                response = await client.get(path)
                assert response.status_code == 404, response.text
                bodies.append(response.json())
        assert all(body == bodies[0] for body in bodies)
        assert bodies[0]["detail"]["error"]["code"] == "NOT_FOUND"

    async def test_membership_less_identity_denied(self, env: SimpleNamespace) -> None:
        async with _client_for(_ws_ctx(env.ws_a1, None), StubScheduleManager()) as client:
            response = await client.get(f"/api/schedules/{env.sched_live.id}")
        assert response.status_code == 403, response.text


# ---------------------------------------------------------------------
# Create boundary
# ---------------------------------------------------------------------


class TestCreateBoundary:
    async def test_editor_create_binds_workspace_and_org_from_live_row(self, env: SimpleNamespace) -> None:
        """Attribution comes from the server-side workspace row — the
        schedule lands in the caller's workspace with that org, and the
        scheduler is registered exactly once after persistence."""
        scheduler = StubScheduleManager()
        async with _client_for(_ws_ctx(env.ws_a1, WorkspaceRole.EDITOR), scheduler) as client:
            response = await client.post("/api/schedules", json=_create_body(agent_id=str(env.agent_a1.id)))
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["workspace_id"] == str(env.ws_a1.id)
        assert body["org_id"] == str(env.org_a.id)
        assert body["state"] == "active"
        assert scheduler.calls == [("add", body["id"])]

        async with shared_session_factory() as session:
            row = await session.get(ScheduledTaskModel, uuid.UUID(body["id"]))
            assert row is not None
            assert row.org_id == env.org_a.id
            assert row.workspace_id == env.ws_a1.id
            assert row.agent_id == env.agent_a1.id

    async def test_viewer_denied_before_cron_validation(self, env: SimpleNamespace) -> None:
        """Role gate precedes business validation: a viewer sending an
        invalid cron gets 403 (not 422) and nothing is stored."""
        scheduler = StubScheduleManager()
        before = await _snapshot()
        async with _client_for(_ws_ctx(env.ws_a1, WorkspaceRole.VIEWER), scheduler) as client:
            response = await client.post("/api/schedules", json=_create_body(cron_expression="not a cron"))
        assert response.status_code == 403, response.text
        assert scheduler.calls == []
        assert await _snapshot() == before

    async def test_invalid_cron_rejected_after_role_gate(self, env: SimpleNamespace) -> None:
        scheduler = StubScheduleManager()
        before = await _snapshot()
        async with _client_for(_ws_ctx(env.ws_a1, WorkspaceRole.EDITOR), scheduler) as client:
            response = await client.post("/api/schedules", json=_create_body(cron_expression="not a cron"))
        assert response.status_code == 422, response.text
        assert response.json()["detail"]["error"]["code"] == "INVALID_CRON"
        assert scheduler.calls == []
        assert await _snapshot() == before

    async def test_binding_validation_rejects_foreign_and_dead_uniformly(self, env: SimpleNamespace) -> None:
        """A foreign (same-org other-workspace) and a soft-deleted
        binding get the same 422 — no reason disclosure — before any
        persistence or scheduler registration."""
        scheduler = StubScheduleManager()
        before = await _snapshot()
        async with _client_for(_ws_ctx(env.ws_a1, WorkspaceRole.EDITOR), scheduler) as client:
            foreign = await client.post("/api/schedules", json=_create_body(agent_id=str(env.agent_a2.id)))
            dead = await client.post("/api/schedules", json=_create_body(agent_id=str(env.agent_dead.id)))
            missing = await client.post("/api/schedules", json=_create_body(agent_id=str(uuid.uuid4())))
        assert foreign.status_code == 422, foreign.text
        assert dead.status_code == 422
        assert missing.status_code == 422
        bodies = [foreign.json(), dead.json(), missing.json()]
        assert all(body == bodies[0] for body in bodies)
        assert bodies[0]["detail"]["error"]["code"] == "INVALID_TARGET_BINDING"
        assert scheduler.calls == []
        assert await _snapshot() == before

    async def test_workflow_binding_in_owned_workspace_accepted(self, env: SimpleNamespace) -> None:
        scheduler = StubScheduleManager()
        async with _client_for(_ws_ctx(env.ws_a1, WorkspaceRole.EDITOR), scheduler) as client:
            response = await client.post("/api/schedules", json=_create_body(workflow_id=str(env.wf_a1.id)))
        assert response.status_code == 201, response.text
        assert response.json()["workflow_id"] == str(env.wf_a1.id)

    async def test_bindingless_create_succeeds(self, env: SimpleNamespace) -> None:
        """No new target-selection rule: a schedule without an explicit
        binding is still creatable (the manager's own execution-time
        resolution is out of this API's scope)."""
        scheduler = StubScheduleManager()
        async with _client_for(_ws_ctx(env.ws_a1, WorkspaceRole.EDITOR), scheduler) as client:
            response = await client.post("/api/schedules", json=_create_body())
        assert response.status_code == 201, response.text
        assert response.json()["agent_id"] is None
        assert response.json()["workflow_id"] is None

    @pytest.mark.parametrize(
        "reserved",
        [
            {"workspace_id": "00000000-0000-0000-0000-000000000000"},
            {"org_id": "00000000-0000-0000-0000-000000000000"},
            {"agent_id": "00000000-0000-0000-0000-000000000000"},
            {"workflow_id": "00000000-0000-0000-0000-000000000000"},
        ],
    )
    async def test_execution_config_reserved_keys_rejected(self, env: SimpleNamespace, reserved: dict) -> None:
        """execution_config cannot smuggle row-level workspace or target
        identity — the executors treat these as selection fallbacks."""
        scheduler = StubScheduleManager()
        before = await _snapshot()
        async with _client_for(_ws_ctx(env.ws_a1, WorkspaceRole.EDITOR), scheduler) as client:
            response = await client.post("/api/schedules", json=_create_body(execution_config=reserved))
        assert response.status_code == 422, response.text
        assert response.json()["detail"]["error"]["code"] == "INVALID_EXECUTION_CONFIG"
        assert scheduler.calls == []
        assert await _snapshot() == before

    async def test_same_org_other_workspace_creates_separately(self, env: SimpleNamespace) -> None:
        """An A2 editor binds an A2 agent: same org, distinct workspace —
        the two workspaces never see each other's rows."""
        scheduler = StubScheduleManager()
        async with _client_for(_ws_ctx(env.ws_a2, WorkspaceRole.EDITOR), scheduler) as client:
            response = await client.post(
                "/api/schedules", json=_create_body(name="a2-schedule", agent_id=str(env.agent_a2.id))
            )
        assert response.status_code == 201, response.text
        assert response.json()["workspace_id"] == str(env.ws_a2.id)

        async with _client_for(_ws_ctx(env.ws_a1, WorkspaceRole.VIEWER), StubScheduleManager()) as client:
            listed = await client.get("/api/schedules")
            assert str(response.json()["id"]) not in {item["id"] for item in listed.json()["items"]}


# ---------------------------------------------------------------------
# Update boundary
# ---------------------------------------------------------------------


class TestUpdateBoundary:
    async def test_editor_update_reregisters_and_recalculates(self, env: SimpleNamespace) -> None:
        scheduler = StubScheduleManager()
        async with _client_for(_ws_ctx(env.ws_a1, WorkspaceRole.EDITOR), scheduler) as client:
            response = await client.put(
                f"/api/schedules/{env.sched_live.id}",
                json={"name": "renamed", "cron_expression": "*/5 * * * *"},
            )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["name"] == "renamed"
        assert body["cron_expression"] == "*/5 * * * *"
        # SQLite round-trips the stored timestamp naive; compare wall time.
        assert datetime.fromisoformat(body["next_run_at"]).replace(tzinfo=None) == scheduler.next_run.replace(
            tzinfo=None
        )
        assert ("update", str(env.sched_live.id)) in scheduler.calls

    async def test_cross_workspace_update_rejected_without_side_effects(self, env: SimpleNamespace) -> None:
        scheduler = StubScheduleManager()
        before = await _snapshot()
        async with _client_for(_ws_ctx(env.ws_a1, WorkspaceRole.EDITOR), scheduler) as client:
            response = await client.put(f"/api/schedules/{env.sched_foreign.id}", json={"name": "hijacked"})
        assert response.status_code == 404, response.text
        assert scheduler.calls == []
        assert await _snapshot() == before

    async def test_soft_deleted_update_rejected(self, env: SimpleNamespace) -> None:
        async with _client_for(_ws_ctx(env.ws_a1, WorkspaceRole.EDITOR), StubScheduleManager()) as client:
            response = await client.put(f"/api/schedules/{env.sched_deleted.id}", json={"name": "revive"})
        assert response.status_code == 404, response.text

    async def test_viewer_denied_after_visibility(self, env: SimpleNamespace) -> None:
        """Visibility passes, the role floor fails: the row and the
        scheduler stay untouched."""
        scheduler = StubScheduleManager()
        before = await _snapshot()
        async with _client_for(_ws_ctx(env.ws_a1, WorkspaceRole.VIEWER), scheduler) as client:
            response = await client.put(f"/api/schedules/{env.sched_live.id}", json={"name": "viewer-write"})
        assert response.status_code == 403, response.text
        assert scheduler.calls == []
        assert await _snapshot() == before

    async def test_update_dead_binding_rejected(self, env: SimpleNamespace) -> None:
        """An update re-registers scheduler work, so the binding is
        re-verified first — a dead target blocks the update."""
        scheduler = StubScheduleManager()
        before = await _snapshot()
        async with _client_for(_ws_ctx(env.ws_a1, WorkspaceRole.EDITOR), scheduler) as client:
            response = await client.put(f"/api/schedules/{env.sched_dead_binding.id}", json={"name": "renamed-dead"})
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["error"]["code"] == "TARGET_BINDING_UNAVAILABLE"
        assert scheduler.calls == []
        assert await _snapshot() == before

    async def test_update_execution_config_reserved_keys_rejected(self, env: SimpleNamespace) -> None:
        scheduler = StubScheduleManager()
        before = await _snapshot()
        async with _client_for(_ws_ctx(env.ws_a1, WorkspaceRole.EDITOR), scheduler) as client:
            response = await client.put(
                f"/api/schedules/{env.sched_live.id}",
                json={"execution_config": {"agent_id": str(env.agent_a2.id)}},
            )
        assert response.status_code == 422, response.text
        assert response.json()["detail"]["error"]["code"] == "INVALID_EXECUTION_CONFIG"
        assert scheduler.calls == []
        assert await _snapshot() == before


# ---------------------------------------------------------------------
# Lifecycle: pause / resume / delete / trigger
# ---------------------------------------------------------------------


class TestLifecycleBoundary:
    async def test_pause_and_delete_of_dead_binding_stay_usable(self, env: SimpleNamespace) -> None:
        """Cleanup paths never check binding liveness: a schedule whose
        target is gone can still be paused and deleted."""
        scheduler = StubScheduleManager()
        async with _client_for(_ws_ctx(env.ws_a1, WorkspaceRole.EDITOR), scheduler) as client:
            paused = await client.post(f"/api/schedules/{env.sched_dead_binding.id}/pause")
            assert paused.status_code == 200, paused.text
            assert paused.json()["state"] == "paused"

            deleted = await client.delete(f"/api/schedules/{env.sched_dead_binding.id}")
            assert deleted.status_code == 204

            detail = await client.get(f"/api/schedules/{env.sched_dead_binding.id}")
            assert detail.status_code == 404
        assert ("remove", str(env.sched_dead_binding.id)) in scheduler.calls

    async def test_pause_denied_for_viewer(self, env: SimpleNamespace) -> None:
        scheduler = StubScheduleManager()
        before = await _snapshot()
        async with _client_for(_ws_ctx(env.ws_a1, WorkspaceRole.VIEWER), scheduler) as client:
            response = await client.post(f"/api/schedules/{env.sched_live.id}/pause")
        assert response.status_code == 403, response.text
        assert scheduler.calls == []
        assert await _snapshot() == before

    async def test_resume_reactivates_live_binding(self, env: SimpleNamespace) -> None:
        async with _client_for(_ws_ctx(env.ws_a1, WorkspaceRole.EDITOR), StubScheduleManager()) as client:
            response = await client.post(f"/api/schedules/{env.sched_paused.id}/resume")
        assert response.status_code == 200, response.text
        assert response.json()["state"] == "active"

    async def test_resume_dead_binding_rejected_stays_paused(self, env: SimpleNamespace) -> None:
        """Resume re-enables work, so the binding is re-verified: a dead
        target keeps the schedule paused with zero side effects."""
        scheduler = StubScheduleManager()
        before = await _snapshot()
        async with _client_for(_ws_ctx(env.ws_a1, WorkspaceRole.EDITOR), scheduler) as client:
            response = await client.post(f"/api/schedules/{env.sched_paused_dead.id}/resume")
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["error"]["code"] == "TARGET_BINDING_UNAVAILABLE"
        assert scheduler.calls == []
        assert await _snapshot() == before

    async def test_trigger_records_manual_entry_without_certifying_execution(self, env: SimpleNamespace) -> None:
        """The manual trigger writes a record entry — by current design
        it does NOT certify that the agent/workflow actually ran; the
        ``manual_trigger`` summary marks bookkeeping, not an execution
        receipt."""
        scheduler = StubScheduleManager()
        async with _client_for(_ws_ctx(env.ws_a1, WorkspaceRole.EDITOR), scheduler) as client:
            response = await client.post(f"/api/schedules/{env.sched_live.id}/trigger")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["triggered_by"] == "manual"
        assert body["result_summary"] == {"manual_trigger": True}

        async with shared_session_factory() as session:
            row = await session.get(ScheduledTaskModel, env.sched_live.id)
            assert row is not None
            assert row.last_run_at is not None
        assert scheduler.calls == []

    async def test_trigger_dead_binding_rejected_no_record(self, env: SimpleNamespace) -> None:
        scheduler = StubScheduleManager()
        before = await _snapshot()
        async with _client_for(_ws_ctx(env.ws_a1, WorkspaceRole.EDITOR), scheduler) as client:
            response = await client.post(f"/api/schedules/{env.sched_dead_binding.id}/trigger")
        assert response.status_code == 409, response.text
        assert scheduler.calls == []
        assert await _snapshot() == before

    async def test_trigger_cross_workspace_no_side_effects(self, env: SimpleNamespace) -> None:
        """A foreign schedule cannot be triggered: uniform 404, its
        last_run_at and execution history stay untouched."""
        scheduler = StubScheduleManager()
        before = await _snapshot()
        async with _client_for(_ws_ctx(env.ws_a1, WorkspaceRole.EDITOR), scheduler) as client:
            response = await client.post(f"/api/schedules/{env.sched_foreign.id}/trigger")
        assert response.status_code == 404, response.text
        assert scheduler.calls == []
        assert await _snapshot() == before

    async def test_trigger_denied_for_viewer(self, env: SimpleNamespace) -> None:
        scheduler = StubScheduleManager()
        before = await _snapshot()
        async with _client_for(_ws_ctx(env.ws_a1, WorkspaceRole.VIEWER), scheduler) as client:
            response = await client.post(f"/api/schedules/{env.sched_live.id}/trigger")
        assert response.status_code == 403, response.text
        assert scheduler.calls == []
        assert await _snapshot() == before

    async def test_system_scope_with_workspace_can_trigger_scoped(self, env: SimpleNamespace) -> None:
        """The system role exemption applies inside an explicit
        workspace only: it may trigger A1's schedule, never A2's."""
        async with _client_for(_system_ctx(env.ws_a1), StubScheduleManager()) as client:
            own = await client.post(f"/api/schedules/{env.sched_live.id}/trigger")
            assert own.status_code == 200, own.text

            foreign = await client.post(f"/api/schedules/{env.sched_foreign.id}/trigger")
            assert foreign.status_code == 404


# ---------------------------------------------------------------------
# Execution history boundary
# ---------------------------------------------------------------------


class TestExecutionsBoundary:
    async def test_history_listed_for_visible_parent(self, env: SimpleNamespace) -> None:
        async with _client_for(_ws_ctx(env.ws_a1, WorkspaceRole.VIEWER), StubScheduleManager()) as client:
            response = await client.get(f"/api/schedules/{env.sched_live.id}/executions")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["total"] == 2
        assert {item["triggered_by"] for item in body["items"]} == {"cron"}

    async def test_invisible_parent_never_enters_history_query(self, env: SimpleNamespace) -> None:
        """Foreign, soft-deleted, historical and missing parents all 404
        before any history is read — and their rows stay untouched."""
        missing = uuid.uuid4()
        scheduler = StubScheduleManager()
        before = await _snapshot()
        async with _client_for(_ws_ctx(env.ws_a1, WorkspaceRole.VIEWER), scheduler) as client:
            bodies = []
            for path in [
                f"/api/schedules/{env.sched_foreign.id}/executions",
                f"/api/schedules/{env.sched_b.id}/executions",
                f"/api/schedules/{env.sched_deleted.id}/executions",
                f"/api/schedules/{env.sched_legacy.id}/executions",
                f"/api/schedules/{missing}/executions",
            ]:
                response = await client.get(path)
                assert response.status_code == 404, response.text
                bodies.append(response.json())
        assert all(body == bodies[0] for body in bodies)
        assert await _snapshot() == before

    async def test_history_requires_role(self, env: SimpleNamespace) -> None:
        async with _client_for(_ws_ctx(env.ws_a1, None), StubScheduleManager()) as client:
            response = await client.get(f"/api/schedules/{env.sched_live.id}/executions")
        assert response.status_code == 403, response.text


# ---------------------------------------------------------------------
# Zero-side-effect denial matrix
# ---------------------------------------------------------------------


def _denial_cases(env: SimpleNamespace) -> list[tuple[str, AuthContext, str, str, dict[str, Any] | None]]:
    """(label, identity, method, path, body) for every denial shape."""
    a1_editor = _ws_ctx(env.ws_a1, WorkspaceRole.EDITOR)
    a1_viewer = _ws_ctx(env.ws_a1, WorkspaceRole.VIEWER)
    a1_roleless = _ws_ctx(env.ws_a1, None)
    live = env.sched_live.id
    foreign = env.sched_foreign.id
    return [
        ("workspaceless-create", _nows_ctx(), "POST", "/api/schedules", _create_body()),
        ("workspaceless-update", _nows_ctx(), "PUT", f"/api/schedules/{live}", {"name": "x"}),
        ("workspaceless-delete", _nows_ctx(), "DELETE", f"/api/schedules/{live}", None),
        ("workspaceless-pause", _nows_ctx(), "POST", f"/api/schedules/{live}/pause", None),
        ("workspaceless-resume", _nows_ctx(), "POST", f"/api/schedules/{live}/resume", None),
        ("workspaceless-trigger", _nows_ctx(), "POST", f"/api/schedules/{live}/trigger", None),
        ("roleless-create", a1_roleless, "POST", "/api/schedules", _create_body()),
        ("roleless-update", a1_roleless, "PUT", f"/api/schedules/{live}", {"name": "x"}),
        ("roleless-delete", a1_roleless, "DELETE", f"/api/schedules/{live}", None),
        ("roleless-pause", a1_roleless, "POST", f"/api/schedules/{live}/pause", None),
        ("roleless-resume", a1_roleless, "POST", f"/api/schedules/{live}/resume", None),
        ("roleless-trigger", a1_roleless, "POST", f"/api/schedules/{live}/trigger", None),
        ("viewer-create", a1_viewer, "POST", "/api/schedules", _create_body()),
        ("viewer-create-invalid-cron", a1_viewer, "POST", "/api/schedules", _create_body(cron_expression="not a cron")),
        ("viewer-update", a1_viewer, "PUT", f"/api/schedules/{live}", {"name": "x"}),
        ("viewer-delete", a1_viewer, "DELETE", f"/api/schedules/{live}", None),
        ("viewer-pause", a1_viewer, "POST", f"/api/schedules/{live}/pause", None),
        ("viewer-resume", a1_viewer, "POST", f"/api/schedules/{live}/resume", None),
        ("viewer-trigger", a1_viewer, "POST", f"/api/schedules/{live}/trigger", None),
        ("cross-ws-update", a1_editor, "PUT", f"/api/schedules/{foreign}", {"name": "x"}),
        ("cross-ws-delete", a1_editor, "DELETE", f"/api/schedules/{foreign}", None),
        ("cross-ws-pause", a1_editor, "POST", f"/api/schedules/{foreign}/pause", None),
        ("cross-ws-resume", a1_editor, "POST", f"/api/schedules/{foreign}/resume", None),
        ("cross-ws-trigger", a1_editor, "POST", f"/api/schedules/{foreign}/trigger", None),
        ("cross-ws-executions", a1_editor, "GET", f"/api/schedules/{foreign}/executions", None),
        ("cross-org-update", a1_editor, "PUT", f"/api/schedules/{env.sched_b.id}", {"name": "x"}),
        ("cross-org-trigger", a1_editor, "POST", f"/api/schedules/{env.sched_b.id}/trigger", None),
        ("soft-deleted-update", a1_editor, "PUT", f"/api/schedules/{env.sched_deleted.id}", {"name": "x"}),
        ("soft-deleted-delete", a1_editor, "DELETE", f"/api/schedules/{env.sched_deleted.id}", None),
        ("soft-deleted-trigger", a1_editor, "POST", f"/api/schedules/{env.sched_deleted.id}/trigger", None),
        ("legacy-update", a1_editor, "PUT", f"/api/schedules/{env.sched_legacy.id}", {"name": "x"}),
        ("legacy-trigger", a1_editor, "POST", f"/api/schedules/{env.sched_legacy.id}/trigger", None),
        ("legacy-executions", a1_editor, "GET", f"/api/schedules/{env.sched_legacy.id}/executions", None),
        (
            "missing-update",
            a1_editor,
            "PUT",
            f"/api/schedules/{uuid.uuid4()}",
            {"name": "x"},
        ),
        ("missing-delete", a1_editor, "DELETE", f"/api/schedules/{uuid.uuid4()}", None),
    ]


class TestServiceRevalidation:
    @pytest.mark.parametrize("kind", ["agent", "workflow"])
    async def test_binding_check_ignores_cached_liveness(self, env, db_session, kind):
        model, target_id = (AgentModel, env.agent_a1.id) if kind == "agent" else (WorkflowModel, env.wf_a1.id)
        cached = await db_session.get(model, target_id)
        assert cached is not None and not cached.deleted
        await db_session.execute(
            update(model).where(model.id == target_id).values(deleted=True).execution_options(synchronize_session=False)
        )
        scheduler = StubScheduleManager()
        with pytest.raises(ScheduleTargetError):
            await ScheduledTaskService(db_session, scheduler).create_task(
                workspace_id=env.ws_a1.id,
                name="stale binding",
                cron_expression="0 9 * * *",
                agent_id=target_id if kind == "agent" else None,
                workflow_id=target_id if kind == "workflow" else None,
            )
        assert scheduler.calls == []

    async def test_resume_reloads_the_schedules_actual_binding(self, env, db_session):
        cached = await db_session.get(ScheduledTaskModel, env.sched_paused.id)
        assert cached is not None and cached.agent_id == env.agent_a1.id
        await db_session.execute(
            update(ScheduledTaskModel)
            .where(ScheduledTaskModel.id == cached.id)
            .values(agent_id=env.agent_a2.id)
            .execution_options(synchronize_session=False)
        )
        scheduler = StubScheduleManager()
        with pytest.raises(ScheduleTargetError):
            await ScheduledTaskService(db_session, scheduler).resume_task(cached.id, workspace_id=env.ws_a1.id)
        assert scheduler.calls == []

    async def test_create_rechecks_cached_workspace_liveness(self, env, db_session):
        cached = await db_session.get(WorkspaceModel, env.ws_a1.id)
        assert cached is not None and not cached.deleted
        await db_session.execute(
            update(WorkspaceModel)
            .where(WorkspaceModel.id == cached.id)
            .values(deleted=True)
            .execution_options(synchronize_session=False)
        )
        scheduler = StubScheduleManager()
        with pytest.raises(LookupError):
            await ScheduledTaskService(db_session, scheduler).create_task(
                workspace_id=cached.id,
                name="deleted workspace",
                cron_expression="0 9 * * *",
            )
        assert scheduler.calls == []

    async def test_trusted_internal_call_signatures_remain_compatible(self, env, db_session):
        service = ScheduledTaskService(db_session)
        task = await service.create_task(org_id=env.org_a.id, name="legacy internal", cron_expression="0 9 * * *")
        assert task.workspace_id is None
        assert (await service.get_task(task.id)).id == task.id
        tasks, _ = await service.list_tasks(env.org_a.id)
        assert task.id in {item.id for item in tasks}
        assert (await service.update_task(task.id, name="renamed")).name == "renamed"
        assert (await service.pause_task(task.id)).state == "paused"
        assert (await service.resume_task(task.id)).state == "active"
        assert await service.trigger_manual_run(task.id) is not None
        history, _ = await service.list_executions(task.id)
        assert len(history) == 1
        assert await service.delete_task(task.id)


class TestZeroSideEffectDenials:
    async def test_denials_leave_no_trace(self, env: SimpleNamespace) -> None:
        """Every denial shape (workspaceless, roleless, viewer writes,
        cross-workspace, cross-org, soft-deleted, legacy, missing) ends
        with identical scheduling tables and a silent scheduler."""
        scheduler = StubScheduleManager()
        for label, ctx, method, path, body in _denial_cases(env):
            before = await _snapshot()
            scheduler.calls.clear()
            async with _client_for(ctx, scheduler) as client:
                response = await client.request(method, path, json=body)
            assert response.status_code in (403, 404), f"{label}: {response.status_code} {response.text}"
            assert scheduler.calls == [], f"{label}: scheduler touched"
            assert await _snapshot() == before, f"{label}: rows changed"
