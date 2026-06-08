"""Tests for knowledge memory agent tools (Task 6.7).

Verifies tool schema structure and exports.
"""

from __future__ import annotations

from hecate.services.memory.knowledge_tools import (
    KNOWLEDGE_INSERT_TOOL,
    KNOWLEDGE_SEARCH_TOOL,
    KNOWLEDGE_TOOLS,
)


def test_knowledge_insert_tool_schema() -> None:
    """Test knowledge_insert tool has correct JSON Schema structure."""
    schema = KNOWLEDGE_INSERT_TOOL
    assert schema["type"] == "function"
    func = schema["function"]
    assert func["name"] == "knowledge_insert"
    assert "content" in func["parameters"]["properties"]
    assert "content" in func["parameters"]["required"]
    assert "tags" in func["parameters"]["properties"]
    assert "importance" in func["parameters"]["properties"]


def test_knowledge_insert_tool_optional_fields() -> None:
    """Test knowledge_insert optional fields have correct types."""
    props = KNOWLEDGE_INSERT_TOOL["function"]["parameters"]["properties"]
    assert props["tags"]["type"] == "array"
    assert props["tags"]["items"]["type"] == "string"
    assert props["importance"]["type"] == "number"


def test_knowledge_search_tool_schema() -> None:
    """Test knowledge_search tool has correct JSON Schema structure."""
    schema = KNOWLEDGE_SEARCH_TOOL
    assert schema["type"] == "function"
    func = schema["function"]
    assert func["name"] == "knowledge_search"
    assert "query" in func["parameters"]["properties"]
    assert "query" in func["parameters"]["required"]


def test_knowledge_search_tool_optional_fields() -> None:
    """Test knowledge_search optional fields have correct types."""
    props = KNOWLEDGE_SEARCH_TOOL["function"]["parameters"]["properties"]
    assert props["top_k"]["type"] == "integer"
    assert "tags" in props
    assert props["tags"]["type"] == "array"


def test_knowledge_tools_list() -> None:
    """Test KNOWLEDGE_TOOLS contains both tool definitions."""
    assert len(KNOWLEDGE_TOOLS) == 2
    names = [t["function"]["name"] for t in KNOWLEDGE_TOOLS]
    assert "knowledge_insert" in names
    assert "knowledge_search" in names


def test_knowledge_tools_are_distinct() -> None:
    """Test that each tool in KNOWLEDGE_TOOLS is a distinct definition."""
    assert KNOWLEDGE_TOOLS[0] is KNOWLEDGE_INSERT_TOOL
    assert KNOWLEDGE_TOOLS[1] is KNOWLEDGE_SEARCH_TOOL
