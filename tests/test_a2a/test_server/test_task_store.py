"""Tests for A2A task store persistence."""

from __future__ import annotations

from hecate.a2a.types import Task, TaskState, TaskStatus


def test_task_creation() -> None:
    """Test Task object creation with default values."""
    task = Task()
    assert task.id is not None
    assert task.context_id is not None
    assert task.status.state == TaskState.SUBMITTED
    assert task.artifacts == []
    assert task.history == []


def test_task_with_status() -> None:
    """Test Task creation with custom status."""
    task = Task(
        id="test-task",
        context_id="test-ctx",
        status=TaskStatus(state=TaskState.COMPLETED),
    )
    assert task.id == "test-task"
    assert task.status.state == TaskState.COMPLETED


def test_task_with_artifacts() -> None:
    """Test Task with artifacts."""
    from hecate.a2a.types import Artifact

    artifact = Artifact(name="response", parts=[{"text": "Hello"}])
    task = Task(artifacts=[artifact])
    assert len(task.artifacts) == 1
    assert task.artifacts[0].name == "response"
