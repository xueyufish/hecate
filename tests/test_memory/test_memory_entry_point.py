"""Tests for ``hecate_memory.provider`` (PR2.2 entry-point factory).

The core package discovers ``hecate_memory`` via the
``hecate.memory_providers`` entry-point group. The factory must return the
in-process ``KnowledgeBaseService`` singleton so that the resolver caches
exactly one instance for the process lifetime.
"""

from __future__ import annotations

import inspect

import pytest


def test_provider_returns_knowledge_base_service() -> None:
    pytest.importorskip("hecate_memory")
    from hecate_memory.provider import provider
    from hecate_memory.rag.service import KnowledgeBaseService

    instance = provider()
    assert isinstance(instance, KnowledgeBaseService)


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


def test_provider_is_registered_under_memory_providers_group() -> None:
    """The pyproject ``[project.entry-points."hecate.memory_providers"]`` registration is effective."""
    from importlib.metadata import entry_points

    names = {ep.name for ep in entry_points(group="hecate.memory_providers")}
    assert "builtin" in names
