"""Task executors for scheduled agent and workflow runs.

Provides:

- :class:`TaskExecutor` — ABC for scheduled task execution
- :class:`AgentExecutor` — runs an agent via AgentService
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

    Uses ``AgentService`` to create a conversation and send a message
    based on the task configuration.

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
            from hecate.services.agent.service import AgentService

            service = AgentService()
            result = await service.chat(
                agent_id=uuid.UUID(agent_id),
                message=message,
            )
            logger.info("AgentExecutor completed for task %s agent %s", task_id, agent_id)
            return {"status": "success", "result": result}
        except Exception as e:
            logger.error("AgentExecutor failed for task %s: %s", task_id, e)
            return {"status": "failed", "error": str(e)}


class WorkflowExecutor(TaskExecutor):
    """Execute a scheduled workflow run.

    Uses ``WorkflowService`` to trigger a workflow execution.

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
            from hecate.services.workflow.service import WorkflowService

            service = WorkflowService()
            result = await service.execute(
                workflow_id=uuid.UUID(workflow_id),
                input_data=task_config.get("input_data", {}),
            )
            logger.info("WorkflowExecutor completed for task %s workflow %s", task_id, workflow_id)
            return {"status": "success", "result": result}
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
