"""A2A Protocol module for Hecate.

Provides A2A v1.2 server and client implementations for
agent-to-agent communication.
"""

from hecate.a2a.types import (
    AgentCard,
    Artifact,
    Message,
    Task,
    TaskState,
    TaskStatus,
)

__all__ = [
    "AgentCard",
    "Artifact",
    "Message",
    "Task",
    "TaskState",
    "TaskStatus",
]
