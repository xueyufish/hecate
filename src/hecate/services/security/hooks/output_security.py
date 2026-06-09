"""Output security hook — toxicity detection and PII deanonymization."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from hecate.engine.guardrail import GuardrailAction, GuardrailResult, PostLLMHook
from hecate.services.security.llm_guard import llm_guard_scanner

logger = logging.getLogger(__name__)


class OutputSecurityHook(PostLLMHook):
    """Post-LLM hook that detects toxicity and deanonymizes PII placeholders."""

    def __init__(
        self,
        *,
        enabled: bool = True,
        toxicity_threshold: float = 0.7,
        deanonymize: bool = True,
        audit_pii_events: bool = False,
        event_store: Any = None,
        session_id: uuid.UUID | None = None,
        superstep: int = 0,
    ) -> None:
        self._enabled = enabled
        self._toxicity_threshold = toxicity_threshold
        self._deanonymize = deanonymize
        self._audit_pii_events = audit_pii_events
        self._event_store = event_store
        self._session_id = session_id
        self._superstep = superstep

    async def on_post_llm_call(
        self,
        response: dict,
        messages: list[dict],
    ) -> GuardrailResult:
        if not self._enabled:
            return GuardrailResult(action=GuardrailAction.ALLOW)

        content = response.get("content", "")
        if not isinstance(content, str) or not content:
            return GuardrailResult(action=GuardrailAction.ALLOW)

        toxicity_result = await self._check_toxicity(content)
        if toxicity_result is not None:
            return toxicity_result

        return self._deanonymize_response(response)

    async def _check_toxicity(self, text: str) -> GuardrailResult | None:
        scan = await llm_guard_scanner.scan_output(text)
        for issue in scan.issues:
            if "Toxicity" in issue:
                return GuardrailResult(
                    action=GuardrailAction.BLOCK,
                    reason=f"Toxic output detected: {issue}",
                )
        return None

    def _deanonymize_response(self, response: dict) -> GuardrailResult:
        if not self._deanonymize:
            return GuardrailResult(action=GuardrailAction.ALLOW)

        content = response.get("content", "")
        if not isinstance(content, str) or "[" not in content:
            return GuardrailResult(action=GuardrailAction.ALLOW)

        if self._audit_pii_events:
            self._emit_pii_audit(content)

        return GuardrailResult(
            action=GuardrailAction.SANITIZE,
            reason="PII deanonymized",
            modified_data={"response": response},
        )

    @staticmethod
    def deanonymize_text(text: str, mappings: dict[str, str]) -> str:
        """Replace PII placeholders with original values from mappings."""
        for original, placeholder in mappings.items():
            text = text.replace(placeholder, original)
        return text

    def _emit_pii_audit(self, content: str) -> None:
        """Emit PII_DETECTED audit event for output deanonymization."""
        if self._event_store is None or self._session_id is None:
            logger.warning("PII audit enabled but event_store or session_id not configured")
            return

        import asyncio
        import re

        from hecate.engine.eventstore import Event, EventType

        placeholders = re.findall(r"\[[A-Z]+_\d+\]", content)
        if not placeholders:
            return

        pii_types: dict[str, int] = {}
        for ph in placeholders:
            ptype = ph.strip("[]").rsplit("_", 1)[0].lower()
            pii_types[ptype] = pii_types.get(ptype, 0) + 1

        event = Event(
            session_id=self._session_id,
            superstep=self._superstep,
            event_type=EventType.PII_DETECTED,
            payload={
                "source": "output",
                "pii_types": pii_types,
                "placeholder_count": len(placeholders),
            },
        )
        try:
            asyncio.get_event_loop().create_task(self._event_store.append(event))
        except RuntimeError:
            logger.warning("Cannot emit PII audit event: no running event loop")
