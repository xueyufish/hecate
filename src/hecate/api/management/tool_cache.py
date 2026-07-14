"""REST API for tool cache management."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api/tools/cache", tags=["tool-cache"])

_cache: object | None = None


def set_cache(cache: object) -> None:
    """Set the module-level ToolCache instance."""
    global _cache  # noqa: PLW0603
    _cache = cache


def _get_cache() -> object:
    if _cache is None:
        from hecate.services.tool.cache import ToolCache

        return ToolCache()
    return _cache


@router.get("/stats")
async def get_cache_stats() -> dict:
    """Get cache statistics.

    Returns:
        Dict with hits, misses, entries, and hit_rate.
    """
    cache = _get_cache()
    if hasattr(cache, "stats"):
        return cache.stats()
    return {"hits": 0, "misses": 0, "entries": 0, "hit_rate": 0.0}


@router.delete("")
async def clear_cache(tool_name: str | None = None) -> dict:
    """Clear cache entries.

    Args:
        tool_name: If set, only clear entries for this tool. Otherwise clear all.

    Returns:
        Dict with count of removed entries.
    """
    cache = _get_cache()
    if hasattr(cache, "invalidate"):
        count = cache.invalidate(tool_name)
        return {"cleared": count}
    return {"cleared": 0}
