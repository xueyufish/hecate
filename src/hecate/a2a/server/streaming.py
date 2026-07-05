"""SSE event emitter for A2A streaming responses."""

from __future__ import annotations

import json
from typing import Any

from hecate.a2a.types import (
    Task,
    TaskArtifactUpdateEvent,
    TaskStatusUpdateEvent,
)


def format_sse_event(event_type: str, data: dict[str, Any]) -> str:
    """Format a Server-Sent Event.

    Args:
        event_type: The SSE event type (e.g., "task/status", "task/artifact").
        data: The event data dict.

    Returns:
        Formatted SSE string.
    """
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


def task_to_status_event(task: Task, final: bool = False) -> str:
    """Format a task status update as an SSE event.

    Args:
        task: The A2A task.
        final: Whether this is the final event.

    Returns:
        Formatted SSE event string.
    """
    event = TaskStatusUpdateEvent(
        task_id=task.id,
        context_id=task.context_id,
        status=task.status,
        final=final,
    )
    return format_sse_event(
        "task/status",
        {
            "taskId": event.task_id,
            "contextId": event.context_id,
            "status": {
                "state": event.status.state.value,
                "message": event.status.message.__dict__ if event.status.message else None,
            },
            "final": event.final,
        },
    )


def task_to_artifact_event(task: Task, artifact_index: int = -1, last_chunk: bool = False) -> str:
    """Format a task artifact update as an SSE event.

    Args:
        task: The A2A task.
        artifact_index: Index of the artifact to emit.
        last_chunk: Whether this is the last chunk.

    Returns:
        Formatted SSE event string.
    """
    if not task.artifacts:
        return ""

    artifact = task.artifacts[artifact_index]
    event = TaskArtifactUpdateEvent(
        task_id=task.id,
        context_id=task.context_id,
        artifact=artifact,
        append=False,
        last_chunk=last_chunk,
    )
    return format_sse_event(
        "task/artifact",
        {
            "taskId": event.task_id,
            "contextId": event.context_id,
            "artifact": {
                "artifactId": event.artifact.artifact_id,
                "name": event.artifact.name,
                "parts": event.artifact.parts,
            },
            "append": event.append,
            "lastChunk": event.last_chunk,
        },
    )
