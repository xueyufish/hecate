"""Output security hook — toxicity detection, PII deanonymization, DLP egress scan."""

from __future__ import annotations

import hashlib
import logging
import uuid
from typing import Any

from hecate.runtime.guardrail import GuardrailAction, GuardrailResult, PostLLMHook
from hecate.services.security.finding_writer import FindingWriterAdapter, SecurityFindingWriter
from hecate.services.security.llm_guard import llm_guard_scanner

logger = logging.getLogger(__name__)


def hashlib_blake2b_16(text: str) -> int:
    """Stable 16-byte hash of ``text`` for short cache-key discriminators."""
    return int.from_bytes(hashlib.blake2b(text.encode("utf-8"), digest_size=16).digest(), "big")


class OutputSecurityHook(PostLLMHook):
    """Post-LLM hook for toxicity, PII deanonymization, and DLP egress scan.

    Per design.md §D2 the DLP scan runs AFTER deanonymization
    (boundary 2: egress policy). Deanonymization replaces placeholders
    with real values; the DLP scanner then sees real values and can
    apply the org's egress policy. Toxicity detection stays at
    boundary 1 of the post-LLM flow.
    """

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
        dlp_scanner: Any = None,
        security_finding_writer: Any = None,
        guardrail_config: dict | None = None,
    ) -> None:
        self._enabled = enabled
        self._toxicity_threshold = toxicity_threshold
        self._deanonymize = deanonymize
        self._audit_pii_events = audit_pii_events
        self._event_store = event_store
        self._session_id = session_id
        self._superstep = superstep
        self._dlp_scanner = dlp_scanner
        self._security_finding_writer = security_finding_writer
        self._guardrail_config = guardrail_config or {}
        self._injection_enabled = bool((self._guardrail_config.get("injection_detection") or {}).get("enabled", True))
        self._prompt_leakage_enabled = bool((self._guardrail_config.get("prompt_leakage") or {}).get("enabled", True))
        self._injection_cfg: dict = self._guardrail_config.get("injection_detection") or {}
        self._prompt_leakage_cfg: dict = self._guardrail_config.get("prompt_leakage") or {}
        self._fingerprint_cache: dict[tuple[Any, ...], set[int]] = {}

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

        deanonymized = self._deanonymize_response(response)

        if self._dlp_scanner is not None:
            dlp_result = await self._apply_dlp(deanonymized, response)
            if dlp_result is not None:
                return dlp_result

        if self._injection_enabled:
            inj_result = await self._check_injection(content, deanonymized)
            if inj_result is not None:
                return inj_result

        if self._prompt_leakage_enabled:
            leak_result = await self._check_prompt_leakage(content, deanonymized, messages)
            if leak_result is not None:
                return leak_result

        return deanonymized

    async def _apply_dlp(
        self,
        deanonymized: GuardrailResult,
        response: dict,
    ) -> GuardrailResult | None:
        """Run the DLP scanner on the deanonymized response.

        Returns a non-None result when the scanner wants to override
        ``deanonymized`` (BLOCK or MASK with new placeholders).
        """
        text = (
            deanonymized.modified_data.get("response", {}).get("content", "")
            if deanonymized.modified_data
            else response.get("content", "")
        )
        if not isinstance(text, str) or not text:
            return None

        result = self._dlp_scanner.scan(text, direction="llm_output")
        await self._write_audit_records(result, response)

        if result.action.value == "block":
            return GuardrailResult(
                action=GuardrailAction.BLOCK,
                reason=(f"DLP blocked output: {', '.join({f.entity_type for f in result.findings}) or 'secrets'}"),
            )

        if result.action.value == "mask" and result.text is not None:
            modified = dict(response)
            modified["content"] = result.text
            return GuardrailResult(
                action=GuardrailAction.SANITIZE,
                reason="DLP masked output",
                modified_data={"response": modified},
            )

        return None

    async def _check_injection(
        self,
        content: str,
        deanonymized: GuardrailResult,
    ) -> GuardrailResult | None:
        """Run 9.1a injection-type detection on the LLM response content."""
        from hecate.services.security.dlp.result import DLPAction
        from hecate.services.security.output.injection_detection.scanner import scan_with_guardrail_cfg

        try:
            findings, overall_action = scan_with_guardrail_cfg(content, guardrail_cfg=self._guardrail_config)
        except Exception as exc:
            logger.warning("injection_detection scan failed: %s", exc.__class__.__name__)
            return None

        if not findings:
            return None

        for finding in findings:
            await self._write_single_finding(
                entity_type=finding.entity_type,
                value=finding.value,
                start=finding.start,
                end=finding.end,
                score=finding.score,
                recognizer=finding.recognizer,
                action=overall_action.value,
                rule_name=f"output.injection_detection.{finding.recognizer}",
                context={
                    "source": "injection_detection",
                    "recognizer": finding.recognizer,
                    "entity_type": finding.entity_type,
                },
            )

        await self._emit_event_async(
            event_type="injection_detected",
            payload={
                "recognizers": sorted({f.recognizer for f in findings}),
                "entity_types": sorted({f.entity_type for f in findings}),
                "finding_count": len(findings),
                "action": overall_action.value,
            },
        )

        if overall_action == DLPAction.BLOCK:
            return GuardrailResult(
                action=GuardrailAction.BLOCK,
                reason=("Injection type detected: " + ", ".join(sorted({f.recognizer for f in findings}))),
            )
        if overall_action == DLPAction.MASK or overall_action == DLPAction.SANITIZE:
            return GuardrailResult(
                action=GuardrailAction.SANITIZE,
                reason="Injection pattern: output sanitized",
                modified_data={
                    "response": deanonymized.modified_data.get("response", {}) if deanonymized.modified_data else {}
                },
            )
        return None

    async def _check_prompt_leakage(
        self,
        content: str,
        deanonymized: GuardrailResult,
        messages: list[dict],
    ) -> GuardrailResult | None:
        """Run 9.2 system prompt leakage detection on the LLM response content."""
        from hecate.services.security.output.prompt_leakage.redactor import redact
        from hecate.services.security.output.prompt_leakage.scanner import (
            resolve_config as resolve_leakage_config,
        )
        from hecate.services.security.output.prompt_leakage.scanner import (
            scan as leakage_scan,
        )

        cfg = resolve_leakage_config(self._guardrail_config)
        if not cfg.enabled:
            return None

        system_prompt = self._extract_system_prompt(messages)
        if not system_prompt:
            return None

        try:
            cache_key = self._fingerprint_cache_key(system_prompt)
            baseline_fps = self._fingerprint_cache.get(cache_key)
            if baseline_fps is None:
                from hecate.services.security.output.prompt_leakage.fingerprint import fingerprint

                baseline_fps = fingerprint(system_prompt, n=cfg.ngram_size)
                self._fingerprint_cache[cache_key] = baseline_fps

            finding = leakage_scan(content, baseline_fingerprint=baseline_fps, config=cfg)
        except Exception as exc:
            logger.warning("prompt_leakage scan failed: %s", exc.__class__.__name__)
            await self._emit_event_async(
                event_type="error",
                payload={"source": "prompt_leakage", "reason": "fingerprint_compute_failed"},
            )
            return None

        if finding is None:
            return None

        await self._write_single_finding(
            entity_type=finding.entity_type,
            value=finding.matched_substring,
            start=finding.match_offset_start,
            end=finding.match_offset_end,
            score=finding.overlap_ratio,
            recognizer=finding.recognizer,
            action=finding.action.value,
            severity=finding.severity,
            rule_name=f"output.prompt_leakage.{finding.category}",
            context={
                "source": "prompt_leakage",
                "category": finding.category,
                "overlap_ratio": finding.overlap_ratio,
            },
        )

        await self._emit_event_async(
            event_type="prompt_leakage_detected",
            payload={
                "severity": finding.severity,
                "overlap_ratio": finding.overlap_ratio,
                "matched_categories": [finding.category],
                "action": finding.action.value,
                "rule_name": f"output.prompt_leakage.{finding.category}",
            },
        )

        if finding.action.value == "block":
            return GuardrailResult(
                action=GuardrailAction.BLOCK,
                reason=(f"System prompt leakage detected ({finding.category}, overlap={finding.overlap_ratio:.0%})"),
            )
        if finding.action.value == "sanitize":
            redacted = redact(content, baseline_fingerprint=baseline_fps, n=cfg.ngram_size)
            modified = dict(deanonymized.modified_data.get("response", {}) if deanonymized.modified_data else {})
            modified["content"] = redacted
            return GuardrailResult(
                action=GuardrailAction.SANITIZE,
                reason="System prompt leakage redacted",
                modified_data={"response": modified},
            )
        return None

    @staticmethod
    def _extract_system_prompt(messages: list[dict]) -> str:
        """Extract system prompt content from the messages list.

        Uses ``messages[0]["content"]`` when ``messages[0]["role"] == "system"``.
        Falls back to concatenating all system-role messages when the first
        message is not system (degraded mode — see prompt-leakage spec).
        """
        if not isinstance(messages, list) or not messages:
            return ""
        first = messages[0]
        if isinstance(first, dict) and first.get("role") == "system":
            content = first.get("content", "")
            return content if isinstance(content, str) else ""
        system_parts: list[str] = []
        for m in messages:
            if isinstance(m, dict) and m.get("role") == "system":
                content = m.get("content", "")
                if isinstance(content, str):
                    system_parts.append(content)
        return "\n".join(system_parts)

    def _fingerprint_cache_key(self, system_prompt: str) -> tuple[Any, ...]:
        """Cache key that auto-invalidates when the prompt or session context changes."""
        return (
            self._session_id,
            self._superstep,
            len(system_prompt),
            hashlib_blake2b_16(system_prompt),
        )

    async def _emit_event_async(self, *, event_type: str, payload: dict[str, Any]) -> None:
        """Append an event to the EventStore if one is configured."""
        if self._event_store is None or self._session_id is None:
            return
        try:
            from hecate.runtime.eventstore import Event, EventType

            try:
                etype = EventType(event_type)
            except ValueError:
                etype = EventType.CUSTOM
            event = Event(
                session_id=self._session_id,
                superstep=self._superstep,
                event_type=etype,
                payload=payload,
            )
            await self._event_store.append(event)
        except Exception as exc:
            logger.warning("event emission failed (%s): %s", event_type, exc.__class__.__name__)

    async def _write_audit_records(self, result: Any, response: dict) -> None:
        if self._security_finding_writer is None or not result.findings:
            return
        for finding in result.findings:
            await self._write_single_finding(
                entity_type=finding.entity_type,
                value=finding.value,
                start=finding.start,
                end=finding.end,
                score=finding.score,
                recognizer=finding.recognizer,
                action=result.action.value,
                context={"source": "output", "subsource": "dlp"},
            )

    async def _write_single_finding(
        self,
        *,
        entity_type: str,
        value: str,
        start: int,
        end: int,
        score: float,
        recognizer: str,
        action: str,
        severity: str = "high",
        rule_name: str | None = None,
        source: str = "output",
        context: dict[str, Any] | None = None,
    ) -> None:
        """Dispatch a single finding to the configured writer.

        Accepts a ``SecurityFindingWriter`` / ``FindingWriterAdapter``
        instance (structured) or a legacy callable (backward compat).
        Failures are caught and logged — best-effort audit.
        """
        if self._security_finding_writer is None:
            return
        try:
            if isinstance(self._security_finding_writer, SecurityFindingWriter | FindingWriterAdapter):
                await self._security_finding_writer.write(
                    entity_type=entity_type,
                    value=value,
                    start=start,
                    end=end,
                    score=score,
                    recognizer=recognizer,
                    action=action,
                    severity=severity,
                    rule_name=rule_name,
                    source=source,
                    context=context,
                )
                return
            self._security_finding_writer(
                entity_type=entity_type,
                value=value,
                start=start,
                end=end,
                score=score,
                recognizer=recognizer,
                action=action,
                context=context if context is not None else {"source": "output"},
            )
        except Exception as exc:
            logger.warning(
                "Finding write failed (entity=%s, recognizer=%s): %s",
                entity_type,
                recognizer,
                exc.__class__.__name__,
            )

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

        from hecate.runtime.eventstore import Event, EventType

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
