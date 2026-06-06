"""Agent worker for executing AGENT-type nodes via nested graph execution.

Resolves the sub-agent by agent_id from config, packages parent channel context
(messages, variables) as initial_input, and calls WorkflowExecutionService for
nested graph execution (NOT direct agent_execute). This enables graph-within-graph
execution and keeps the door open for P3 Agent-Workflow composability.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from hecate.engine.types import WorkerResult
from hecate.engine.worker import Worker

logger = logging.getLogger(__name__)


class AgentWorker(Worker):
    """Worker that executes AGENT-type nodes via nested graph execution.

    Resolves the sub-agent by ID, packages parent channel context as
    initial_input, and delegates to the workflow execution service for
    nested graph execution. This approach (Decision 9) ensures sub-agents
    get full template resolution, compilation, and guard hook injection.

    The execution service is injected at construction time to avoid circular
    imports between engine and services layers.
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

        messages = channel_snapshot.get("messages", [])
        if not isinstance(messages, list):
            messages = []

        # Extract parent context for nested execution
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
                result = await self._port.agent_execute(
                    agent_id=agent_id,
                    messages=messages,
                    channel_snapshot=channel_snapshot,
                    context={"node_id": node_id},
                )
            else:
                return WorkerResult(
                    node_id=node_id,
                    error=RuntimeError("AgentWorker has no execution service or port configured"),
                )
        except Exception as e:
            logger.warning("Agent execution failed for node '%s': %s", node_id, e)
            return WorkerResult(node_id=node_id, error=e)

        response_content = result.get("response", "")
        channel_updates: dict[str, Any] = {
            "messages": [{"role": "assistant", "content": response_content}],
        }
        if "usage" in result:
            channel_updates["_agent_usage"] = result["usage"]

        return WorkerResult(node_id=node_id, channel_updates=channel_updates)
