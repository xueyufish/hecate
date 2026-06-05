"""Suggestion worker for generating opening remarks and follow-up questions.

Calls SuggestionService to produce either opening greetings with starter
questions or follow-up question suggestions based on the conversation context.
"""

from __future__ import annotations

import logging
from typing import Any

from hecate.engine.types import WorkerResult
from hecate.engine.worker import Worker

logger = logging.getLogger(__name__)


class SuggestionWorker(Worker):
    """Worker that generates opening remarks or follow-up question suggestions.

    Reads configuration from node config to determine whether to generate
    opening remarks (``generate_opening: true``) or follow-up suggestions.
    Writes ``suggested_questions`` and optionally ``content`` to channel_updates.

    The suggestion service is injected at construction time.
    """

    def __init__(self, suggestion_service: Any = None) -> None:
        self._suggestion_service = suggestion_service

    async def execute(
        self,
        node_id: str,
        node_config: dict,
        channel_snapshot: dict,
    ) -> WorkerResult:
        if self._suggestion_service is None:
            return WorkerResult(
                node_id=node_id,
                channel_updates={"messages": [], "suggested_questions": []},
            )

        generate_opening = node_config.get("generate_opening", False)
        enable_suggestions = node_config.get("enable_suggestions", True)
        agent_persona = node_config.get("agent_persona", "")

        if generate_opening:
            return await self._generate_opening(node_id, agent_persona)

        if not enable_suggestions:
            return WorkerResult(
                node_id=node_id,
                channel_updates={"messages": [], "suggested_questions": []},
            )

        return await self._generate_suggestions(node_id, agent_persona, channel_snapshot)

    async def _generate_opening(self, node_id: str, agent_persona: str) -> WorkerResult:
        """Generate opening remarks with starter questions.

        Args:
            node_id: The node being executed.
            agent_persona: Agent persona description for context.

        Returns:
            WorkerResult with opening content and suggested questions.
        """
        try:
            result = await self._suggestion_service.generate_opening(
                agent_persona=agent_persona,
                agent_capabilities=[],
            )
        except Exception as e:
            logger.warning("Opening remarks generation failed: %s", e)
            return WorkerResult(
                node_id=node_id,
                channel_updates={"messages": [], "suggested_questions": []},
            )

        greeting = agent_persona or "Hello! How can I help you today?"
        return WorkerResult(
            node_id=node_id,
            channel_updates={
                "messages": [{"role": "assistant", "content": greeting}],
                "content": greeting,
                "suggested_questions": result.questions,
            },
        )

    async def _generate_suggestions(
        self,
        node_id: str,
        agent_persona: str,
        channel_snapshot: dict,
    ) -> WorkerResult:
        """Generate follow-up question suggestions.

        Args:
            node_id: The node being executed.
            agent_persona: Agent persona description.
            channel_snapshot: Current channel state with messages.

        Returns:
            WorkerResult with suggested questions.
        """
        messages = channel_snapshot.get("messages", [])
        recent = messages[-4:] if len(messages) > 4 else messages

        history_parts: list[str] = []
        for msg in recent:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if content:
                history_parts.append(f"{role}: {content}")
        history_str = "\n".join(history_parts)

        # Find the last assistant message for current_response
        current_response = ""
        for msg in reversed(messages):
            if msg.get("role") == "assistant" and msg.get("content"):
                current_response = msg["content"]
                break

        if not current_response:
            return WorkerResult(
                node_id=node_id,
                channel_updates={"messages": [], "suggested_questions": []},
            )

        try:
            result = await self._suggestion_service.generate_suggestions(
                agent_persona=agent_persona,
                conversation_history=history_str,
                current_response=current_response,
            )
        except Exception as e:
            logger.warning("Suggestion generation failed: %s", e)
            return WorkerResult(
                node_id=node_id,
                channel_updates={"messages": [], "suggested_questions": []},
            )

        return WorkerResult(
            node_id=node_id,
            channel_updates={
                "messages": [],
                "suggested_questions": result.questions,
            },
        )
