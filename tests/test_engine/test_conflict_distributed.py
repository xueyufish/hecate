"""Tests for distributed conflict handling strategies."""

from __future__ import annotations

import pytest

from hecate.engine.temporal.conflict import ConflictResolver, ConflictStrategy


@pytest.fixture
def resolver() -> ConflictResolver:
    """Create a fresh ConflictResolver for each test."""
    return ConflictResolver()


async def test_distributed_lock_acquire(resolver: ConflictResolver) -> None:
    """Test that distributed lock can be acquired."""
    result = await resolver.resolve_distributed(
        channel_key="test_channel",
        current_value="old",
        proposed_value="new",
        strategy=ConflictStrategy.DISTRIBUTED_LOCK,
        agent_id="agent_1",
    )
    assert result.resolved is True
    assert result.final_value == "new"
    assert result.strategy_used == "distributed_lock"


async def test_distributed_lock_conflict(resolver: ConflictResolver) -> None:
    """Test that second agent cannot acquire held lock."""
    # First agent acquires lock
    await resolver.resolve_distributed(
        channel_key="test_channel",
        current_value="old",
        proposed_value="new_1",
        strategy=ConflictStrategy.DISTRIBUTED_LOCK,
        agent_id="agent_1",
    )

    # Second agent tries to acquire same lock
    result = await resolver.resolve_distributed(
        channel_key="test_channel",
        current_value="old",
        proposed_value="new_2",
        strategy=ConflictStrategy.DISTRIBUTED_LOCK,
        agent_id="agent_2",
    )
    assert result.resolved is False
    assert result.final_value == "old"


async def test_negotiation_fallback_to_lww(resolver: ConflictResolver) -> None:
    """Test that negotiation falls back to last-write-wins."""
    result = await resolver.resolve_distributed(
        channel_key="test_channel",
        current_value="old",
        proposed_value="new",
        strategy=ConflictStrategy.NEGOTIATION,
        agent_id="agent_1",
    )
    assert result.resolved is True
    assert result.final_value == "new"
    assert result.strategy_used == "negotiation"


def test_task_conflict_single_agent(resolver: ConflictResolver) -> None:
    """Test that single agent claiming task has no conflict."""
    result = resolver.detect_task_conflict(task_id="task_1", claiming_agents=["agent_1"])
    assert result.resolved is True
    assert result.final_value == "agent_1"


def test_task_conflict_multiple_agents(resolver: ConflictResolver) -> None:
    """Test that multiple agents claiming same task is a conflict."""
    result = resolver.detect_task_conflict(
        task_id="task_1",
        claiming_agents=["agent_1", "agent_2"],
    )
    assert result.resolved is False
    assert result.strategy_used == "task_conflict_detected"
    assert result.final_value["claiming_agents"] == ["agent_1", "agent_2"]


def test_permission_mismatch_allowed(resolver: ConflictResolver) -> None:
    """Test that allowed action passes permission check."""
    result = resolver.detect_permission_mismatch(
        agent_id="agent_1",
        requested_action="read_data",
        allowed_scope=["read_data", "write_data"],
    )
    assert result.resolved is True


def test_permission_mismatch_denied(resolver: ConflictResolver) -> None:
    """Test that disallowed action fails permission check."""
    result = resolver.detect_permission_mismatch(
        agent_id="agent_1",
        requested_action="delete_data",
        allowed_scope=["read_data", "write_data"],
    )
    assert result.resolved is False
    assert result.strategy_used == "permission_mismatch"
