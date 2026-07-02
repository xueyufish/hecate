"""Tests for scheduling executors — registry, agent, workflow."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

from hecate.services.scheduling.executors import (
    AgentExecutor,
    ExecutorRegistry,
    WorkflowExecutor,
    create_default_registry,
)


class TestExecutorRegistry:
    def test_register_and_get(self) -> None:
        registry = ExecutorRegistry()
        executor = AgentExecutor()
        registry.register("agent", executor)
        assert registry.get("agent") is executor

    def test_get_unknown_type_returns_none(self) -> None:
        registry = ExecutorRegistry()
        assert registry.get("unknown") is None

    def test_registered_types(self) -> None:
        registry = create_default_registry()
        types = registry.registered_types
        assert "agent" in types
        assert "workflow" in types

    def test_create_default_registry(self) -> None:
        registry = create_default_registry()
        assert isinstance(registry.get("agent"), AgentExecutor)
        assert isinstance(registry.get("workflow"), WorkflowExecutor)


class TestAgentExecutor:
    async def test_missing_agent_id(self) -> None:
        executor = AgentExecutor()
        result = await executor.execute(uuid.uuid4(), {})
        assert result["status"] == "failed"
        assert "agent_id" in result["error"]

    async def test_service_failure(self) -> None:
        executor = AgentExecutor()
        agent_id = str(uuid.uuid4())
        task_id = uuid.uuid4()

        with patch(
            "hecate.services.scheduling.executors.AgentService",
            create=True,
        ) as mock_service_cls:
            mock_instance = AsyncMock()
            mock_instance.chat = AsyncMock(side_effect=RuntimeError("Service error"))
            mock_service_cls.return_value = mock_instance

            result = await executor.execute(task_id, {"agent_id": agent_id, "message": "hello"})
            assert result["status"] == "failed"


class TestWorkflowExecutor:
    async def test_missing_workflow_id(self) -> None:
        executor = WorkflowExecutor()
        result = await executor.execute(uuid.uuid4(), {})
        assert result["status"] == "failed"
        assert "workflow_id" in result["error"]

    async def test_service_failure(self) -> None:
        executor = WorkflowExecutor()
        workflow_id = str(uuid.uuid4())
        task_id = uuid.uuid4()

        with patch(
            "hecate.services.scheduling.executors.WorkflowService",
            create=True,
        ) as mock_service_cls:
            mock_instance = AsyncMock()
            mock_instance.execute = AsyncMock(side_effect=RuntimeError("Service error"))
            mock_service_cls.return_value = mock_instance

            result = await executor.execute(task_id, {"workflow_id": workflow_id})
            assert result["status"] == "failed"
