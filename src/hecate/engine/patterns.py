"""Collaboration pattern classification and graph generation.

This module provides a unified vocabulary for 6 collaboration patterns,
structural inference to detect patterns from existing graphs, and a
builder that generates GraphConfig instances from pattern selection.
"""

from __future__ import annotations

from enum import StrEnum

from hecate.engine.templates import (
    build_broadcast_pipeline,
    build_debate_graph,
    build_fan_out_pipeline,
    build_negotiation_graph,
    build_sequential_pipeline,
)
from hecate.engine.types import (
    ChannelDef,
    ChannelType,
    Edge,
    GraphConfig,
    NodeConfig,
    NodeType,
)


class CollaborationPattern(StrEnum):
    """Canonical collaboration patterns for multi-agent orchestration."""

    SEQUENTIAL = "sequential"
    PARALLEL = "parallel"
    HANDOFF = "handoff"
    BROADCAST = "broadcast"
    NEGOTIATION = "negotiation"
    DEBATE = "debate"


def infer_pattern(config: GraphConfig) -> CollaborationPattern | None:
    """Detect the collaboration pattern of a graph from its structure.

    Uses deterministic structural heuristics based on node types, edge
    triggers, and channel configurations.  First matching rule wins.

    Args:
        config: A parsed GraphConfig to analyze.

    Returns:
        The detected CollaborationPattern, or None if no pattern matches.
    """
    node_types = {nid: n.type for nid, n in config.nodes.items()}
    edges = config.edges

    has_fan_out = any(t == NodeType.FAN_OUT for t in node_types.values())
    has_merge = any(t == NodeType.MERGE for t in node_types.values())

    # PARALLEL: contains FAN_OUT or MERGE nodes
    if has_fan_out or has_merge:
        return CollaborationPattern.PARALLEL

    # HANDOFF: all edges have trigger="handoff"
    handoff_edges = [e for e in edges if e.trigger == "handoff"]
    if edges and len(handoff_edges) == len(edges):
        return CollaborationPattern.HANDOFF

    agent_nodes = [nid for nid, t in node_types.items() if t == NodeType.AGENT]
    agent_count = len(agent_nodes)
    condition_nodes = [nid for nid, t in node_types.items() if t == NodeType.CONDITION]

    # NEGOTIATION: 2 agents + 1 condition + loop edge, no fan-out/merge
    if (
        agent_count == 2
        and len(condition_nodes) == 1
        and not has_fan_out
        and not has_merge
        and _has_loop_edge(edges)
        and "agreement_status" in config.state
    ):
        return CollaborationPattern.NEGOTIATION

    # DEBATE: 2+ agents + loop edge + round counter channel, no fan-out/merge
    if agent_count >= 2 and not has_fan_out and not has_merge and _has_loop_edge(edges) and _has_round_counter(config):
        return CollaborationPattern.DEBATE

    # SEQUENTIAL: linear chain with per-stage output channels
    # (may have revision loops via condition nodes, distinguishing from broadcast)
    if agent_count >= 2 and not handoff_edges and _has_per_stage_channels(config):
        return CollaborationPattern.SEQUENTIAL

    # BROADCAST: all agent nodes share read/write on a single TOPIC channel
    # and there are no other distinguishing state channels (checked last)
    if len(agent_nodes) >= 2 and not has_fan_out and not has_merge and _is_broadcast_pattern(config, agent_nodes):
        return CollaborationPattern.BROADCAST

    return None


def build_graph_from_pattern(
    pattern: CollaborationPattern,
    config: dict,
) -> GraphConfig:
    """Generate a GraphConfig from a pattern selection and configuration.

    Delegates to the appropriate builder function from templates.py,
    translating the unified parameter schema to function-specific args.

    Args:
        pattern: The collaboration pattern to generate.
        config: Pattern-specific configuration parameters.

    Returns:
        A GraphConfig ready for compilation and execution.

    Raises:
        ValueError: If the pattern is not recognized or config is invalid.
    """
    match pattern:
        case CollaborationPattern.SEQUENTIAL:
            stages = config.get("stages", [])
            if not stages:
                msg = "Sequential pattern requires 'stages' parameter."
                raise ValueError(msg)
            return build_sequential_pipeline(
                stages=stages,
                revision_config=config.get("revision_config"),
            )

        case CollaborationPattern.PARALLEL:
            coordinator = config.get("coordinator")
            workers = config.get("workers")
            aggregator = config.get("aggregator")
            if not coordinator or not workers or not aggregator:
                msg = "Parallel pattern requires 'coordinator', 'workers', and 'aggregator' parameters."
                raise ValueError(msg)
            return build_fan_out_pipeline(
                researcher_model=coordinator["model"],
                analyst_model=workers[0]["model"],
                summarizer_model=aggregator["model"],
                researcher_prompt=coordinator.get("system_prompt", "You are a coordinator agent."),
                summarizer_prompt=aggregator.get("system_prompt", "You are an aggregator agent."),
            )

        case CollaborationPattern.HANDOFF:
            router = config.get("router")
            specialists = config.get("specialists")
            if not router or not specialists:
                msg = "Handoff pattern requires 'router' and 'specialists' parameters."
                raise ValueError(msg)
            return _build_handoff_graph(router, specialists)

        case CollaborationPattern.BROADCAST:
            participants = config.get("participants")
            if not participants:
                msg = "Broadcast pattern requires 'participants' parameter."
                raise ValueError(msg)
            return build_broadcast_pipeline(
                participants=participants,
                moderator=config.get("moderator"),
            )

        case CollaborationPattern.NEGOTIATION:
            proposer = config.get("proposer")
            responder = config.get("responder")
            if not proposer or not responder:
                msg = "Negotiation pattern requires 'proposer' and 'responder' parameters."
                raise ValueError(msg)
            return build_negotiation_graph(
                proposer_model=proposer["model"],
                responder_model=responder["model"],
                proposer_prompt=proposer.get("system_prompt", "You are a proposer."),
                responder_prompt=responder.get("system_prompt", "You are a responder."),
                max_rounds=config.get("max_rounds", 5),
            )

        case CollaborationPattern.DEBATE:
            debater_a = config.get("debater_a")
            debater_b = config.get("debater_b")
            if not debater_a or not debater_b:
                msg = "Debate pattern requires 'debater_a' and 'debater_b' parameters."
                raise ValueError(msg)
            return build_debate_graph(
                debater_a_model=debater_a["model"],
                debater_b_model=debater_b["model"],
                debater_a_prompt=debater_a.get("system_prompt", "You are Debater A."),
                debater_b_prompt=debater_b.get("system_prompt", "You are Debater B."),
                judge_model=config.get("judge", {}).get("model"),
                judge_prompt=config.get("judge", {}).get("system_prompt", "You are a judge."),
                rounds=config.get("rounds", 3),
            )

        case _:
            msg = f"Unknown collaboration pattern: {pattern}"
            raise ValueError(msg)


def _is_broadcast_pattern(config: GraphConfig, agent_nodes: list[str]) -> bool:
    """Check if all agent nodes share read/write on a common TOPIC channel."""
    topic_channels = {name for name, ch in config.state.items() if ch.type == ChannelType.TOPIC}
    if not topic_channels:
        return False

    for nid in agent_nodes:
        node = config.nodes[nid]
        ch_config = node.config.get("channels", {})
        readable = set(ch_config.get("readable", []))
        writable = set(ch_config.get("writable", []))
        # At least one TOPIC channel must be shared (read AND write)
        if not (readable & topic_channels) or not (writable & topic_channels):
            return False

    return True


def _has_loop_edge(edges: list[Edge]) -> bool:
    """Check if any edge creates a cycle (target appears earlier in chain)."""
    seen_sources: set[str] = set()
    for edge in edges:
        targets = [edge.target] if isinstance(edge.target, str) else list(edge.target.values())
        for t in targets:
            if t in seen_sources and t != "__end__":
                return True
        seen_sources.add(edge.source)
    return False


def _has_round_counter(config: GraphConfig) -> bool:
    """Check if graph has a round/iteration counter channel."""
    for ch in config.state.values():
        if ch.type == ChannelType.LAST_VALUE:
            if ch.reduce_fn is not None and "round" in ch.reduce_fn:
                return True
            if ch.default is not None and "round" in str(ch.default):
                return True
    # Check channel names for round indicators
    return any("round" in name.lower() for name in config.state)


def _is_linear_chain(config: GraphConfig, agent_nodes: list[str]) -> bool:
    """Check if agents form a linear chain (no branching)."""
    return all(not isinstance(edge.target, dict) for edge in config.edges)


def _has_per_stage_channels(config: GraphConfig) -> bool:
    """Check if graph has per-stage output channels (e.g. stage_output)."""
    return any("_output" in name for name in config.state)


def _build_handoff_graph(
    router: dict[str, str],
    specialists: list[dict[str, str]],
) -> GraphConfig:
    """Build a handoff graph: router agent with handoff edges to specialists.

    Args:
        router: Dict with 'id', 'model', 'system_prompt'.
        specialists: List of dicts, each with 'id', 'model', 'system_prompt'.

    Returns:
        A GraphConfig with handoff edges.
    """
    router_id = router.get("id", "router")
    nodes: dict[str, NodeConfig] = {
        router_id: NodeConfig(
            id=router_id,
            type=NodeType.AGENT,
            config={
                "model": router["model"],
                "system_prompt": router.get("system_prompt", "You are a router agent."),
                "channels": {"readable": ["messages"], "writable": ["messages"]},
            },
        ),
    }

    edges: list[Edge] = []
    for spec in specialists:
        spec_id = spec["id"]
        nodes[spec_id] = NodeConfig(
            id=spec_id,
            type=NodeType.AGENT,
            config={
                "model": spec["model"],
                "system_prompt": spec.get("system_prompt", f"You are a specialist agent ({spec_id})."),
                "channels": {"readable": ["messages"], "writable": ["messages"]},
            },
        )
        edges.append(Edge(source=router_id, target=spec_id, trigger="handoff"))
        edges.append(Edge(source=spec_id, target="__end__"))

    state = {
        "messages": ChannelDef(type=ChannelType.TOPIC, default=[]),
    }

    return GraphConfig(
        version="1.0",
        name="handoff-pipeline",
        state=state,
        nodes=nodes,
        edges=edges,
        entry=router_id,
    )


PATTERN_DEFINITIONS: list[dict] = [
    {
        "id": CollaborationPattern.SEQUENTIAL,
        "name": "Sequential Pipeline",
        "description": (
            "A linear chain of agents where each stage processes "
            "the output of the previous one. Best for deterministic "
            "multi-step processes."
        ),
        "parameters": {
            "type": "object",
            "required": ["stages"],
            "properties": {
                "stages": {
                    "type": "array",
                    "minItems": 2,
                    "items": {
                        "type": "object",
                        "required": ["id", "model", "system_prompt"],
                        "properties": {
                            "id": {"type": "string", "description": "Unique stage identifier"},
                            "model": {"type": "string", "description": "LLM model for this stage"},
                            "system_prompt": {"type": "string", "description": "Agent system prompt"},
                        },
                    },
                },
                "revision_config": {
                    "type": "object",
                    "description": "Optional revision loop configuration",
                    "properties": {
                        "expression": {"type": "string"},
                        "target_stage": {"type": "string"},
                    },
                },
            },
        },
        "preview": {"min_nodes": 2, "min_edges": 1},
    },
    {
        "id": CollaborationPattern.PARALLEL,
        "name": "Parallel Fan-Out",
        "description": (
            "A coordinator fans out work to parallel agents, then merges results. Best for multi-perspective analysis."
        ),
        "parameters": {
            "type": "object",
            "required": ["coordinator", "workers", "aggregator"],
            "properties": {
                "coordinator": {
                    "type": "object",
                    "required": ["model"],
                    "properties": {
                        "model": {"type": "string"},
                        "system_prompt": {"type": "string"},
                    },
                },
                "workers": {
                    "type": "array",
                    "minItems": 2,
                    "items": {
                        "type": "object",
                        "required": ["model"],
                        "properties": {
                            "model": {"type": "string"},
                            "system_prompt": {"type": "string"},
                        },
                    },
                },
                "aggregator": {
                    "type": "object",
                    "required": ["model"],
                    "properties": {
                        "model": {"type": "string"},
                        "system_prompt": {"type": "string"},
                    },
                },
            },
        },
        "preview": {"min_nodes": 5, "min_edges": 6},
    },
    {
        "id": CollaborationPattern.HANDOFF,
        "name": "Handoff Routing",
        "description": (
            "A router agent transfers control to specialist agents "
            "based on context. Best for triage and task delegation."
        ),
        "parameters": {
            "type": "object",
            "required": ["router", "specialists"],
            "properties": {
                "router": {
                    "type": "object",
                    "required": ["model"],
                    "properties": {
                        "id": {"type": "string"},
                        "model": {"type": "string"},
                        "system_prompt": {"type": "string"},
                    },
                },
                "specialists": {
                    "type": "array",
                    "minItems": 2,
                    "items": {
                        "type": "object",
                        "required": ["id", "model"],
                        "properties": {
                            "id": {"type": "string"},
                            "model": {"type": "string"},
                            "system_prompt": {"type": "string"},
                        },
                    },
                },
            },
        },
        "preview": {"min_nodes": 3, "min_edges": 4},
    },
    {
        "id": CollaborationPattern.BROADCAST,
        "name": "Broadcast Discussion",
        "description": (
            "Agents share a common message space and speak in sequence. "
            "Best for round-robin discussions and brainstorming."
        ),
        "parameters": {
            "type": "object",
            "required": ["participants"],
            "properties": {
                "participants": {
                    "type": "array",
                    "minItems": 2,
                    "items": {
                        "type": "object",
                        "required": ["id", "model", "system_prompt"],
                        "properties": {
                            "id": {"type": "string"},
                            "model": {"type": "string"},
                            "system_prompt": {"type": "string"},
                        },
                    },
                },
                "moderator": {
                    "type": "object",
                    "properties": {
                        "model": {"type": "string"},
                        "system_prompt": {"type": "string"},
                    },
                },
            },
        },
        "preview": {"min_nodes": 2, "min_edges": 1},
    },
    {
        "id": CollaborationPattern.NEGOTIATION,
        "name": "Negotiation Loop",
        "description": (
            "A proposer and responder negotiate in a loop until agreement. Best for collaborative decision-making."
        ),
        "parameters": {
            "type": "object",
            "required": ["proposer", "responder"],
            "properties": {
                "proposer": {
                    "type": "object",
                    "required": ["model"],
                    "properties": {
                        "model": {"type": "string"},
                        "system_prompt": {"type": "string"},
                    },
                },
                "responder": {
                    "type": "object",
                    "required": ["model"],
                    "properties": {
                        "model": {"type": "string"},
                        "system_prompt": {"type": "string"},
                    },
                },
                "max_rounds": {"type": "integer", "default": 5},
            },
        },
        "preview": {"min_nodes": 3, "min_edges": 3},
    },
    {
        "id": CollaborationPattern.DEBATE,
        "name": "Debate Arena",
        "description": (
            "Two debaters alternate arguments for a set number of rounds, "
            "with an optional judge. Best for adversarial analysis."
        ),
        "parameters": {
            "type": "object",
            "required": ["debater_a", "debater_b"],
            "properties": {
                "debater_a": {
                    "type": "object",
                    "required": ["model"],
                    "properties": {
                        "model": {"type": "string"},
                        "system_prompt": {"type": "string"},
                    },
                },
                "debater_b": {
                    "type": "object",
                    "required": ["model"],
                    "properties": {
                        "model": {"type": "string"},
                        "system_prompt": {"type": "string"},
                    },
                },
                "judge": {
                    "type": "object",
                    "properties": {
                        "model": {"type": "string"},
                        "system_prompt": {"type": "string"},
                    },
                },
                "rounds": {"type": "integer", "default": 3},
            },
        },
        "preview": {"min_nodes": 3, "min_edges": 3},
    },
]
