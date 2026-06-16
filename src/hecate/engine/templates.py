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


def build_sequential_pipeline(
    stages: list[dict[str, str]],
    revision_config: dict[str, str] | None = None,
) -> GraphConfig:
    """Build a sequential pipeline: stage_0 → stage_1 → ... → stage_N.

    Each stage is an AGENT node that reads from a shared ``messages`` TOPIC
    channel and an optional per-stage LAST_VALUE channel carrying the previous
    stage's output.

    **Architecture pattern (without revision):**

    ::

        [__start__] → [stage_0] → [stage_1] → ... → [stage_N] → [__end__]

    **Architecture pattern (with revision):**

    ::

        [__start__] → [stage_0] → ... → [stage_N] → [check_revision] ──(true)──→ [target_stage]
                                                            │                        │
                                                            (false)                  │
                                                            ▼                        │
                                                         [__end__]          ←←←←←←←←┘

    **State channels:**
    - ``messages`` (TOPIC): shared across all stages — accumulates every turn.
    - ``{stage_id}_output`` (LAST_VALUE): written by stage N, read by stage N+1.
    - ``revision_status`` (LAST_VALUE): only present when *revision_config* is set.

    Args:
        stages: Ordered list of stage definitions.  Each dict MUST contain
            ``id`` (unique identifier), ``model`` (LLM model), and
            ``system_prompt``.
        revision_config: Optional dict with ``expression`` (condition to
            evaluate) and ``target_stage`` (stage ID to loop back to when the
            expression is true).

    Returns:
        A GraphConfig ready for compilation and execution.

    Raises:
        ValueError: If fewer than 2 stages or duplicate stage IDs.
    """
    if len(stages) < 2:
        msg = "Sequential pipeline requires at least 2 stages."
        raise ValueError(msg)

    stage_ids = [s["id"] for s in stages]
    if len(stage_ids) != len(set(stage_ids)):
        msg = f"Duplicate stage IDs: {stage_ids}"
        raise ValueError(msg)

    nodes: dict[str, NodeConfig] = {}
    state: dict[str, ChannelDef] = {
        "messages": ChannelDef(type=ChannelType.TOPIC, default=[]),
    }

    for i, stage in enumerate(stages):
        readable = ["messages"]
        writable = ["messages"]

        if i > 0:
            prev_output = f"{stages[i - 1]['id']}_output"
            readable.append(prev_output)

        output_channel = f"{stage['id']}_output"
        state[output_channel] = ChannelDef(type=ChannelType.LAST_VALUE, default=None)

        if i < len(stages) - 1:
            writable.append(output_channel)
        elif revision_config is not None:
            writable.append(output_channel)
            writable.append("revision_status")
            readable.append("revision_status")

        nodes[stage["id"]] = NodeConfig(
            id=stage["id"],
            type=NodeType.AGENT,
            config={
                "model": stage["model"],
                "system_prompt": stage["system_prompt"],
                "channels": {"readable": readable, "writable": writable},
            },
        )

    edges: list[Edge] = []
    for i in range(len(stages) - 1):
        edges.append(Edge(source=stages[i]["id"], target=stages[i + 1]["id"]))

    if revision_config is not None:
        state["revision_status"] = ChannelDef(type=ChannelType.LAST_VALUE, default="")

        target_id = revision_config["target_stage"]
        if target_id in nodes:
            target_readable = nodes[target_id].config.setdefault("channels", {}).setdefault("readable", [])
            if "revision_status" not in target_readable:
                target_readable.append("revision_status")

        nodes["check_revision"] = NodeConfig(
            id="check_revision",
            type=NodeType.CONDITION,
            config={
                "expression": revision_config["expression"],
                "channels": {"readable": ["revision_status"]},
            },
        )

        last_stage_id = stages[-1]["id"]
        edges.append(Edge(source=last_stage_id, target="check_revision"))
        edges.append(
            Edge(source="check_revision", target={"true": target_id, "false": "__end__"}),
        )
    else:
        edges.append(Edge(source=stages[-1]["id"], target="__end__"))

    return GraphConfig(
        version="1.0",
        name="sequential-pipeline",
        state=state,
        nodes=nodes,
        edges=edges,
        entry=stages[0]["id"],
    )


def build_broadcast_pipeline(
    participants: list[dict[str, str]],
    moderator: dict[str, str] | None = None,
) -> GraphConfig:
    """Build a broadcast pipeline: sequential round-robin with shared messages.

    All participants share the same ``messages`` TOPIC channel so every agent
    sees all previous turns.  An optional moderator frames the discussion at
    the beginning and end.

    **Architecture pattern (without moderator):**

    ::

        [__start__] → [p_0] → [p_1] → ... → [p_N] → [__end__]

    **Architecture pattern (with moderator):**

    ::

        [__start__] → [moderator] → [p_0] → ... → [p_N] → [moderator_summary] → [__end__]

    **State channels:**
    - ``messages`` (TOPIC): shared by all nodes — every participant reads and
      writes to the same channel.

    Args:
        participants: Ordered list of participant definitions.  Each dict MUST
            contain ``id``, ``model``, and ``system_prompt``.
        moderator: Optional moderator definition with ``model`` and
            ``system_prompt``.  When provided, a ``moderator`` node is inserted
            at the start and a ``moderator_summary`` node at the end.

    Returns:
        A GraphConfig ready for compilation and execution.

    Raises:
        ValueError: If fewer than 2 participants or duplicate participant IDs.
    """
    if len(participants) < 2:
        msg = "Broadcast pipeline requires at least 2 participants."
        raise ValueError(msg)

    participant_ids = [p["id"] for p in participants]
    if len(participant_ids) != len(set(participant_ids)):
        msg = f"Duplicate participant IDs: {participant_ids}"
        raise ValueError(msg)

    nodes: dict[str, NodeConfig] = {}
    edges: list[Edge] = []
    state: dict[str, ChannelDef] = {
        "messages": ChannelDef(type=ChannelType.TOPIC, default=[]),
    }

    shared_channels = {"readable": ["messages"], "writable": ["messages"]}

    if moderator is not None:
        nodes["moderator"] = NodeConfig(
            id="moderator",
            type=NodeType.AGENT,
            config={
                "model": moderator["model"],
                "system_prompt": moderator["system_prompt"],
                "channels": dict(shared_channels),
            },
        )
        nodes["moderator_summary"] = NodeConfig(
            id="moderator_summary",
            type=NodeType.AGENT,
            config={
                "model": moderator["model"],
                "system_prompt": moderator["system_prompt"],
                "channels": dict(shared_channels),
            },
        )

    for p in participants:
        nodes[p["id"]] = NodeConfig(
            id=p["id"],
            type=NodeType.AGENT,
            config={
                "model": p["model"],
                "system_prompt": p["system_prompt"],
                "channels": dict(shared_channels),
            },
        )

    ordered_ids: list[str] = []
    if moderator is not None:
        ordered_ids.append("moderator")
    ordered_ids.extend(p["id"] for p in participants)
    if moderator is not None:
        ordered_ids.append("moderator_summary")

    for i in range(len(ordered_ids) - 1):
        edges.append(Edge(source=ordered_ids[i], target=ordered_ids[i + 1]))
    edges.append(Edge(source=ordered_ids[-1], target="__end__"))

    return GraphConfig(
        version="1.0",
        name="broadcast-pipeline",
        state=state,
        nodes=nodes,
        edges=edges,
        entry=ordered_ids[0],
    )


def build_negotiation_graph(
    proposer_model: str,
    responder_model: str,
    proposer_prompt: str = "You are a proposer. Make a proposal for the given task.",
    responder_prompt: str = (
        "You are a responder. Evaluate proposals and respond with "
        "'accepted' or a counter-proposal. Set agreement_status to "
        "'accepted' when you agree, or 'counter' for counter-proposal."
    ),
    max_rounds: int = 5,
) -> GraphConfig:
    """Build a negotiation graph: proposer → responder → check agreement loop.

    Two agents negotiate via a shared ``negotiation_channel``. The responder
    sets ``agreement_status`` to signal acceptance or counter-proposal. A
    condition node checks the status and either terminates or loops back.

    **Architecture pattern:**

    ::

        [__start__] → [proposer] → [responder] → [check_agreement]
                                                         │
                                              (accepted)  │  (counter)
                                                 ▼        │
                                              [__end__]   │
                                                         │
                                                ←←←←←←←←┘

    **State channels:**
    - ``messages`` (TOPIC): accumulates all negotiation turns.
    - ``agreement_status`` (LAST_VALUE): "accepted" or "counter".
    - ``negotiation_round`` (LAST_VALUE): round counter for max_rounds guard.

    Args:
        proposer_model: Model for the proposer node.
        responder_model: Model for the responder node.
        proposer_prompt: System prompt for the proposer.
        responder_prompt: System prompt for the responder.
        max_rounds: Maximum negotiation rounds before forced termination.

    Returns:
        A GraphConfig ready for compilation and execution.
    """
    nodes = {
        "proposer": NodeConfig(
            id="proposer",
            type=NodeType.AGENT,
            config={
                "model": proposer_model,
                "system_prompt": proposer_prompt,
                "channels": {
                    "readable": ["messages", "agreement_status"],
                    "writable": ["messages", "negotiation_channel"],
                },
            },
        ),
        "responder": NodeConfig(
            id="responder",
            type=NodeType.AGENT,
            config={
                "model": responder_model,
                "system_prompt": responder_prompt,
                "channels": {
                    "readable": ["messages", "negotiation_channel"],
                    "writable": ["messages", "agreement_status"],
                },
            },
        ),
        "check_agreement": NodeConfig(
            id="check_agreement",
            type=NodeType.CONDITION,
            config={"expression": "agreement_status == 'accepted'"},
        ),
    }

    edges = [
        Edge(source="proposer", target="responder"),
        Edge(source="responder", target="check_agreement"),
        Edge(source="check_agreement", target={"true": "__end__", "false": "proposer"}),
    ]

    state = {
        "messages": ChannelDef(type=ChannelType.TOPIC, default=[]),
        "agreement_status": ChannelDef(type=ChannelType.LAST_VALUE, default=""),
        "negotiation_channel": ChannelDef(type=ChannelType.LAST_VALUE, default=""),
        "negotiation_round": ChannelDef(type=ChannelType.LAST_VALUE, default=0),
    }

    return GraphConfig(
        version="1.0",
        name="negotiation",
        state=state,
        nodes=nodes,
        edges=edges,
        entry="proposer",
    )


def build_debate_graph(
    debater_a_model: str,
    debater_b_model: str,
    debater_a_prompt: str = "You are Debater A. Present clear, evidence-based arguments.",
    debater_b_prompt: str = "You are Debater B. Present counter-arguments with supporting reasoning.",
    judge_model: str | None = None,
    judge_prompt: str = ("You are a debate judge. Review all arguments and deliver a balanced verdict."),
    rounds: int = 3,
) -> GraphConfig:
    """Build a debate graph: alternating arguments with optional judge evaluation.

    Two debaters take turns presenting arguments and rebuttals. After all
    rounds, an optional judge reviews the debate and produces a verdict.

    **Architecture pattern (with judge):**

    ::

        [__start__] → [debater_a] → [debater_b] → [check_rounds]
                                                       │
                                          (continue)   │  (done)
                                              ▼        │
                                        [debater_a]    │
                                              ↑        │
                                              └────────┘
                                              │
                                              ▼
                                          [judge] → [__end__]

    **State channels:**
    - ``messages`` (TOPIC): accumulates all debate arguments.
    - ``debate_round`` (LAST_VALUE): current round counter.
    - ``max_debate_rounds`` (LAST_VALUE): total rounds limit.

    Args:
        debater_a_model: Model for Debater A.
        debater_b_model: Model for Debater B.
        debater_a_prompt: System prompt for Debater A.
        debater_b_prompt: System prompt for Debater B.
        judge_model: Optional model for the judge. None = no judge.
        judge_prompt: System prompt for the judge (unused if no judge).
        rounds: Number of debate rounds.

    Returns:
        A GraphConfig ready for compilation and execution.
    """
    nodes: dict[str, NodeConfig] = {
        "debater_a": NodeConfig(
            id="debater_a",
            type=NodeType.AGENT,
            config={
                "model": debater_a_model,
                "system_prompt": debater_a_prompt,
                "channels": {"readable": ["messages"], "writable": ["messages"]},
            },
        ),
        "debater_b": NodeConfig(
            id="debater_b",
            type=NodeType.AGENT,
            config={
                "model": debater_b_model,
                "system_prompt": debater_b_prompt,
                "channels": {"readable": ["messages"], "writable": ["messages"]},
            },
        ),
        "check_rounds": NodeConfig(
            id="check_rounds",
            type=NodeType.CONDITION,
            config={"expression": "debate_round < max_debate_rounds"},
        ),
    }

    if judge_model is not None:
        nodes["judge"] = NodeConfig(
            id="judge",
            type=NodeType.AGENT,
            config={
                "model": judge_model,
                "system_prompt": judge_prompt,
                "channels": {"readable": ["messages"], "writable": ["messages"]},
            },
        )

    debate_done_target = "judge" if judge_model is not None else "__end__"

    edges = [
        Edge(source="debater_a", target="debater_b"),
        Edge(source="debater_b", target="check_rounds"),
        Edge(source="check_rounds", target={"true": "debater_a", "false": debate_done_target}),
    ]

    if judge_model is not None:
        edges.append(Edge(source="judge", target="__end__"))

    state = {
        "messages": ChannelDef(type=ChannelType.TOPIC, default=[]),
        "debate_round": ChannelDef(type=ChannelType.LAST_VALUE, default=0),
        "max_debate_rounds": ChannelDef(type=ChannelType.LAST_VALUE, default=rounds),
    }

    return GraphConfig(
        version="1.0",
        name="debate",
        state=state,
        nodes=nodes,
        edges=edges,
        entry="debater_a",
    )
