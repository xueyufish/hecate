"""Browser session management — per-agent-session Playwright lifecycle.

The :class:`BrowserSession` represents a single agent session's browser
instance. Each session is backed by a dedicated sandbox container acquired
from :class:`hecate.services.sandbox.pool.SandboxPool`; inside the
container, the HTTP driver (``docker/sandbox/entrypoint.py``) holds the
singleton Chromium and answers JSON-over-HTTP tool calls.

:class:`BrowserSessionManager` owns the lifecycle: it lazily allocates a
container + driver on first use, reuses it for subsequent tool calls in the
same session, and recycles the container back to the pool on session end.

Communication transport: the main process invokes each tool by running
``docker exec <container_id> curl -sX POST http://127.0.0.1:8080/<tool> -d
'<args-json>'`` inside the container's network namespace. This avoids the
need to publish container ports to the host.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from hecate_sandbox.sandbox.executor import SandboxConfig, SandboxExecutor
from hecate_sandbox.sandbox.pool import PooledContainer, SandboxPool

logger = logging.getLogger(__name__)


class BrowserSession:
    """A single agent session's browser instance.

    Holds a container reference and a reference to the ``SandboxPool`` so the
    caller can return the container to the pool when the session ends. All
    browser tool methods route through :meth:`_call_driver` which POSTs JSON
    to the in-container HTTP driver.
    """

    def __init__(
        self,
        session_id: str,
        container: PooledContainer,
        pool: SandboxPool,
        *,
        call_timeout: float = 60.0,
    ) -> None:
        self.session_id = session_id
        self.container = container
        self.pool = pool
        self._call_timeout = call_timeout
        self._closed = False

    async def _call_driver(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        """Invoke the in-container driver and return the parsed JSON result.

        Args:
            tool: One of the tool names registered in the driver
                (``browser_navigate``, ``browser_click``, etc.).
            args: Tool arguments to JSON-encode in the request body.

        Returns:
            Driver response dict. Driver errors are propagated as
            ``{"error": "<code>", "detail": "..."}``.
        """
        if self._closed:
            return {"error": "session_closed", "detail": f"session {self.session_id} is closed"}

        payload = json.dumps({"args": args})
        curl_cmd = [
            "curl",
            "-sS",
            "-X",
            "POST",
            "-H",
            "Content-Type: application/json",
            "-d",
            payload,
            f"http://127.0.0.1:8080/{tool}",
        ]
        docker_cmd = ["docker", "exec", self.container.container_id, *curl_cmd]

        try:
            proc = await asyncio.create_subprocess_exec(
                *docker_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=self._call_timeout)
        except TimeoutError:
            return {
                "error": "driver_timeout",
                "detail": f"driver call {tool!r} exceeded {self._call_timeout}s",
            }
        except Exception as exc:
            return {"error": "driver_call_failed", "detail": str(exc)}

        if proc.returncode != 0:
            return {
                "error": "driver_nonzero_exit",
                "detail": stderr.decode(errors="replace").strip() or f"exit={proc.returncode}",
            }

        try:
            return json.loads(stdout.decode())
        except json.JSONDecodeError as exc:
            return {"error": "driver_invalid_json", "detail": str(exc)}

    async def navigate(self, url: str, wait_until: str = "load") -> dict[str, Any]:
        return await self._call_driver("browser_navigate", {"url": url, "wait_until": wait_until})

    async def click(self, selector: str | None, text: str | None = None, index: int = 0) -> dict[str, Any]:
        return await self._call_driver("browser_click", {"selector": selector, "text": text, "index": index})

    async def type_text(
        self,
        selector: str,
        text: str,
        submit: bool = False,
    ) -> dict[str, Any]:
        return await self._call_driver(
            "browser_type",
            {"selector": selector, "text": text, "submit": submit},
        )

    async def extract(self, selector: str | None = None, mode: str = "a11y") -> dict[str, Any]:
        return await self._call_driver("browser_extract", {"selector": selector, "mode": mode})

    async def screenshot(self, full_page: bool = False, selector: str | None = None) -> dict[str, Any]:
        return await self._call_driver(
            "browser_screenshot",
            {"full_page": full_page, "selector": selector},
        )

    async def fill_form(self, fields: list[dict[str, Any]]) -> dict[str, Any]:
        return await self._call_driver("browser_fill_form", {"fields": fields})

    async def close(self) -> None:
        """Close the session and recycle the container back to the pool."""
        if self._closed:
            return
        self._closed = True
        try:
            await self.pool.recycle(self.container)
        except Exception:
            logger.exception("failed to recycle container %s", self.container.container_id[:12])


class BrowserSessionManager:
    """Manages per-agent-session browser instances.

    Sessions are keyed by an opaque string (typically the agent session id).
    On first call for a session id, a sandbox container is allocated and the
    HTTP driver is launched inside it; subsequent calls reuse the same
    container until the session is closed or the pool retires the container.
    """

    def __init__(
        self,
        pool: SandboxPool,
        *,
        driver_ready_timeout: float = 30.0,
        driver_call_timeout: float = 60.0,
    ) -> None:
        self._pool = pool
        self._driver_ready_timeout = driver_ready_timeout
        self._driver_call_timeout = driver_call_timeout
        self._sessions: dict[str, BrowserSession] = {}
        self._lock = asyncio.Lock()

    async def get_or_create(self, session_id: str) -> BrowserSession:
        """Return the session for ``session_id``, creating it if missing.

        Args:
            session_id: Opaque session identifier (typically agent session id).

        Returns:
            A :class:`BrowserSession` ready to dispatch tool calls.
        """
        existing = self._sessions.get(session_id)
        if existing is not None and not existing._closed:
            return existing

        async with self._lock:
            existing = self._sessions.get(session_id)
            if existing is not None and not existing._closed:
                return existing
            container = await self._pool.allocate()
            await self._launch_driver(container)
            session = BrowserSession(
                session_id=session_id,
                container=container,
                pool=self._pool,
                call_timeout=self._driver_call_timeout,
            )
            self._sessions[session_id] = session
            return session

    async def close(self, session_id: str) -> None:
        """Close and recycle the session's container."""
        session = self._sessions.pop(session_id, None)
        if session is None:
            return
        await session.close()

    async def close_all(self) -> None:
        """Close every active session. Called during shutdown."""
        sessions = list(self._sessions.values())
        self._sessions.clear()
        for session in sessions:
            await session.close()

    async def _launch_driver(self, container: PooledContainer) -> None:
        """Launch the HTTP driver inside ``container`` and wait for readiness."""
        container_id = container.container_id
        # `docker exec -d` runs the command detached inside the container;
        # the container's main process (sleep infinity) keeps running, the
        # driver runs alongside.
        launch_cmd = [
            "docker",
            "exec",
            "-d",
            container_id,
            "sh",
            "-c",
            "nohup python /opt/sandbox/entrypoint.py > /tmp/browser-driver.log 2>&1 &",
        ]
        proc = await asyncio.create_subprocess_exec(
            *launch_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"failed to launch browser driver in container {container_id[:12]}")

        deadline = time.monotonic() + self._driver_ready_timeout
        health_cmd = ["docker", "exec", container_id, "curl", "-sf", "http://127.0.0.1:8080/healthz"]
        while time.monotonic() < deadline:
            proc = await asyncio.create_subprocess_exec(
                *health_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
            if proc.returncode == 0:
                return
            await asyncio.sleep(0.5)
        raise RuntimeError(
            f"browser driver in container {container_id[:12]} did not become healthy within "
            f"{self._driver_ready_timeout}s"
        )


def build_browser_pool(image: str = "hecate-browser-sandbox:latest") -> SandboxPool:
    """Construct a :class:`SandboxPool` configured for browser sessions.

    Args:
        image: Docker image tag for browser sandbox containers. Defaults to
            ``hecate-browser-sandbox:latest``.

    Returns:
        A pool with bridge networking (so Chromium can reach external hosts)
        and per-tool-class image selection (so the ~600MB Chromium image
        stays separate from the lightweight ``hecate-sandbox`` image used by
        ``execute_code``).
    """
    config = SandboxConfig(image=image, network_mode="bridge")
    executor = SandboxExecutor(config=config)
    return SandboxPool(executor=executor)
