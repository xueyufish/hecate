"""IM message-bus execution adapter over the platform entry service (step5d).

``IMMessageBus`` consumes an injected object exposing ``execute(...)``.
This adapter is that seam in production: per message it opens a session,
resolves the target agent, and delegates to the shared
``EntryExecutionService`` so IM executions get the same assembly
(tools, guardrail bundle, event/checkpoint stores) and Task/Run
correlation as the HTTP and MCP chat entries. Correlation is fail-open.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

logger = logging.getLogger(__name__)


class IMEntryExecutionAdapter:
    """Workflow-service seam for the IM message bus over the entry service."""

    def __init__(self, entry_name: str = "im-chat") -> None:
        self._entry_name = entry_name

    async def execute(self, **kwargs: Any) -> Any:
        from hecate_llm.service import llm_service
        from sqlalchemy import select

        from hecate.core.composition.entry_assembly import (
            build_tool_registry,
            get_shared_event_store,
            load_agent_tools,
        )
        from hecate.core.composition.guardrail_platform import assemble_guardrails
        from hecate.core.composition.runtime_port_adapter import create_runtime_port
        from hecate.core.database import async_session_factory
        from hecate.execution.entry_service import CorrelationInput, EntryExecutionService
        from hecate.models.agent import AgentModel

        agent_id = kwargs.get("agent_id")
        workspace_id = kwargs.get("workspace_id")
        agent = None
        agent_uuid: uuid.UUID | None = None
        if agent_id is not None:
            agent_uuid = agent_id if isinstance(agent_id, uuid.UUID) else uuid.UUID(str(agent_id))

        async with async_session_factory() as db:
            if agent_uuid is not None:
                row = await db.execute(select(AgentModel).where(AgentModel.id == agent_uuid, ~AgentModel.deleted))
                agent = row.scalar_one_or_none()
            effective_workspace = workspace_id if workspace_id is not None else (agent.workspace_id if agent else None)

            tool_registry = None
            effective_tools: list[dict[str, Any]] = []
            bundle = None
            event_store = None
            if agent is not None and agent.tools:
                event_store = get_shared_event_store()
                tool_registry = build_tool_registry(
                    db, skill_ref_manifest=getattr(agent, "_resolved_ref_manifest", None)
                )
                effective_tools = await load_agent_tools(db, agent.tools or [])
                if effective_tools:
                    bundle = await assemble_guardrails(
                        db,
                        workspace_id=agent.workspace_id,
                        agent_id=agent.id,
                        guardrail_config=getattr(agent, "guardrail_config", None),
                        event_store=event_store,
                        session_id=None,
                        dlp_scanner=None,
                    )

            port = create_runtime_port(db, llm_service, tool_registry=tool_registry)
            entry = EntryExecutionService(
                port=port,
                entry_name=self._entry_name,
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
                agent_id=agent.id if agent is not None else agent_uuid,
                user_id=kwargs.get("user_id"),
                session_id=None,
                goal=next(
                    (m.get("content", "")[:200] for m in kwargs.get("messages", []) if m.get("content")),
                    None,
                ),
            )
            outcome = await entry.execute(
                correlation=correlation,
                **kwargs,
            )
            if not outcome.correlated:
                logger.warning(
                    "IM entry correlation missing (%s)",
                    outcome.correlation.reason,
                )
            await db.commit()
            return outcome.result
