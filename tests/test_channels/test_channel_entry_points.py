"""Entry-point registration tests for the channel plugin packages (PR5b).

Mirrors ``tests/test_memory/test_memory_entry_point.py``: the plugin
packages must register ``slack`` / ``feishu`` under the
``hecate.channel_providers`` group, and their zero-arg factories must
return ``None`` when unconfigured (the resolver skips ``None``).
"""

from __future__ import annotations

from collections.abc import Callable
from importlib.metadata import entry_points

import pytest

_GROUP = "hecate.channel_providers"


def _entries() -> dict[str, Callable[[], object]]:
    return {ep.name: ep.load() for ep in entry_points().select(group=_GROUP)}  # type: ignore[no-any-return]


@pytest.fixture()
def _no_channel_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "HECATE_IM_FEISHU_APP_ID",
        "HECATE_IM_FEISHU_APP_SECRET",
        "HECATE_IM_SLACK_BOT_TOKEN",
        "HECATE_IM_SLACK_SIGNING_SECRET",
    ):
        monkeypatch.delenv(var, raising=False)


def test_slack_and_feishu_entries_registered() -> None:
    entries = _entries()
    assert "slack" in entries
    assert "feishu" in entries


def test_factory_contract_returns_none_when_unconfigured(_no_channel_env: None) -> None:
    entries = _entries()
    for name in ("slack", "feishu"):
        factory = entries[name]
        assert callable(factory)
        assert factory() is None
