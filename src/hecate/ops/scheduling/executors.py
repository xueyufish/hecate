"""Task executors for scheduled agent and workflow runs.

Provides:

- :class:`TaskExecutor` — ABC for scheduled task execution
- :class:`AgentExecutor` — runs an agent through the platform entry
  execution service (same assembly and Task/Run correlation as the
  HTTP/MCP/IM/A2A entries)
- :class:`WorkflowExecutor` — runs a scheduled workflow through the
  platform entry execution service (step5d closure)
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
    async def execute(
        self,
        task_id: uuid.UUID,
        task_config: dict[str, Any],
        *,
        workspace_id: uuid.UUID | None = None,
        agent_id: uuid.UUID | str | None = None,
        workflow_id: uuid.UUID | str | None = None,
    ) -> dict[str, Any]:
        """Execute a scheduled task.

        Args:
            task_id: UUID of the scheduled task.
            task_config: Task configuration (agent_id, input params, etc.).
            workspace_id: Workspace of the scheduled-task row, when the
                caller has it — the authoritative workspace attribution
                (overrides ``task_config``-derived values).
            agent_id: Agent binding of the scheduled-task row, if any.
            workflow_id: Workflow binding of the scheduled-task row, if any.

        Returns:
            Dict with execution result (at minimum ``{"status": "success"}``).
        """


class AgentExecutor(TaskExecutor):
    """Execute a scheduled agent run through the platform entry service.

    The executor opens its own session (a background scheduler has no
    request context), resolves the agent row, and delegates to
    ``EntryExecutionService`` — the same entry service the HTTP chat,
    MCP, IM and A2A chains use — so the run gets the shared assembly
    (agent tools, guardrail bundle, event store) and Task/Run
    correlation. There is no direct LLM call here.

    Expected task_config keys:

    - ``agent_id`` (str): UUID of the agent to run (fallback when the
      executor is called without the row-context ``agent_id`` argument).
    - ``message`` (str): User message to send.
    """

    async def execute(
        self,
        task_id: uuid.UUID,
        task_config: dict[str, Any],
        *,
        workspace_id: uuid.UUID | None = None,
        agent_id: uuid.UUID | str | None = None,
        workflow_id: uuid.UUID | str | None = None,
    ) -> dict[str, Any]:
        """Run an agent with the configured message."""
        del workflow_id  # agent executor has no workflow binding
        resolved_agent_id = agent_id or task_config.get("agent_id")
        message = task_config.get("message", "")

        if not resolved_agent_id:
            return {"status": "failed", "error": "Missing agent_id in task_config"}

        try:
            from sqlalchemy import select

            from hecate.core.database import async_session_factory
            from hecate.models.agent import AgentModel

            agent_uuid = (
                resolved_agent_id if isinstance(resolved_agent_id, uuid.UUID) else uuid.UUID(str(resolved_agent_id))
            )
            async with async_session_factory() as db:
                result = await db.execute(select(AgentModel).where(AgentModel.id == agent_uuid))
                agent = result.scalar_one_or_none()
                if agent is None:
                    return {"status": "failed", "error": f"Agent {resolved_agent_id} not found"}

                content = await self._run_via_entry_service(db, agent, message, workspace_id=workspace_id)
                # Commit so the Task/Run correlation rows written on this
                # session survive past its close (fail-open: a correlation
                # failure never blocks the execution result).
                await db.commit()

            logger.info("AgentExecutor completed for task %s agent %s", task_id, agent_uuid)
            return {"status": "success", "result": content}
        except Exception as e:
            logger.error("AgentExecutor failed for task %s: %s", task_id, e)
            return {"status": "failed", "error": str(e)}

    async def _run_via_entry_service(
        self,
        db: Any,
        agent: Any,
        message: str,
        *,
        workspace_id: uuid.UUID | None,
    ) -> str:
        """Run one execution through the platform entry service."""
        from hecate_llm.service import llm_service

        from hecate.core.composition.entry_assembly import (
            build_tool_registry,
            get_shared_event_store,
            load_agent_tools,
        )
        from hecate.core.composition.guardrail_platform import assemble_guardrails
        from hecate.core.composition.runtime_port_adapter import create_runtime_port
        from hecate.execution.entry_service import CorrelationInput, EntryExecutionService

        effective_workspace = workspace_id or agent.workspace_id
        session_id = uuid.uuid4()

        tool_registry = None
        effective_tools: list[dict[str, Any]] = []
        bundle = None
        event_store = None
        if agent.tools:
            event_store = get_shared_event_store()
            tool_registry = build_tool_registry(
                db, skill_ref_manifest=getattr(agent, "_resolved_ref_manifest", None), workspace_id=agent.workspace_id
            )
            effective_tools = await load_agent_tools(db, agent.tools or [], workspace_id=agent.workspace_id)
            if effective_tools:
                bundle = await assemble_guardrails(
                    db,
                    workspace_id=effective_workspace,
                    agent_id=agent.id,
                    guardrail_config=getattr(agent, "guardrail_config", None),
                    event_store=event_store,
                    session_id=session_id,
                    dlp_scanner=None,
                )

        model_name = (
            agent.model_config_db.get("model", "gpt-4o") if isinstance(agent.model_config_db, dict) else "gpt-4o"
        )
        port = create_runtime_port(
            db,
            llm_service,
            tool_registry=tool_registry,
            workspace_id=effective_workspace,
            agent_id=agent.id,
        )
        entry = EntryExecutionService(
            port=port,
            entry_name="scheduled-agent",
            db=db,
            event_store=event_store,
            access_policy=bundle.access_policy if bundle else None,
            approval_callback=bundle.approval_callback if bundle else None,
            tool_policy_rules=bundle.rules if bundle else None,
            middleware_chains=bundle.middleware_chains if bundle else None,
            denial_tracker=bundle.denial_tracker if bundle else None,
        )
        correlation = CorrelationInput(
            workspace_id=effective_workspace,
            agent_id=agent.id,
            user_id=None,
            session_id=session_id,
            goal=message[:200] or None,
        )
        # agent_id makes the execution service resolve persona/skills from
        # the live agent row; model and tools are caller-resolved inputs.
        outcome = await entry.execute(
            session_id=session_id,
            agent_mode="chat",
            messages=[{"role": "user", "content": message}],
            model=model_name,
            tools=effective_tools or None,
            stream=False,
            agent_id=agent.id,
            workspace_id=effective_workspace,
            correlation=correlation,
        )
        if not outcome.correlated:
            logger.warning("Scheduled agent entry correlation missing (%s)", outcome.correlation.reason)
        result = outcome.result
        if not isinstance(result, dict):
            msg = f"Expected dict result for scheduled agent execution, got {type(result)}"
            raise TypeError(msg)
        return result.get("content", "") or ""


class WorkflowExecutor(TaskExecutor):
    """Execute a scheduled workflow run through the platform entry path.

    Uses the shared entry service and preserves explicit workflow input
    channels. A standalone workflow currently has no deployment identity,
    so correlation records a Task and explicitly reports the missing Run.
    The studio runner remains dedicated to its test endpoint. The returned
    dict is bound to ``ScheduledTaskExecution.result_summary``.

    Expected task_config keys:

    - ``workflow_id`` (str): UUID of the workflow to run.
    - ``input_data`` (dict, optional): Input parameters for the workflow.
    """

    async def execute(
        self,
        task_id: uuid.UUID,
        task_config: dict[str, Any],
        *,
        workspace_id: uuid.UUID | None = None,
        agent_id: uuid.UUID | str | None = None,
        workflow_id: uuid.UUID | str | None = None,
    ) -> dict[str, Any]:
        """Run a workflow with the configured input."""
        del agent_id
        resolved_workflow_id = workflow_id or task_config.get("workflow_id")

        if not resolved_workflow_id:
            return {"status": "failed", "error": "Missing workflow_id in task_config"}

        try:
            from hecate_llm.service import llm_service

            from hecate.core.composition.entry_assembly import (
                build_tool_registry,
                get_shared_event_store,
                get_shared_session_state_store,
            )
            from hecate.core.composition.runtime_port_adapter import create_runtime_port
            from hecate.core.database import async_session_factory
            from hecate.execution.entry_service import CorrelationInput, EntryExecutionService
            from hecate.models.workflow import WorkflowModel

            async with async_session_factory() as db:
                workflow_uuid = uuid.UUID(str(resolved_workflow_id))
                workflow = await db.get(WorkflowModel, workflow_uuid)
                if (
                    workflow is None
                    or workflow.deleted
                    or (workspace_id is not None and workflow.workspace_id != workspace_id)
                ):
                    return {"status": "failed", "error": f"Workflow {resolved_workflow_id} not found"}

                port = create_runtime_port(
                    db,
                    llm_service,
                    tool_registry=build_tool_registry(db, workspace_id=workflow.workspace_id),
                    workspace_id=workflow.workspace_id,
                    agent_id=None,
                )
                entry = EntryExecutionService(
                    port=port,
                    entry_name="scheduled-workflow",
                    db=db,
                    event_store=get_shared_event_store(),
                    checkpoint_store=get_shared_session_state_store(),
                )
                input_data = task_config.get("input_data") or {}
                outcome = await entry.execute(
                    agent_mode="workflow",
                    workflow_id=workflow_uuid,
                    workspace_id=workflow.workspace_id,
                    session_id=uuid.uuid4(),
                    input_data=input_data,
                    stream=False,
                    correlation=CorrelationInput(
                        workspace_id=workflow.workspace_id,
                        goal=f"scheduled workflow {workflow.name}".strip()[:200] or None,
                    ),
                )
                # Commit so the Task/Run correlation rows and the terminal
                # projection written on this session survive its close.
                await db.commit()

            result = outcome.result if isinstance(outcome.result, dict) else {}
            status = "success" if result.get("finish_reason") == "stop" else "failed"
            logger.info(
                "WorkflowExecutor completed for task %s workflow %s status %s",
                task_id,
                resolved_workflow_id,
                status,
            )
            return {
                "status": status,
                "result": {
                    "content": str(result.get("content", "") or "")[:2000],
                    "finish_reason": result.get("finish_reason"),
                    "correlation": outcome.correlation.to_dict(),
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
