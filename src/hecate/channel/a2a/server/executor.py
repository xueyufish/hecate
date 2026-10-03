"""A2A AgentExecutor delegating to the platform entry execution service.

The executor is the A2A entry adapter: it resolves the target agent and
maps the protocol result shape, while execution itself (graph engine,
agent tools, guardrail bundle, event store, Task/Run correlation) goes
through ``EntryExecutionService`` — the same service the HTTP chat, MCP
and IM entries use. There is deliberately no direct LLM call here.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.channel.a2a.types import (
    Artifact,
    Message,
    Task,
    TaskState,
    TaskStatus,
)
from hecate.models.agent import AgentModel

logger = logging.getLogger(__name__)

ENTRY_NAME = "a2a-chat"


class HecateAgentExecutor:
    """Executes A2A tasks via the platform entry execution service.

    Guardrails, tracing, audit logging and Task/Run correlation apply to
    A2A-initiated executions the same as any other entry chain.
    """

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def execute(self, message: Message, task_id: str, context_id: str) -> Task:
        """Execute an A2A task by running the default agent.

        Args:
            message: The incoming A2A message.
            task_id: The task ID for this execution.
            context_id: The conversation context ID.

        Returns:
            Task with execution results.
        """

        # Find the default agent (first agent in workspace). Selection
        # semantics are unchanged; per-caller agent identity is a
        # registered A2A protocol gap, not something this migration alters.
        result = await self._db.execute(select(AgentModel).where(AgentModel.deleted.is_(False)).limit(1))
        agent = result.scalar_one_or_none()

        if agent is None:
            return Task(
                id=task_id,
                context_id=context_id,
                status=TaskStatus(
                    state=TaskState.FAILED,
                    message=Message(
                        role="agent",
                        parts=[{"text": "No agent configured in Hecate"}],
                    ),
                ),
            )

        # Extract text from message parts
        text_parts = [p.get("text", "") for p in message.parts if "text" in p]
        user_message = " ".join(text_parts) if text_parts else ""

        try:
            response_text = await self._execute_via_entry_service(agent, user_message)

            artifacts = []
            if response_text:
                artifacts.append(
                    Artifact(
                        name="response",
                        parts=[{"text": response_text}],
                    )
                )

            return Task(
                id=task_id,
                context_id=context_id,
                status=TaskStatus(
                    state=TaskState.COMPLETED,
                    message=Message(
                        role="agent",
                        parts=[{"text": response_text}],
                    ),
                ),
                artifacts=artifacts,
            )

        except Exception as e:
            logger.exception("A2A task execution failed")
            return Task(
                id=task_id,
                context_id=context_id,
                status=TaskStatus(
                    state=TaskState.FAILED,
                    message=Message(
                        role="agent",
                        parts=[{"text": f"Execution failed: {e!s}"}],
                    ),
                ),
            )

    async def _execute_via_entry_service(self, agent: AgentModel, user_message: str) -> str:
        """Run one execution through the platform entry service."""
        from hecate_llm.service import llm_service

        from hecate.core.composition.guardrail_platform import assemble_guardrails
        from hecate.core.composition.im_entry import _get_shared_event_store
        from hecate.core.composition.runtime_port_adapter import create_runtime_port
        from hecate.execution.entry_service import CorrelationInput, EntryExecutionService

        tool_registry = None
        effective_tools: list[dict[str, Any]] = []
        bundle = None
        event_store = None
        if agent.tools:
            from hecate.channel.api.v1.chat import _build_tool_registry, _load_agent_tools

            event_store = _get_shared_event_store()
            tool_registry = _build_tool_registry(
                self._db, skill_ref_manifest=getattr(agent, "_resolved_ref_manifest", None)
            )
            effective_tools = await _load_agent_tools(self._db, agent.tools or [])
            if effective_tools:
                bundle = await assemble_guardrails(
                    self._db,
                    workspace_id=agent.workspace_id,
                    agent_id=agent.id,
                    guardrail_config=getattr(agent, "guardrail_config", None),
                    event_store=event_store,
                    session_id=None,
                    dlp_scanner=None,
                )

        model_name = (
            agent.model_config_db.get("model", "gpt-4o") if isinstance(agent.model_config_db, dict) else "gpt-4o"
        )
        port = create_runtime_port(self._db, llm_service, tool_registry=tool_registry)
        entry = EntryExecutionService(
            port=port,
            entry_name=ENTRY_NAME,
            db=self._db,
            event_store=event_store,
            access_policy=bundle.access_policy if bundle else None,
            approval_callback=bundle.approval_callback if bundle else None,
            tool_policy_rules=bundle.rules if bundle else None,
            middleware_chains=bundle.middleware_chains if bundle else None,
            denial_tracker=bundle.denial_tracker if bundle else None,
        )
        correlation = CorrelationInput(
            workspace_id=agent.workspace_id,
            agent_id=agent.id,
            user_id=None,
            session_id=None,
            goal=user_message[:200] or None,
        )
        # agent_id makes the execution service resolve persona/skills from
        # the live agent row; model and tools are caller-resolved inputs.
        outcome = await entry.execute(
            agent_mode="chat",
            messages=[{"role": "user", "content": user_message}],
            model=model_name,
            tools=effective_tools or None,
            stream=False,
            agent_id=agent.id,
            workspace_id=agent.workspace_id,
            correlation=correlation,
        )
        if not outcome.correlated:
            # Fail-open correlation: the protocol result stays unchanged;
            # the missing registration is explicit in logs, never claimed.
            logger.warning("A2A entry correlation missing (%s)", outcome.correlation.reason)
        result = outcome.result
        if not isinstance(result, dict):
            msg = f"Expected dict result for A2A execution, got {type(result)}"
            raise TypeError(msg)
        return result.get("content", "") or ""
