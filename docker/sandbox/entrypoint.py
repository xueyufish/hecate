"""Browser sandbox HTTP driver.

Runs inside the hecate-browser-sandbox Docker container, launched via
``docker exec -d <container_id> python /opt/sandbox/entrypoint.py``. The
process holds a single headless Chromium instance (started lazily on the
first tool call) and exposes a small JSON-over-HTTP API on
``127.0.0.1:8080`` so the main process can drive it through
``docker exec <container_id> curl http://127.0.0.1:8080/<tool>``.

Transport: the main process invokes the driver via
``docker exec <container_id> curl -sX POST http://127.0.0.1:8080/<tool>
-H 'Content-Type: application/json' -d '<json>'``. The HTTP server runs in
the container's network namespace, so localhost resolves to the container's
loopback and no port mapping is required at the host level.
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import signal
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

logger = logging.getLogger("hecate.browser_sandbox")
logging.basicConfig(
    level=os.environ.get("BROWSER_SANDBOX_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

_BROWSER: Any = None
_BROWSER_LOCK = threading.Lock()


def _get_browser() -> Any:
    """Lazy-init the singleton Playwright browser."""
    global _BROWSER
    if _BROWSER is not None:
        return _BROWSER
    with _BROWSER_LOCK:
        if _BROWSER is not None:
            return _BROWSER
        from playwright.sync_api import sync_playwright

        playwright = sync_playwright().start()
        _BROWSER = playwright.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"],
        )
        logger.info("browser launched, version=%s", _BROWSER.version)
        return _BROWSER


def _new_page() -> tuple[Any, Any]:
    """Create a fresh isolated browser context and page."""
    browser = _get_browser()
    context = browser.new_context()
    return context, context.new_page()


def _render_a11y(node: dict[str, Any] | None, max_bytes: int = 50_000) -> str:
    """Render a Playwright accessibility snapshot to readable text.

    Output format per line: ``[role] name [state]``. Children are indented
    two spaces per depth level. Output is truncated to ``max_bytes``.
    """
    if not node:
        return ""

    parts: list[str] = []

    def _walk(n: dict[str, Any] | None, d: int) -> None:
        if n is None:
            return
        role = n.get("role", "")
        name = n.get("name", "")
        value = n.get("value")
        props = n.get("properties") or {}
        states = [k for k, v in props.items() if v] if isinstance(props, dict) else []
        bits: list[str] = []
        if role:
            bits.append(f"[{role}]")
        if name:
            bits.append(str(name))
        if value:
            bits.append(f"={value!r}")
        if states:
            bits.append(f"<{','.join(states)}>")
        line = "  " * d + " ".join(bits)
        if line.strip():
            parts.append(line)
        if sum(len(p) for p in parts) > max_bytes:
            parts.append("  " * d + "[truncated]")
            return
        for child in n.get("children") or []:
            _walk(child, d + 1)

    _walk(node, 0)
    return "\n".join(parts)


def handle_browser_navigate(args: dict[str, Any]) -> dict[str, Any]:
    url = args.get("url")
    if not isinstance(url, str) or not url:
        return {"error": "invalid_args", "detail": "url is required"}
    wait_until = args.get("wait_until", "load")
    _, page = _new_page()
    try:
        response = page.goto(url, wait_until=wait_until, timeout=30_000)
        return {
            "url": page.url,
            "title": page.title(),
            "status": response.status if response else None,
        }
    except Exception as exc:
        return {"error": "navigation_failed", "detail": str(exc), "partial_url": page.url}


def handle_browser_click(args: dict[str, Any]) -> dict[str, Any]:
    selector = args.get("selector")
    text = args.get("text")
    index = int(args.get("index", 0))
    _, page = _new_page()
    try:
        if text:
            locator = page.get_by_text(text, exact=False)
        elif selector:
            locator = page.locator(selector)
        else:
            return {"error": "invalid_args", "detail": "selector or text is required"}
        count = locator.count()
        if count == 0:
            return {"error": "element_not_found", "selector": selector, "text": text}
        if count > 1 and text is None and index >= count:
            return {"error": "ambiguous_selector", "count": count, "selector": selector}
        locator.nth(index).click(timeout=5_000)
        return {"clicked": True, "selector": selector, "text": text}
    except Exception as exc:
        return {"error": "click_failed", "detail": str(exc)}


def handle_browser_type(args: dict[str, Any]) -> dict[str, Any]:
    selector = args.get("selector")
    text = args.get("text", "")
    submit = bool(args.get("submit", False))
    if not isinstance(selector, str) or not selector:
        return {"error": "invalid_args", "detail": "selector is required"}
    _, page = _new_page()
    try:
        page.locator(selector).fill("", timeout=5_000)
        page.locator(selector).type(text, timeout=10_000)
        if submit:
            page.keyboard.press("Enter")
        return {"typed": True, "length": len(text), "submitted": submit}
    except Exception as exc:
        return {"error": "type_failed", "detail": str(exc)}


def handle_browser_extract(args: dict[str, Any]) -> dict[str, Any]:
    selector = args.get("selector")
    mode = args.get("mode", "a11y")
    if mode not in {"text", "html", "a11y"}:
        return {"error": "invalid_args", "detail": f"unsupported mode: {mode!r}"}
    _, page = _new_page()
    try:
        target = page.locator(selector) if selector else page
        if mode == "text":
            return {"mode": "text", "content": target.inner_text(timeout=5_000)}
        if mode == "html":
            return {"mode": "html", "content": target.inner_html(timeout=5_000)}
        snapshot = page.accessibility.snapshot()
        return {"mode": "a11y", "content": _render_a11y(snapshot)}
    except Exception as exc:
        return {"error": "extract_failed", "detail": str(exc)}


def handle_browser_screenshot(args: dict[str, Any]) -> dict[str, Any]:
    full_page = bool(args.get("full_page", False))
    selector = args.get("selector")
    _, page = _new_page()
    try:
        if selector:
            png_bytes = page.locator(selector).screenshot(timeout=5_000)
        else:
            png_bytes = page.screenshot(full_page=full_page, timeout=10_000)
        return {
            "image_base64": base64.b64encode(png_bytes).decode("ascii"),
            "url": page.url,
        }
    except Exception as exc:
        return {"error": "screenshot_failed", "detail": str(exc)}


def handle_browser_fill_form(args: dict[str, Any]) -> dict[str, Any]:
    fields = args.get("fields")
    if not isinstance(fields, list) or not fields:
        return {"error": "invalid_args", "detail": "fields must be a non-empty list"}
    _, page = _new_page()
    results: list[dict[str, Any]] = []
    partial = False
    for field in fields:
        selector = field.get("selector")
        value = field.get("value", "")
        if not isinstance(selector, str) or not selector:
            results.append({"selector": selector, "ok": False, "error": "missing_selector"})
            partial = True
            continue
        try:
            page.locator(selector).fill(value, timeout=5_000)
            results.append({"selector": selector, "ok": True})
        except Exception as exc:
            results.append({"selector": selector, "ok": False, "error": str(exc)})
            partial = True
    return {"filled": results, "partial": partial}


HANDLERS: dict[str, Any] = {
    "browser_navigate": handle_browser_navigate,
    "browser_click": handle_browser_click,
    "browser_type": handle_browser_type,
    "browser_extract": handle_browser_extract,
    "browser_screenshot": handle_browser_screenshot,
    "browser_fill_form": handle_browser_fill_form,
    "echo": lambda args: {"echo": args},
    "healthz": lambda args: {"status": "ok"},
}


class _DriverHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
        logger.debug(format, *args)

    def do_POST(self) -> None:  # noqa: N802 - stdlib signature
        tool_name = self.path.lstrip("/")
        handler = HANDLERS.get(tool_name)
        if handler is None:
            self._respond(404, {"error": "unknown_tool", "tool": tool_name})
            return
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(raw) if raw else {}
            args = payload.get("args", {})
        except json.JSONDecodeError as exc:
            self._respond(400, {"error": "invalid_json", "detail": str(exc)})
            return
        try:
            result = handler(args)
            self._respond(200, result)
        except Exception as exc:
            logger.exception("handler %s raised", tool_name)
            self._respond(500, {"error": exc.__class__.__name__, "detail": str(exc)})

    def do_GET(self) -> None:  # noqa: N802 - stdlib signature
        if self.path == "/healthz":
            self._respond(200, {"status": "ok"})
        else:
            self._respond(404, {"error": "not_found"})

    def _respond(self, status: int, body: dict[str, Any]) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def serve(host: str, port: int) -> int:
    server = ThreadingHTTPServer((host, port), _DriverHandler)
    logger.info("browser driver listening on http://%s:%d", host, port)

    def _shutdown(signum: int, _frame: Any) -> None:
        logger.info("received signal %d, shutting down", signum)
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    try:
        server.serve_forever()
    finally:
        server.server_close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Hecate browser sandbox HTTP driver")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    return serve(args.host, args.port)


if __name__ == "__main__":
    raise SystemExit(main())
