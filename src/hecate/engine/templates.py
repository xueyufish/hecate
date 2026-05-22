"""Preset graph templates for common agent architectures.

This module provides factory functions that return pre-built GraphConfig instances.
These templates encode proven patterns and can be used as-is or as starting points
for customization via the Graph DSL.
"""

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

    **Architecture pattern:**

    1. **Guard** (CONVERSATION) -- inspects user input for safety/policy violations
       and passes safe messages to the planner.
    2. **Planner** (CONVERSATION) -- decides the next action. After each step, the
       ``check_tools`` condition node inspects the planner's output:
       - If a tool call is present (``has_tool_call`` is true), execution routes to
         the ``tool_call`` node which executes the tool and loops back to the planner.
       - If no tool call is present, execution routes to the sub-agent.
    3. **Sub-Agent** (AGENT) -- handles the actual task and the graph ends.

    **State channels:**
    - ``messages`` (TOPIC): accumulates all conversation turns and tool results.
    - ``context`` (LAST_VALUE): holds the latest planning context (overwritten each step).

    Use this template when you need a standard input-safety-check -> plan -> execute
    workflow. For custom control flows, build a GraphConfig directly or modify the
    one returned here.

    Args:
        guard_model: LLM model identifier for the guard node.
        planner_model: LLM model identifier for the planner node.
        sub_agent_model: Model or agent reference for the sub-agent node.
        guard_prompt: System prompt for the guard node.
        planner_prompt: System prompt for the planner node.
        sub_agent_prompt: System prompt for the sub-agent node.

    Returns:
        A complete GraphConfig ready for compilation and execution.
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
