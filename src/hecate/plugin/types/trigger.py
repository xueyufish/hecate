"""TriggerPluginABC — base class for trigger-type plugins (event-driven invocation)."""

from __future__ import annotations

from typing import Any


class TriggerPluginABC:
    """Base class for plugins that respond to external events.

    Set ``trigger_type`` to one of: ``webhook``, ``schedule``, ``event``.
    Implement the handler method(s) matching your trigger type.
    """

    trigger_type: str = "webhook"

    async def on_webhook(self, payload: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    async def on_schedule(self) -> dict[str, Any]:
        raise NotImplementedError

    async def on_event(self, event_type: str, data: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError
