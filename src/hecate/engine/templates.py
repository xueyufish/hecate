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


def build_chat_graph(
    model: str,
    system_prompt: str = "You are a helpful assistant.",
    enable_suggestions: bool = False,
    generate_opening: bool = False,
    max_tool_iterations: int = 10,
) -> GraphConfig:
    """Build a chat-mode graph template that replicates ConversationService orchestration.

    **Architecture pattern:**

    Without suggestions::

        [__start__] → [llm] → [check_tools] ──(has tool)──→ [tool_call] → [llm] (loop)
                                     │
                                     (no tool)
                                     ▼
                                  [__end__]

    With suggestions::

        [__start__] → [llm] → [check_tools] ──(has tool)──→ [tool_call] → [llm] (loop)
                                     │
                                     (no tool)
                                     ▼
                               [suggestions] → [__end__]

    **State channels:**
    - ``messages`` (TOPIC): accumulates all conversation turns and tool results.
    - ``_has_tool_call`` (LAST_VALUE): flag set by LLMWorker when response contains tool_calls.
    - ``_route`` (LAST_VALUE): routing decision from ConditionWorker.
    - ``_session_id`` (LAST_VALUE): session identifier for evidence tracking.
    - ``_agent_id`` (LAST_VALUE): agent identifier for memory operations.
    - ``_user_id`` (LAST_VALUE): user identifier for memory retrieval.
    - ``_turn_index`` (LAST_VALUE): turn counter for evidence tracking.

    Args:
        model: LLM model identifier for the conversation node.
        system_prompt: System prompt for the LLM node.
        enable_suggestions: If True, add a SUGGESTION node after conversation.
        generate_opening: If True, configure the suggestion node for opening remarks.
        max_tool_iterations: Upper bound for tool-calling loop (enforced by PregelRuntime's max_supersteps).

    Returns:
        A GraphConfig ready for compilation and execution.
    """
    nodes: dict[str, NodeConfig] = {
        "llm": NodeConfig(
            id="llm",
            type=NodeType.CONVERSATION,
            config={
                "model": model,
                "system_prompt": system_prompt,
                "channels": {"readable": ["messages"], "writable": ["messages"]},
            },
        ),
        "check_tools": NodeConfig(
            id="check_tools",
            type=NodeType.CONDITION,
            config={"expression": "has_tool_call"},
        ),
        "tool_call": NodeConfig(
            id="tool_call",
            type=NodeType.TOOL_CALL,
            config={
                "channels": {"readable": ["messages"], "writable": ["messages"]},
            },
        ),
    }

    if enable_suggestions:
        nodes["suggestions"] = NodeConfig(
            id="suggestions",
            type=NodeType.SUGGESTION,
            config={
                "enable_suggestions": enable_suggestions,
                "generate_opening": generate_opening,
            },
        )

    edges = [
        Edge(source="llm", target="check_tools"),
        Edge(
            source="check_tools",
            target={"true": "tool_call", "false": "suggestions" if enable_suggestions else "__end__"},
        ),
        Edge(source="tool_call", target="llm"),
    ]

    if enable_suggestions:
        edges.append(Edge(source="suggestions", target="__end__"))

    state = {
        "messages": ChannelDef(type=ChannelType.TOPIC, default=[]),
        "_has_tool_call": ChannelDef(type=ChannelType.LAST_VALUE, default=False),
        "_route": ChannelDef(type=ChannelType.LAST_VALUE, default=""),
        "_session_id": ChannelDef(type=ChannelType.LAST_VALUE, default=""),
        "_agent_id": ChannelDef(type=ChannelType.LAST_VALUE, default=""),
        "_user_id": ChannelDef(type=ChannelType.LAST_VALUE, default=""),
        "_turn_index": ChannelDef(type=ChannelType.LAST_VALUE, default=0),
    }

    return GraphConfig(
        version="1.0",
        name="chat-agent",
        state=state,
        nodes=nodes,
        edges=edges,
        entry="llm",
    )


def build_three_layer_graph(
    guard_model: str = "",
    planner_model: str = "gpt-4o",
    sub_agent_model: str = "gpt-4o",
    guard_prompt: str = "You are a guard agent. Check user input for safety.",
    planner_prompt: str = "You are a planner agent. Decide the next action.",
    sub_agent_prompt: str = "You are a sub-agent. Execute the given task.",
) -> GraphConfig:
    """Build the preset three-layer Agent graph: Planner -> Tool Loop -> Sub-Agent.

    **Architecture pattern:**

    1. **Planner** (CONVERSATION) -- decides the next action. After each step, the
       ``check_tools`` condition node inspects the planner's output:
       - If a tool call is present (``has_tool_call`` is true), execution routes to
         the ``tool_call`` node which executes the tool and loops back to the planner.
       - If no tool call is present, execution routes to the sub-agent.
    2. **Sub-Agent** (AGENT) -- handles the actual task and the graph ends.

    Security is handled by Guardrail Hooks (PreLLMHook, PostLLMHook, PreToolHook,
    PostToolHook) injected into Workers by WorkflowExecutionService, NOT by a
    guard graph node. All execution modes get security protection automatically.

    **State channels:**
    - ``messages`` (TOPIC): accumulates all conversation turns and tool results.
    - ``context`` (LAST_VALUE): holds the latest planning context (overwritten each step).

    Args:
        guard_model: Kept for API compatibility; no longer used (guard is a Hook).
        planner_model: LLM model identifier for the planner node.
        sub_agent_model: Model or agent reference for the sub-agent node.
        guard_prompt: Kept for API compatibility; no longer used.
        planner_prompt: System prompt for the planner node.
        sub_agent_prompt: System prompt for the sub-agent node.

    Returns:
        A complete GraphConfig ready for compilation and execution.
    """
    nodes = {
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
        entry="planner",
    )


def build_fan_out_pipeline(
    researcher_model: str,
    analyst_model: str,
    summarizer_model: str,
    researcher_prompt: str = "You are a research agent. Gather relevant information on the given topic.",
    analyst_a_prompt: str = "You are Analyst A. Analyze from a financial perspective.",
    analyst_b_prompt: str = "You are Analyst B. Analyze from a technical perspective.",
    analyst_c_prompt: str = "You are Analyst C. Analyze from a market perspective.",
    summarizer_prompt: str = "You are a summarizer. Synthesize analysis from multiple perspectives.",
) -> GraphConfig:
    """Build a fan-out pipeline: researcher → parallel analysts → merge → summarizer.

    Args:
        researcher_model: Model for the researcher node.
        analyst_model: Model for the analyst nodes.
        summarizer_model: Model for the summarizer node.
        researcher_prompt: System prompt for the researcher.
        analyst_a_prompt: System prompt for Analyst A.
        analyst_b_prompt: System prompt for Analyst B.
        analyst_c_prompt: System prompt for Analyst C.
        summarizer_prompt: System prompt for the summarizer.

    Returns:
        A GraphConfig with parallel fan-out pattern.
    """
    nodes = {
        "researcher": NodeConfig(
            id="researcher",
            type=NodeType.AGENT,
            config={
                "model": researcher_model,
                "system_prompt": researcher_prompt,
                "channels": {"readable": ["messages"], "writable": ["messages", "research_data"]},
            },
        ),
        "fanout": NodeConfig(
            id="fanout",
            type=NodeType.FAN_OUT,
            config={"branches": ["analyst_a", "analyst_b", "analyst_c"]},
        ),
        "analyst_a": NodeConfig(
            id="analyst_a",
            type=NodeType.AGENT,
            config={
                "model": analyst_model,
                "system_prompt": analyst_a_prompt,
                "channels": {"readable": ["messages", "research_data"], "writable": ["messages"]},
            },
        ),
        "analyst_b": NodeConfig(
            id="analyst_b",
            type=NodeType.AGENT,
            config={
                "model": analyst_model,
                "system_prompt": analyst_b_prompt,
                "channels": {"readable": ["messages", "research_data"], "writable": ["messages"]},
            },
        ),
        "analyst_c": NodeConfig(
            id="analyst_c",
            type=NodeType.AGENT,
            config={
                "model": analyst_model,
                "system_prompt": analyst_c_prompt,
                "channels": {"readable": ["messages", "research_data"], "writable": ["messages"]},
            },
        ),
        "merge": NodeConfig(
            id="merge",
            type=NodeType.MERGE,
            config={"fan_out_source": "fanout", "output_channel": "analysis_results"},
        ),
        "summarizer": NodeConfig(
            id="summarizer",
            type=NodeType.AGENT,
            config={
                "model": summarizer_model,
                "system_prompt": summarizer_prompt,
                "channels": {"readable": ["messages", "analysis_results"], "writable": ["messages"]},
            },
        ),
    }

    edges = [
        Edge(source="researcher", target="fanout"),
        Edge(source="analyst_a", target="merge"),
        Edge(source="analyst_b", target="merge"),
        Edge(source="analyst_c", target="merge"),
        Edge(source="merge", target="summarizer"),
        Edge(source="summarizer", target="__end__"),
    ]

    state = {
        "messages": ChannelDef(type=ChannelType.TOPIC, default=[]),
        "research_data": ChannelDef(type=ChannelType.LAST_VALUE, default={}),
        "analysis_results": ChannelDef(type=ChannelType.LAST_VALUE, default={}),
    }

    return GraphConfig(
        version="1.0",
        name="fan-out-pipeline",
        state=state,
        nodes=nodes,
        edges=edges,
        entry="researcher",
    )


def build_conditional_pipeline(
    classifier_model: str,
    specialist_model: str,
    classifier_prompt: str = "You are a classifier. Categorize input as finance, tech, or legal.",
    finance_prompt: str = "You are a finance specialist.",
    tech_prompt: str = "You are a tech specialist.",
    legal_prompt: str = "You are a legal specialist.",
    general_prompt: str = "You are a general-purpose agent.",
) -> GraphConfig:
    """Build a conditional routing pipeline: classifier → multi-key condition → specialists.

    Args:
        classifier_model: Model for the classifier node.
        specialist_model: Model for the specialist nodes.
        classifier_prompt: System prompt for the classifier.
        finance_prompt: System prompt for the finance specialist.
        tech_prompt: System prompt for the tech specialist.
        legal_prompt: System prompt for the legal specialist.
        general_prompt: System prompt for the general fallback agent.

    Returns:
        A GraphConfig with multi-key conditional routing.
    """
    nodes = {
        "classifier": NodeConfig(
            id="classifier",
            type=NodeType.AGENT,
            config={
                "model": classifier_model,
                "system_prompt": classifier_prompt,
                "channels": {"readable": ["messages"], "writable": ["messages", "category"]},
            },
        ),
        "check_category": NodeConfig(
            id="check_category",
            type=NodeType.CONDITION,
            config={"expression": "category"},
        ),
        "finance_agent": NodeConfig(
            id="finance_agent",
            type=NodeType.AGENT,
            config={
                "model": specialist_model,
                "system_prompt": finance_prompt,
                "channels": {"readable": ["messages"], "writable": ["messages"]},
            },
        ),
        "tech_agent": NodeConfig(
            id="tech_agent",
            type=NodeType.AGENT,
            config={
                "model": specialist_model,
                "system_prompt": tech_prompt,
                "channels": {"readable": ["messages"], "writable": ["messages"]},
            },
        ),
        "legal_agent": NodeConfig(
            id="legal_agent",
            type=NodeType.AGENT,
            config={
                "model": specialist_model,
                "system_prompt": legal_prompt,
                "channels": {"readable": ["messages"], "writable": ["messages"]},
            },
        ),
        "general_agent": NodeConfig(
            id="general_agent",
            type=NodeType.AGENT,
            config={
                "model": specialist_model,
                "system_prompt": general_prompt,
                "channels": {"readable": ["messages"], "writable": ["messages"]},
            },
        ),
    }

    edges = [
        Edge(source="classifier", target="check_category"),
        Edge(
            source="check_category",
            target={
                "finance": "finance_agent",
                "tech": "tech_agent",
                "legal": "legal_agent",
                "default": "general_agent",
            },
        ),
        Edge(source="finance_agent", target="__end__"),
        Edge(source="tech_agent", target="__end__"),
        Edge(source="legal_agent", target="__end__"),
        Edge(source="general_agent", target="__end__"),
    ]

    state = {
        "messages": ChannelDef(type=ChannelType.TOPIC, default=[]),
        "category": ChannelDef(type=ChannelType.LAST_VALUE, default=""),
    }

    return GraphConfig(
        version="1.0",
        name="conditional-pipeline",
        state=state,
        nodes=nodes,
        edges=edges,
        entry="classifier",
    )


def build_reflection_loop(
    drafter_model: str,
    reviewer_model: str,
    reviser_model: str,
    drafter_prompt: str = "You are a drafter. Create an initial draft.",
    reviewer_prompt: str = (
        "You are a reviewer. Evaluate quality. Set quality_status to 'approved' or 'needs_improvement'."
    ),
    reviser_prompt: str = "You are a reviser. Improve the draft based on feedback.",
) -> GraphConfig:
    """Build a reflection loop: drafter → reviewer → check quality → revise or finish.

    Args:
        drafter_model: Model for the drafter node.
        reviewer_model: Model for the reviewer node.
        reviser_model: Model for the reviser node.
        drafter_prompt: System prompt for the drafter.
        reviewer_prompt: System prompt for the reviewer.
        reviser_prompt: System prompt for the reviser.

    Returns:
        A GraphConfig with iterative refinement loop.
    """
    nodes = {
        "drafter": NodeConfig(
            id="drafter",
            type=NodeType.AGENT,
            config={
                "model": drafter_model,
                "system_prompt": drafter_prompt,
                "channels": {"readable": ["messages"], "writable": ["messages", "draft"]},
            },
        ),
        "reviewer": NodeConfig(
            id="reviewer",
            type=NodeType.AGENT,
            config={
                "model": reviewer_model,
                "system_prompt": reviewer_prompt,
                "channels": {"readable": ["messages", "draft"], "writable": ["messages", "quality_status"]},
            },
        ),
        "check_quality": NodeConfig(
            id="check_quality",
            type=NodeType.CONDITION,
            config={"expression": "quality_status == 'needs_improvement'"},
        ),
        "reviser": NodeConfig(
            id="reviser",
            type=NodeType.AGENT,
            config={
                "model": reviser_model,
                "system_prompt": reviser_prompt,
                "channels": {"readable": ["messages", "draft", "quality_status"], "writable": ["messages", "draft"]},
            },
        ),
    }

    edges = [
        Edge(source="drafter", target="reviewer"),
        Edge(source="reviewer", target="check_quality"),
        Edge(source="check_quality", target={"true": "reviser", "false": "__end__"}),
        Edge(source="reviser", target="reviewer"),
    ]

    state = {
        "messages": ChannelDef(type=ChannelType.TOPIC, default=[]),
        "draft": ChannelDef(type=ChannelType.LAST_VALUE, default=""),
        "quality_status": ChannelDef(type=ChannelType.LAST_VALUE, default=""),
    }

    return GraphConfig(
        version="1.0",
        name="reflection-loop",
        state=state,
        nodes=nodes,
        edges=edges,
        entry="drafter",
    )
