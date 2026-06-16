"""Tests for suggestion prompt template functions.

Validates that build_opening_prompt and build_followup_prompt produce
well-formed prompt strings with the expected content for various input
combinations.
"""

from __future__ import annotations

from hecate.services.suggestions.prompts import build_followup_prompt, build_opening_prompt


class TestBuildOpeningPrompt:
    """Validate build_opening_prompt with various persona and capability combinations."""

    def test_with_persona_and_capabilities(self) -> None:
        """A prompt with both persona and capabilities includes both sections."""
        prompt = build_opening_prompt(
            persona="A helpful coding assistant",
            capabilities=["Python programming", "Code review"],
        )
        assert "Agent persona: A helpful coding assistant" in prompt
        assert "- Python programming" in prompt
        assert "- Code review" in prompt
        assert "JSON array" in prompt

    def test_with_no_persona(self) -> None:
        """A prompt without a persona uses the default 'no persona' message."""
        prompt = build_opening_prompt(persona=None, capabilities=["Search"])
        assert "No specific persona configured" in prompt
        assert "- Search" in prompt

    def test_with_empty_capabilities(self) -> None:
        """A prompt with empty capabilities reports no specific capabilities."""
        prompt = build_opening_prompt(persona="Helper", capabilities=[])
        assert "Agent persona: Helper" in prompt
        assert "no specific capabilities configured" in prompt.lower()

    def test_with_no_persona_and_no_capabilities(self) -> None:
        """A prompt with neither persona nor capabilities uses both defaults."""
        prompt = build_opening_prompt(persona=None, capabilities=[])
        assert "No specific persona configured" in prompt
        assert "no specific capabilities configured" in prompt.lower()

    def test_requests_json_array_format(self) -> None:
        """The prompt instructs the LLM to respond with a JSON array."""
        prompt = build_opening_prompt(persona=None, capabilities=[])
        assert '["Question 1?", "Question 2?", "Question 3?"]' in prompt


class TestBuildFollowupPrompt:
    """Validate build_followup_prompt with various history and response combinations."""

    def test_basic_followup_prompt(self) -> None:
        """A followup prompt includes persona, history, and response sections."""
        prompt = build_followup_prompt(
            persona="A coding assistant",
            history="User: How do I sort a list?\nAssistant: You can use sorted()",
            response="You can use sorted() to sort a list in Python.",
        )
        assert "Agent persona: A coding assistant" in prompt
        assert "User: How do I sort a list?" in prompt
        assert "You can use sorted()" in prompt
        assert "JSON array" in prompt

    def test_followup_with_no_persona(self) -> None:
        """A followup prompt without a persona uses the default message."""
        prompt = build_followup_prompt(
            persona=None,
            history="User: Hello",
            response="Hi there!",
        )
        assert "No specific persona configured" in prompt
        assert "User: Hello" in prompt
        assert "Hi there!" in prompt

    def test_followup_requests_json_format(self) -> None:
        """The followup prompt instructs the LLM to respond with a JSON array."""
        prompt = build_followup_prompt(persona=None, history="", response="")
        assert '["Follow-up 1?", "Follow-up 2?", "Follow-up 3?"]' in prompt

    def test_followup_includes_conversation_context(self) -> None:
        """The prompt includes the full conversation history and response."""
        history = "User: What is Python?\nAssistant: Python is a programming language."
        response = "Python is a high-level, interpreted programming language."
        prompt = build_followup_prompt(persona=None, history=history, response=response)
        assert history in prompt
        assert response in prompt
