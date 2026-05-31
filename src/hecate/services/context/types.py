"""Types and data structures for context engineering.

Defines the core data structures used across the context engineering module:
AssembledContext, message priorities, task phases, and provider strategies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class TaskPhase(StrEnum):
    """Detected task phase from conversation pattern.

    - EXPLORE: Initial investigation, gathering information
    - CONVERGE: Narrowing down, focusing on specific solutions
    - EXECUTE: Active implementation, tool calls, code changes
    - VERIFY: Checking results, testing, validation
    """

    EXPLORE = "explore"
    CONVERGE = "converge"
    EXECUTE = "execute"
    VERIFY = "verify"


class MessagePriority(StrEnum):
    """Priority level for messages in context degradation decisions."""

    CRITICAL = "critical"  # System prompt, current user message — never drop
    HIGH = "high"  # Recent exchanges, tool results — last to drop
    MEDIUM = "medium"  # Older messages — compress first
    LOW = "low"  # Early messages, notifications — drop first


@dataclass
class AssembledContext:
    """Result of context assembly for an LLM invocation.

    Contains the final messages, tools, and metadata ready to be passed
    to the LLM service after all context engineering transformations.

    Attributes:
        messages: Final list of messages with priorities assigned.
        tools: Filtered list of tool definitions for the current phase.
        knowledge: Relevant knowledge chunks from RAG retrieval.
        phase: Detected task phase (explore/converge/execute/verify).
        total_tokens: Total token count of the assembled context.
        priorities: Priority level assigned to each message.
        metadata: Additional metadata about the assembly process.
    """

    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]]
    knowledge: list[dict[str, Any]] = field(default_factory=list)
    phase: TaskPhase = TaskPhase.EXECUTE
    total_tokens: int = 0
    priorities: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_llm_messages(self) -> list[dict[str, Any]]:
        """Convert to message format expected by LLM service.

        Strips priority metadata and returns clean messages.

        Returns:
            List of messages in LLM-compatible format.
        """
        return [msg for msg in self.messages if "priority" not in msg]


@dataclass
class SessionMeta:
    """Session metadata for context assembly decisions.

    Contains information about the current session that influences
    how context is assembled and managed.

    Attributes:
        session_id: Unique session identifier.
        agent_id: Agent running the session.
        turn_index: Current turn number in the conversation.
        model: Target LLM model identifier.
        custom_budget: Optional custom token budget override.
        agent_name: Agent display name for suggestion prompts.
        agent_persona: Agent persona description for suggestion prompts.
    """

    session_id: str
    agent_id: str
    turn_index: int = 0
    model: str = "gpt-4o"
    custom_budget: int | None = None
    agent_name: str = "Assistant"
    agent_persona: str | None = None


@dataclass
class PhaseToolMapping:
    """Mapping of task phases to tool categories.

    Defines which tools are relevant for each task phase to enable
    dynamic tool filtering.

    Attributes:
        explore: Tools available during exploration phase.
        converge: Tools available during convergence phase.
        execute: Tools available during execution phase.
        verify: Tools available during verification phase.
    """

    explore: list[str] = field(default_factory=lambda: ["search", "read", "list", "grep", "glob"])
    converge: list[str] = field(default_factory=lambda: ["search", "read", "list", "grep", "glob", "edit"])
    execute: list[str] = field(default_factory=list)  # Empty means all tools
    verify: list[str] = field(default_factory=lambda: ["read", "test", "check", "validate"])
