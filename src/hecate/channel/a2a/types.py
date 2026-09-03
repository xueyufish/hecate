"""A2A Protocol type definitions for Hecate.

Provides Pydantic models wrapping the official a2a-sdk types for
AgentCard, Task, Message, Artifact, and related protocol objects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import uuid4


class TaskState(StrEnum):
    """A2A task lifecycle states."""

    SUBMITTED = "submitted"
    WORKING = "working"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"
    INPUT_REQUIRED = "input_required"
    REJECTED = "rejected"


@dataclass
class AgentCard:
    """A2A AgentCard describing Hecate's capabilities."""

    name: str
    description: str
    version: str
    url: str
    capabilities: dict[str, bool] = field(default_factory=lambda: {"streaming": True, "pushNotifications": False})
    skills: list[dict[str, Any]] = field(default_factory=list)
    security_schemes: dict[str, Any] = field(default_factory=dict)
    default_input_modes: list[str] = field(default_factory=lambda: ["text/plain"])
    default_output_modes: list[str] = field(default_factory=lambda: ["text/plain"])


@dataclass
class Message:
    """A2A message with parts."""

    role: str
    parts: list[dict[str, Any]]
    message_id: str = field(default_factory=lambda: str(uuid4()))
    context_id: str | None = None


@dataclass
class TaskStatus:
    """A2A task status."""

    state: TaskState
    message: Message | None = None
    timestamp: str | None = None


@dataclass
class Artifact:
    """A2A artifact with parts."""

    artifact_id: str = field(default_factory=lambda: str(uuid4()))
    name: str | None = None
    description: str | None = None
    parts: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Task:
    """A2A task object."""

    id: str = field(default_factory=lambda: str(uuid4()))
    context_id: str = field(default_factory=lambda: str(uuid4()))
    status: TaskStatus = field(default_factory=lambda: TaskStatus(state=TaskState.SUBMITTED))
    artifacts: list[Artifact] = field(default_factory=list)
    history: list[Message] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class TaskStatusUpdateEvent:
    """SSE event for task status updates."""

    task_id: str
    context_id: str
    status: TaskStatus
    final: bool = False


@dataclass
class TaskArtifactUpdateEvent:
    """SSE event for task artifact updates."""

    task_id: str
    context_id: str
    artifact: Artifact
    append: bool = False
    last_chunk: bool = False


@dataclass
class SendMessageRequest:
    """JSON-RPC SendMessage request."""

    message: Message
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class GetTaskRequest:
    """JSON-RPC GetTask request."""

    task_id: str


@dataclass
class CancelTaskRequest:
    """JSON-RPC CancelTask request."""

    task_id: str


@dataclass
class SendStreamingMessageRequest:
    """JSON-RPC SendStreamingMessage request."""

    message: Message
    metadata: dict[str, Any] = field(default_factory=dict)
