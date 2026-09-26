"""Tool side-effect classification and retry-recovery rules (tool-recovery).

Classifies each tool by what a retry would do to the world, so crash
recovery can decide between automatic retry, confirmed-safe retry, and
human review. The registry is code, not database: built-in tools get a
static mapping, everything else defaults to ``unknown`` — the most
conservative treatment (never auto-retry).

Result states live on the TOOL_RESULT receipt (see ToolWorker):
``succeeded`` / ``failed`` (definitively did not take effect) /
``unknown`` (outcome indeterminate — timeout, lost connection).
"""

from __future__ import annotations

from enum import StrEnum


class SideEffectClass(StrEnum):
    """What a retry of this tool would do to external state."""

    READONLY = "readonly"
    IDEMPOTENT_WRITE = "idempotent_write"
    NON_IDEMPOTENT_WRITE = "non_idempotent_write"
    EXTERNAL_SIDE_EFFECT = "external_side_effect"
    UNKNOWN = "unknown"


# Receipt states carried on TOOL_RESULT events.
RECEIPT_SUCCEEDED = "succeeded"
RECEIPT_FAILED = "failed"
RECEIPT_UNKNOWN = "unknown"

# Built-in tool mapping. Anything absent falls back to UNKNOWN.
_BUILTIN_CLASSIFICATIONS: dict[str, SideEffectClass] = {
    # Pure reads
    "web_search": SideEffectClass.READONLY,
    "read_file": SideEffectClass.READONLY,
    "list_files": SideEffectClass.READONLY,
    "recall": SideEffectClass.READONLY,
    "load_skill": SideEffectClass.READONLY,
    "memory_search": SideEffectClass.READONLY,
    "conversation_search": SideEffectClass.READONLY,
    "reflection_search": SideEffectClass.READONLY,
    "work_context_query": SideEffectClass.READONLY,
    "browser_extract": SideEffectClass.READONLY,
    "browser_screenshot": SideEffectClass.READONLY,
    # Writes that repeat safely at the same target
    "write_file": SideEffectClass.IDEMPOTENT_WRITE,
    "memory_replace": SideEffectClass.IDEMPOTENT_WRITE,
    "memory_update": SideEffectClass.IDEMPOTENT_WRITE,
    "memory_rethink": SideEffectClass.IDEMPOTENT_WRITE,
    "memory_forget": SideEffectClass.IDEMPOTENT_WRITE,
    # Writes that may duplicate on retry
    "memory_add": SideEffectClass.NON_IDEMPOTENT_WRITE,
    "memory_insert": SideEffectClass.NON_IDEMPOTENT_WRITE,
    "execute_code": SideEffectClass.NON_IDEMPOTENT_WRITE,
    # Interactions with external systems a retry would visibly repeat
    "browser_navigate": SideEffectClass.EXTERNAL_SIDE_EFFECT,
    "browser_click": SideEffectClass.EXTERNAL_SIDE_EFFECT,
    "browser_type": SideEffectClass.EXTERNAL_SIDE_EFFECT,
    "browser_fill_form": SideEffectClass.EXTERNAL_SIDE_EFFECT,
}


def classify(tool_name: str) -> SideEffectClass:
    """Classify a tool by retry safety; unregistered tools are UNKNOWN."""
    return _BUILTIN_CLASSIFICATIONS.get(tool_name, SideEffectClass.UNKNOWN)


def should_auto_retry(classification: SideEffectClass, receipt_status: str | None) -> bool:
    """Whether a recovery flow may automatically retry this execution.

    ``receipt_status`` is the TOOL_RESULT receipt state for the attempt in
    question (None = no receipt found, e.g. log loss). Only provably-safe
    combinations auto-retry; anything indeterminate goes to human review.
    """
    if receipt_status == RECEIPT_SUCCEEDED:
        return False  # already took effect
    if receipt_status == RECEIPT_UNKNOWN:
        return False  # indeterminate — human review
    if receipt_status == RECEIPT_FAILED:
        # Definitively did not take effect — safe to redo any class whose
        # semantics we know; unknown stays with human review.
        return classification is not SideEffectClass.UNKNOWN
    # No receipt at all (log gap / worker died pre-receipt): retry only
    # what is repeat-safe by definition; everything else goes to review.
    return classification in (SideEffectClass.READONLY, SideEffectClass.IDEMPOTENT_WRITE)
