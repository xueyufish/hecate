"""Scheduler dispatch wiring tests (step6 platform track, step5 tail).

``ScheduleManager._execute_task`` must route cron triggers through the
executor registry and record the executor's real result — success and
failure alike. The executor is stubbed (the real agent/workflow
executors have their own suites); the manager's dispatch, concurrency
gate, and execution-record mapping run for real.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import hecate.models.scheduled_task  # noqa: F401  (metadata registration)
import hecate.ops.scheduling.manager as manager_module
import tests.conftest  # noqa: F401  (registers the full model set)
from hecate.core.database import Base
from hecate.models.scheduled_task import (
    ExecutionStatus,
    ScheduledTaskExecutionModel,
    ScheduledTaskModel,
    ScheduleState,
)
from hecate.ops.scheduling.executors import ExecutorRegistry, TaskExecutor
from hecate.ops.scheduling.manager import ScheduleManager, set_executor_registry


class RecordingExecutor(TaskExecutor):
    """Stub executor capturing dispatch arguments."""

    def __init__(self, outcome: dict[str, Any]) -> None:
        self.outcome = outcome
        self.calls: list[dict[str, Any]] = []

    async def execute(
        self,
        task_id: uuid.UUID,
        task_config: dict[str, Any],
        *,
        workspace_id: uuid.UUID | None = None,
        agent_id: uuid.UUID | str | None = None,
        workflow_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "task_id": task_id,
                "task_config": task_config,
                "workspace_id": workspace_id,
                "agent_id": agent_id,
                "workflow_id": workflow_id,
            }
        )
        return self.outcome


@pytest.fixture
async def dispatch_db():
    """Isolated engine whose session factory the manager is pointed at."""

    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    original = manager_module.async_session_factory
    manager_module.async_session_factory = factory
    try:
        yield factory
    finally:
        manager_module.async_session_factory = original
    await engine.dispose()


@pytest.fixture(autouse=True)
def _restore_registry():
    yield
    set_executor_registry(None)


async def _seed_task(
    factory: async_sessionmaker[AsyncSession], *, agent_id: uuid.UUID | None, workflow_id=None, config=None
) -> uuid.UUID:
    async with factory() as db:
        task = ScheduledTaskModel(
            org_id=uuid.UUID(int=1),
            workspace_id=uuid.UUID(int=2),
            name=f"cron-{uuid.uuid4().hex[:6]}",
            cron_expression="*/5 * * * *",
            agent_id=agent_id,
            workflow_id=workflow_id,
            execution_config=config or {},
            state=ScheduleState.ACTIVE.value,
            enabled=True,
        )
        db.add(task)
        await db.commit()
        return task.id


async def _execution_row(factory, task_id: uuid.UUID) -> ScheduledTaskExecutionModel:
    async with factory() as db:
        row = (
            (
                await db.execute(
                    select(ScheduledTaskExecutionModel)
                    .where(ScheduledTaskExecutionModel.task_id == task_id)
                    .order_by(ScheduledTaskExecutionModel.created_at.desc())
                )
            )
            .scalars()
            .first()
        )
        assert row is not None
        return row


async def test_dispatch_runs_agent_executor_and_records_success(dispatch_db):
    agent_id = uuid.uuid4()
    executor = RecordingExecutor({"status": "success", "result": "done"})
    registry = ExecutorRegistry()
    registry.register("agent", executor)
    set_executor_registry(registry)

    task_id = await _seed_task(dispatch_db, agent_id=agent_id, config={"message": "hello"})
    await ScheduleManager()._execute_task(task_id)

    assert len(executor.calls) == 1
    call = executor.calls[0]
    assert call["task_id"] == task_id
    assert call["agent_id"] == agent_id
    assert call["task_config"] == {"message": "hello"}

    row = await _execution_row(dispatch_db, task_id)
    assert row.status == ExecutionStatus.SUCCESS.value
    assert row.result_summary == {"status": "success", "result": "done"}


async def test_dispatch_maps_executor_failure(dispatch_db):
    executor = RecordingExecutor({"status": "failed", "error": "agent missing"})
    registry = ExecutorRegistry()
    registry.register("agent", executor)
    set_executor_registry(registry)

    task_id = await _seed_task(dispatch_db, agent_id=uuid.uuid4())
    await ScheduleManager()._execute_task(task_id)

    row = await _execution_row(dispatch_db, task_id)
    assert row.status == ExecutionStatus.FAILED.value
    assert "agent missing" in (row.error_message or "")


async def test_dispatch_without_target_fails_explicitly(dispatch_db):
    executor = RecordingExecutor({"status": "success"})
    registry = ExecutorRegistry()
    registry.register("agent", executor)
    set_executor_registry(registry)

    task_id = await _seed_task(dispatch_db, agent_id=None, workflow_id=None)
    await ScheduleManager()._execute_task(task_id)

    row = await _execution_row(dispatch_db, task_id)
    assert row.status == ExecutionStatus.FAILED.value
    assert "neither agent_id nor workflow_id" in (row.error_message or "")
    assert executor.calls == []


async def test_dispatch_without_executor_fails_explicitly(dispatch_db):
    set_executor_registry(ExecutorRegistry())  # nothing registered
    task_id = await _seed_task(dispatch_db, agent_id=uuid.uuid4())
    await ScheduleManager()._execute_task(task_id)

    row = await _execution_row(dispatch_db, task_id)
    assert row.status == ExecutionStatus.FAILED.value
    assert "no executor registered" in (row.error_message or "")
