"""Tests for A2A JSON-RPC handler methods."""

from __future__ import annotations

from hecate.a2a.types import TaskState


def test_task_state_values() -> None:
    """Test that TaskState enum has all required values."""
    assert TaskState.SUBMITTED == "submitted"
    assert TaskState.WORKING == "working"
    assert TaskState.COMPLETED == "completed"
    assert TaskState.FAILED == "failed"
    assert TaskState.CANCELED == "canceled"
    assert TaskState.INPUT_REQUIRED == "input_required"
    assert TaskState.REJECTED == "rejected"


def test_task_state_is_string_enum() -> None:
    """Test that TaskState values are strings."""
    for state in TaskState:
        assert isinstance(state.value, str)
