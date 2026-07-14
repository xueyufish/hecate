"""Tests for ToolCache and is_cacheable priority chain."""

from __future__ import annotations

import time

from hecate.services.tool.cache import (
    DANGEROUS_BUILTINS,
    SIDE_EFFECT_PREFIXES,
    ToolCache,
    is_cacheable,
)

# ---------------------------------------------------------------------------
# ToolCache tests
# ---------------------------------------------------------------------------


def test_cache_set_and_get() -> None:
    cache = ToolCache(max_entries=100, default_ttl=60)
    cache.set("key1", "result1")
    assert cache.get("key1") == "result1"


def test_cache_miss_returns_none() -> None:
    cache = ToolCache()
    assert cache.get("nonexistent") is None


def test_cache_ttl_expiry() -> None:
    cache = ToolCache(default_ttl=1)
    cache.set("key1", "result1", ttl=0)
    time.sleep(0.01)
    assert cache.get("key1") is None


def test_cache_lru_eviction() -> None:
    cache = ToolCache(max_entries=2)
    cache.set("key1", "r1")
    cache.set("key2", "r2")
    cache.get("key1")  # access key1, making key2 the LRU
    cache.set("key3", "r3")  # should evict key2
    assert cache.get("key1") == "r1"
    assert cache.get("key2") is None
    assert cache.get("key3") == "r3"


def test_cache_canonical_key_dict_ordering() -> None:
    cache = ToolCache()
    key1 = cache.make_key("tool", {"b": 2, "a": 1})
    key2 = cache.make_key("tool", {"a": 1, "b": 2})
    assert key1 == key2


def test_cache_ignored_args_stripped() -> None:
    cache = ToolCache(ignored_args={"request_id"})
    key1 = cache.make_key("tool", {"query": "test", "request_id": "abc"})
    key2 = cache.make_key("tool", {"query": "test", "request_id": "xyz"})
    assert key1 == key2


def test_cache_session_scoped_keys() -> None:
    cache = ToolCache()
    key_a = cache.make_key("tool", {"q": "test"}, session_id="session_a")
    key_b = cache.make_key("tool", {"q": "test"}, session_id="session_b")
    assert key_a != key_b


def test_cache_invalidate_by_tool_name() -> None:
    cache = ToolCache()
    key1 = cache.make_key("web_search", {"q": "test"}, session_id="s1")
    key2 = cache.make_key("bash", {"cmd": "ls"}, session_id="s1")
    cache.set(key1, "result1", tool_name="web_search")
    cache.set(key2, "result2", tool_name="bash")
    count = cache.invalidate("web_search")
    assert count >= 1
    assert cache.get(key1) is None
    assert cache.get(key2) == "result2"


def test_cache_invalidate_all() -> None:
    cache = ToolCache()
    cache.set("k1", "r1")
    cache.set("k2", "r2")
    count = cache.invalidate()
    assert count == 2
    assert cache.get("k1") is None


def test_cache_stats_accuracy() -> None:
    cache = ToolCache()
    cache.set("k1", "r1")
    cache.get("k1")  # hit
    cache.get("k2")  # miss
    stats = cache.stats()
    assert stats["hits"] == 1
    assert stats["misses"] == 1
    assert stats["entries"] == 1
    assert stats["hit_rate"] == 0.5


def test_cache_read_updates_lru_order() -> None:
    cache = ToolCache(max_entries=2)
    cache.set("k1", "r1")
    cache.set("k2", "r2")
    cache.get("k1")
    cache.set("k3", "r3")
    assert cache.get("k1") == "r1"
    assert cache.get("k2") is None


# ---------------------------------------------------------------------------
# is_cacheable priority chain tests
# ---------------------------------------------------------------------------


def test_explicit_cacheable_true_overrides_everything() -> None:
    meta = {"name": "bash", "cacheable": True}
    assert is_cacheable(meta) is True


def test_explicit_cacheable_false_overrides_everything() -> None:
    meta = {"name": "web_search", "cacheable": False}
    assert is_cacheable(meta) is False


def test_side_effect_prefix_not_cached() -> None:
    for prefix in SIDE_EFFECT_PREFIXES:
        meta = {"name": f"{prefix}something", "risk_level": "low"}
        assert is_cacheable(meta) is False


def test_dangerous_builtin_not_cached() -> None:
    for name in DANGEROUS_BUILTINS:
        meta = {"name": name, "risk_level": "low"}
        assert is_cacheable(meta) is False


def test_low_risk_no_sandbox_cached() -> None:
    meta = {"name": "web_search", "risk_level": "low", "sandbox_enabled": False}
    assert is_cacheable(meta) is True


def test_medium_risk_no_sandbox_cached() -> None:
    meta = {"name": "read_file", "risk_level": "medium", "sandbox_enabled": False}
    assert is_cacheable(meta) is True


def test_high_risk_not_cached_by_default() -> None:
    meta = {"name": "dangerous_tool", "risk_level": "high", "sandbox_enabled": False}
    assert is_cacheable(meta) is False


def test_critical_risk_not_cached_by_default() -> None:
    meta = {"name": "very_dangerous", "risk_level": "critical", "sandbox_enabled": False}
    assert is_cacheable(meta) is False


def test_sandbox_enabled_not_cached() -> None:
    meta = {"name": "some_tool", "risk_level": "low", "sandbox_enabled": True}
    assert is_cacheable(meta) is False


def test_no_metadata_defaults_to_not_cached() -> None:
    meta = {"name": "unknown_tool", "risk_level": "critical"}
    assert is_cacheable(meta) is False
