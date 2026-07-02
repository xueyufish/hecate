"""Tests for AgentToolProvider — agent-as-tool schema generation and lookup."""

from __future__ import annotations

import uuid

from hecate.models.agent import AgentModel
from hecate.services.orchestration.agent_tool import (
    build_agent_tool_schema,
    find_agent_id_for_tool,
    is_agent_tool,
)


def _make_agent(name: str = "Specialist", persona: str = "A specialist agent") -> AgentModel:
    """Create a minimal AgentModel for testing."""
    agent = AgentModel(
        name=name,
        persona=persona,
        model_config_db={"model": "gpt-4o"},
    )
    agent.id = uuid.uuid4()
    return agent


def test_build_agent_tool_schema():
    """Agent tool schema has correct name, description, and parameters."""
    agent = _make_agent(name="Billing Helper", persona="Handles billing questions")

    schema = build_agent_tool_schema(agent)

    func = schema["function"]
    assert func["name"] == "agent_billing_helper"
    assert "Billing Helper" in func["description"]
    assert "Handles billing questions" in func["description"]
    assert "task" in func["parameters"]["properties"]
    assert "task" in func["parameters"]["required"]
    assert schema["_agent_id"] == str(agent.id)


def test_build_agent_tool_schema_sanitizes_name():
    """Agent names with spaces/hyphens are sanitized to underscores."""
    agent = _make_agent(name="Data-Analyzer Pro")

    schema = build_agent_tool_schema(agent)

    assert schema["function"]["name"] == "agent_data_analyzer_pro"


def test_is_agent_tool():
    """Correctly identifies agent tool names."""
    assert is_agent_tool("agent_specialist") is True
    assert is_agent_tool("agent_billing") is True
    assert is_agent_tool("agent") is False
    assert is_agent_tool("search") is False
    assert is_agent_tool("handoff_to_agent") is False


def test_find_agent_id_for_tool():
    """Finds the agent_id metadata for a known tool name."""
    agent = _make_agent(name="Helper")
    tools = [build_agent_tool_schema(agent)]

    found_id = find_agent_id_for_tool("agent_helper", tools)

    assert found_id == str(agent.id)


def test_find_agent_id_for_tool_not_found():
    """Returns None when tool name is not in the list."""
    tools = [{"type": "function", "function": {"name": "search"}}]

    result = find_agent_id_for_tool("agent_helper", tools)

    assert result is None
