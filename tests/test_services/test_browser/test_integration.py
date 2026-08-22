"""Real-Playwright integration tests for the browser sandbox driver.

These tests exercise the in-container entrypoint against an actual headless
Chromium. They are gated behind ``_has_chromium_runtime`` which checks for
Playwright and a usable browser; on any CI without a Chromium install the
tests skip without error.

Run locally with: ``pytest tests/test_services/test_browser/test_integration.py -v``
"""

from __future__ import annotations

import asyncio
import json
import socket
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
ENTRYPOINT = REPO_ROOT / "docker" / "sandbox" / "entrypoint.py"


def _has_chromium_runtime() -> bool:
    """Probe whether Playwright + a usable Chromium binary are available."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
            browser.close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _has_chromium_runtime(),
    reason="Playwright + Chromium not installed locally",
)


@pytest.fixture
def driver_url() -> str:
    """Start the entrypoint driver in a background thread and return its URL."""
    from http.server import ThreadingHTTPServer

    sys.path.insert(0, str(ENTRYPOINT.parent))
    from entrypoint import _DriverHandler  # type: ignore[import-not-found]

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    server = ThreadingHTTPServer(("127.0.0.1", port), _DriverHandler)
    server.daemon_threads = True
    asyncio.get_event_loop().run_in_executor(None, server.serve_forever)
    yield f"http://127.0.0.1:{port}"
    server.shutdown()
    server.server_close()


def test_echo_round_trip(driver_url: str) -> None:
    import urllib.request

    req = urllib.request.Request(
        f"{driver_url}/echo",
        data=json.dumps({"args": {"hello": "world"}}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as response:  # noqa: S310 - test-only localhost URL
        body = json.loads(response.read())
    assert body == {"echo": {"hello": "world"}}


def test_healthz_returns_ok(driver_url: str) -> None:
    import urllib.request

    with urllib.request.urlopen(f"{driver_url}/healthz", timeout=10) as response:  # noqa: S310 - localhost
        body = json.loads(response.read())
    assert body == {"status": "ok"}


def test_unknown_tool_returns_404(driver_url: str) -> None:
    import urllib.error
    import urllib.request

    req = urllib.request.Request(
        f"{driver_url}/no_such_tool",
        data=b"{}",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req, timeout=10)  # noqa: S310 - localhost
    assert exc_info.value.code == 404
    body = json.loads(exc_info.value.read())
    assert body["error"] == "unknown_tool"


def test_navigate_real_http(driver_url: str) -> None:
    """Hit a local HTTP test server via the driver."""
    import http.server
    import threading

    captured = {}

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            captured["path"] = self.path
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><head><title>OK</title></head><body>hi</body></html>")

        def log_message(self, *_args: object, **_kwargs: object) -> None:
            return

    test_server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    port = test_server.server_address[1]
    threading.Thread(target=test_server.serve_forever, daemon=True).start()

    try:
        import urllib.request

        req = urllib.request.Request(  # noqa: S310 - localhost only
            f"{driver_url}/browser_navigate",
            data=json.dumps({"args": {"url": f"http://127.0.0.1:{port}/probe"}}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as response:
            body = json.loads(response.read())
        assert body["status"] == 200
        assert body["title"] == "OK"
        assert captured["path"] == "/probe"
    finally:
        test_server.shutdown()
