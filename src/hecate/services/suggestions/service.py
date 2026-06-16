"""Suggestion service for generating opening remarks and follow-up questions.

Uses the LLM service to produce contextually relevant question suggestions,
with a static fallback that extracts questions from the agent persona when
the LLM call fails or times out.
"""

from __future__ import annotations

import json
import logging
import re

from hecate.services.llm.service import LLMService
from hecate.services.suggestions.prompts import build_followup_prompt, build_opening_prompt
from hecate.services.suggestions.types import SuggestionResult

logger = logging.getLogger(__name__)

_LLM_TIMEOUT_SECONDS = 2.0

_GENERIC_QUESTIONS = [
    "What can you help me with?",
    "How does this work?",
    "Can you explain that in more detail?",
]


def _extract_questions_from_text(text: str) -> list[str]:
    """Extract sentences ending with '?' from the given text.

    Args:
        text: Source text to scan for questions.

    Returns:
        List of stripped question sentences found in the text.
    """
    return [m.strip() for m in re.findall(r"[^.!?\n]*\?", text)]


class SuggestionService:
    """Generates suggested questions via LLM with static fallback.

    Args:
        llm_service: The LLM service instance used for chat completions.
        default_model: Default model identifier for suggestion generation.
    """

    def __init__(self, llm_service: LLMService, default_model: str = "gpt-4o") -> None:
        self._llm = llm_service
        self._default_model = default_model

    async def generate_opening(
        self,
        agent_persona: str | None,
        agent_capabilities: list[str],
    ) -> SuggestionResult:
        """Generate opening remark suggestions based on agent persona and capabilities.

        Args:
            agent_persona: The agent's persona description, or None.
            agent_capabilities: List of capability descriptions.

        Returns:
            SuggestionResult with suggested questions, model name, and usage.
        """
        prompt = build_opening_prompt(agent_persona, agent_capabilities)
        return await self._generate(prompt, agent_persona)

    async def generate_suggestions(
        self,
        agent_persona: str | None,
        conversation_history: str,
        current_response: str,
    ) -> SuggestionResult:
        """Generate follow-up question suggestions after a response.

        Args:
            agent_persona: The agent's persona description, or None.
            conversation_history: Recent conversation history (last 2 turns).
            current_response: The agent's most recent response content.

        Returns:
            SuggestionResult with suggested questions, model name, and usage.
        """
        prompt = build_followup_prompt(agent_persona, conversation_history, current_response)
        return await self._generate(prompt, agent_persona)

    async def _generate(self, prompt: str, agent_persona: str | None) -> SuggestionResult:
        """Call the LLM and parse the result, falling back to static extraction.

        Args:
            prompt: The fully-built prompt string.
            agent_persona: Agent persona text for fallback question extraction.

        Returns:
            SuggestionResult with questions, model, and usage.
        """
        try:
            response = await self._llm.chat(
                messages=[{"role": "user", "content": prompt}],
                model=self._default_model,
                timeout=_LLM_TIMEOUT_SECONDS,
            )
            questions = self._parse_json_array(response.content or "")
            return SuggestionResult(
                questions=questions,
                model=response.model,
                usage=response.usage,
            )
        except Exception:
            logger.warning("LLM suggestion generation failed, using static fallback")
            return self._fallback(agent_persona)

    @staticmethod
    def _parse_json_array(content: str) -> list[str]:
        """Parse a JSON array of strings from LLM response content.

        Args:
            content: Raw LLM response text expected to contain a JSON array.

        Returns:
            List of question strings parsed from the JSON array.

        Raises:
            json.JSONDecodeError: If content is not valid JSON.
            ValueError: If parsed result is not a list of strings.
        """
        parsed = json.loads(content)
        if not isinstance(parsed, list):
            msg = f"Expected JSON array, got {type(parsed).__name__}"
            raise ValueError(msg)
        return [str(item) for item in parsed]

    @staticmethod
    def _fallback(agent_persona: str | None) -> SuggestionResult:
        """Build a static fallback result by extracting questions from persona.

        Args:
            agent_persona: Agent persona text to scan for questions.

        Returns:
            SuggestionResult with extracted or generic questions and empty usage.
        """
        questions: list[str] = []
        if agent_persona:
            questions = _extract_questions_from_text(agent_persona)
        if not questions:
            questions = list(_GENERIC_QUESTIONS)
        return SuggestionResult(questions=questions, model="fallback", usage={})
