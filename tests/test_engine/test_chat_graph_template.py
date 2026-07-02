"""Tests for the chat graph template (build_chat_graph).

Verifies node count, edge topology, channel definitions, and entry point
for both the basic and suggestion-enabled variants.
"""

from __future__ import annotations

from hecate.engine.templates import build_chat_graph
from hecate.engine.types import ChannelType, NodeType


class TestBuildChatGraphBasic:
    """Verify the basic chat graph without suggestions."""

    def test_node_count(self):
        """Basic chat graph has 3 nodes: llm, check_tools, tool_call."""
        graph = build_chat_graph(model="gpt-4o")
        assert len(graph.nodes) == 3
        assert "llm" in graph.nodes
        assert "check_tools" in graph.nodes
        assert "tool_call" in graph.nodes

    def test_node_types(self):
        """Nodes have correct types."""
        graph = build_chat_graph(model="gpt-4o")
        assert graph.nodes["llm"].type == NodeType.CONVERSATION
        assert graph.nodes["check_tools"].type == NodeType.CONDITION
        assert graph.nodes["tool_call"].type == NodeType.TOOL_CALL

    def test_entry_point(self):
        """Entry point is 'llm'."""
        graph = build_chat_graph(model="gpt-4o")
        assert graph.entry == "llm"

    def test_edge_topology(self):
        """Edges form: llm → check_tools → {true: tool_call, false: __end__}, tool_call → llm."""
        graph = build_chat_graph(model="gpt-4o")
        assert len(graph.edges) == 3

        # llm → check_tools
        e0 = graph.edges[0]
        assert e0.source == "llm"
        assert e0.target == "check_tools"

        # check_tools → conditional
        e1 = graph.edges[1]
        assert e1.source == "check_tools"
        assert isinstance(e1.target, dict)
        assert e1.target["true"] == "tool_call"
        assert e1.target["false"] == "__end__"

        # tool_call → llm (cycle)
        e2 = graph.edges[2]
        assert e2.source == "tool_call"
        assert e2.target == "llm"

    def test_channel_definitions(self):
        """Required channels are defined with correct types."""
        graph = build_chat_graph(model="gpt-4o")
        assert "messages" in graph.state
        assert graph.state["messages"].type == ChannelType.TOPIC
        assert "_has_tool_call" in graph.state
        assert graph.state["_has_tool_call"].type == ChannelType.LAST_VALUE
        assert "_route" in graph.state
        assert "_session_id" in graph.state
        assert "_agent_id" in graph.state
        assert "_user_id" in graph.state
        assert "_turn_index" in graph.state

    def test_graph_name(self):
        """Graph name is 'chat-agent'."""
        graph = build_chat_graph(model="gpt-4o")
        assert graph.name == "chat-agent"

    def test_model_propagated_to_llm_node(self):
        """Model parameter is stored in llm node config."""
        graph = build_chat_graph(model="gpt-4o-mini")
        assert graph.nodes["llm"].config["model"] == "gpt-4o-mini"


class TestBuildChatGraphWithSuggestions:
    """Verify the chat graph with suggestions enabled."""

    def test_node_count_with_suggestions(self):
        """Chat graph with suggestions has 4 nodes."""
        graph = build_chat_graph(model="gpt-4o", enable_suggestions=True)
        assert len(graph.nodes) == 4
        assert "suggestions" in graph.nodes
        assert graph.nodes["suggestions"].type == NodeType.SUGGESTION

    def test_suggestion_routing(self):
        """False branch from check_tools routes to suggestions, not __end__."""
        graph = build_chat_graph(model="gpt-4o", enable_suggestions=True)
        # check_tools → conditional
        e1 = graph.edges[1]
        assert e1.target["false"] == "suggestions"

    def test_suggestions_to_end(self):
        """Suggestion node has an edge to __end__."""
        graph = build_chat_graph(model="gpt-4o", enable_suggestions=True)
        suggestion_edges = [e for e in graph.edges if e.source == "suggestions"]
        assert len(suggestion_edges) == 1
        assert suggestion_edges[0].target == "__end__"

    def test_generate_opening_in_config(self):
        """generate_opening flag is stored in suggestion node config."""
        graph = build_chat_graph(model="gpt-4o", enable_suggestions=True, generate_opening=True)
        assert graph.nodes["suggestions"].config["generate_opening"] is True
