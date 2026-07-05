"""Tests for A2A AgentCard discovery."""

from __future__ import annotations

from hecate.a2a.client.discovery import discover_agent_card


def test_discover_agent_card_module_exists() -> None:
    """Test that discovery module is importable."""
    assert callable(discover_agent_card)
