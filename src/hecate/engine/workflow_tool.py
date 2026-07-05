"""Workflow-as-Tool capability for agent invocation of workflows.

Provides WorkflowTool (callable tool wrapper) analogous to AgentTool.
WorkflowTool allows an agent to invoke a workflow as a tool with
input/output schema generation from the workflow's Start Node.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from uuid import UUID

logger = logging.getLogger(__name__)


class WorkflowTool:
    """Wraps a workflow as a callable tool for LLM invocation.

    When registered in an agent's tool list, the LLM can invoke
    this tool to execute the workflow with the provided input.
    """

    def __init__(
        self,
        workflow_id: UUID,
        name: str,
        description: str,
        parameters: dict[str, Any] | None = None,
    ) -> None:
        """Initialize the workflow tool.

        Args:
            workflow_id: UUID of the workflow to execute.
            name: Tool name visible to the LLM.
            description: Tool description visible to the LLM.
            parameters: JSON Schema for the workflow's input variables.
        """
        self._workflow_id = workflow_id
        self._name = name
        self._description = description
        self._parameters = parameters or {"type": "object", "properties": {}}

    @property
    def name(self) -> str:
        """Tool name derived from workflow name."""
        return self._name

    @property
    def description(self) -> str:
        """Tool description from the workflow."""
        return self._description

    @property
    def parameters(self) -> dict[str, Any]:
        """JSON Schema for the workflow's input parameters."""
        return self._parameters

    async def execute(
        self,
        args: dict[str, Any],
        port: Any,
        channel_snapshot: dict[str, Any] | None = None,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        """Execute the workflow via EnginePort.workflow_execute().

        Args:
            args: Tool arguments (workflow input data).
            port: EnginePort-like object with workflow_execute().
            channel_snapshot: Current channel state (for context).
            timeout_seconds: Maximum execution time in seconds.

        Returns:
            The workflow execution result, or an error dict on timeout.
        """
        coro = port.workflow_execute(
            workflow_id=self._workflow_id,
            input_data=args,
            context={"channel_snapshot": channel_snapshot or {}},
        )

        try:
            if timeout_seconds is not None:
                result = await asyncio.wait_for(coro, timeout=timeout_seconds)
            else:
                result = await coro
        except TimeoutError:
            return {"error": "Workflow execution timed out", "timed_out": True}
        except Exception as e:
            logger.exception("Workflow execution failed")
            return {"error": f"Workflow execution failed: {e!s}"}

        return result

    def to_tool_schema(self) -> dict[str, Any]:
        """Generate tool schema for LLM registration.

        Returns:
            Tool schema dict with name, description, and parameters.
        """
        return {
            "name": self._name,
            "description": self._description,
            "parameters": self._parameters,
        }
