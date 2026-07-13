"""ExtensionPluginABC — base class for extension-type plugins (Google ADK BasePlugin pattern).

All callback methods are optional. Override only the ones you need.
The loader detects implemented callbacks via hasattr().
"""

from __future__ import annotations

from typing import Any

from hecate.engine.guardrail import GuardrailResult


class ExtensionPluginABC:
    """Base class for plugins that inject logic into the agent execution flow.

    All methods are optional — implement only the callbacks you need.
    Returning a GuardrailResult with action BLOCK from a callback
    short-circuits the corresponding execution stage.
    """

    def on_pre_llm(self, messages: list[dict[str, Any]], config: dict[str, Any]) -> GuardrailResult | None:
        return None

    def on_post_llm(self, response: dict[str, Any], config: dict[str, Any]) -> GuardrailResult | None:
        return None

    def on_pre_tool(self, tool_name: str, args: dict[str, Any]) -> GuardrailResult | None:
        return None

    def on_post_tool(self, tool_name: str, result: dict[str, Any]) -> GuardrailResult | None:
        return None
