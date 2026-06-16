"""Tool result security hook — PII detection and masking in tool execution results."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from hecate.engine.guardrail import GuardrailAction, GuardrailResult, PostToolHook
from hecate.services.security.anonymizer import pii_anonymizer

logger = logging.getLogger(__name__)


class ToolResultSecurityHook(PostToolHook):
    """Post-tool hook that detects and masks PII in tool results."""

    def __init__(
        self,
        *,
        mask_tool_results: bool = True,
        audit_pii_events: bool = False,
        event_store: Any = None,
        session_id: uuid.UUID | None = None,
        superstep: int = 0,
    ) -> None:
        self._mask_tool_results = mask_tool_results
        self._audit_pii_events = audit_pii_events
        self._event_store = event_store
        self._session_id = session_id
        self._superstep = superstep

    async def on_post_tool_call(
        self,
        name: str,
        result: Any,
        context: dict | None,
    ) -> GuardrailResult:
        if not self._mask_tool_results:
            return GuardrailResult(action=GuardrailAction.ALLOW)

        result_str = str(result) if result is not None else ""
        if not result_str:
            return GuardrailResult(action=GuardrailAction.ALLOW)

        if not pii_anonymizer.has_pii(result_str):
            return GuardrailResult(action=GuardrailAction.ALLOW)

        anonymized = pii_anonymizer.anonymize(result_str)

        if self._audit_pii_events:
            self._emit_pii_audit(name, result_str)

        return GuardrailResult(
            action=GuardrailAction.SANITIZE,
            reason="PII masked in tool result",
            modified_data={"result": anonymized.text},
        )

    def _emit_pii_audit(self, tool_name: str, original_result: str) -> None:
        """Emit PII_DETECTED audit event for tool result masking."""
        if self._event_store is None or self._session_id is None:
            logger.warning("PII audit enabled but event_store or session_id not configured")
            return

        import asyncio
        import re

        from hecate.engine.eventstore import Event, EventType

        pii_types: dict[str, int] = {}
        for pii_type, pattern in pii_anonymizer.PATTERNS.items():
            matches = re.findall(pattern, original_result)
            if matches:
                pii_types[pii_type] = len(matches)

        if not pii_types:
            return

        event = Event(
            session_id=self._session_id,
            superstep=self._superstep,
            event_type=EventType.PII_DETECTED,
            payload={
                "source": "tool_result",
                "tool_name": tool_name,
                "pii_types": pii_types,
                "placeholder_count": sum(pii_types.values()),
            },
        )
        try:
            asyncio.get_event_loop().create_task(self._event_store.append(event))
        except RuntimeError:
            logger.warning("Cannot emit PII audit event: no running event loop")
