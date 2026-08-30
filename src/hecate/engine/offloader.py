"""Context offloading for overflow conversation messages.

When the LLMWorker context pipeline drops messages to fit the token budget,
the ContextOffloader writes those dropped messages to the AgentEnvironment
filesystem instead of letting them be permanently discarded by compression.

The offloaded messages are stored as JSON files under
``memory/sessions/{session_id}/offloaded_{timestamp}.json`` and a compact
reference stub replaces them in the live context. The agent can retrieve the
full content on demand via the existing ``read_file`` tool.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hecate.services.environment.environment import AgentEnvironment

logger = logging.getLogger(__name__)

# Hard cap on the reference stub content length. Keeps the stub tiny so it
# consumes only a small slice of the token budget it is trying to preserve.
_STUB_MAX_CHARS = 500

# Number of characters to extract from each user message when building the
# heuristic topic summary. Cheap, non-LLM signal that helps the agent decide
# whether retrieving the offloaded block is worthwhile.
_SUMMARY_PER_MESSAGE_CHARS = 200


class ContextOffloader:
    """Offload overflow context messages to the AgentEnvironment filesystem.

    The offloader serializes dropped messages to a timestamped JSON file inside
    the agent's environment and returns a compact reference stub that replaces
    the dropped block in the live context. Offloaded content stays retrievable
    via the existing ``read_file`` tool — no new tool registration is required.

    Lifecycle:
    - Constructed once per PregelRuntime (or once per LLMWorker pipeline run)
      with the agent's ``AgentEnvironment``.
    - ``is_enabled()`` returns ``False`` when no environment is attached,
      signalling the pipeline to skip the offload step entirely.
    - ``offload(...)`` is called by the pipeline with the dropped messages.
    """

    def __init__(
        self,
        environment: AgentEnvironment | None = None,
        threshold_tokens: int = 6000,
    ) -> None:
        """Initialize the offloader with an optional agent environment.

        Args:
            environment: The agent's persistent environment. When ``None``,
                ``is_enabled()`` returns ``False`` and ``offload`` raises.
            threshold_tokens: Minimum tokens in the dropped-message block that
                triggers an offload. Prevents trivially small file writes.
        """
        self._environment = environment
        self._threshold_tokens = threshold_tokens

    def is_enabled(self) -> bool:
        """Return whether offloading can proceed.

        Returns:
            ``True`` only when a backing environment is attached.
        """
        return self._environment is not None

    @property
    def threshold_tokens(self) -> int:
        """Minimum dropped tokens that trigger an offload."""
        return self._threshold_tokens

    async def offload(
        self,
        messages: list[dict[str, Any]],
        session_id: str,
    ) -> dict[str, Any]:
        """Offload a block of messages to the environment filesystem.

        Serializes ``messages`` as JSON, writes the result to
        ``memory/sessions/{session_id}/offloaded_{timestamp}.json`` inside the
        environment, and returns a compact reference stub that replaces the
        offloaded block in the live context.

        Args:
            messages: The messages being dropped from the live context.
                Full structure (role, content, tool_calls, tool_call_id) is
                preserved in the JSON file.
            session_id: The current session id, used to namespace offload
                files per session.

        Returns:
            A single ``system``-role message dict containing the file path,
            a heuristic topic summary, and a ``read_file`` retrieval hint.

        Raises:
            RuntimeError: If no environment is attached. Callers should guard
                with ``is_enabled()``.
        """
        if self._environment is None:
            raise RuntimeError("ContextOffloader.offload called without an environment")

        path = await self._resolve_unique_path(session_id)
        payload = json.dumps(messages, ensure_ascii=False).encode("utf-8")
        await self._environment.write_file(path, payload)
        logger.info(
            "Offloaded %d messages (%d bytes) to %s",
            len(messages),
            len(payload),
            path,
        )
        return self._build_stub(path, messages)

    async def _resolve_unique_path(self, session_id: str) -> str:
        """Build a timestamped offload path, suffixing on same-second collisions.

        Args:
            session_id: The current session id.

        Returns:
            A path of the form ``memory/sessions/{sid}/offloaded_{ts}.json``
            (or ``offloaded_{ts}_N.json`` on collision).
        """
        base = f"memory/sessions/{session_id}/offloaded_{self._timestamp()}"
        candidate = f"{base}.json"
        suffix = 1
        while await self._environment.exists(candidate):
            candidate = f"{base}_{suffix}.json"
            suffix += 1
        return candidate

    @staticmethod
    def _timestamp() -> str:
        """Return the current UTC time as ``YYYYMMDDHHMMSS``.

        Returns:
            A 14-digit timestamp string.
        """
        return datetime.now(UTC).strftime("%Y%m%d%H%M%S")

    @staticmethod
    def _build_stub(path: str, messages: list[dict[str, Any]]) -> dict[str, Any]:
        """Build the compact reference stub that replaces the offloaded block.

        The stub is a ``system``-role message whose content contains:
        - The offload file path
        - A heuristic topic summary extracted from user messages
        - An explicit ``read_file`` retrieval instruction

        The total content length is capped at ``_STUB_MAX_CHARS``.

        Args:
            path: The environment-relative path the messages were written to.
            messages: The offloaded messages (used to derive the topic summary).

        Returns:
            A message dict with ``role="system"`` and compact ``content``.
        """
        summary = ContextOffloader._heuristic_summary(messages)
        retrieval_hint = f'Use read_file("{path}") to retrieve the full content.'
        content = f"[Earlier conversation offloaded to {path}. Topics: {summary}. {retrieval_hint}]"
        if len(content) > _STUB_MAX_CHARS:
            content = content[: _STUB_MAX_CHARS - 3] + "..."
        return {"role": "system", "content": content}

    @staticmethod
    def _heuristic_summary(messages: list[dict[str, Any]]) -> str:
        """Extract a heuristic topic summary from user messages.

        Takes the first ``_SUMMARY_PER_MESSAGE_CHARS`` characters of each
        ``user``-role message, joins them with a separator, and truncates the
        result so that the combined summary stays well under the stub cap.

        Args:
            messages: The offloaded messages.

        Returns:
            A single-line topic summary string.
        """
        parts: list[str] = []
        for msg in messages:
            if msg.get("role") != "user":
                continue
            content = msg.get("content", "")
            if not isinstance(content, str):
                content = str(content)
            if not content:
                continue
            parts.append(content[:_SUMMARY_PER_MESSAGE_CHARS].strip())
        summary = " | ".join(parts)
        # Reserve room for the surrounding stub template (~120 chars) so the
        # overall content stays under _STUB_MAX_CHARS after wrapping.
        budget = _STUB_MAX_CHARS - 120
        if len(summary) > budget:
            summary = summary[: budget - 3] + "..."
        return summary or "no user messages"
