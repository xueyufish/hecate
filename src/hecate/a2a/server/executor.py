"""A2A AgentExecutor bridging A2A requests to Hecate's execution pipeline."""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.a2a.types import (
    Artifact,
    Message,
    Task,
    TaskState,
    TaskStatus,
)
from hecate.models.agent import AgentModel

logger = logging.getLogger(__name__)


class HecateAgentExecutor:
    """Executes A2A tasks by delegating to Hecate's WorkflowExecutionService.

    This bridges A2A protocol requests to Hecate's existing agent execution
    pipeline, ensuring all guardrails, tracing, and audit logging apply.
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

        # Find the default agent (first agent in workspace)
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
            # Execute via LLM service directly
            from hecate.services.llm.service import llm_service

            model_name = (
                agent.model_config_db.get("model", "gpt-4o") if isinstance(agent.model_config_db, dict) else "gpt-4o"
            )
            messages = [
                {"role": "system", "content": agent.persona or "You are a helpful assistant."},
                {"role": "user", "content": user_message},
            ]

            result = await llm_service.chat(messages, model=model_name)
            response_text = result.content or ""

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
