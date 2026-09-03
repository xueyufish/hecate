"""Audit-pipeline contract tests for browser_* tools.

The actual ``ToolDecisionModel`` recording and PostToolHook DLP scan fire at
the engine layer (see ``engine/workers/tool_worker.py`` and
``services/security/decision_service.py``); they apply uniformly to every
tool, builtin or otherwise. These tests verify the contract the executor
must honor: every browser_* result is a JSON-serialisable ``dict`` so the
audit / DLP pipeline can consume it without special-casing.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from hecate.core.config import settings
from hecate.tools.tool.builtin import BuiltInToolExecutor
from hecate.tools.tool.search import SearchProvider


class _StubSearchProvider(SearchProvider):
    async def search(self, query: str, max_results: int = 5) -> list[dict[str, Any]]:
        return []


@pytest.fixture
def session_manager() -> MagicMock:
    manager = MagicMock()
    session = MagicMock()
    session.navigate = AsyncMock(return_value={"url": "https://example.com/", "title": "Ex", "status": 200})
    session.click = AsyncMock(return_value={"clicked": True, "selector": "button.submit"})
    session.type_text = AsyncMock(return_value={"typed": True, "length": 5, "submitted": False})
    session.extract = AsyncMock(return_value={"mode": "a11y", "content": "[heading] Welcome"})
    session.screenshot = AsyncMock(return_value={"image_base64": "iVBORw0K", "url": "https://example.com/"})
    session.fill_form = AsyncMock(return_value={"filled": [{"selector": "input[u]", "ok": True}], "partial": False})
    manager.get_or_create = AsyncMock(return_value=session)
    return manager


@pytest.fixture
def executor(session_manager: MagicMock, monkeypatch: pytest.MonkeyPatch) -> BuiltInToolExecutor:
    monkeypatch.setattr(settings, "AGENT_ENV_BACKEND", "docker", raising=False)
    return BuiltInToolExecutor(
        search_provider=_StubSearchProvider(),
        browser_session_manager=session_manager,
        allowed_domains=["example.com"],
    )


@pytest.mark.asyncio
async def test_every_browser_result_is_json_serializable(executor: BuiltInToolExecutor) -> None:
    """ToolDecisionModel stores tool args/results; audit pipeline requires JSON-friendly dicts."""
    cases = [
        ("browser_navigate", {"url": "https://example.com/"}),
        ("browser_click", {"selector": "button.submit"}),
        ("browser_type", {"selector": "input", "text": "hello"}),
        ("browser_extract", {"mode": "a11y"}),
        ("browser_screenshot", {}),
        ("browser_fill_form", {"fields": [{"selector": "input[u]", "value": "alice"}]}),
    ]
    for tool_name, args in cases:
        result = await executor.execute(tool_name, args, context={"session_id": "s"})
        assert isinstance(result, dict), f"{tool_name} returned non-dict: {type(result)!r}"
        # Must serialize cleanly — exceptions here would break audit log persistence.
        try:
            json.dumps(result)
        except (TypeError, ValueError) as exc:
            pytest.fail(f"{tool_name} result is not JSON-serializable: {exc} | result={result!r}")


@pytest.mark.asyncio
async def test_browser_navigate_error_envelope_is_structured(executor: BuiltInToolExecutor) -> None:
    """Errors flow through the same dict envelope so audit rows have a stable shape."""
    result = await executor.execute("browser_navigate", {"url": "https://evil.com/"})
    assert isinstance(result, dict)
    assert "error" in result
    # Serialisable so audit row can persist it
    json.dumps(result)


@pytest.mark.asyncio
async def test_browser_screenshot_returns_string_base64(executor: BuiltInToolExecutor) -> None:
    """DLP recognizers expect the image as a string; verify it round-trips through JSON."""
    result = await executor.execute("browser_screenshot", {}, context={"session_id": "s"})
    import base64

    encoded = result["image_base64"]
    assert isinstance(encoded, str)
    base64.b64decode(encoded)  # would raise on malformed


@pytest.mark.asyncio
async def test_session_id_propagates_to_audit(executor: BuiltInToolExecutor, session_manager: MagicMock) -> None:
    """Audit logs include session_id; verify the same id reaches the session manager."""
    await executor.execute("browser_navigate", {"url": "https://example.com/"}, context={"session_id": "audit-007"})
    session_manager.get_or_create.assert_awaited_with("audit-007")
