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
        self._resolution_failure = "No agent configured for A2A execution"

    async def execute(self, message: Message, task_id: str, context_id: str) -> Task:
        """Execute an A2A task by running the scoped agent.

        Args:
            message: The incoming A2A message.
            task_id: The task ID for this execution.
            context_id: The conversation context ID.

        Returns:
            Task with execution results.
        """

        agent = await self._resolve_agent()
        if agent is None:
            # Scope unconfigured or not resolvable to exactly one agent —
            # refuse in-protocol instead of picking a global agent (which
            # could cross workspaces/tenants).
            return Task(
                id=task_id,
                context_id=context_id,
                status=TaskStatus(
                    state=TaskState.FAILED,
                    message=Message(
                        role="agent",
                        parts=[{"text": self._resolution_failure}],
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

        except Exception:
            # Stable client-facing text only; internals stay in server logs.
            logger.exception("A2A task execution failed")
            return Task(
                id=task_id,
                context_id=context_id,
                status=TaskStatus(
                    state=TaskState.FAILED,
                    message=Message(
                        role="agent",
                        parts=[{"text": "Execution failed"}],
                    ),
                ),
            )

    async def _resolve_agent(self) -> AgentModel | None:
        """Resolve the single agent inside the configured workspace scope.

        ``A2A_AGENT_WORKSPACE_ID`` must name a workspace holding exactly
        one non-deleted agent; empty, invalid, zero-hit, or ambiguous
        scopes all refuse (``None``) with the reason recorded in
        ``self._resolution_failure`` for the protocol response.
        """
        import uuid as uuid_mod

        from hecate.core.config import settings

        self._resolution_failure = "No agent configured for A2A execution"
        raw = (settings.A2A_AGENT_WORKSPACE_ID or "").strip()
        if not raw:
            logger.error("A2A agent workspace scope is not configured; refusing execution")
            return None
        try:
            workspace_uuid = uuid_mod.UUID(raw)
        except ValueError:
            logger.error("A2A agent workspace scope is not a valid workspace id; refusing execution")
            return None
        result = await self._db.execute(
            select(AgentModel).where(AgentModel.workspace_id == workspace_uuid, AgentModel.deleted.is_(False))
        )
        agents = result.scalars().all()
        if not agents:
            logger.error("A2A agent workspace scope %s has no agents; refusing execution", raw)
            self._resolution_failure = "No agent available in the configured A2A workspace"
            return None
        if len(agents) > 1:
            logger.error(
                "A2A agent workspace scope %s resolves to %d agents; refusing execution",
                raw,
                len(agents),
            )
            self._resolution_failure = "The configured A2A workspace does not resolve to a single agent"
            return None
        return agents[0]

    async def _execute_via_entry_service(self, agent: AgentModel, user_message: str) -> str:
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

        tool_registry = None
        effective_tools: list[dict[str, Any]] = []
        bundle = None
        event_store = None
        if agent.tools:
            event_store = get_shared_event_store()
            tool_registry = build_tool_registry(
                self._db, skill_ref_manifest=getattr(agent, "_resolved_ref_manifest", None)
            )
            effective_tools = await load_agent_tools(self._db, agent.tools or [])
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
