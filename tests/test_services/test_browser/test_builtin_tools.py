"""Integration tests for browser_* built-in tools (6.27).

Exercises ``BuiltInToolExecutor`` end-to-end with a mocked
``BrowserSessionManager`` to verify:

- Per-tool routing and JSON-schema validation
- Domain allow-list enforcement (``browser_navigate``)
- Static ``risk_level`` propagation from the definition
- ``AGENT_ENV_BACKEND=local`` failure mode
- ``AGENT_ENV_BACKEND=docker`` with no session manager configured
- Session id propagation from ``context``
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from hecate.core.config import settings
from hecate.services.tool.builtin import (
    BUILTIN_TOOL_DEFINITIONS,
    BuiltInToolExecutor,
    get_browser_tool_names,
    get_risk_level,
)
from hecate.services.tool.search import SearchProvider


class _StubSearchProvider(SearchProvider):
    async def search(self, query: str, max_results: int = 5) -> list[dict[str, Any]]:
        return [{"title": "stub", "url": "https://x", "snippet": query}]


@pytest.fixture
def search_provider() -> SearchProvider:
    return _StubSearchProvider()


@pytest.fixture
def session_manager() -> MagicMock:
    manager = MagicMock()
    session = MagicMock()
    session.navigate = AsyncMock(return_value={"url": "https://example.com/", "title": "Ex", "status": 200})
    session.click = AsyncMock(return_value={"clicked": True, "selector": "button"})
    session.type_text = AsyncMock(return_value={"typed": True, "length": 3, "submitted": False})
    session.extract = AsyncMock(return_value={"mode": "a11y", "content": "[button] Sign In"})
    session.screenshot = AsyncMock(return_value={"image_base64": "iVBORw0K", "url": "https://x"})
    session.fill_form = AsyncMock(return_value={"filled": [], "partial": False})
    manager.get_or_create = AsyncMock(return_value=session)
    manager.close = AsyncMock(return_value=None)
    manager.close_all = AsyncMock(return_value=None)
    return manager


@pytest.fixture
def executor(
    search_provider: SearchProvider,
    session_manager: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> BuiltInToolExecutor:
    monkeypatch.setattr(settings, "AGENT_ENV_BACKEND", "docker", raising=False)
    return BuiltInToolExecutor(
        search_provider=search_provider,
        browser_session_manager=session_manager,
        allowed_domains=["example.com"],
    )


@pytest.mark.asyncio
async def test_browser_navigate_allowed_domain(executor: BuiltInToolExecutor) -> None:
    result = await executor.execute("browser_navigate", {"url": "https://example.com/"})
    assert result == {"url": "https://example.com/", "title": "Ex", "status": 200}


@pytest.mark.asyncio
async def test_browser_navigate_denies_non_allowed_domain(executor: BuiltInToolExecutor) -> None:
    result = await executor.execute("browser_navigate", {"url": "https://evil.com/"})
    assert result["error"] == "domain_not_allowed"
    assert result["url"] == "https://evil.com/"
    assert result["risk_level"] == "HIGH"


@pytest.mark.asyncio
async def test_browser_navigate_empty_allowlist_denies_everything(
    search_provider: SearchProvider, session_manager: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "AGENT_ENV_BACKEND", "docker", raising=False)
    executor = BuiltInToolExecutor(
        search_provider=search_provider,
        browser_session_manager=session_manager,
        allowed_domains=[],
    )
    result = await executor.execute("browser_navigate", {"url": "https://example.com/"})
    assert result["error"] == "domain_not_allowed"


@pytest.mark.asyncio
async def test_browser_navigate_local_env_returns_disabled(
    search_provider: SearchProvider, session_manager: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "AGENT_ENV_BACKEND", "local", raising=False)
    executor = BuiltInToolExecutor(
        search_provider=search_provider,
        browser_session_manager=session_manager,
        allowed_domains=["example.com"],
    )
    result = await executor.execute("browser_navigate", {"url": "https://example.com/"})
    assert result == {"error": "browser_disabled", "reason": "sandbox_required"}


@pytest.mark.asyncio
async def test_browser_navigate_no_session_manager_returns_disabled(
    search_provider: SearchProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "AGENT_ENV_BACKEND", "docker", raising=False)
    executor = BuiltInToolExecutor(
        search_provider=search_provider,
        browser_session_manager=None,
        allowed_domains=["example.com"],
    )
    result = await executor.execute("browser_navigate", {"url": "https://example.com/"})
    assert result == {"error": "browser_disabled", "reason": "session_manager_unconfigured"}


@pytest.mark.asyncio
async def test_browser_click_routes_to_session(executor: BuiltInToolExecutor, session_manager: MagicMock) -> None:
    result = await executor.execute("browser_click", {"selector": "button.submit"}, context={"session_id": "agent-1"})
    assert result == {"clicked": True, "selector": "button"}
    session_manager.get_or_create.assert_awaited_once_with("agent-1")


@pytest.mark.asyncio
async def test_browser_type_routes_with_submit(executor: BuiltInToolExecutor, session_manager: MagicMock) -> None:
    await executor.execute(
        "browser_type",
        {"selector": "input[name=q]", "text": "foo", "submit": True},
        context={"session_id": "s"},
    )
    # Verify routing — the mock records the awaited call with submit=True.
    session = await session_manager.get_or_create("s")
    session.type_text.assert_awaited_once_with("input[name=q]", "foo", submit=True)


@pytest.mark.asyncio
async def test_browser_extract_routes_with_mode(executor: BuiltInToolExecutor, session_manager: MagicMock) -> None:
    await executor.execute("browser_extract", {"mode": "text"}, context={"session_id": "s"})
    # Verify routing — the mock records the awaited call with mode=text.
    session = await session_manager.get_or_create("s")
    session.extract.assert_awaited_once_with(selector=None, mode="text")


@pytest.mark.asyncio
async def test_browser_screenshot_routes(executor: BuiltInToolExecutor) -> None:
    result = await executor.execute("browser_screenshot", {"full_page": True}, context={"session_id": "s"})
    assert "image_base64" in result


@pytest.mark.asyncio
async def test_browser_fill_form_routes(executor: BuiltInToolExecutor) -> None:
    result = await executor.execute(
        "browser_fill_form",
        {"fields": [{"selector": "input[u]", "value": "alice"}]},
        context={"session_id": "s"},
    )
    assert result["partial"] is False


def test_get_browser_tool_names_returns_six_tools() -> None:
    assert get_browser_tool_names() == frozenset(
        {
            "browser_navigate",
            "browser_click",
            "browser_type",
            "browser_extract",
            "browser_screenshot",
            "browser_fill_form",
        }
    )


def test_get_risk_level_for_browser_tools_returns_medium() -> None:
    for tool in (
        "browser_navigate",
        "browser_click",
        "browser_type",
        "browser_extract",
        "browser_screenshot",
        "browser_fill_form",
    ):
        assert get_risk_level(tool) == "MEDIUM"


def test_get_risk_level_unknown_tool_defaults_low() -> None:
    assert get_risk_level("nonexistent_tool") == "LOW"


def test_get_risk_level_web_search_returns_low() -> None:
    assert get_risk_level("web_search") == "LOW"


def test_get_risk_level_write_file_returns_medium() -> None:
    assert get_risk_level("write_file") == "MEDIUM"


@pytest.mark.asyncio
async def test_browser_navigate_uses_context_allowed_domains_override(
    search_provider: SearchProvider, session_manager: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Per-call allowed_domains in context overrides executor default."""
    monkeypatch.setattr(settings, "AGENT_ENV_BACKEND", "docker", raising=False)
    executor = BuiltInToolExecutor(
        search_provider=search_provider,
        browser_session_manager=session_manager,
        allowed_domains=["deny-everything.example"],
    )
    # The executor's default blocks example.com, but context allows it.
    result = await executor.execute(
        "browser_navigate",
        {"url": "https://example.com/"},
        context={"allowed_domains": ["example.com"]},
    )
    assert result == {"url": "https://example.com/", "title": "Ex", "status": 200}


@pytest.mark.asyncio
async def test_all_6_browser_tools_are_in_builtin_definitions() -> None:
    for name in (
        "browser_navigate",
        "browser_click",
        "browser_type",
        "browser_extract",
        "browser_screenshot",
        "browser_fill_form",
    ):
        assert name in BUILTIN_TOOL_DEFINITIONS
        params = BUILTIN_TOOL_DEFINITIONS[name]["parameters"]
        assert params["type"] == "object"
        assert "description" in BUILTIN_TOOL_DEFINITIONS[name]
        assert "properties" in params
