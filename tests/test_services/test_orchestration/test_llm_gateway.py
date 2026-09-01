"""Tests for the hecate.llm_providers entry-point resolver (phase-4 follow-ups A).

Mirrors ``tests/test_services/test_orchestration/test_memory_provider.py``
(the PR2.2 pattern): single-select by ``settings.LLM_PROVIDER``, module-level
caching, graceful degradation to ``None``.
"""

from __future__ import annotations

from typing import Any

import pytest

from hecate.core.config import settings
from hecate.services.orchestration import llm_gateway as gateway_mod
from hecate.services.orchestration.llm_gateway import (
    reset_llm_gateway_cache,
    resolve_llm_gateway,
)


class _StubGateway:
    """Minimal duck-typed LLM gateway for resolver tests."""

    def __init__(self, name: str = "stub") -> None:
        self.name = name

    async def chat(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover
        raise NotImplementedError

    def chat_stream(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover
        raise NotImplementedError

    def list_models(self, **kwargs: Any) -> list[str]:  # pragma: no cover
        return []

    async def test_connection(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover
        raise NotImplementedError


class _FakeEntryPoint:
    def __init__(self, name: str, factory: Any) -> None:
        self.name = name
        self._factory = factory

    def load(self) -> Any:
        return self._factory


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    """Reset the module-level cache between tests so monkeypatching works."""
    reset_llm_gateway_cache()
    yield
    reset_llm_gateway_cache()


def test_resolve_returns_litellm_singleton_when_installed() -> None:
    """Default ``LLM_PROVIDER='litellm'`` resolves to the shipped gateway singleton."""
    pytest.importorskip("hecate_llm")
    from hecate_llm.service import LLMService, llm_service

    gateway = resolve_llm_gateway()
    assert isinstance(gateway, LLMService)
    assert gateway is llm_service


def test_resolve_returns_none_when_no_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty group → None; callers fall back to the singleton import."""
    monkeypatch.setattr(gateway_mod, "entry_points", lambda group: [])
    monkeypatch.setattr(settings, "LLM_PROVIDER", "litellm")

    assert resolve_llm_gateway() is None


def test_resolve_skips_non_matching_names(monkeypatch: pytest.MonkeyPatch) -> None:
    """When ``LLM_PROVIDER='openrouter'`` only the ``openrouter`` entry is selected."""
    litellm = _StubGateway(name="litellm")
    openrouter = _StubGateway(name="openrouter")
    monkeypatch.setattr(
        gateway_mod,
        "entry_points",
        lambda group: [
            _FakeEntryPoint("litellm", lambda: litellm),
            _FakeEntryPoint("openrouter", lambda: openrouter),
        ],
    )
    monkeypatch.setattr(settings, "LLM_PROVIDER", "openrouter")

    gateway = resolve_llm_gateway()
    assert gateway is openrouter
    assert gateway is not litellm


def test_resolve_returns_none_when_name_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    """Configured name not present in group → None + warning, no crash."""
    monkeypatch.setattr(
        gateway_mod,
        "entry_points",
        lambda group: [_FakeEntryPoint("litellm", lambda: _StubGateway())],
    )
    monkeypatch.setattr(settings, "LLM_PROVIDER", "does-not-exist")

    assert resolve_llm_gateway() is None


def test_resolve_tolerates_raising_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    """Factory raising during construction → None, no unhandled exception."""

    def _boom() -> _StubGateway:
        raise RuntimeError("vendor misconfigured")

    monkeypatch.setattr(
        gateway_mod,
        "entry_points",
        lambda group: [_FakeEntryPoint("litellm", _boom)],
    )
    monkeypatch.setattr(settings, "LLM_PROVIDER", "litellm")

    assert resolve_llm_gateway() is None


def test_resolve_caches_result(monkeypatch: pytest.MonkeyPatch) -> None:
    """Repeated calls return the same instance; the entry-point scan runs once."""
    litellm = _StubGateway(name="litellm")
    scan_calls: list[str] = []

    def _scanning_entry_points(group: str) -> list[_FakeEntryPoint]:
        scan_calls.append(group)
        return [_FakeEntryPoint("litellm", lambda: litellm)]

    monkeypatch.setattr(gateway_mod, "entry_points", _scanning_entry_points)
    monkeypatch.setattr(settings, "LLM_PROVIDER", "litellm")

    first = resolve_llm_gateway()
    second = resolve_llm_gateway()
    third = resolve_llm_gateway()

    assert first is second is third is litellm
    assert scan_calls == ["hecate.llm_providers"]
