"""Tests for :class:`BrowserSession` and :class:`BrowserSessionManager`."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from hecate.services.browser.session import (
    BrowserSession,
    BrowserSessionManager,
    build_browser_pool,
)
from hecate.services.sandbox.pool import PooledContainer, SandboxPool


def _driver_response(payload: dict) -> bytes:
    return json.dumps(payload).encode()


@pytest.mark.asyncio
async def test_session_navigate_routes_to_driver(mock_pool: MagicMock, mock_subprocess: list) -> None:
    mock_subprocess.append(("curl", _driver_response({"url": "https://example.com/", "title": "Ex", "status": 200})))

    container = PooledContainer(container_id="cid", use_count=1, in_use=True)
    session = BrowserSession(session_id="s1", container=container, pool=mock_pool)

    result = await session.navigate("https://example.com")

    assert result == {"url": "https://example.com/", "title": "Ex", "status": 200}
    assert mock_pool.recycle.call_count == 0  # not closed yet


@pytest.mark.asyncio
async def test_session_click_with_selector(mock_pool: MagicMock, mock_subprocess: list) -> None:
    mock_subprocess.append(("curl", _driver_response({"clicked": True, "selector": "button.submit"})))

    container = PooledContainer(container_id="cid", use_count=1, in_use=True)
    session = BrowserSession(session_id="s1", container=container, pool=mock_pool)
    result = await session.click(selector="button.submit")

    assert result == {"clicked": True, "selector": "button.submit"}


@pytest.mark.asyncio
async def test_session_click_with_text(mock_pool: MagicMock, mock_subprocess: list) -> None:
    mock_subprocess.append(("curl", _driver_response({"clicked": True, "text": "Buy"})))

    container = PooledContainer(container_id="cid", use_count=1, in_use=True)
    session = BrowserSession(session_id="s1", container=container, pool=mock_pool)
    result = await session.click(selector=None, text="Buy")

    assert result == {"clicked": True, "text": "Buy"}


@pytest.mark.asyncio
async def test_session_type_with_submit(mock_pool: MagicMock, mock_subprocess: list) -> None:
    mock_subprocess.append(("curl", _driver_response({"typed": True, "length": 6, "submitted": True})))

    container = PooledContainer(container_id="cid", use_count=1, in_use=True)
    session = BrowserSession(session_id="s1", container=container, pool=mock_pool)
    result = await session.type_text("input[name=q]", "python", submit=True)

    assert result == {"typed": True, "length": 6, "submitted": True}


@pytest.mark.asyncio
async def test_session_extract_a11y(mock_pool: MagicMock, mock_subprocess: list) -> None:
    mock_subprocess.append(
        (
            "curl",
            _driver_response({"mode": "a11y", "content": "[button] Sign In"}),
        )
    )

    container = PooledContainer(container_id="cid", use_count=1, in_use=True)
    session = BrowserSession(session_id="s1", container=container, pool=mock_pool)
    result = await session.extract(mode="a11y")

    assert result == {"mode": "a11y", "content": "[button] Sign In"}


@pytest.mark.asyncio
async def test_session_screenshot_returns_base64(mock_pool: MagicMock, mock_subprocess: list) -> None:
    fake_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    import base64

    mock_subprocess.append(
        (
            "curl",
            _driver_response({"image_base64": base64.b64encode(fake_png).decode(), "url": "https://x"}),
        )
    )

    container = PooledContainer(container_id="cid", use_count=1, in_use=True)
    session = BrowserSession(session_id="s1", container=container, pool=mock_pool)
    result = await session.screenshot()

    assert "image_base64" in result
    assert base64.b64decode(result["image_base64"]) == fake_png


@pytest.mark.asyncio
async def test_session_fill_form(mock_pool: MagicMock, mock_subprocess: list) -> None:
    mock_subprocess.append(
        (
            "curl",
            _driver_response(
                {
                    "filled": [
                        {"selector": "input[u]", "ok": True},
                        {"selector": "input[p]", "ok": True},
                    ],
                    "partial": False,
                }
            ),
        )
    )

    container = PooledContainer(container_id="cid", use_count=1, in_use=True)
    session = BrowserSession(session_id="s1", container=container, pool=mock_pool)
    result = await session.fill_form(
        [
            {"selector": "input[u]", "value": "alice"},
            {"selector": "input[p]", "value": "s3cret"},
        ]
    )

    assert result["partial"] is False
    assert all(item["ok"] for item in result["filled"])


@pytest.mark.asyncio
async def test_session_propagates_driver_error(mock_pool: MagicMock, mock_subprocess: list) -> None:
    mock_subprocess.append(
        (
            "curl",
            _driver_response({"error": "navigation_failed", "detail": "DNS error"}),
        )
    )

    container = PooledContainer(container_id="cid", use_count=1, in_use=True)
    session = BrowserSession(session_id="s1", container=container, pool=mock_pool)
    result = await session.navigate("https://broken.invalid")

    assert result == {"error": "navigation_failed", "detail": "DNS error"}


@pytest.mark.asyncio
async def test_session_close_recycles_container(mock_pool: MagicMock) -> None:
    container = PooledContainer(container_id="cid", use_count=1, in_use=True)
    session = BrowserSession(session_id="s1", container=container, pool=mock_pool)

    await session.close()

    mock_pool.recycle.assert_called_once_with(container)
    assert session._closed is True


@pytest.mark.asyncio
async def test_session_close_is_idempotent(mock_pool: MagicMock) -> None:
    container = PooledContainer(container_id="cid", use_count=1, in_use=True)
    session = BrowserSession(session_id="s1", container=container, pool=mock_pool)

    await session.close()
    await session.close()

    mock_pool.recycle.assert_called_once_with(container)


@pytest.mark.asyncio
async def test_session_call_after_close_returns_session_closed(mock_pool: MagicMock) -> None:
    container = PooledContainer(container_id="cid", use_count=1, in_use=True)
    session = BrowserSession(session_id="s1", container=container, pool=mock_pool)
    await session.close()

    result = await session.navigate("https://example.com")
    assert result == {"error": "session_closed", "detail": "session s1 is closed"}


@pytest.mark.asyncio
async def test_manager_get_or_create_returns_cached_session(mock_pool: MagicMock, mock_subprocess: list) -> None:
    # First subprocess call is the `docker exec -d ... nohup ...` driver launch.
    # Second is the `curl /healthz` health check. Both return success.
    mock_subprocess.append(("", b""))  # launch
    mock_subprocess.append(("healthz", b""))  # healthz

    manager = BrowserSessionManager(mock_pool, driver_ready_timeout=0.01)

    session_a = await manager.get_or_create("session-1")
    session_b = await manager.get_or_create("session-1")

    assert session_a is session_b
    mock_pool.allocate.assert_awaited_once()
    assert manager._sessions["session-1"] is session_a


@pytest.mark.asyncio
async def test_manager_close_recycles_and_drops_session(mock_pool: MagicMock, mock_subprocess: list) -> None:
    mock_subprocess.append(("", b""))  # launch
    mock_subprocess.append(("healthz", b""))  # healthz

    manager = BrowserSessionManager(mock_pool, driver_ready_timeout=0.01)
    await manager.get_or_create("session-1")
    await manager.close("session-1")

    assert "session-1" not in manager._sessions
    mock_pool.recycle.assert_awaited_once()


@pytest.mark.asyncio
async def test_manager_close_all_clears_every_session(mock_pool: MagicMock, mock_subprocess: list) -> None:
    # Two get_or_create's, each consumes 2 subprocess slots (launch + healthz).
    for _ in range(4):
        mock_subprocess.append(("", b""))

    manager = BrowserSessionManager(mock_pool, driver_ready_timeout=0.01)
    await manager.get_or_create("a")
    await manager.get_or_create("b")
    await manager.close_all()

    assert manager._sessions == {}
    assert mock_pool.recycle.await_count == 2


def test_build_browser_pool_uses_browser_image() -> None:
    pool = build_browser_pool()
    assert isinstance(pool, SandboxPool)
    assert pool._executor.config.image == "hecate-browser-sandbox:latest"
    assert pool._executor.config.network_mode == "bridge"


def test_build_browser_pool_honors_image_override() -> None:
    pool = build_browser_pool(image="custom-browser:1.0")
    assert pool._executor.config.image == "custom-browser:1.0"
