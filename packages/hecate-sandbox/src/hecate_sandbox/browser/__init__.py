"""Browser automation subsystem (6.27).

Provides :class:`BrowserSession` (per-session Playwright wrapper) and
:class:`BrowserSessionManager` (per-agent-session lifecycle over the existing
``SandboxPool``). Browser tools run inside the dedicated
``hecate-browser-sandbox`` Docker image; the main process drives each session
over HTTP via ``docker exec curl http://127.0.0.1:8080/<tool>``.
"""

from __future__ import annotations

from hecate_sandbox.browser.session import BrowserSession, BrowserSessionManager

__all__ = ["BrowserSession", "BrowserSessionManager"]
