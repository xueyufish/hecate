from __future__ import annotations

from hecate.engine.types import (
    ChannelDef,
    ChannelType,
    Edge,
    GraphConfig,
    NodeConfig,
    NodeType,
)


def build_three_layer_graph(
    guard_model: str,
    planner_model: str,
    sub_agent_model: str,
    guard_prompt: str = "You are a guard agent. Check user input for safety.",
    planner_prompt: str = "You are a planner agent. Decide the next action.",
    sub_agent_prompt: str = "You are a sub-agent. Execute the given task.",
) -> GraphConfig:
    """Build the preset three-layer Agent graph: Guard -> Planner -> Sub-Agent.

    The planner checks for tool calls after each step. If a tool call is present,
    execution goes to the tool_call node and loops back to the planner. Otherwise,
    the sub_agent node handles the task and the graph ends.
    """
    nodes = {
        "guard": NodeConfig(
            id="guard",
            type=NodeType.CONVERSATION,
            config={
                "model": guard_model,
                "system_prompt": guard_prompt,
                "channels": {"readable": ["messages", "context"], "writable": ["messages"]},
            },
        ),
        "planner": NodeConfig(
            id="planner",
            type=NodeType.CONVERSATION,
            config={
                "model": planner_model,
                "system_prompt": planner_prompt,
                "channels": {"readable": ["messages", "context"], "writable": ["messages", "context"]},
            },
        ),
        "sub_agent": NodeConfig(
            id="sub_agent",
            type=NodeType.AGENT,
            config={
                "agent_ref": "sub-agent",
                "channels": {"readable": ["messages", "context"], "writable": ["messages"]},
            },
        ),
        "tool_call": NodeConfig(
            id="tool_call",
            type=NodeType.TOOL_CALL,
            config={
                "channels": {"readable": ["messages"], "writable": ["messages"]},
            },
        ),
        "check_tools": NodeConfig(
            id="check_tools",
            type=NodeType.CONDITION,
            config={
                "expression": "has_tool_call",
            },
        ),
    }

    edges = [
        Edge(source="guard", target="planner"),
        Edge(source="planner", target="check_tools"),
        Edge(source="check_tools", target={"true": "tool_call", "false": "sub_agent"}),
        Edge(source="tool_call", target="planner"),
        Edge(source="sub_agent", target="__end__"),
    ]

    state = {
        "messages": ChannelDef(type=ChannelType.TOPIC, default=[]),
        "context": ChannelDef(type=ChannelType.LAST_VALUE, default={}),
    }

    return GraphConfig(
        version="1.0",
        name="three-layer-agent",
        state=state,
        nodes=nodes,
        edges=edges,
        entry="guard",
    )
