"""Agent worker for executing AGENT-type nodes.

Supports two invocation modes:
- ``direct`` (default): Execute the agent inline via nested graph execution
  or port-based agent_execute.
- ``tool``: Expose the agent as a callable tool via AgentDefinition for
  hierarchical delegation. The parent LLM can invoke the agent as a tool.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from hecate.engine.agent_tool import AgentDefinition, AgentTool
from hecate.engine.types import Command, WorkerResult
from hecate.engine.worker import Worker

logger = logging.getLogger(__name__)


class AgentWorker(Worker):
    """Worker that executes AGENT-type nodes.

    Supports two invocation modes controlled by ``invocation_mode`` in
    node config:

    - ``"direct"`` (default): Delegates to WorkflowExecutionService for
      nested graph execution, or falls back to port.agent_execute().
    - ``"tool"``: Creates an AgentTool from agent_definition and registers
      it for the parent LLM to invoke as a callable tool.

    The execution service and port are injected at construction time.
    """

    def __init__(self, execution_service: Any = None, port: Any = None, event_store: Any = None) -> None:
        super().__init__(event_store=event_store)
        self._execution_service = execution_service
        self._port = port

    async def execute(
        self,
        node_id: str,
        node_config: dict,
        channel_snapshot: dict,
        execution_context: dict | None = None,
    ) -> WorkerResult:
        agent_id_str = node_config.get("agent_id")
        if not agent_id_str:
            return WorkerResult(
                node_id=node_id,
                error=ValueError(f"AGENT node '{node_id}' missing required config field 'agent_id'"),
            )

        try:
            agent_id = UUID(agent_id_str) if isinstance(agent_id_str, str) else agent_id_str
        except (ValueError, AttributeError) as e:
            return WorkerResult(
                node_id=node_id,
                error=ValueError(f"AGENT node '{node_id}' has invalid agent_id: {e}"),
            )

        invocation_mode = node_config.get("invocation_mode", "direct")

        if invocation_mode == "tool":
            return self._handle_tool_mode(node_id, node_config, agent_id)

        return await self._handle_direct_mode(node_id, node_config, agent_id, channel_snapshot)

    def _handle_tool_mode(
        self,
        node_id: str,
        node_config: dict,
        agent_id: UUID,
    ) -> WorkerResult:
        """Handle tool invocation mode — expose agent as callable tool.

        Creates an AgentTool from the agent_definition in node config
        and registers it by writing the tool info to the _agent_tools channel.
        """
        agent_definition_raw = node_config.get("agent_definition")
        if agent_definition_raw and isinstance(agent_definition_raw, dict):
            definition = AgentDefinition(
                agent_id=agent_id,
                description=agent_definition_raw.get("description", "A specialist agent"),
                prompt_override=agent_definition_raw.get("prompt_override"),
                tools=agent_definition_raw.get("tools"),
                disallowed_tools=agent_definition_raw.get("disallowed_tools", ["agent_execute"]),
                skills=agent_definition_raw.get("skills"),
                model_override=agent_definition_raw.get("model_override"),
                context_mode=agent_definition_raw.get("context_mode", "inherited"),
                max_turns=agent_definition_raw.get("max_turns"),
                timeout_seconds=agent_definition_raw.get("timeout_seconds"),
            )
        else:
            definition = AgentDefinition(
                agent_id=agent_id,
                description=node_config.get("description", "A specialist agent"),
            )

        agent_tool = AgentTool(definition=definition, agent_name=node_config.get("name", ""))
        tool_schema = {
            "type": "function",
            "function": {
                "name": agent_tool.name,
                "description": agent_tool.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "task": {
                            "type": "string",
                            "description": "The task to delegate to this agent",
                        },
                    },
                    "required": ["task"],
                },
            },
            "_agent_id": str(agent_id),
            "_invocation_mode": "tool",
        }

        return WorkerResult(
            node_id=node_id,
            channel_updates={"_agent_tools": [tool_schema]},
        )

    async def _handle_direct_mode(
        self,
        node_id: str,
        node_config: dict,
        agent_id: UUID,
        channel_snapshot: dict,
    ) -> WorkerResult:
        """Handle direct invocation mode — execute agent inline (existing behavior)."""
        messages = channel_snapshot.get("messages", [])
        if not isinstance(messages, list):
            messages = []

        initial_input = {
            "messages": messages,
            "_parent_session_id": channel_snapshot.get("_session_id"),
            "_parent_agent_id": channel_snapshot.get("_agent_id"),
            "_parent_user_id": channel_snapshot.get("_user_id"),
        }
        for key in ("context", "variables", "category"):
            if key in channel_snapshot:
                initial_input[key] = channel_snapshot[key]

        try:
            if self._execution_service is not None:
                result = await self._execution_service(
                    agent_id=agent_id,
                    messages=messages,
                    channel_snapshot=channel_snapshot,
                    initial_input=initial_input,
                    context={"node_id": node_id, "parent_channel": channel_snapshot},
                )
            elif self._port is not None:
                agent_definition = node_config.get("agent_definition")
                result = await self._port.agent_execute(
                    agent_id=agent_id,
                    messages=messages,
                    channel_snapshot=channel_snapshot,
                    context={"node_id": node_id},
                    agent_definition=agent_definition,
                )
            else:
                return WorkerResult(
                    node_id=node_id,
                    error=RuntimeError("AgentWorker has no execution service or port configured"),
                )
        except Exception as e:
            logger.warning("Agent execution failed for node '%s': %s", node_id, e)
            return WorkerResult(node_id=node_id, error=e)

        handoff_to: str | None = result.get("handoff_to")
        if handoff_to:
            from hecate.services.orchestration.handoff import build_handoff_channel_updates

            handoff_config = node_config.get("handoff") or {}
            context_mode = handoff_config.get("context_mode", "inherited")
            tool_call_id: str = result.get("_handoff_tool_call_id", "")
            tool_call_message = result.get("_handoff_messages_snapshot")

            handoff_messages = build_handoff_channel_updates(
                messages_snapshot=messages,
                source_node_id=node_id,
                target_node_id=handoff_to,
                context_mode=context_mode,
                tool_call_id=tool_call_id,
                llm_tool_call_message=tool_call_message,
            )

            updates: dict[str, Any] = {
                "messages": handoff_messages,
            }
            if "usage" in result:
                updates["_agent_usage"] = result["usage"]

            return WorkerResult(
                node_id=node_id,
                channel_updates=updates,
                command=Command(goto=handoff_to),
            )

        response_content = result.get("response", "")
        result_updates: dict[str, Any] = {
            "messages": [{"role": "assistant", "content": response_content}],
        }
        if "usage" in result:
            result_updates["_agent_usage"] = result["usage"]

        return WorkerResult(node_id=node_id, channel_updates=result_updates)
