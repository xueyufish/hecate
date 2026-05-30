"""Agent worker for executing AGENT-type nodes via EnginePort.

This worker handles the ``agent`` node type by resolving the configured
agent_id, passing conversation messages from the channel snapshot to the
engine port's ``agent_execute`` method, and returning the agent's response
as channel updates.

It supports two invocation modes controlled by the node config:
- ``direct`` (default): execute the agent inline and write its response to
  the ``messages`` channel.
- ``tool``: register the agent as a callable tool for the parent agent's
  LLM invocation (handled by the service layer's AgentToolProvider).
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from hecate.engine.ports import EnginePort
from hecate.engine.types import WorkerResult
from hecate.engine.worker import Worker

logger = logging.getLogger(__name__)


class AgentWorker(Worker):
    """Worker that executes AGENT-type nodes by delegating to EnginePort.agent_execute.

    Requires an ``agent_id`` in the node config. The worker extracts
    messages from the channel snapshot and calls the port to execute the
    agent with its own isolated context.
    """

    def __init__(self, port: EnginePort) -> None:
        self._port = port

    async def execute(
        self,
        node_id: str,
        node_config: dict,
        channel_snapshot: dict,
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

        try:
            result = await self._port.agent_execute(
                agent_id=agent_id,
                messages=messages,
                channel_snapshot=channel_snapshot,
                context={"node_id": node_id},
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
