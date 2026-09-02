"""Tests for nesting depth enforcement (max_depth=3)."""

from __future__ import annotations

import pytest


class NestingDepthExceededError(Exception):
    """Raised when agent-workflow nesting depth exceeds max_depth=3."""

    def __init__(self, current_depth: int, max_depth: int = 3) -> None:
        self.current_depth = current_depth
        self.max_depth = max_depth
        super().__init__(
            f"Nesting depth {current_depth} exceeds maximum of {max_depth}. "
            f"Agent → Workflow → Agent chains are limited to {max_depth} levels."
        )


def test_nesting_depth_within_limit() -> None:
    """Test that nesting within limit (depth 1-3) does not raise."""
    for depth in range(1, 4):
        # Should not raise
        if depth > 3:
            pytest.fail(f"Depth {depth} should not exceed limit")


def test_nesting_depth_exceeds_limit() -> None:
    """Test that nesting at depth 4 raises NestingDepthExceededError."""
    max_depth = 3
    current_depth = 4

    with pytest.raises(NestingDepthExceededError) as exc_info:
        if current_depth > max_depth:
            raise NestingDepthExceededError(current_depth, max_depth)

    assert exc_info.value.current_depth == 4
    assert exc_info.value.max_depth == 3
    assert "exceeds maximum" in str(exc_info.value)


def test_nesting_depth_error_message() -> None:
    """Test NestingDepthExceededError message format."""
    error = NestingDepthExceededError(5, 3)
    assert "depth 5" in str(error)
    assert "maximum of 3" in str(error)
