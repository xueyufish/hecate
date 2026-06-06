"""Tests for the EvictionPolicy abstract interface and implementations.

Validates the pluggable eviction contract:

- EvictionPolicy ABC cannot be instantiated directly.
- NoEviction never evicts (default behavior).
- SizeBasedEviction evicts when size exceeds max.
"""

from __future__ import annotations

import pytest

from hecate.engine.channel import ChannelManager
from hecate.engine.eviction import EvictionPolicy, NoEviction, SizeBasedEviction
from hecate.engine.types import ChannelDef, ChannelType

# --- EvictionPolicy ABC ---


def test_eviction_policy_is_abstract():
    """EvictionPolicy SHALL NOT be instantiable directly."""
    with pytest.raises(TypeError):
        EvictionPolicy()  # type: ignore[abstract]


# --- NoEviction ---


@pytest.fixture
def no_eviction() -> NoEviction:
    return NoEviction()


def test_no_eviction_should_evict_always_false(no_eviction: NoEviction):
    """NoEviction.should_evict SHALL always return False."""
    assert no_eviction.should_evict("messages", 10000, {}) is False
    assert no_eviction.should_evict("messages", 0, {}) is False


def test_no_eviction_select_victim_returns_all(no_eviction: NoEviction):
    """NoEviction.select_victim SHALL return all items unchanged."""
    items = ["a", "b", "c", "d", "e"]
    result = no_eviction.select_victim(items)
    assert result == items
    assert result is not items


# --- SizeBasedEviction ---


@pytest.fixture
def size_eviction() -> SizeBasedEviction:
    return SizeBasedEviction(max_size=100)


def test_size_eviction_below_max(size_eviction: SizeBasedEviction):
    """SizeBasedEviction SHALL return False when below max_size."""
    assert size_eviction.should_evict("messages", 50, {}) is False


def test_size_eviction_at_max(size_eviction: SizeBasedEviction):
    """SizeBasedEviction SHALL return True when at max_size."""
    assert size_eviction.should_evict("messages", 100, {}) is True


def test_size_eviction_above_max(size_eviction: SizeBasedEviction):
    """SizeBasedEviction SHALL return True when above max_size."""
    assert size_eviction.should_evict("messages", 150, {}) is True


def test_size_eviction_select_victim_keeps_newest():
    """SizeBasedEviction.select_victim SHALL keep the newest items."""
    eviction = SizeBasedEviction(max_size=3)
    items = ["a", "b", "c", "d", "e"]
    result = eviction.select_victim(items)
    assert result == ["c", "d", "e"]


def test_size_eviction_select_victim_within_limit():
    """SizeBasedEviction.select_victim SHALL return all items when within limit."""
    eviction = SizeBasedEviction(max_size=10)
    items = ["a", "b", "c"]
    result = eviction.select_victim(items)
    assert result == ["a", "b", "c"]


def test_channel_manager_default_no_eviction():
    """ChannelManager without eviction policy SHALL keep all writes."""
    mgr = ChannelManager()
    mgr.register("msgs", ChannelDef(type=ChannelType.TOPIC, default=[]))
    for i in range(100):
        mgr.write("msgs", i)
    assert mgr.read("msgs") == list(range(100))


def test_channel_manager_size_based_eviction():
    """ChannelManager SHALL evict when TOPIC size exceeds eviction threshold."""
    mgr = ChannelManager(eviction_policy=SizeBasedEviction(max_size=5))
    mgr.register("msgs", ChannelDef(type=ChannelType.TOPIC, default=[]))
    for i in range(10):
        mgr.write("msgs", i)
    assert mgr.read("msgs") == [5, 6, 7, 8, 9]


def test_channel_manager_eviction_skips_last_value():
    """Eviction policy SHALL NOT apply to LAST_VALUE channels."""
    mgr = ChannelManager(eviction_policy=SizeBasedEviction(max_size=3))
    mgr.register("val", ChannelDef(type=ChannelType.LAST_VALUE))
    for ch in ["a", "b", "c", "d", "e"]:
        mgr.write("val", ch)
    assert mgr.read("val") == "e"


def test_channel_manager_eviction_skips_accumulator():
    """Eviction policy SHALL NOT apply to ACCUMULATOR channels."""
    mgr = ChannelManager(eviction_policy=SizeBasedEviction(max_size=3))
    mgr.register("count", ChannelDef(type=ChannelType.ACCUMULATOR, initial=0, reduce_fn="add"))
    for value in [1, 2, 3, 4, 5]:
        mgr.write("count", value)
    assert mgr.read("count") == 15


def test_channel_manager_restore_bypasses_eviction():
    """Restore MUST reproduce snapshot state without applying eviction."""
    mgr = ChannelManager(eviction_policy=SizeBasedEviction(max_size=3))
    mgr.register("msgs", ChannelDef(type=ChannelType.TOPIC, default=[]))
    mgr.restore({"msgs": list(range(10))})
    assert mgr.read("msgs") == list(range(10))
