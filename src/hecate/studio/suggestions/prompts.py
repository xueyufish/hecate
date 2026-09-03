"""Prompt templates for suggestion generation.

Provides functions that build LLM prompt strings for generating opening
remarks and follow-up question suggestions based on agent configuration
and conversation context.
"""

from __future__ import annotations


def build_opening_prompt(persona: str | None, capabilities: list[str]) -> str:
    """Build a prompt for generating opening remarks and suggested questions.

    Args:
        persona: The agent's persona description, or None if not configured.
        capabilities: List of capability descriptions (tools, skills, knowledge bases).

    Returns:
        A prompt string instructing the LLM to generate opening remarks with
        3-5 suggested questions in JSON array format.
    """
    persona_section = f"Agent persona: {persona}" if persona else "No specific persona configured."

    if capabilities:
        capabilities_text = "\n".join(f"- {cap}" for cap in capabilities)
        capabilities_section = f"The agent has the following capabilities:\n{capabilities_text}"
    else:
        capabilities_section = "The agent has no specific capabilities configured."

    return f"""You are a helpful assistant. Based on the agent's persona and capabilities,
generate a friendly greeting message and 3-5 suggested questions that a user might want to ask.

{persona_section}

{capabilities_section}

Respond with ONLY a JSON array containing the suggested questions. Example:
["Question 1?", "Question 2?", "Question 3?"]"""


def build_followup_prompt(persona: str | None, history: str, response: str) -> str:
    """Build a prompt for generating follow-up question suggestions after a response.

    Args:
        persona: The agent's persona description, or None if not configured.
        history: The recent conversation history (last 2 turns).
        response: The agent's most recent response content.

    Returns:
        A prompt string instructing the LLM to generate 3-5 follow-up questions
        in JSON array format.
    """
    persona_section = f"Agent persona: {persona}" if persona else "No specific persona configured."

    return f"""Based on the conversation below, suggest 3-5 relevant follow-up questions
the user might want to ask next.

{persona_section}

Recent conversation:
{history}

Agent's last response:
{response}

Respond with ONLY a JSON array of follow-up questions. Example:
["Follow-up 1?", "Follow-up 2?", "Follow-up 3?"]"""
