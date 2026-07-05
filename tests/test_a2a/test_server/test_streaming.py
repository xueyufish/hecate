"""Tests for A2A SSE streaming format."""

from __future__ import annotations

from hecate.a2a.server.streaming import format_sse_event, task_to_status_event
from hecate.a2a.types import Task, TaskState, TaskStatus


def test_format_sse_event() -> None:
    """Test SSE event formatting."""
    event = format_sse_event("task/status", {"state": "working"})
    assert event.startswith("event: task/status\n")
    assert "data:" in event
    assert '"state": "working"' in event
    assert event.endswith("\n\n")


def test_task_to_status_event() -> None:
    """Test task status update SSE event."""
    task = Task(
        id="test-123",
        context_id="ctx-456",
        status=TaskStatus(state=TaskState.WORKING),
    )
    event = task_to_status_event(task, final=False)
    assert "event: task/status" in event
    assert "test-123" in event
    assert "working" in event
    assert '"final": false' in event


def test_task_to_status_event_final() -> None:
    """Test final task status update SSE event."""
    task = Task(
        id="test-123",
        context_id="ctx-456",
        status=TaskStatus(state=TaskState.COMPLETED),
    )
    event = task_to_status_event(task, final=True)
    assert '"final": true' in event
    assert "completed" in event
