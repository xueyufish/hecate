"""Core type definitions for the Hecate execution engine.

This module defines all data structures used across the runtime: node types, channel
types, streaming modes, control commands, graph definitions, and execution results.
These types are the shared vocabulary between the graph DSL parser, the compiler,
the Pregel runtime, and the worker pool.

Design principle: all types are plain dataclasses/enums with no business logic,
so they can be freely serialized and passed between runtime components.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

# Engine-wide fan-out ceilings. Defaults may be overridden by graph config or
# per-node ``fanout.max_fanout``; the absolute cap cannot be exceeded and acts
# as the platform-level guardrail.
DEFAULT_MAX_FANOUT_PER_DISPATCH: int = 64
DEFAULT_MAX_INVOCATIONS_PER_SUPERSTEP: int = 256
ABSOLUTE_MAX_FANOUT: int = 1024


class RoutingMode(StrEnum):
    """Routing mode for CONDITION nodes.

    - CONDITION: expression-based routing (default, existing behavior).
    - INTENT: pattern matching + optional LLM intent classification.
    - DYNAMIC: LLM selects next speaker from candidate agents.
    """

    CONDITION = "condition"
    INTENT = "intent"
    DYNAMIC = "dynamic"


@dataclass
class ChannelAccess:
    """Per-node channel access boundaries.

    Attributes:
        readable: Set of channel names the node is allowed to read.
        writable: Set of channel names the node is allowed to write.
    """

    readable: set[str] = field(default_factory=set)
    writable: set[str] = field(default_factory=set)


@dataclass
class IntentPattern:
    """A single intent pattern for routing.

    Attributes:
        pattern: Regex pattern to match against input.
        target: Target node ID to route to when pattern matches.
    """

    pattern: str
    target: str


class NodeType(StrEnum):
    """Supported node types in the execution graph.

    Each type corresponds to a distinct execution behavior:
    - CONVERSATION: invokes an LLM with the current channel state as context.
    - TOOL_CALL: executes a tool (builtin, custom, or MCP) and returns the result.
    - CONDITION: evaluates an expression against channel state to determine which
      outgoing edge to follow (branching node).
    - AGENT: delegates execution to a sub-graph representing another agent.
    - KNOWLEDGE_RETRIEVAL: queries knowledge bases via RuntimePort.knowledge_query().
    - VARIABLE_SET: sets/updates channel variables based on expressions.
    - SUGGESTION: generates opening remarks or follow-up question suggestions.
    - FAN_OUT: dispatches multiple parallel branches concurrently (no worker invoked).
    - MERGE: collects results from all branches of a preceding FAN_OUT node.
    - COORDINATOR: dynamic orchestration node (1.3.18) — turns goal +
      agent roster into a runtime task DAG via the LLM planner,
      dispatches workers in an isolated child session, and folds their
      outputs through a synthesis step.
    """

    CONVERSATION = "conversation"
    TOOL_CALL = "tool-call"
    CONDITION = "condition"
    AGENT = "agent"
    KNOWLEDGE_RETRIEVAL = "knowledge-retrieval"
    VARIABLE_SET = "variable-set"
    SUGGESTION = "suggestion"
    FAN_OUT = "fan-out"
    MERGE = "merge"
    COORDINATOR = "coordinator"


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
    """

    VALUES = "values"
    UPDATES = "updates"
    MESSAGES = "messages"


class ExecutionMode(StrEnum):
    """Execution mode for workflow runs.

    - CONVERSATIONAL: multi-turn, stateful, supports interrupt/resume and streaming.
    - TASK: single-shot, stateless, no interaction nodes allowed, no checkpointing.
    """

    CONVERSATIONAL = "conversational"
    TASK = "task"


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
        reduce_fn: reduction function name resolved against the registry in
            ``channel.py`` (built-ins "add" and "append"; register more via
            ``channel.register_reducer`` — unknown names fail compilation).
        persistent: whether the channel persists across sessions (checkpoint).
            This is orthogonal to write semantics — any type can be persistent.
    """

    type: ChannelType
    default: Any = None
    initial: Any = None
    reduce_fn: str | None = None
    persistent: bool = False


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
        interrupt_before: node IDs whose superstep pauses before dispatch
            (declarative interrupts; empty means none).
        interrupt_after: node IDs whose superstep pauses after their writes
            are committed (declarative interrupts; empty means none).
    """

    version: str = "1.0"
    name: str = ""
    state: dict[str, ChannelDef] = field(default_factory=dict)
    nodes: dict[str, NodeConfig] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)
    entry: str = ""
    interrupt_before: list[str] = field(default_factory=list)
    interrupt_after: list[str] = field(default_factory=list)


@dataclass
class WorkerResult:
    """Result produced by a Worker after executing a node.

    Attributes:
        node_id: the node that was executed.
        channel_updates: values to write into channels, keyed by channel name.
            For TOPIC channels, values are appended; for LAST_VALUE, overwritten.
        command: optional control instruction (goto, interrupt, return).
        error: if set, the runtime will raise this error instead of applying updates.
        cache_hit: whether this result was served from the node cache
            (1.3.21IV). False for normally executed nodes; the engine copies
            the flag into the NODE_END ``cached`` marker on both paths so hit
            and miss trajectories differ only in the marker values.
        cache_key: the derived cache key hash for cache-policy nodes
            (``None`` when the node has no policy or missed).
    """

    node_id: str
    channel_updates: dict[str, Any] = field(default_factory=dict)
    command: Command | None = None
    error: Exception | None = None
    cache_hit: bool = False
    cache_key: str | None = None


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
        channel_access: per-node channel read/write access boundaries.
        interrupt_before: node IDs whose superstep pauses before dispatch
            (declarative interrupts; empty means none).
        interrupt_after: node IDs whose superstep pauses after their writes
            are committed (declarative interrupts; empty means none).
    """

    nodes: dict[str, NodeConfig]
    edges: list[Edge]
    channels: dict[str, ChannelDef]
    entry_point: str
    name: str = ""
    channel_access: dict[str, ChannelAccess] = field(default_factory=dict)
    interrupt_before: list[str] = field(default_factory=list)
    interrupt_after: list[str] = field(default_factory=list)

    def to_json(self) -> dict:
        """Serialize the compiled graph to a JSON-compatible dict.

        Used for persisting graph definitions and for the API to return graph configs.
        Conditional edges serialize their target as a dict mapping route keys to node IDs.
        """
        return {
            "version": "1.0",
            "name": self.name,
            "state": {
                k: {"type": v.type.value, "default": v.default, "persistent": v.persistent}
                for k, v in self.channels.items()
            },
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
            "interrupt_before": list(self.interrupt_before),
            "interrupt_after": list(self.interrupt_after),
        }


@dataclass
class DispatchPacket:
    """One runtime dispatch packet for dynamic fan-out (1.3.21③).

    A planner (CONDITION node with a ``fanout`` config) emits a list of these
    into the ``_dispatch`` channel. Each packet names the target node and
    carries the per-branch state slice that becomes the branch's input.

    Both fields are intentionally JSON-serializable — the plan flows through
    the WAL as a plain ``CHANNEL_WRITE`` payload. This avoids the
    LangGraph-Send-style serialization pitfall (Send objects are not
    msgpack-friendly and historically broke checkpointers).
    """

    node: str
    state: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class InvocationIdentity:
    """Identity attached to each scheduled invocation for branch disambiguation.

    ``fanout_source`` is the node that emitted the dispatch plan (or
    ``None`` for non-fanout invocations). ``branch_index`` is the 0-based
    offset within that source's packet list — distinct calls of the same
    target node under one planner share ``fanout_source`` but have
    distinct ``branch_index`` values.
    """

    fanout_source: str | None
    branch_index: int


@dataclass(frozen=True)
class Invocation:
    """One scheduled unit of work for the next superstep.

    ``target`` is the node to dispatch. ``identity`` distinguishes parallel
    invocations of the same target (branching identity in NODE events).
    ``sub_channel`` is the name of the per-invocation sub-channel the
    engine writes the seed state into (None for non-fanout invocations).

    The type replaces the previous ``list[str]`` next-nodes view while
    keeping the static fan-out path bit-identical (a single invocation
    per branch, identity.fanout_source=None).
    """

    target: str
    identity: InvocationIdentity
    sub_channel: str | None = None
