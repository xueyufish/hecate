from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class NodeType(StrEnum):
    """Supported node types in the execution graph."""

    CONVERSATION = "conversation"
    TOOL_CALL = "tool-call"
    CONDITION = "condition"
    AGENT = "agent"


class ChannelType(StrEnum):
    """Channel write semantics determining how values are stored."""

    LAST_VALUE = "last_value"
    TOPIC = "topic"
    PERSISTENT_TOPIC = "persistent_topic"
    ACCUMULATOR = "accumulator"


class StreamMode(StrEnum):
    """Streaming output modes for graph execution."""

    VALUES = "values"
    UPDATES = "updates"
    MESSAGES = "messages"
    DEBUG = "debug"


@dataclass
class Command:
    """Control instruction returned by a Worker to influence graph execution flow."""

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

    target is a string for unconditional edges, or a dict mapping route keys
    to node IDs for conditional branching.
    """

    source: str
    target: str | dict[str, str]
    trigger: str | None = None


@dataclass
class ChannelDef:
    """Definition of a state channel including its type and reduction strategy."""

    type: ChannelType
    default: Any = None
    initial: Any = None
    reduce_fn: str | None = None


@dataclass
class NodeConfig:
    """Configuration for a single node in the graph."""

    id: str
    type: NodeType
    config: dict[str, Any] = field(default_factory=dict)


@dataclass
class GraphConfig:
    """Parsed graph configuration ready for compilation."""

    version: str = "1.0"
    name: str = ""
    state: dict[str, ChannelDef] = field(default_factory=dict)
    nodes: dict[str, NodeConfig] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)
    entry: str = ""


@dataclass
class WorkerResult:
    """Result produced by a Worker after executing a node."""

    node_id: str
    channel_updates: dict[str, Any] = field(default_factory=dict)
    command: Command | None = None
    error: Exception | None = None


@dataclass
class CompiledGraph:
    """Validated and compiled graph ready for execution by the Pregel runtime."""

    nodes: dict[str, NodeConfig]
    edges: list[Edge]
    channels: dict[str, ChannelDef]
    entry_point: str
    name: str = ""

    def to_json(self) -> dict:
        """Serialize the compiled graph to a JSON-compatible dict."""
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
