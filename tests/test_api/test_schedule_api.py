"""Tests for schedule API endpoints — real CRUD over the shared app.

The scheduler dependency is stubbed: cron validation and external
registrations are recorded in-process, without APScheduler/croniter
(optional extras) or PostgreSQL advisory locks. These cover the
authorized happy paths on the shared SQLite fixtures; the workspace/
role/ownership denial matrix lives in
``test_schedule_access_boundary.py``.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.organization import OrganizationModel
from hecate.models.scheduled_task import ScheduledTaskModel
from hecate.models.workspace import WorkspaceModel
from tests.conftest import DEFAULT_WORKSPACE_ID


class StubScheduleManager:
    """Scheduler double: validates crons structurally and records
    external registrations instead of touching APScheduler."""

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


@pytest.fixture
async def schedule_client(client: AsyncClient) -> AsyncClient:
    """The shared app client with the scheduler dependency stubbed."""
    from hecate.main import app
    from hecate.ops.api.schedules import get_schedule_manager

    stub = StubScheduleManager()
    app.dependency_overrides[get_schedule_manager] = lambda: stub
    try:
        yield client
    finally:
        app.dependency_overrides.pop(get_schedule_manager, None)


def _create_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"name": "daily-report", "cron_expression": "0 9 * * *"}
    body.update(overrides)
    return body


class TestScheduleLifecycle:
    async def test_create_get_list_delete_lifecycle(
        self,
        schedule_client: AsyncClient,
        default_workspace: WorkspaceModel,
        default_org: OrganizationModel,
    ) -> None:
        """Create binds the caller's live workspace (org attribution from
        the workspace row), then detail/list see it and delete removes it."""
        created = await schedule_client.post("/api/schedules", json=_create_body())
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["workspace_id"] == str(DEFAULT_WORKSPACE_ID)
        assert body["org_id"] == str(default_org.id)
        assert body["state"] == "active"

        detail = await schedule_client.get(f"/api/schedules/{body['id']}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["name"] == "daily-report"

        listed = await schedule_client.get("/api/schedules")
        assert listed.status_code == 200, listed.text
        assert body["id"] in {item["id"] for item in listed.json()["items"]}

        deleted = await schedule_client.delete(f"/api/schedules/{body['id']}")
        assert deleted.status_code == 204

        gone = await schedule_client.get(f"/api/schedules/{body['id']}")
        assert gone.status_code == 404

    async def test_create_rejects_invalid_cron(self, schedule_client: AsyncClient) -> None:
        response = await schedule_client.post("/api/schedules", json=_create_body(cron_expression="not-a-cron"))
        assert response.status_code == 422, response.text
        assert response.json()["detail"]["error"]["code"] == "INVALID_CRON"

    async def test_update_recalculates_next_run(self, schedule_client: AsyncClient) -> None:
        created = await schedule_client.post("/api/schedules", json=_create_body())
        assert created.status_code == 201, created.text
        task_id = created.json()["id"]

        updated = await schedule_client.put(
            f"/api/schedules/{task_id}",
            json={"name": "renamed", "cron_expression": "*/5 * * * *"},
        )
        assert updated.status_code == 200, updated.text
        body = updated.json()
        assert body["name"] == "renamed"
        assert body["cron_expression"] == "*/5 * * * *"
        assert body["next_run_at"] is not None

    async def test_pause_resume_lifecycle(self, schedule_client: AsyncClient) -> None:
        created = await schedule_client.post("/api/schedules", json=_create_body())
        assert created.status_code == 201, created.text
        task_id = created.json()["id"]

        paused = await schedule_client.post(f"/api/schedules/{task_id}/pause")
        assert paused.status_code == 200, paused.text
        assert paused.json()["state"] == "paused"

        resumed = await schedule_client.post(f"/api/schedules/{task_id}/resume")
        assert resumed.status_code == 200, resumed.text
        assert resumed.json()["state"] == "active"

    async def test_manual_trigger_records_execution_entry(self, schedule_client: AsyncClient) -> None:
        """The trigger endpoint writes a record entry (bookkeeping); by
        current design it does not certify that the execution ran."""
        created = await schedule_client.post("/api/schedules", json=_create_body())
        assert created.status_code == 201, created.text
        task_id = created.json()["id"]

        triggered = await schedule_client.post(f"/api/schedules/{task_id}/trigger")
        assert triggered.status_code == 200, triggered.text
        body = triggered.json()
        assert body["triggered_by"] == "manual"
        assert body["result_summary"] == {"manual_trigger": True}

        history = await schedule_client.get(f"/api/schedules/{task_id}/executions")
        assert history.status_code == 200, history.text
        assert history.json()["total"] == 1
        assert history.json()["items"][0]["triggered_by"] == "manual"

    async def test_execution_history_paginated(self, schedule_client: AsyncClient, db_session: AsyncSession) -> None:
        from hecate.models.scheduled_task import ScheduledTaskExecutionModel

        created = await schedule_client.post("/api/schedules", json=_create_body())
        assert created.status_code == 201, created.text
        task_id = uuid.UUID(created.json()["id"])

        db_session.add_all(
            [
                ScheduledTaskExecutionModel(task_id=task_id, status="success", triggered_by="cron"),
                ScheduledTaskExecutionModel(task_id=task_id, status="failed", triggered_by="cron"),
                ScheduledTaskExecutionModel(task_id=task_id, status="success", triggered_by="cron"),
            ]
        )
        await db_session.flush()

        page_one = await schedule_client.get(f"/api/schedules/{task_id}/executions", params={"page_size": 2})
        assert page_one.status_code == 200, page_one.text
        assert page_one.json()["total"] == 3
        assert len(page_one.json()["items"]) == 2

        page_two = await schedule_client.get(f"/api/schedules/{task_id}/executions", params={"page": 2, "page_size": 2})
        assert page_two.status_code == 200, page_two.text
        assert len(page_two.json()["items"]) == 1
