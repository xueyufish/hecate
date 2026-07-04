"""Tests for InMemoryCacheStrategy and cache key generation."""

from __future__ import annotations

import asyncio

from hecate.model_hub.cache import InMemoryCacheStrategy, generate_cache_key


class TestInMemoryCacheStrategy:
    async def test_get_miss(self) -> None:
        cache = InMemoryCacheStrategy()
        result = await cache.get("nonexistent")
        assert result is None

    async def test_set_and_get(self) -> None:
        cache = InMemoryCacheStrategy()
        await cache.set("key1", {"response": "hello"}, ttl=300)
        result = await cache.get("key1")
        assert result == {"response": "hello"}

    async def test_ttl_expiry(self) -> None:
        cache = InMemoryCacheStrategy()
        await cache.set("key1", {"response": "hello"}, ttl=0)
        await asyncio.sleep(0.01)
        result = await cache.get("key1")
        assert result is None

    async def test_invalidate_pattern(self) -> None:
        cache = InMemoryCacheStrategy()
        await cache.set("gpt-4o:abc", {"r": "1"}, ttl=300)
        await cache.set("gpt-4o:def", {"r": "2"}, ttl=300)
        await cache.set("claude:xyz", {"r": "3"}, ttl=300)
        count = await cache.invalidate("gpt-4o:*")
        assert count == 2
        assert await cache.get("gpt-4o:abc") is None
        assert await cache.get("claude:xyz") == {"r": "3"}

    async def test_stats(self) -> None:
        cache = InMemoryCacheStrategy()
        await cache.set("key1", {"r": "1"}, ttl=300)
        await cache.get("key1")  # hit
        await cache.get("nonexistent")  # miss
        stats = await cache.stats()
        assert stats["hits"] == 1
        assert stats["misses"] == 1
        assert stats["size"] == 1


class TestCacheKeyGeneration:
    def test_same_params_same_key(self) -> None:
        messages = [{"role": "user", "content": "Hello"}]
        key1 = generate_cache_key("gpt-4o", messages, 0.7)
        key2 = generate_cache_key("gpt-4o", messages, 0.7)
        assert key1 == key2

    def test_different_temp_different_key(self) -> None:
        messages = [{"role": "user", "content": "Hello"}]
        key1 = generate_cache_key("gpt-4o", messages, 0.7)
        key2 = generate_cache_key("gpt-4o", messages, 0.0)
        assert key1 != key2

    def test_key_has_model_prefix(self) -> None:
        key = generate_cache_key("gpt-4o", [], 0.7)
        assert key.startswith("gpt-4o:")
