"""Agent-as-Tool capability for controlled sub-agent invocation.

Provides AgentDefinition (per-invocation configuration) and AgentTool
(callable tool wrapper). AgentTool allows an agent to invoke another
agent as a tool with fine-grained permission control including tool
whitelist/blacklist, context isolation, model override, and timeouts.

Design follows Deer-flow's SubagentConfig pattern (whitelist + blacklist
dual-track) for production-validated permission scoping.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import UUID

logger = logging.getLogger(__name__)


@dataclass
class AgentDefinition:
    """Per-invocation configuration for an agent-as-tool call.

    Specifies how a target agent should be invoked, including permission
    scoping (tool whitelist/blacklist), context mode, model override,
    and execution limits. Each invocation can use a different AgentDefinition
    for the same target agent.

    Attributes:
        agent_id: The target agent's unique identifier.
        description: Tool description visible to the calling LLM.
        prompt_override: Optional system prompt override for this invocation.
        tools: Whitelist of allowed tool names. None = inherit all tools.
        disallowed_tools: Blacklist of denied tool names (applied after whitelist).
        skills: Whitelist of skill names. None = inherit all skills.
        model_override: Optional model name override.
        context_mode: "inherited" shares parent messages; "isolated" starts fresh.
        max_turns: Maximum execution turns for the sub-agent.
        timeout_seconds: Maximum execution time in seconds.
    """

    agent_id: UUID
    description: str
    prompt_override: str | None = None
    tools: list[str] | None = None
    disallowed_tools: list[str] = field(default_factory=lambda: ["agent_execute"])
    skills: list[str] | None = None
    model_override: str | None = None
    context_mode: Literal["inherited", "isolated"] = "inherited"
    max_turns: int | None = None
    timeout_seconds: float | None = None


class AgentTool:
    """Wraps an AgentDefinition as a callable tool for LLM invocation.

    When registered in a parent agent's tool list, the LLM can invoke
    this tool to delegate work to the target agent with the permissions
    and configuration specified in the AgentDefinition.
    """

    def __init__(self, definition: AgentDefinition, agent_name: str = "") -> None:
        """Initialize the agent tool.

        Args:
            definition: The per-invocation configuration.
            agent_name: Optional human-readable agent name for tool naming.
        """
        self._definition = definition
        self._agent_name = agent_name

    @property
    def name(self) -> str:
        """Tool name derived from agent_name or agent_id."""
        if self._agent_name:
            return f"agent_{self._agent_name}"
        return f"agent_{self._definition.agent_id.hex[:8]}"

    @property
    def description(self) -> str:
        """Tool description from the AgentDefinition."""
        return self._definition.description

    @property
    def definition(self) -> AgentDefinition:
        """The underlying AgentDefinition."""
        return self._definition

    def resolve_tools(self, parent_tools: list[str] | None = None) -> list[str]:
        """Resolve the effective tool list using whitelist/blacklist rules.

        Resolution order:
        1. If tools is not None: use whitelist as base.
        2. If tools is None: inherit parent_tools (or [] if None).
        3. Remove all tools in disallowed_tools from the base.

        Args:
            parent_tools: The parent agent's tool list (for inheritance).

        Returns:
            The filtered list of allowed tool names.
        """
        if self._definition.tools is not None:
            base = list(self._definition.tools)
        else:
            base = list(parent_tools) if parent_tools is not None else []

        disallowed_set = set(self._definition.disallowed_tools)
        return [t for t in base if t not in disallowed_set]

    async def execute(
        self,
        args: dict[str, Any],
        port: Any,
        channel_snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute the agent-as-tool invocation.

        Args:
            args: Tool arguments (expects "task" or "input" key).
            port: EnginePort-like object with agent_execute().
            channel_snapshot: Current channel state (for inherited context).

        Returns:
            The agent execution result, or an error dict on timeout.
        """
        task = args.get("task", args.get("input", ""))
        snapshot = channel_snapshot or {}

        messages = self._build_messages(task, snapshot)
        context = self._build_context()

        coro = port.agent_execute(
            agent_id=self._definition.agent_id,
            messages=messages,
            channel_snapshot=snapshot,
            context=context,
            agent_definition=self._definition,
        )

        try:
            if self._definition.timeout_seconds is not None:
                result = await asyncio.wait_for(coro, timeout=self._definition.timeout_seconds)
            else:
                result = await coro
        except TimeoutError:
            return {"error": "Agent execution timed out", "timed_out": True}

        return result

    def _build_messages(
        self,
        task: str,
        channel_snapshot: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Build the message list based on context_mode."""
        if self._definition.context_mode == "isolated":
            return [{"role": "user", "content": task}]

        inherited = list(channel_snapshot.get("messages", []))
        inherited.append({"role": "user", "content": task})
        return inherited

    def _build_context(self) -> dict[str, Any]:
        """Build the context dict for agent_execute."""
        ctx: dict[str, Any] = {}
        if self._definition.prompt_override is not None:
            ctx["prompt_override"] = self._definition.prompt_override
        if self._definition.model_override is not None:
            ctx["model_override"] = self._definition.model_override
        if self._definition.max_turns is not None:
            ctx["max_turns"] = self._definition.max_turns
        if self._definition.skills is not None:
            ctx["skills"] = self._definition.skills
        ctx["agent_definition"] = self._definition
        return ctx

    async def execute_remote(
        self,
        args: dict[str, Any],
        remote_url: str,
        api_key: str | None = None,
    ) -> dict[str, Any]:
        """Execute against a remote A2A agent.

        Args:
            args: Tool arguments (expects "task" or "input" key).
            remote_url: URL of the remote A2A agent.
            api_key: Optional API key for authentication.

        Returns:
            The remote agent's response, or an error dict.
        """
        from hecate.a2a.client.client import A2AClient
        from hecate.a2a.types import Message

        task = args.get("task", args.get("input", ""))
        message = Message(role="user", parts=[{"text": task}])

        try:
            client = A2AClient(agent_url=remote_url, api_key=api_key)
            result_task = await client.send_message(message)
            response_text = ""
            if result_task.status.message and result_task.status.message.parts:
                response_text = result_task.status.message.parts[0].get("text", "")
            return {"response": response_text}
        except Exception as e:
            logger.exception("Remote A2A agent execution failed")
            return {"error": f"Remote agent failed: {e!s}"}

    async def execute_workflow(
        self,
        args: dict[str, Any],
        port: Any,
        workflow_id: Any,
    ) -> dict[str, Any]:
        """Execute a workflow as a tool.

        Args:
            args: Tool arguments (workflow input data).
            port: EnginePort-like object with workflow_execute().
            workflow_id: UUID of the workflow to execute.

        Returns:
            The workflow execution result, or an error dict.
        """
        from hecate.engine.workflow_tool import WorkflowTool

        tool = WorkflowTool(
            workflow_id=workflow_id,
            name=self._agent_name or f"workflow_{workflow_id}",
            description=self._definition.description,
        )
        return await tool.execute(args, port)
