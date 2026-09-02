"""Tool result security hook — PII detection and masking in tool execution results."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from hecate.runtime.guardrail import GuardrailAction, GuardrailResult, PostToolHook
from hecate.services.security.anonymizer import pii_anonymizer

logger = logging.getLogger(__name__)


class ToolResultSecurityHook(PostToolHook):
    """Post-tool hook that masks PII in tool results.

    When a DLPScanner is wired in, PII detection delegates to
    ``dlp_scanner.scan(result_str, direction="tool_output")`` so the org's
    DLP policy (including custom regex/dictionary recognizers) applies.
    Without a DLP scanner the hook falls back to the built-in
    :class:`PIIAnonymizer` for backward compatibility.

    The ``pii_storage_mode`` config controls how PII is stored after
    masking: ``"mask_only"`` (default, replaces with placeholders) or
    ``"mask_and_encrypt"`` (replaces with encrypted tokens that can be
    round-tripped by the deanonymizer). Only honored when a DLP scanner
    is configured; the PIIAnonymizer fallback always uses ``mask_only``.
    """

    def __init__(
        self,
        *,
        mask_tool_results: bool = True,
        audit_pii_events: bool = False,
        event_store: Any = None,
        session_id: uuid.UUID | None = None,
        superstep: int = 0,
        dlp_scanner: Any = None,
        pii_storage_mode: str = "mask_only",
        encryption_key: bytes | None = None,
    ) -> None:
        self._mask_tool_results = mask_tool_results
        self._audit_pii_events = audit_pii_events
        self._event_store = event_store
        self._session_id = session_id
        self._superstep = superstep
        self._dlp_scanner = dlp_scanner
        if pii_storage_mode not in {"mask_only", "mask_and_encrypt"}:
            raise ValueError(
                f"Invalid pii_storage_mode: {pii_storage_mode!r}; expected 'mask_only' or 'mask_and_encrypt'"
            )
        self._pii_storage_mode = pii_storage_mode
        self._encryption_key = encryption_key

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

        if self._dlp_scanner is not None:
            return await self._handle_via_dlp(name, result_str)
        return self._handle_via_anonymizer(name, result_str)

    async def _handle_via_dlp(self, name: str, result_str: str) -> GuardrailResult:
        result = self._dlp_scanner.scan(result_str, direction="tool_output")
        if result.action.value in ("allow", "audit"):
            return GuardrailResult(action=GuardrailAction.ALLOW)

        if result.action.value == "block":
            if self._audit_pii_events:
                self._emit_pii_audit(name, result_str)
            entity_types = ", ".join({f.entity_type for f in result.findings}) or "secrets"
            return GuardrailResult(
                action=GuardrailAction.BLOCK,
                reason=f"DLP blocked tool result: {entity_types} detected",
            )

        masked = result.text if result.text is not None else result_str
        if self._pii_storage_mode == "mask_and_encrypt":
            masked = self._encrypt(masked)

        if self._audit_pii_events:
            self._emit_pii_audit(name, result_str)

        return GuardrailResult(
            action=GuardrailAction.SANITIZE,
            reason="DLP masked PII in tool result",
            modified_data={"result": masked, "tool": name},
        )

    def _handle_via_anonymizer(self, name: str, result_str: str) -> GuardrailResult:
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

    def _encrypt(self, text: str) -> str:
        if self._encryption_key is None:
            return text
        try:
            import base64

            from cryptography.fernet import Fernet

            fernet_key = base64.urlsafe_b64encode(self._encryption_key[:32].ljust(32, b"\0"))
            return Fernet(fernet_key).encrypt(text.encode("utf-8")).decode("utf-8")
        except Exception:
            logger.exception("Failed to encrypt masked text; falling back to plaintext")
            return text

    def _emit_pii_audit(self, tool_name: str, original_result: str) -> None:
        """Emit PII_DETECTED audit event for tool result masking."""
        if self._event_store is None or self._session_id is None:
            logger.warning("PII audit enabled but event_store or session_id not configured")
            return

        import asyncio
        import re

        from hecate.runtime.eventstore import Event, EventType

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
