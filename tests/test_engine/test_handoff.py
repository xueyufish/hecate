"""Tests for handoff tool generation and cycle detection."""

from __future__ import annotations

import pytest

from hecate.engine.compiler import GraphCompiler
from hecate.engine.graph_dsl import GraphValidationError, parse_graph
from hecate.engine.types import ChannelDef, ChannelType, CompiledGraph, Edge, NodeConfig, NodeType
from hecate.services.orchestration.handoff import (
    build_handoff_channel_updates,
    build_handoff_tool_schema,
    create_handoff_worker_result,
    filter_messages_for_handoff,
    get_handoff_targets_for_node,
    inject_handoff_tools,
    inject_handoff_tools_from_targets,
    is_handoff_tool_call,
    validate_handoff_target,
    validate_handoff_target_from_list,
)


def _make_compiled_with_handoff(
    edges: list[tuple[str, str, str | None]],
) -> CompiledGraph:
    """Create a minimal compiled graph with agent nodes and handoff edges."""
    node_ids: set[str] = set()
    for src, tgt, _ in edges:
        node_ids.add(src)
        node_ids.add(tgt)

    nodes = {nid: NodeConfig(id=nid, type=NodeType.AGENT) for nid in node_ids}
    edge_objects = [Edge(source=src, target=tgt, trigger=trigger) for src, tgt, trigger in edges]
    return CompiledGraph(
        nodes=nodes,
        edges=edge_objects,
        channels={"messages": ChannelDef(type=ChannelType.TOPIC)},
        entry_point=next(iter(node_ids)) if node_ids else "",
    )


def test_build_handoff_tool_schema():
    """Handoff tool schema has correct structure and valid targets."""
    schema = build_handoff_tool_schema(["billing", "technical"])

    assert schema["type"] == "function"
    func = schema["function"]
    assert func["name"] == "handoff_to_agent"
    params = func["parameters"]["properties"]["target"]
    assert params["enum"] == ["billing", "technical"]
    assert "target" in func["parameters"]["required"]


def test_get_handoff_targets_for_node():
    """Returns only handoff edge targets for a specific node."""
    compiled = _make_compiled_with_handoff(
        [
            ("router", "billing", "handoff"),
            ("router", "technical", "handoff"),
            ("billing", "__end__", None),
        ]
    )

    targets = get_handoff_targets_for_node(compiled, "router")

    assert set(targets) == {"billing", "technical"}


def test_get_handoff_targets_no_handoff_edges():
    """Returns empty list when node has no handoff edges."""
    compiled = _make_compiled_with_handoff(
        [
            ("router", "billing", None),
        ]
    )

    targets = get_handoff_targets_for_node(compiled, "router")

    assert targets == []


def test_inject_handoff_tools_adds_tool():
    """Injects handoff tool when handoff edges exist for the node."""
    compiled = _make_compiled_with_handoff(
        [
            ("router", "billing", "handoff"),
        ]
    )
    existing_tools = [{"type": "function", "function": {"name": "search"}}]

    result = inject_handoff_tools(existing_tools, compiled, "router")

    assert len(result) == 2
    assert result[0]["function"]["name"] == "search"
    assert result[1]["function"]["name"] == "handoff_to_agent"


def test_inject_handoff_tools_no_change():
    """Returns tools unchanged when no handoff edges exist."""
    compiled = _make_compiled_with_handoff(
        [
            ("router", "billing", None),
        ]
    )
    existing_tools = [{"type": "function", "function": {"name": "search"}}]

    result = inject_handoff_tools(existing_tools, compiled, "router")

    assert len(result) == 1
    assert result[0]["function"]["name"] == "search"


def test_is_handoff_tool_call():
    """Correctly identifies handoff tool calls."""
    assert is_handoff_tool_call("handoff_to_agent") is True
    assert is_handoff_tool_call("search") is False
    assert is_handoff_tool_call("handoff") is False


def test_create_handoff_worker_result():
    """Handoff worker result contains Command(goto=target)."""
    result = create_handoff_worker_result("router", "billing")

    assert result.node_id == "router"
    assert result.command is not None
    assert result.command.goto == "billing"
    assert any("Transferring" in m.get("content", "") for m in result.channel_updates["messages"])


def test_handoff_cycle_detection_raises():
    """Compiler rejects circular handoff chains."""
    dsl = {
        "version": "1.0",
        "name": "circular-handoff",
        "state": {"messages": {"type": "topic"}},
        "nodes": {
            "a": {"type": "agent", "config": {"agent_id": "00000000-0000-0000-0000-000000000001"}},
            "b": {"type": "agent", "config": {"agent_id": "00000000-0000-0000-0000-000000000002"}},
            "c": {"type": "agent", "config": {"agent_id": "00000000-0000-0000-0000-000000000003"}},
        },
        "edges": [
            {"source": "a", "target": "b", "type": "handoff"},
            {"source": "b", "target": "c", "type": "handoff"},
            {"source": "c", "target": "a", "type": "handoff"},
        ],
        "entry": "a",
    }

    graph_config = parse_graph(dsl)
    with pytest.raises(GraphValidationError, match="Circular handoff"):
        GraphCompiler().compile(graph_config)


def test_handoff_non_circular_accepted():
    """Compiler accepts non-circular handoff chains."""
    dsl = {
        "version": "1.0",
        "name": "linear-handoff",
        "state": {"messages": {"type": "topic"}},
        "nodes": {
            "a": {"type": "agent", "config": {"agent_id": "00000000-0000-0000-0000-000000000001"}},
            "b": {"type": "agent", "config": {"agent_id": "00000000-0000-0000-0000-000000000002"}},
        },
        "edges": [
            {"source": "a", "target": "b", "type": "handoff"},
            {"source": "b", "target": "__end__"},
        ],
        "entry": "a",
    }

    graph_config = parse_graph(dsl)
    compiled = GraphCompiler().compile(graph_config)
    assert compiled is not None


def test_handoff_non_agent_source_rejected():
    """Compiler rejects handoff from non-agent node."""
    dsl = {
        "version": "1.0",
        "name": "bad-handoff-source",
        "state": {"messages": {"type": "topic"}},
        "nodes": {
            "conv": {"type": "conversation", "config": {"model": "gpt-4o"}},
            "agent_b": {"type": "agent", "config": {"agent_id": "00000000-0000-0000-0000-000000000002"}},
        },
        "edges": [
            {"source": "conv", "target": "agent_b", "type": "handoff"},
            {"source": "agent_b", "target": "__end__"},
        ],
        "entry": "conv",
    }

    graph_config = parse_graph(dsl)
    with pytest.raises(GraphValidationError, match="must be an agent node"):
        GraphCompiler().compile(graph_config)


def test_dynamic_handoff_injects_tool_with_multiple_targets():
    """Dynamic handoff edges inject handoff tool with all candidate targets."""
    compiled = _make_compiled_with_handoff(
        [
            ("router", "billing", "dynamic_handoff"),
            ("router", "technical", "dynamic_handoff"),
            ("router", "support", "dynamic_handoff"),
        ]
    )
    existing_tools = [{"type": "function", "function": {"name": "search"}}]

    result = inject_handoff_tools(existing_tools, compiled, "router")

    assert len(result) == 2
    handoff_tool = result[1]
    assert handoff_tool["function"]["name"] == "handoff_to_agent"
    valid_targets = handoff_tool["function"]["parameters"]["properties"]["target"]["enum"]
    assert set(valid_targets) == {"billing", "technical", "support"}


def test_dynamic_handoff_invalid_target_raises():
    """validate_handoff_target raises ValueError for invalid target."""
    compiled = _make_compiled_with_handoff(
        [
            ("router", "billing", "dynamic_handoff"),
            ("router", "technical", "dynamic_handoff"),
        ]
    )

    with pytest.raises(ValueError, match="Invalid handoff target"):
        validate_handoff_target(compiled, "router", "unknown_agent")


def test_dynamic_handoff_valid_target_accepted():
    """validate_handoff_target returns target when valid."""
    compiled = _make_compiled_with_handoff(
        [
            ("router", "billing", "dynamic_handoff"),
            ("router", "technical", "dynamic_handoff"),
        ]
    )

    result = validate_handoff_target(compiled, "router", "billing")
    assert result == "billing"


def test_mixed_handoff_and_dynamic_handoff():
    """Both handoff and dynamic_handoff edges contribute targets."""
    compiled = _make_compiled_with_handoff(
        [
            ("router", "billing", "handoff"),
            ("router", "technical", "dynamic_handoff"),
        ]
    )

    targets = get_handoff_targets_for_node(compiled, "router")
    assert set(targets) == {"billing", "technical"}


def test_build_handoff_tool_schema_with_descriptions():
    """Per-target descriptions are included in the tool description."""
    schema = build_handoff_tool_schema(
        ["billing", "technical"],
        descriptions_by_target={"billing": "Handles billing inquiries", "technical": "Tech support"},
    )
    desc = schema["function"]["description"]
    assert "billing: Handles billing inquiries" in desc
    assert "technical: Tech support" in desc


def test_build_handoff_tool_schema_with_source_description():
    """Source description overrides the auto-generated description."""
    schema = build_handoff_tool_schema(
        ["billing"],
        source_description="Custom handoff description",
    )
    assert schema["function"]["description"] == "Custom handoff description"


def test_inject_handoff_tools_from_targets():
    """inject_handoff_tools_from_targets adds handoff tool using target list."""
    targets = [
        {"node_id": "billing", "description": "Billing specialist"},
        {"node_id": "tech", "description": "Tech support"},
    ]
    tools = [{"name": "existing_tool", "description": "A tool", "parameters": {}}]
    result = inject_handoff_tools_from_targets(tools, targets)
    assert len(result) == 2
    assert result[0]["name"] == "existing_tool"
    assert result[1]["function"]["name"] == "handoff_to_agent"


def test_inject_handoff_tools_from_targets_empty():
    """inject_handoff_tools_from_targets returns tools unchanged when no targets."""
    tools = [{"name": "existing_tool", "description": "A tool", "parameters": {}}]
    result = inject_handoff_tools_from_targets(tools, [])
    assert result == tools


def test_validate_handoff_target_from_list_valid():
    """validate_handoff_target_from_list accepts valid target."""
    targets = [{"node_id": "billing", "description": "desc"}, {"node_id": "tech", "description": "desc"}]
    assert validate_handoff_target_from_list(targets, "billing") == "billing"


def test_validate_handoff_target_from_list_invalid():
    """validate_handoff_target_from_list rejects invalid target."""
    targets = [{"node_id": "billing", "description": "desc"}]
    with pytest.raises(ValueError, match="Invalid handoff target"):
        validate_handoff_target_from_list(targets, "unknown")


def test_filter_messages_for_handoff_inherited():
    """inherited mode passes full history."""
    messages = [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}]
    result = filter_messages_for_handoff(messages, "inherited", "src", "tgt")
    assert result == messages


def test_filter_messages_for_handoff_isolated():
    """isolated mode returns only system note."""
    messages = [{"role": "user", "content": "hello"}]
    result = filter_messages_for_handoff(messages, "isolated", "src", "tgt")
    assert len(result) == 1
    assert result[0]["role"] == "system"
    assert "Handed off from src" in result[0]["content"]


def test_filter_messages_for_handoff_summarized():
    """summarized mode returns structured summary."""
    messages = [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}]
    result = filter_messages_for_handoff(messages, "summarized", "src", "tgt")
    assert len(result) == 1
    assert result[0]["role"] == "system"
    assert "src" in result[0]["content"]


def test_filter_messages_for_handoff_invalid_mode():
    """Invalid context_mode raises ValueError."""
    with pytest.raises(ValueError, match="Invalid context_mode"):
        filter_messages_for_handoff([], "secure", "src", "tgt")


def test_build_handoff_channel_updates_inherited():
    """inherited mode includes full history + AIMessage + ToolMessage."""
    messages = [{"role": "user", "content": "hello"}]
    result = build_handoff_channel_updates(messages, "src", "tgt", "inherited", "call_123")
    assert len(result) == 3  # user + AIMessage + ToolMessage
    assert result[0]["role"] == "user"
    assert result[1]["role"] == "assistant"
    assert result[1]["tool_calls"][0]["id"] == "call_123"
    assert result[2]["role"] == "tool"
    assert result[2]["tool_call_id"] == "call_123"


def test_build_handoff_channel_updates_isolated():
    """isolated mode includes only system note + AIMessage + ToolMessage."""
    messages = [{"role": "user", "content": "hello"}]
    result = build_handoff_channel_updates(messages, "src", "tgt", "isolated", "call_123")
    assert len(result) == 3  # system + AIMessage + ToolMessage
    assert result[0]["role"] == "system"
    assert result[1]["role"] == "assistant"
    assert result[2]["role"] == "tool"


def test_build_handoff_channel_updates_preserves_tool_call_id():
    """tool_call_id is preserved exactly on both AIMessage and ToolMessage."""
    result = build_handoff_channel_updates([], "src", "tgt", "inherited", "call_xyz")
    aimessage = result[-2]
    toolmessage = result[-1]
    assert aimessage["tool_calls"][0]["id"] == "call_xyz"
    assert toolmessage["tool_call_id"] == "call_xyz"
