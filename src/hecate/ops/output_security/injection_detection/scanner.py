"""Injection detection scanner facade (9.1a).

Applies the built-in recognizer registry plus user-supplied custom patterns
to the LLM response content, returning the merged finding list and the
most-restrictive action per the ``DLPAction`` ordering reused from DLP.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from hecate.ops.dlp.result import DLPAction
from hecate.ops.output_security.injection_detection.recognizers import (
    BUILTIN_RECOGNIZERS,
)
from hecate.ops.output_security.injection_detection.recognizers.base import (
    InjectionFinding,
    Recognizer,
    make_recognizer_from_custom_pattern,
)

logger = logging.getLogger(__name__)

_ALLOWED_ACTIONS = {"audit", "block", "mask", "sanitize"}
_ACTION_TO_DLP: dict[str, DLPAction] = {
    "audit": DLPAction.AUDIT,
    "block": DLPAction.BLOCK,
    "mask": DLPAction.MASK,
    "sanitize": DLPAction.SANITIZE,
}


@dataclass(frozen=True)
class InjectionConfig:
    """Resolved configuration for the injection detection scanner."""

    enabled: bool
    per_type_actions: dict[str, DLPAction]
    custom_recognizers: tuple[Recognizer, ...]
    pattern_timeout_ms: int


def _build_custom_recognizers(custom_patterns: list[dict] | None) -> tuple[Recognizer, ...]:
    if not custom_patterns:
        return ()
    recognizers: list[Recognizer] = []
    for idx, entry in enumerate(custom_patterns):
        pattern = entry.get("pattern")
        entity_type = entry.get("entity_type")
        severity = entry.get("severity", "high")
        recognizer_id = entry.get("recognizer", f"custom_{idx}")
        if not isinstance(pattern, str) or not isinstance(entity_type, str):
            logger.warning(
                "injection_detection: skipping malformed custom_pattern entry at index %d"
                " (missing pattern/entity_type)",
                idx,
            )
            continue
        try:
            recognizers.append(
                make_recognizer_from_custom_pattern(
                    entity_type=entity_type,
                    pattern=pattern,
                    severity=severity,
                    recognizer_id=recognizer_id,
                )
            )
        except re.error as exc:
            logger.warning(
                "injection_detection: skipping custom_pattern at index %d (regex error: %s)",
                idx,
                exc,
            )
    return tuple(recognizers)


def resolve_config(guardrail_cfg: dict | None) -> InjectionConfig:
    """Translate the raw ``injection_detection`` config dict to ``InjectionConfig``."""
    cfg = (guardrail_cfg or {}).get("injection_detection") or {}
    enabled = bool(cfg.get("enabled", True))

    types_cfg = cfg.get("types") or {}
    per_type_actions: dict[str, DLPAction] = {}
    for recognizer_cls in BUILTIN_RECOGNIZERS:
        rid = recognizer_cls().id
        action_value = (types_cfg.get(rid) or {}).get("action", "audit")
        if action_value not in _ALLOWED_ACTIONS:
            logger.warning(
                "injection_detection: invalid action %r for %s; defaulting to 'audit'",
                action_value,
                rid,
            )
            action_value = "audit"
        per_type_actions[rid] = _ACTION_TO_DLP[action_value]

    custom_patterns = cfg.get("custom_patterns") or []
    custom_recognizers = _build_custom_recognizers(custom_patterns)

    pattern_timeout_ms = int(cfg.get("pattern_timeout_ms", 50))

    return InjectionConfig(
        enabled=enabled,
        per_type_actions=per_type_actions,
        custom_recognizers=custom_recognizers,
        pattern_timeout_ms=pattern_timeout_ms,
    )


def scan(content: str, *, config: InjectionConfig) -> tuple[list[InjectionFinding], DLPAction]:
    """Run the full recognizer set on ``content``.

    Returns a tuple of (all findings, most-restrictive merged action). When
    ``config.enabled`` is False, returns ``([], DLPAction.ALLOW)`` without
    inspecting content.
    """
    if not config.enabled:
        return [], DLPAction.ALLOW
    if not isinstance(content, str) or not content:
        return [], DLPAction.ALLOW

    findings: list[InjectionFinding] = []
    actions: list[DLPAction] = []
    for recognizer_cls in BUILTIN_RECOGNIZERS:
        recognizer = recognizer_cls()
        rid = recognizer.id
        per_findings = recognizer.detect(content)
        if per_findings:
            findings.extend(per_findings)
            actions.append(config.per_type_actions.get(rid, DLPAction.AUDIT))

    for custom_recognizer in config.custom_recognizers:
        per_findings = custom_recognizer.detect(content)
        if per_findings:
            findings.extend(per_findings)
            rid = custom_recognizer.id
            action_value = config.per_type_actions.get(rid) or DLPAction.AUDIT
            actions.append(action_value)

    overall = DLPAction.overall_action(actions) if actions else DLPAction.ALLOW
    return findings, overall


def scan_with_guardrail_cfg(content: str, *, guardrail_cfg: dict | None) -> tuple[list[InjectionFinding], DLPAction]:
    """Convenience wrapper: resolve config + scan in one call."""
    return scan(content, config=resolve_config(guardrail_cfg))
