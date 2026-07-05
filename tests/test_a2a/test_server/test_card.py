"""Tests for A2A AgentCard generation."""

from __future__ import annotations

from hecate.a2a.server.card import generate_agent_card


def test_generate_agent_card_defaults() -> None:
    """Test AgentCard generation with default config."""
    card = generate_agent_card()
    assert card.name == "Hecate Agent"
    assert "Enterprise-grade" in card.description
    assert card.version == "1.0.0"
    assert card.capabilities["streaming"] is True
    assert card.capabilities["pushNotifications"] is False


def test_generate_agent_card_with_skills() -> None:
    """Test AgentCard generation with custom skills."""
    skills = [
        {"id": "search", "name": "Search", "description": "Search the web"},
        {"id": "code", "name": "Code", "description": "Execute code"},
    ]
    card = generate_agent_card(skills=skills)
    assert len(card.skills) == 2
    assert card.skills[0]["name"] == "Search"


def test_generate_agent_card_security_schemes() -> None:
    """Test that AgentCard includes API key security scheme."""
    card = generate_agent_card()
    assert "apiKeyAuth" in card.security_schemes
    scheme = card.security_schemes["apiKeyAuth"]["apiKeySecurityScheme"]
    assert scheme["location"] == "header"
    assert scheme["name"] == "X-API-Key"
