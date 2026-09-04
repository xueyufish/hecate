"""Task executors for scheduled agent and workflow runs.

Provides:

- :class:`TaskExecutor` — ABC for scheduled task execution
- :class:`AgentExecutor` — runs an agent via a one-shot LLM call
- :class:`WorkflowExecutor` — runs a workflow via WorkflowService
- :class:`ExecutorRegistry` — maps task_type strings to executor instances
"""

from __future__ import annotations

import logging
import uuid
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger(__name__)


class TaskExecutor(ABC):
    """Abstract base class for scheduled task execution.

    Subclasses implement the actual execution logic for a specific
    task type (agent, workflow, etc.).
    """

    @abstractmethod
    async def execute(self, task_id: uuid.UUID, task_config: dict[str, Any]) -> dict[str, Any]:
        """Execute a scheduled task.

        Args:
            task_id: UUID of the scheduled task.
            task_config: Task configuration (agent_id, input params, etc.).

        Returns:
            Dict with execution result (at minimum ``{"status": "success"}``).
        """


class AgentExecutor(TaskExecutor):
    """Execute a scheduled agent run.

    Runs a one-shot LLM call with the agent's persona and model
    (same pattern as ``channel/a2a/server/executor.py``): load the agent
    row, call ``llm_service`` directly. The pre-Phase-R ``AgentService``
    chat path no longer exists; full graph-engine wiring
    (WorkflowExecutionService + RuntimePort) needs request-scoped handles
    a background scheduler does not have.

    Expected task_config keys:

    - ``agent_id`` (str): UUID of the agent to run.
    - ``message`` (str): User message to send.
    """

    async def execute(self, task_id: uuid.UUID, task_config: dict[str, Any]) -> dict[str, Any]:
        """Run an agent with the configured message."""
        agent_id = task_config.get("agent_id")
        message = task_config.get("message", "")

        if not agent_id:
            return {"status": "failed", "error": "Missing agent_id in task_config"}

        try:
            from hecate_llm.service import llm_service
            from sqlalchemy import select

            from hecate.core.database import async_session_factory
            from hecate.models.agent import AgentModel

            async with async_session_factory() as db:
                result = await db.execute(select(AgentModel).where(AgentModel.id == uuid.UUID(agent_id)))
                agent = result.scalar_one_or_none()
            if agent is None:
                return {"status": "failed", "error": f"Agent {agent_id} not found"}

            model_name = (
                agent.model_config_db.get("model", "gpt-4o") if isinstance(agent.model_config_db, dict) else "gpt-4o"
            )
            messages = [
                {"role": "system", "content": agent.persona or "You are a helpful assistant."},
                {"role": "user", "content": message},
            ]
            llm_result = await llm_service.chat(messages, model=model_name)
            logger.info("AgentExecutor completed for task %s agent %s", task_id, agent_id)
            return {"status": "success", "result": llm_result.content or ""}
        except Exception as e:
            logger.error("AgentExecutor failed for task %s: %s", task_id, e)
            return {"status": "failed", "error": str(e)}


class WorkflowExecutor(TaskExecutor):
    """Execute a scheduled workflow run.

    Runs the workflow through ``WorkflowTestRunner`` — the same
    workflow-level execution entry the management ``test-run`` endpoint
    uses — with ``mock=False`` for a real run. The pre-Phase-R
    ``WorkflowService.execute(workflow_id, ...)`` no longer exists.

    Expected task_config keys:

    - ``workflow_id`` (str): UUID of the workflow to run.
    - ``input_data`` (dict, optional): Input parameters for the workflow.
    """

    async def execute(self, task_id: uuid.UUID, task_config: dict[str, Any]) -> dict[str, Any]:
        """Run a workflow with the configured input."""
        workflow_id = task_config.get("workflow_id")

        if not workflow_id:
            return {"status": "failed", "error": "Missing workflow_id in task_config"}

        try:
            from hecate.core.database import async_session_factory
            from hecate.studio.workflows.test_runner import WorkflowTestRunner

            async with async_session_factory() as db:
                runner = WorkflowTestRunner(db)
                result = await runner.run_test(
                    workflow_id=uuid.UUID(workflow_id),
                    input_data=task_config.get("input_data", {}),
                    mock=False,
                )
            status = "success" if result.status == "completed" else "failed"
            logger.info(
                "WorkflowExecutor completed for task %s workflow %s status %s",
                task_id,
                workflow_id,
                result.status,
            )
            return {
                "status": status,
                "result": {
                    "run_id": str(result.run_id),
                    "workflow_status": result.status,
                    "total_duration_ms": result.total_duration_ms,
                    "error": result.error,
                },
            }
        except Exception as e:
            logger.error("WorkflowExecutor failed for task %s: %s", task_id, e)
            return {"status": "failed", "error": str(e)}


class ExecutorRegistry:
    """Registry mapping task_type strings to executor instances.

    Usage::

        registry = ExecutorRegistry()
        registry.register("agent", AgentExecutor())
        registry.register("workflow", WorkflowExecutor())

        executor = registry.get("agent")
        result = await executor.execute(task_id, config)
    """

    def __init__(self) -> None:
        self._executors: dict[str, TaskExecutor] = {}

    def register(self, task_type: str, executor: TaskExecutor) -> None:
        """Register an executor for a task type."""
        self._executors[task_type] = executor

    def get(self, task_type: str) -> TaskExecutor | None:
        """Return the executor for the given task type, or None."""
        return self._executors.get(task_type)

    @property
    def registered_types(self) -> list[str]:
        """Return all registered task types."""
        return list(self._executors.keys())


def create_default_registry() -> ExecutorRegistry:
    """Create an ExecutorRegistry with built-in executors registered."""
    registry = ExecutorRegistry()
    registry.register("agent", AgentExecutor())
    registry.register("workflow", WorkflowExecutor())
    return registry
