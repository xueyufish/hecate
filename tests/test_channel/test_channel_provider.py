"""Tests for the hecate.channel_providers entry-point resolver (PR5a).

Multi-instance semantics: unlike memory/llm (single-select → one provider
or None), channels resolve to a ``dict[str, ChannelBase]`` filtered by
``settings.CHANNEL_PROVIDERS`` (a tuple). Mirrors the
``test_memory_provider.py`` skeleton.
"""

from __future__ import annotations

from typing import Any

import pytest

from hecate.channel import resolver as resolver_mod
from hecate.channel.adapter import ChannelBase
from hecate.channel.resolver import (
    im_channel_names,
    reset_channel_providers_cache,
    resolve_channel_providers,
)
from hecate.core.config import settings


class _StubChannel(ChannelBase):
    def __init__(self, name: str = "stub") -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return "stub channel"

    @property
    def capabilities(self) -> Any:  # pragma: no cover — shape only
        from hecate.channel.capabilities import ChannelCapabilities

        return ChannelCapabilities()

    async def receive(self, raw: object) -> Any:  # pragma: no cover
        raise NotImplementedError

    async def respond(self, message_id: str, response: object) -> None:  # pragma: no cover
        raise NotImplementedError

    async def stream(self, message_id: str, chunks: Any) -> None:  # pragma: no cover
        raise NotImplementedError


class _FakeEntryPoint:
    def __init__(self, name: str, factory: Any) -> None:
        self.name = name
        self._factory = factory

    def load(self) -> Any:
        return self._factory


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    reset_channel_providers_cache()
    yield
    reset_channel_providers_cache()


def test_multi_instance_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both configured channels resolve; the dict is keyed by entry name."""
    feishu = _StubChannel("feishu")
    slack = _StubChannel("slack")
    monkeypatch.setattr(
        resolver_mod,
        "entry_points",
        lambda group: [
            _FakeEntryPoint("feishu", lambda: feishu),
            _FakeEntryPoint("slack", lambda: slack),
        ],
    )
    monkeypatch.setattr(settings, "CHANNEL_PROVIDERS", ("feishu", "slack"))

    resolved = resolve_channel_providers()
    assert resolved == {"feishu": feishu, "slack": slack}


def test_unconfigured_factory_returns_none_and_is_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Factory returning None (missing env) is skipped without breaking others."""
    configured = _StubChannel("slack")
    monkeypatch.setattr(
        resolver_mod,
        "entry_points",
        lambda group: [
            _FakeEntryPoint("feishu", lambda: None),  # env not configured
            _FakeEntryPoint("slack", lambda: configured),
        ],
    )
    monkeypatch.setattr(settings, "CHANNEL_PROVIDERS", ("feishu", "slack"))

    resolved = resolve_channel_providers()
    assert resolved == {"slack": configured}


def test_raising_factory_skipped_but_others_survive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One broken channel never blocks the others."""

    def _boom() -> Any:
        raise RuntimeError("vendor misconfigured")

    configured = _StubChannel("slack")
    monkeypatch.setattr(
        resolver_mod,
        "entry_points",
        lambda group: [
            _FakeEntryPoint("feishu", _boom),
            _FakeEntryPoint("slack", lambda: configured),
        ],
    )
    monkeypatch.setattr(settings, "CHANNEL_PROVIDERS", ("feishu", "slack"))

    assert resolve_channel_providers() == {"slack": configured}


def test_names_not_in_settings_are_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    """Entry points outside CHANNEL_PROVIDERS never load their factories."""
    loaded: list[str] = []

    def _factory(name: str) -> Any:
        loaded.append(name)
        return _StubChannel(name)

    monkeypatch.setattr(
        resolver_mod,
        "entry_points",
        lambda group: [_FakeEntryPoint("dingtalk", lambda: _factory("dingtalk"))],
    )
    monkeypatch.setattr(settings, "CHANNEL_PROVIDERS", ("feishu", "slack"))

    assert resolve_channel_providers() == {}
    assert loaded == []  # factory never invoked


def test_result_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    """Repeated calls return the same dict; the entry-point scan runs once."""
    slack = _StubChannel("slack")
    scans: list[str] = []

    def _scanning_entry_points(group: str) -> list[_FakeEntryPoint]:
        scans.append(group)
        return [_FakeEntryPoint("slack", lambda: slack)]

    monkeypatch.setattr(resolver_mod, "entry_points", _scanning_entry_points)
    monkeypatch.setattr(settings, "CHANNEL_PROVIDERS", ("slack",))

    first = resolve_channel_providers()
    second = resolve_channel_providers()
    assert first is second
    assert scans == ["hecate.channel_providers"]


def test_default_install_with_no_env_resolves_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real entry points + no env vars → factories return None → empty dict."""
    monkeypatch.setattr(settings, "CHANNEL_PROVIDERS", ("feishu", "slack"))
    # Do NOT monkeypatch entry_points — exercise the real group from the
    # root pyproject. With no HECATE_IM_* env vars, providers return None.
    monkeypatch.delenv("HECATE_IM_FEISHU_APP_ID", raising=False)
    monkeypatch.delenv("HECATE_IM_FEISHU_APP_SECRET", raising=False)
    monkeypatch.delenv("HECATE_IM_SLACK_BOT_TOKEN", raising=False)
    monkeypatch.delenv("HECATE_IM_SLACK_SIGNING_SECRET", raising=False)

    assert resolve_channel_providers() == {}


def test_im_channel_names_unions_resolver_and_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prefix tuple union: hardcoded prefixes + configured channels, deduped."""
    slack = _StubChannel("slack")
    dingtalk = _StubChannel("dingtalk")
    monkeypatch.setattr(
        resolver_mod,
        "entry_points",
        lambda group: [
            _FakeEntryPoint("slack", lambda: slack),
            _FakeEntryPoint("dingtalk", lambda: dingtalk),
        ],
    )
    monkeypatch.setattr(settings, "CHANNEL_PROVIDERS", ("slack", "dingtalk"))

    names = im_channel_names()
    # Hardcoded fallback preserved...
    for legacy in ("feishu", "dingtalk", "wecom", "telegram"):
        assert legacy in names
    # ...resolver results unioned, order stable, no duplicates.
    assert "slack" in names
    assert names.count("slack") == 1
    assert names[0] == "feishu"  # fallback first, resolver additions appended


def test_im_channel_names_degrades_on_resolver_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the resolver explodes, im_channel_names falls back to the tuple."""

    def _boom(group: str) -> Any:
        raise RuntimeError("metadata db corrupt")

    monkeypatch.setattr(resolver_mod, "entry_points", _boom)
    reset_channel_providers_cache()

    assert im_channel_names() == ("feishu", "slack", "dingtalk", "wecom", "telegram")
