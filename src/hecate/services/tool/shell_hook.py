"""Shell command hook implementation.

Executes shell commands at hook points. Receives event data as JSON on stdin.
Exit codes: 0=proceed, 2=block. Stdout injected into context for
SessionStart and UserPromptSubmit events.

Gated by HOOK_SHELL_ENABLED setting (default False).
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from hecate.engine.guardrail import GuardrailAction, GuardrailResult
from hecate.engine.session_hooks import HookAction, HookResult

logger = logging.getLogger(__name__)


class ShellCommandHook:
    """Execute shell commands at hook points.

    Args:
        command: Shell command to execute.
        timeout: Max execution time in seconds.
        event_type: The hook event type this handles.
    """

    def __init__(
        self,
        command: str,
        timeout: int = 30,
        event_type: str = "PreToolUse",
    ) -> None:
        self._command = command
        self._timeout = timeout
        self._event_type = event_type

    async def execute_session_hook(
        self,
        event_data: dict[str, Any],
    ) -> HookResult:
        """Execute as a session hook (SessionStart/UserPromptSubmit/PreCompact).

        Args:
            event_data: JSON-serializable event context.

        Returns:
            HookResult with INJECT for context injection or BLOCK.
        """
        stdout, stderr, returncode = await self._run(event_data)

        if returncode == 2:
            reason = stderr.strip() or "Shell hook blocked the action"
            return HookResult(action=HookAction.BLOCK, reason=reason)

        if stdout.strip() and self._event_type in ("SessionStart", "UserPromptSubmit"):
            return HookResult(action=HookAction.INJECT, context_text=stdout.strip())

        return HookResult(action=HookAction.ALLOW)

    async def execute_guardrail_hook(
        self,
        event_data: dict[str, Any],
    ) -> GuardrailResult:
        """Execute as a guardrail hook (PreToolUse/PostToolUse).

        Args:
            event_data: JSON-serializable event context.

        Returns:
            GuardrailResult with BLOCK or ALLOW.
        """
        _, stderr, returncode = await self._run(event_data)

        if returncode == 2:
            reason = stderr.strip() or "Shell hook blocked the action"
            return GuardrailResult(action=GuardrailAction.BLOCK, reason=reason)

        return GuardrailResult(action=GuardrailAction.ALLOW)

    async def _run(self, event_data: dict[str, Any]) -> tuple[str, str, int]:
        """Run the shell command with event data on stdin.

        Returns:
            Tuple of (stdout, stderr, returncode).
        """
        input_json = json.dumps(event_data, default=str)

        try:
            proc = await asyncio.create_subprocess_shell(
                self._command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(input=input_json.encode()),
                    timeout=self._timeout,
                )
            except TimeoutError:
                proc.kill()
                await proc.wait()
                logger.warning("Shell hook timed out after %ds: %s", self._timeout, self._command)
                return "", "Shell hook timed out", 1

            return (
                stdout_bytes.decode(errors="replace"),
                stderr_bytes.decode(errors="replace"),
                proc.returncode or 0,
            )

        except Exception as e:
            logger.error("Shell hook execution failed: %s", e)
            return "", str(e), 1
