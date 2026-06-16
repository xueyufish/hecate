"""Input security hook — prompt injection detection, PII anonymization, secrets detection."""

from __future__ import annotations

import copy
import logging
import uuid
from typing import Any

from hecate.engine.guardrail import GuardrailAction, GuardrailResult, PreLLMHook
from hecate.services.security.anonymizer import pii_anonymizer
from hecate.services.security.llm_guard import llm_guard_scanner

logger = logging.getLogger(__name__)

_DEFAULT_PII_ENTITIES = frozenset({"email", "phone", "credit_card", "ssn", "ip_address"})


class InputSecurityHook(PreLLMHook):
    """Pre-LLM hook that scans input for injection, PII, and secrets."""

    def __init__(
        self,
        *,
        enabled: bool = True,
        prompt_injection_threshold: float = 0.5,
        pii_entities: list[str] | None = None,
        block_on_injection: bool = True,
        audit_pii_events: bool = False,
        event_store: Any = None,
        session_id: uuid.UUID | None = None,
        superstep: int = 0,
    ) -> None:
        self._enabled = enabled
        self._injection_threshold = prompt_injection_threshold
        self._pii_entities = set(pii_entities) if pii_entities else set(_DEFAULT_PII_ENTITIES)
        self._block_on_injection = block_on_injection
        self._audit_pii_events = audit_pii_events
        self._event_store = event_store
        self._session_id = session_id
        self._superstep = superstep

    async def on_pre_llm_call(
        self,
        messages: list[dict],
        model: str,
        tools: list[dict] | None,
    ) -> GuardrailResult:
        if not self._enabled:
            return GuardrailResult(action=GuardrailAction.ALLOW)

        all_text = " ".join(m.get("content", "") for m in messages if isinstance(m.get("content"), str))

        injection_result = await self._check_injection(all_text)
        if injection_result is not None:
            return injection_result

        secrets_result = await self._check_secrets(all_text)
        if secrets_result is not None:
            return secrets_result

        return self._anonymize_pii(messages)

    async def _check_injection(self, text: str) -> GuardrailResult | None:
        scan = await llm_guard_scanner.scan_prompt(text)
        for issue in scan.issues:
            if "PromptInjection" in issue:
                if self._block_on_injection:
                    return GuardrailResult(
                        action=GuardrailAction.BLOCK,
                        reason=f"Prompt injection detected: {issue}",
                    )
                logger.warning("Prompt injection detected but block_on_injection=False: %s", issue)
        return None

    async def _check_secrets(self, text: str) -> GuardrailResult | None:
        scan = await llm_guard_scanner.scan_prompt(text)
        for issue in scan.issues:
            if "Secrets" in issue:
                return GuardrailResult(
                    action=GuardrailAction.BLOCK,
                    reason=f"Secrets detected in input: {issue}",
                )
        return None

    def _anonymize_pii(self, messages: list[dict]) -> GuardrailResult:
        filtered_patterns = {k: v for k, v in pii_anonymizer.PATTERNS.items() if k in self._pii_entities}
        has_pii = False
        modified = copy.deepcopy(messages)
        all_mappings: dict[str, str] = {}

        for msg in modified:
            content = msg.get("content")
            if not isinstance(content, str):
                continue
            for pii_type, pattern in filtered_patterns.items():
                import re

                counter = len(all_mappings) + 1
                matches = list(re.finditer(pattern, content))
                if not matches:
                    continue
                for match in matches:
                    original = match.group()
                    if original not in all_mappings:
                        placeholder = f"[{pii_type.upper()}_{counter}]"
                        all_mappings[original] = placeholder
                        counter += 1
                    content = content.replace(original, all_mappings[original])
                msg["content"] = content
                has_pii = True

        if not has_pii:
            return GuardrailResult(action=GuardrailAction.ALLOW)

        if self._audit_pii_events:
            self._emit_pii_audit(all_mappings)

        return GuardrailResult(
            action=GuardrailAction.SANITIZE,
            reason="PII anonymized",
            modified_data={
                "messages": modified,
                "_pii_mappings": all_mappings,
            },
        )

    def _emit_pii_audit(self, mappings: dict[str, str]) -> None:
        """Emit PII_DETECTED audit event with type counts but NOT original values."""
        if self._event_store is None or self._session_id is None:
            logger.warning("PII audit enabled but event_store or session_id not configured")
            return

        from hecate.engine.eventstore import Event, EventType

        pii_types: dict[str, int] = {}
        for placeholder in mappings.values():
            ptype = placeholder.strip("[]").rsplit("_", 1)[0].lower()
            pii_types[ptype] = pii_types.get(ptype, 0) + 1

        import asyncio

        event = Event(
            session_id=self._session_id,
            superstep=self._superstep,
            event_type=EventType.PII_DETECTED,
            payload={
                "source": "input",
                "pii_types": pii_types,
                "placeholder_count": len(mappings),
            },
        )
        try:
            asyncio.get_event_loop().create_task(self._event_store.append(event))
        except RuntimeError:
            logger.warning("Cannot emit PII audit event: no running event loop")
