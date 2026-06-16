"""Unit tests for ConflictResolver."""

from __future__ import annotations

from hecate.engine.channel import (
    AccumulatorBehavior,
    LastValueBehavior,
    TopicBehavior,
)
from hecate.engine.temporal.conflict import ConflictResolver, ConflictStrategy


class TestConflictResolver:
    """Tests for the ConflictResolver class."""

    def test_resolve_last_value_wins(self) -> None:
        """Test last-value channel uses last-write-wins."""
        resolver = ConflictResolver()

        result = resolver.resolve(
            channel_key="state",
            current_value="old",
            proposed_value="new",
            behavior=LastValueBehavior(),
        )

        assert result.resolved is True
        assert result.final_value == "new"

    def test_resolve_topic_merges_lists(self) -> None:
        """Test topic channel merges lists."""
        resolver = ConflictResolver()

        result = resolver.resolve(
            channel_key="messages",
            current_value=["msg1", "msg2"],
            proposed_value=["msg3"],
            behavior=TopicBehavior(),
        )

        assert result.resolved is True
        assert "msg1" in result.final_value
        assert "msg2" in result.final_value
        assert "msg3" in result.final_value

    def test_resolve_topic_deduplicates(self) -> None:
        """Test topic channel deduplicates values."""
        resolver = ConflictResolver()

        result = resolver.resolve(
            channel_key="messages",
            current_value=["msg1", "msg2"],
            proposed_value=["msg2", "msg3"],
            behavior=TopicBehavior(),
        )

        assert result.resolved is True
        assert result.final_value.count("msg2") == 1

    def test_resolve_accumulator_sums(self) -> None:
        """Test accumulator channel sums values."""
        resolver = ConflictResolver()

        result = resolver.resolve(
            channel_key="counter",
            current_value=5,
            proposed_value=3,
            behavior=AccumulatorBehavior(),
        )

        assert result.resolved is True
        assert result.final_value == 8

    def test_resolve_default_last_write_wins(self) -> None:
        """Test no behavior defaults to last-write-wins."""
        resolver = ConflictResolver()

        result = resolver.resolve(
            channel_key="unknown",
            current_value="old",
            proposed_value="new",
        )

        assert result.resolved is True
        assert result.final_value == "new"
        assert result.strategy_used == ConflictStrategy.LAST_WRITE_WINS.value

    def test_merge_lists_with_non_list_current(self) -> None:
        """Test merge when current value is not a list."""
        resolver = ConflictResolver()

        result = resolver._merge_lists("not_a_list", ["item1", "item2"])

        assert isinstance(result, list)
        assert "not_a_list" in result

    def test_merge_maps(self) -> None:
        """Test map merging."""
        resolver = ConflictResolver()

        result = resolver._merge_maps(
            {"key1": "val1", "key2": "val2"},
            {"key2": "new_val2", "key3": "val3"},
        )

        assert result["key1"] == "val1"
        assert result["key2"] == "new_val2"
        assert result["key3"] == "val3"

    def test_resolve_custom_behavior(self) -> None:
        """Test custom behavior conflict resolution."""
        resolver = ConflictResolver()

        result = resolver.resolve(
            channel_key="counter",
            current_value=10,
            proposed_value=20,
            behavior=AccumulatorBehavior(),
        )

        assert result.resolved is True
        assert result.final_value == 30
        assert result.strategy_used == "behavior_delegated"

    def test_resolve_behavior_exception_falls_back(self) -> None:
        """Test fallback to last-write-wins when behavior raises exception."""
        resolver = ConflictResolver()

        class BrokenBehavior(LastValueBehavior):
            def resolve_conflict(self, current, proposed):
                raise TypeError("broken")

        result = resolver.resolve(
            channel_key="broken",
            current_value="old",
            proposed_value="new",
            behavior=BrokenBehavior(),
        )

        assert result.resolved is True
        assert result.final_value == "new"
        assert result.strategy_used == ConflictStrategy.LAST_WRITE_WINS.value
