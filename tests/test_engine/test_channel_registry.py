"""Unit tests for ChannelBehavior implementations and ChannelTypeRegistry."""

from __future__ import annotations

import logging
from typing import Any

import pytest

from hecate.engine.channel import (
    AccumulatorBehavior,
    ChannelBehavior,
    LastValueBehavior,
    TopicBehavior,
    get,
    list_types,
    register,
)
from hecate.engine.types import ChannelDef, ChannelType


class TestLastValueBehavior:
    """Tests for LastValueBehavior."""

    def test_initial_value_returns_none(self) -> None:
        behavior = LastValueBehavior()
        defn = ChannelDef(type=ChannelType.LAST_VALUE)
        assert behavior.initial_value(defn) is None

    def test_write_overwrites(self) -> None:
        behavior = LastValueBehavior()
        defn = ChannelDef(type=ChannelType.LAST_VALUE)
        assert behavior.write("old", "new", defn) == "new"

    def test_write_overwrites_none(self) -> None:
        behavior = LastValueBehavior()
        defn = ChannelDef(type=ChannelType.LAST_VALUE)
        assert behavior.write(None, "new", defn) == "new"

    def test_is_evictable_false(self) -> None:
        behavior = LastValueBehavior()
        assert behavior.is_evictable() is False

    def test_resolve_conflict_last_write_wins(self) -> None:
        behavior = LastValueBehavior()
        assert behavior.resolve_conflict("old", "new") == "new"


class TestTopicBehavior:
    """Tests for TopicBehavior."""

    def test_initial_value_returns_empty_list(self) -> None:
        behavior = TopicBehavior()
        defn = ChannelDef(type=ChannelType.TOPIC)
        assert behavior.initial_value(defn) == []

    def test_write_appends_scalar(self) -> None:
        behavior = TopicBehavior()
        defn = ChannelDef(type=ChannelType.TOPIC)
        result = behavior.write([1, 2], 3, defn)
        assert result == [1, 2, 3]

    def test_write_extends_list(self) -> None:
        behavior = TopicBehavior()
        defn = ChannelDef(type=ChannelType.TOPIC)
        result = behavior.write([1, 2], [3, 4], defn)
        assert result == [1, 2, 3, 4]

    def test_is_evictable_true(self) -> None:
        behavior = TopicBehavior()
        assert behavior.is_evictable() is True

    def test_resolve_conflict_merges_lists(self) -> None:
        behavior = TopicBehavior()
        result = behavior.resolve_conflict([1, 2], [3, 4])
        assert result == [1, 2, 3, 4]

    def test_resolve_conflict_deduplicates(self) -> None:
        behavior = TopicBehavior()
        result = behavior.resolve_conflict([1, 2], [2, 3])
        assert result == [1, 2, 3]

    def test_resolve_conflict_handles_non_list_current(self) -> None:
        behavior = TopicBehavior()
        result = behavior.resolve_conflict("scalar", [1, 2])
        assert result == ["scalar", 1, 2]

    def test_resolve_conflict_handles_non_list_proposed(self) -> None:
        behavior = TopicBehavior()
        result = behavior.resolve_conflict([1, 2], "scalar")
        assert result == [1, 2, "scalar"]

    def test_resolve_conflict_handles_none_values(self) -> None:
        behavior = TopicBehavior()
        result = behavior.resolve_conflict(None, None)
        assert result == []


class TestAccumulatorBehavior:
    """Tests for AccumulatorBehavior."""

    def test_initial_value_returns_initial(self) -> None:
        behavior = AccumulatorBehavior()
        defn = ChannelDef(type=ChannelType.ACCUMULATOR, initial=0)
        assert behavior.initial_value(defn) == 0

    def test_initial_value_returns_zero_when_none(self) -> None:
        behavior = AccumulatorBehavior()
        defn = ChannelDef(type=ChannelType.ACCUMULATOR)
        assert behavior.initial_value(defn) == 0

    def test_write_adds_values(self) -> None:
        behavior = AccumulatorBehavior()
        defn = ChannelDef(type=ChannelType.ACCUMULATOR, reduce_fn="add")
        assert behavior.write(5, 3, defn) == 8

    def test_write_overwrites_for_unknown_reduce(self) -> None:
        behavior = AccumulatorBehavior()
        defn = ChannelDef(type=ChannelType.ACCUMULATOR, reduce_fn=None)
        assert behavior.write(5, 3, defn) == 3

    def test_is_evictable_false(self) -> None:
        behavior = AccumulatorBehavior()
        assert behavior.is_evictable() is False

    def test_resolve_conflict_sums_values(self) -> None:
        behavior = AccumulatorBehavior()
        assert behavior.resolve_conflict(5, 3) == 8

    def test_resolve_conflict_handles_none_current(self) -> None:
        behavior = AccumulatorBehavior()
        assert behavior.resolve_conflict(None, 3) == 3

    def test_resolve_conflict_handles_type_error(self) -> None:
        behavior = AccumulatorBehavior()
        assert behavior.resolve_conflict("not_a_number", 3) == 3


class TestChannelBehaviorABC:
    """Tests for the ChannelBehavior ABC itself."""

    def test_cannot_instantiate_abc(self) -> None:
        with pytest.raises(TypeError):
            ChannelBehavior()

    def test_must_implement_all_methods(self) -> None:
        class IncompleteBehavior(ChannelBehavior):
            def initial_value(self, defn):
                return None

        with pytest.raises(TypeError):
            IncompleteBehavior()


class TestChannelTypeRegistry:
    """Tests for the ChannelTypeRegistry."""

    def test_list_types_includes_builtins(self) -> None:
        types = list_types()
        assert "last_value" in types
        assert "topic" in types
        assert "accumulator" in types
        assert "persistent_topic" in types

    def test_get_returns_correct_behavior(self) -> None:
        assert isinstance(get("last_value"), LastValueBehavior)
        assert isinstance(get("topic"), TopicBehavior)
        assert isinstance(get("accumulator"), AccumulatorBehavior)

    def test_get_persistent_topic_returns_topic_behavior(self) -> None:
        assert isinstance(get("persistent_topic"), TopicBehavior)

    def test_get_unknown_raises_key_error(self) -> None:
        with pytest.raises(KeyError, match="unknown_type"):
            get("unknown_type")

    def test_register_custom_type(self) -> None:
        class CustomBehavior(ChannelBehavior):
            def initial_value(self, defn):
                return []

            def write(self, current, value, defn):
                return [*current, value]

            def is_evictable(self):
                return False

            def resolve_conflict(self, current, proposed):
                return proposed

        register("custom", CustomBehavior())
        assert "custom" in list_types()
        assert isinstance(get("custom"), CustomBehavior)

        from hecate.engine.channel import _REGISTRY

        del _REGISTRY["custom"]


class TestChannelAccess:
    """Tests for ChannelManager node_id-based channel access warnings."""

    def test_read_logs_warning_for_undeclared_access(self, caplog: Any) -> None:
        from hecate.engine.channel import ChannelManager
        from hecate.engine.types import ChannelAccess, ChannelDef, ChannelType

        cm = ChannelManager(
            channel_access={
                "agent_a": ChannelAccess(readable={"messages"}, writable=set()),
            }
        )
        cm.register("messages", ChannelDef(type=ChannelType.TOPIC))
        cm.register("secret", ChannelDef(type=ChannelType.LAST_VALUE))

        with caplog.at_level(logging.WARNING):
            cm.read("secret", node_id="agent_a")
        assert any("secret" in r.message and "readable" in r.message for r in caplog.records)

    def test_write_logs_warning_for_undeclared_access(self, caplog: Any) -> None:
        from hecate.engine.channel import ChannelManager
        from hecate.engine.types import ChannelAccess, ChannelDef, ChannelType

        cm = ChannelManager(
            channel_access={
                "agent_a": ChannelAccess(readable=set(), writable={"messages"}),
            }
        )
        cm.register("messages", ChannelDef(type=ChannelType.TOPIC))
        cm.register("results", ChannelDef(type=ChannelType.LAST_VALUE))

        with caplog.at_level(logging.WARNING):
            cm.write("results", "data", node_id="agent_a")
        assert any("results" in r.message and "writable" in r.message for r in caplog.records)

    def test_read_no_warning_when_node_id_is_none(self, caplog: Any) -> None:
        from hecate.engine.channel import ChannelManager
        from hecate.engine.types import ChannelDef, ChannelType

        cm = ChannelManager()
        cm.register("messages", ChannelDef(type=ChannelType.TOPIC))
        cm.write("messages", ["hello"])

        with caplog.at_level(logging.WARNING):
            cm.read("messages")
        assert not any("readable" in r.message for r in caplog.records)

    def test_read_no_warning_for_declared_access(self, caplog: Any) -> None:
        from hecate.engine.channel import ChannelManager
        from hecate.engine.types import ChannelAccess, ChannelDef, ChannelType

        cm = ChannelManager(
            channel_access={
                "agent_a": ChannelAccess(readable={"messages"}, writable=set()),
            }
        )
        cm.register("messages", ChannelDef(type=ChannelType.TOPIC))
        cm.write("messages", ["hello"])

        with caplog.at_level(logging.WARNING):
            cm.read("messages", node_id="agent_a")
        assert not any("readable" in r.message for r in caplog.records)
