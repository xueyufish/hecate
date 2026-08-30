"""Tests for the hecate.memory_providers entry-point resolver (PR2.2).

Verifies single-selection by ``settings.MEMORY_PROVIDER``, module-level caching,
graceful degradation on missing/raising factories, and the chat-path integration
where ``AgentExecutionPort.knowledge_query`` routes through the resolver instead
of importing ``hecate_memory`` directly.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock

import pytest

from hecate.core.config import settings
from hecate.services.orchestration import memory_provider as mp_mod
from hecate.services.orchestration.memory_provider import (
    reset_memory_provider_cache,
    resolve_memory_provider,
)


@dataclass
class _StubHit:
    content: str
    score: float
    metadata: dict[str, Any]


class _StubProvider:
    """Minimal duck-typed memory provider for resolver tests."""

    def __init__(self, name: str = "stub", hits: list[_StubHit] | None = None) -> None:
        self.name = name
        self.calls: list[dict[str, Any]] = []
        self._hits = hits or []

    async def search(
        self,
        collection_name: str,
        query: str,
        *,
        limit: int = 10,
        mode: str = "hybrid",
        workspace_id: str | None = None,
    ) -> list[_StubHit]:
        self.calls.append(
            {
                "collection_name": collection_name,
                "query": query,
                "limit": limit,
                "mode": mode,
                "workspace_id": workspace_id,
            }
        )
        return list(self._hits)


class _FakeEntryPoint:
    def __init__(self, name: str, factory: Any) -> None:
        self.name = name
        self._factory = factory

    def load(self) -> Any:
        return self._factory


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    """Reset the module-level cache between tests so monkeypatching works."""
    reset_memory_provider_cache()
    yield
    reset_memory_provider_cache()


def test_resolve_returns_builtin_when_installed() -> None:
    """Default ``MEMORY_PROVIDER='builtin'`` resolves to the shipped in-process backend."""
    pytest.importorskip("hecate_memory")
    from hecate_memory.rag.service import KnowledgeBaseService

    provider = resolve_memory_provider()
    assert isinstance(provider, KnowledgeBaseService)


def test_resolve_returns_none_when_no_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty group → None, chat path degrades to []."""
    monkeypatch.setattr(mp_mod, "entry_points", lambda group: [])
    monkeypatch.setattr(settings, "MEMORY_PROVIDER", "builtin")

    assert resolve_memory_provider() is None


def test_resolve_skips_non_matching_names(monkeypatch: pytest.MonkeyPatch) -> None:
    """When ``MEMORY_PROVIDER='mem0'`` only the ``mem0`` entry is selected."""
    builtin = _StubProvider(name="builtin")
    mem0 = _StubProvider(name="mem0")
    monkeypatch.setattr(
        mp_mod,
        "entry_points",
        lambda group: [
            _FakeEntryPoint("builtin", lambda: builtin),
            _FakeEntryPoint("mem0", lambda: mem0),
        ],
    )
    monkeypatch.setattr(settings, "MEMORY_PROVIDER", "mem0")

    provider = resolve_memory_provider()
    assert provider is mem0
    assert provider is not builtin


def test_resolve_returns_none_when_name_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    """Configured name not present in group → None + warning, no crash."""
    builtin = _StubProvider(name="builtin")
    monkeypatch.setattr(
        mp_mod,
        "entry_points",
        lambda group: [_FakeEntryPoint("builtin", lambda: builtin)],
    )
    monkeypatch.setattr(settings, "MEMORY_PROVIDER", "does-not-exist")

    assert resolve_memory_provider() is None


def test_resolve_tolerates_raising_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    """Factory raising during construction → None, no crash, no unhandled exception."""

    def _boom() -> _StubProvider:
        raise RuntimeError("vendor misconfigured")

    monkeypatch.setattr(
        mp_mod,
        "entry_points",
        lambda group: [_FakeEntryPoint("builtin", _boom)],
    )
    monkeypatch.setattr(settings, "MEMORY_PROVIDER", "builtin")

    assert resolve_memory_provider() is None


def test_resolve_caches_result(monkeypatch: pytest.MonkeyPatch) -> None:
    """Repeated calls return the same instance; the entry-point scan runs once."""
    builtin = _StubProvider(name="builtin")
    scan_calls: list[str] = []

    def _scanning_entry_points(group: str) -> list[_FakeEntryPoint]:
        scan_calls.append(group)
        return [_FakeEntryPoint("builtin", lambda: builtin)]

    monkeypatch.setattr(mp_mod, "entry_points", _scanning_entry_points)
    monkeypatch.setattr(settings, "MEMORY_PROVIDER", "builtin")

    first = resolve_memory_provider()
    second = resolve_memory_provider()
    third = resolve_memory_provider()

    assert first is second is third is builtin
    assert scan_calls == ["hecate.memory_providers"]


@pytest.mark.asyncio
async def test_knowledge_query_returns_empty_when_provider_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """chat path: resolver returns None → knowledge_query short-circuits to []."""
    from hecate.services.orchestration.agent_execution_port import AgentExecutionPort

    monkeypatch.setattr(mp_mod, "entry_points", lambda group: [])
    # Without configuring any KB IDs the function still returns [] cheaply.
    port = AgentExecutionPort(db=MagicMockSession())  # type: ignore[abstract]
    result = await port.knowledge_query(query="anything", kb_ids=[uuid.uuid4()])
    assert result == []


@pytest.mark.asyncio
async def test_knowledge_query_uses_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """chat path: injected stub provider receives the search call and its hits shape the response."""

    from hecate.models.knowledge import KnowledgeBaseModel
    from hecate.services.orchestration.agent_execution_port import AgentExecutionPort

    hit = _StubHit(content="hello world", score=0.9, metadata={"source": "doc-1"})
    stub = _StubProvider(hits=[hit])

    kb_id = uuid.uuid4()
    kb = KnowledgeBaseModel(id=kb_id, name="KB-A", collection_name="kb_a")
    db = _AsyncSessionStub(_scalar_one_or_none=kb)

    monkeypatch.setattr(mp_mod, "resolve_memory_provider", lambda: stub)

    from hecate.services.orchestration import agent_execution_port as aep

    monkeypatch.setattr(aep, "resolve_memory_provider", lambda: stub)

    port = AgentExecutionPort(db=db)  # type: ignore[abstract]
    chunks = await port.knowledge_query(query="hi", kb_ids=[kb_id])

    assert stub.calls and stub.calls[0]["collection_name"] == "kb_a"
    assert chunks[0]["content"] == "hello world"
    assert chunks[0]["metadata"]["score"] == 0.9
    assert chunks[0]["metadata"]["kb_id"] == str(kb_id)
    assert chunks[0]["metadata"]["kb_name"] == "KB-A"


# ----- async stubs (no DB engine, no real store) -----


class MagicMockSession:
    """Stand-in passed to AgentExecutionPort — never actually executed because resolver short-circuits."""


class _AsyncSessionStub:
    def __init__(self, _scalar_one_or_none: Any = None) -> None:
        self._scalar = _scalar_one_or_none
        self.execute = AsyncMock(return_value=MagicResult(self._scalar))

    async def __aenter__(self) -> _AsyncSessionStub:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None


class MagicResult:
    def __init__(self, scalar: Any) -> None:
        self._scalar = scalar

    def scalar_one_or_none(self) -> Any:
        return self._scalar
