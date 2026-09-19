"""Tests for ``hecate_memory.provider`` (PR2.2 entry-point factory).

The core package discovers ``hecate_memory`` via the
``hecate.memory_providers`` entry-point group. The factory must return the
``BuiltinMemoryProvider`` singleton — the full-contract tiered backend — so
that the resolver caches exactly one instance for the process lifetime, and
``search`` must stay async (the tier-1 duck-typed contract the resolver's
consumers rely on).
"""

from __future__ import annotations

import inspect

import pytest


def test_provider_returns_builtin_memory_provider() -> None:
    pytest.importorskip("hecate_memory")
    from hecate_memory.memory.provider_impl import BuiltinMemoryProvider
    from hecate_memory.provider import provider

    instance = provider()
    assert isinstance(instance, BuiltinMemoryProvider)


def test_provider_returns_singleton() -> None:
    """Repeated calls hand back the same instance — no per-request re-init."""
    pytest.importorskip("hecate_memory")
    from hecate_memory.provider import provider

    assert provider() is provider()


def test_provider_signature_matches_resolver_contract() -> None:
    """Factory must expose ``async def search`` (the duck-typed contract)."""
    pytest.importorskip("hecate_memory")
    from hecate_memory.provider import provider

    instance = provider()
    assert hasattr(instance, "search")
    assert inspect.iscoroutinefunction(instance.search)


def test_provider_declares_full_capability_set() -> None:
    """The builtin backend implements every tier of the provider contract."""
    pytest.importorskip("hecate_memory")
    from hecate_memory.provider import provider

    from hecate.core.composition.memory_provider import (
        CAP_ADD_MEMORY,
        CAP_FORGET_MEMORY,
        CAP_PREFETCH,
        CAP_SEARCH,
        CAP_SEARCH_MEMORIES,
        CAP_SEARCH_RECALL,
        CAP_SYNC_TURN,
        CAP_UPDATE_MEMORY,
        provider_supports,
    )

    instance = provider()
    for cap in (
        CAP_SEARCH,
        CAP_SEARCH_MEMORIES,
        CAP_SEARCH_RECALL,
        CAP_ADD_MEMORY,
        CAP_UPDATE_MEMORY,
        CAP_FORGET_MEMORY,
        CAP_PREFETCH,
        CAP_SYNC_TURN,
    ):
        assert provider_supports(instance, cap), f"builtin provider missing capability: {cap}"


def test_provider_is_registered_under_memory_providers_group() -> None:
    """The pyproject ``[project.entry-points."hecate.memory_providers"]`` registration is effective."""
    from importlib.metadata import entry_points

    names = {ep.name for ep in entry_points(group="hecate.memory_providers")}
    assert "builtin" in names
