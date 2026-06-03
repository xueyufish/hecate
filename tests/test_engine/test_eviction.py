"""Tests for the EvictionPolicy abstract interface and implementations.

Validates the pluggable eviction contract:

- EvictionPolicy ABC cannot be instantiated directly.
- NoEviction never evicts (default behavior).
- SizeBasedEviction evicts when size exceeds max.
"""

from __future__ import annotations

import pytest

from hecate.engine.eviction import EvictionPolicy, NoEviction, SizeBasedEviction

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
