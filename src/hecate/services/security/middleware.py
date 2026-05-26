"""Security middleware for request/response scanning.

Integrates LLM Guard and NeMo Guardrails into the request pipeline
for comprehensive safety checks.
"""

from __future__ import annotations

import logging
from typing import Any

from hecate.services.security.llm_guard import llm_guard_scanner
from hecate.services.security.nemo_guardrails import nemo_config

logger = logging.getLogger(__name__)


class SecurityMiddleware:
    """Middleware for security scanning of requests and responses.

    Provides:
    - Input scanning via LLM Guard
    - Output scanning via LLM Guard
    - Topic control via NeMo Guardrails
    """

    async def check_input(self, message: str) -> dict[str, Any]:
        """Check input message for safety.

        Args:
            message: The user message to check.

        Returns:
            dict with is_safe flag and optional issues.
        """
        nemo_safe = await nemo_config.check_input(message)
        if not nemo_safe:
            return {
                "is_safe": False,
                "issues": ["Message blocked by NeMo Guardrails"],
            }

        llm_guard_result = await llm_guard_scanner.scan_prompt(message)
        if not llm_guard_result.is_safe:
            return {
                "is_safe": False,
                "issues": llm_guard_result.issues,
            }

        return {"is_safe": True, "issues": []}

    async def check_output(self, output: str, prompt: str = "") -> dict[str, Any]:
        """Check LLM output for safety.

        Args:
            output: The LLM output to check.
            prompt: The original prompt for context.

        Returns:
            dict with is_safe flag and optional issues.
        """
        llm_guard_result = await llm_guard_scanner.scan_output(output, prompt)
        if not llm_guard_result.is_safe:
            return {
                "is_safe": False,
                "issues": llm_guard_result.issues,
            }

        return {"is_safe": True, "issues": []}


security_middleware = SecurityMiddleware()
