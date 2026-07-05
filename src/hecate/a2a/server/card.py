"""AgentCard generator for Hecate A2A server."""

from __future__ import annotations

from typing import Any

from hecate.a2a.types import AgentCard
from hecate.core.config import settings


def generate_agent_card(skills: list[dict[str, Any]] | None = None) -> AgentCard:
    """Generate an AgentCard describing Hecate's A2A capabilities.

    Args:
        skills: Optional list of skill dicts from SkillRegistry.
            If None, returns a card with empty skills (populated later).

    Returns:
        AgentCard with Hecate's configuration.
    """
    return AgentCard(
        name=settings.A2A_AGENT_NAME,
        description="Enterprise-grade, self-hosted, model-agnostic Agent platform with MCP-first architecture",
        version="1.0.0",
        url=settings.A2A_SERVER_URL,
        capabilities={
            "streaming": True,
            "pushNotifications": False,
            "stateTransitionHistory": True,
        },
        skills=skills or [],
        security_schemes={
            "apiKeyAuth": {
                "apiKeySecurityScheme": {
                    "location": "header",
                    "name": "X-API-Key",
                }
            }
        },
        default_input_modes=["text/plain", "application/json"],
        default_output_modes=["text/plain", "application/json"],
    )
