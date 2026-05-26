"""Core type definitions for the Hecate execution engine.

This module defines all data structures used across the engine: node types, channel
types, streaming modes, control commands, graph definitions, and execution results.
These types are the shared vocabulary between the graph DSL parser, the compiler,
the Pregel runtime, and the worker pool.

Design principle: all types are plain dataclasses/enums with no business logic,
so they can be freely serialized and passed between engine components.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class NodeType(StrEnum):
    """Supported node types in the execution graph.

    Each type corresponds to a distinct execution behavior:
    - CONVERSATION: invokes an LLM with the current channel state as context.
    - TOOL_CALL: executes a tool (builtin, custom, or MCP) and returns the result.
    - CONDITION: evaluates an expression against channel state to determine which
      outgoing edge to follow (branching node).
    - AGENT: delegates execution to a sub-graph representing another agent.
    """

    CONVERSATION = "conversation"
    TOOL_CALL = "tool-call"
    CONDITION = "condition"
    AGENT = "agent"


class ChannelType(StrEnum):
    """Determines how values are stored when written to a channel.

    - LAST_VALUE: overwrites the previous value on each write (e.g., current context).
    - TOPIC: appends each written value to a list (e.g., message history).
    - PERSISTENT_TOPIC: like TOPIC but values persist across sessions via checkpoint.
    - ACCUMULATOR: reduces values using a function (e.g., "add" for counters).
    """

    LAST_VALUE = "last_value"
    TOPIC = "topic"
    PERSISTENT_TOPIC = "persistent_topic"
    ACCUMULATOR = "accumulator"


class StreamMode(StrEnum):
    """Controls what events the Pregel runtime yields during execution.

    - VALUES: yields the full channel state snapshot after each superstep.
    - UPDATES: yields per-node channel deltas (only what changed).
    - MESSAGES: yields individual message tokens for SSE streaming (P2).
    - DEBUG: yields detailed internal events for development (P2).
    """

    VALUES = "values"
    UPDATES = "updates"
    MESSAGES = "messages"
    DEBUG = "debug"


@dataclass
class Command:
    """Control instruction returned by a Worker to influence graph execution flow.

    A worker can return at most one type of command per execution:
    - goto: jump to a specific node, bypassing normal edge resolution.
    - return_value: signal that the graph has produced its final output.
    - interrupt: pause execution and wait for external input (human-in-the-loop).
    - update: write additional channel values before the next superstep begins.
    """

    goto: str | None = None
    return_value: Any = None
    interrupt: Any = None
    update: dict[str, Any] = field(default_factory=dict)

    def is_goto(self) -> bool:
        """Return True if this command directs execution to a specific node."""
        return self.goto is not None

    def is_return(self) -> bool:
        """Return True if this command signals execution termination."""
        return self.return_value is not None

    def is_interrupt(self) -> bool:
        """Return True if this command pauses execution for human input."""
        return self.interrupt is not None


@dataclass
class Edge:
    """Directed edge connecting two nodes in the graph.

    Attributes:
        source: the node ID where this edge originates.
        target: either a string node ID for unconditional edges, or a dict mapping
            route keys (e.g., "true"/"false") to node IDs for conditional branching.
            The special node ID "__end__" terminates graph execution.
        trigger: optional label describing when this edge is followed (reserved for P2).
    """

    source: str
    target: str | dict[str, str]
    trigger: str | None = None


@dataclass
class ChannelDef:
    """Definition of a state channel including its type and reduction strategy.

    Attributes:
        type: the ChannelType determining write semantics.
        default: initial value set when the channel is first registered.
        initial: starting value for ACCUMULATOR channels (e.g., 0 for "add").
        reduce_fn: reduction function name (currently only "add" is supported).
    """

    type: ChannelType
    default: Any = None
    initial: Any = None
    reduce_fn: str | None = None


@dataclass
class NodeConfig:
    """Configuration for a single node in the graph.

    Attributes:
        id: unique node identifier matching the key in GraphConfig.nodes.
        type: the NodeType determining execution behavior.
        config: node-type-specific settings, e.g. {"model": "gpt-4o",
            "system_prompt": "...", "channels": {"readable": [...], "writable": [...]}}.
    """

    id: str
    type: NodeType
    config: dict[str, Any] = field(default_factory=dict)


@dataclass
class GraphConfig:
    """Parsed graph configuration ready for compilation.

    Produced by graph_dsl.parse_graph() from a JSON definition. The compiler
    validates this and produces a CompiledGraph for the Pregel runtime.

    Attributes:
        version: DSL version string (currently "1.0").
        name: human-readable graph name.
        state: channel definitions keyed by channel name.
        nodes: node configurations keyed by node ID.
        edges: ordered list of directed edges.
        entry: the node ID where execution begins.
    """

    version: str = "1.0"
    name: str = ""
    state: dict[str, ChannelDef] = field(default_factory=dict)
    nodes: dict[str, NodeConfig] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)
    entry: str = ""


@dataclass
class WorkerResult:
    """Result produced by a Worker after executing a node.

    Attributes:
        node_id: the node that was executed.
        channel_updates: values to write into channels, keyed by channel name.
            For TOPIC channels, values are appended; for LAST_VALUE, overwritten.
        command: optional control instruction (goto, interrupt, return).
        error: if set, the runtime will raise this error instead of applying updates.
    """

    node_id: str
    channel_updates: dict[str, Any] = field(default_factory=dict)
    command: Command | None = None
    error: Exception | None = None


@dataclass
class CompiledGraph:
    """Validated and compiled graph ready for execution by the Pregel runtime.

    Produced by GraphCompiler.compile(). All edges reference valid nodes, and
    unreachable nodes have been flagged as warnings.

    Attributes:
        nodes: node configurations keyed by node ID.
        edges: ordered list of validated directed edges.
        channels: channel definitions keyed by channel name.
        entry_point: the node ID where execution begins.
        name: human-readable graph name.
    """

    nodes: dict[str, NodeConfig]
    edges: list[Edge]
    channels: dict[str, ChannelDef]
    entry_point: str
    name: str = ""

    def to_json(self) -> dict:
        """Serialize the compiled graph to a JSON-compatible dict.

        Used for persisting graph definitions and for the API to return graph configs.
        Conditional edges serialize their target as a dict mapping route keys to node IDs.
        """
        return {
            "version": "1.0",
            "name": self.name,
            "state": {k: {"type": v.type.value, "default": v.default} for k, v in self.channels.items()},
            "nodes": {k: {"type": v.type.value, "config": v.config} for k, v in self.nodes.items()},
            "edges": [
                {
                    "source": e.source,
                    "target": e.target if isinstance(e.target, str) else e.target,
                    "trigger": e.trigger,
                }
                for e in self.edges
            ],
            "entry": self.entry_point,
        }
