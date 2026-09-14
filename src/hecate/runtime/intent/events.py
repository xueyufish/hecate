"""Event emission for intent recognition and controller routing (6.23/2.6a).

Both events are additive EventTypes (ADR-030 §1 contract) and carry
decision metadata only — never the few-shot evidence payloads, and never
the raw utterance (the utterance fingerprint suffices for cache-hit
correlation; full input already lives in the session trace).
"""

from __future__ import annotations

import logging
from typing import Any

from hecate.runtime.eventstore import Event, EventStore, EventType
from hecate.runtime.intent.cache import fingerprint, normalize_utterance
from hecate.runtime.intent.types import IntentRecognitionResult

logger = logging.getLogger(__name__)


async def _append(
    event_store: EventStore | None,
    session_id: Any,
    superstep: int,
    event_type: EventType,
    node_id: str | None,
    payload: dict,
    trace_id: Any = None,
) -> None:
    """Best-effort append — recognition must not fail on event errors."""
    if event_store is None:
        return
    try:
        await event_store.append(
            Event(
                session_id=session_id,
                superstep=superstep,
                event_type=event_type,
                node_id=node_id,
                payload=payload,
                trace_id=trace_id,
            )
        )
    except Exception:  # pragma: no cover — defensive
        logger.exception("Failed to append %s event", event_type.value)


async def emit_intent_recognized(
    event_store: EventStore | None,
    session_id: Any,
    result: IntentRecognitionResult,
    node_id: str | None = None,
    superstep: int = 0,
    trace_id: Any = None,
) -> None:
    """Append ``INTENT_RECOGNIZED`` for one recognition."""
    await _append(
        event_store,
        session_id,
        superstep,
        EventType.INTENT_RECOGNIZED,
        node_id,
        {
            "utterance_fingerprint": fingerprint(normalize_utterance(result.utterance)),
            "atomic": {
                "label": result.atomic.label,
                "confidence": result.atomic.confidence,
                "source": result.atomic.source.value,
            },
            "workflow": {"label": result.workflow.label, "active": result.workflow.active},
            "domain": result.domain,
            "gated": result.gated,
            "cache_hit": result.cache_hit,
            "evidence_ref": result.evidence_ref,
            "evidence_available": result.evidence_available,
            "goal_shift": result.goal_shift,
            "latency_ms": result.latency_ms,
            "session_intent_update": result.session_intent_update,
        },
        trace_id=trace_id,
    )


async def emit_controller_routed(
    event_store: EventStore | None,
    session_id: Any,
    *,
    target: str,
    level: str,
    label: str | None,
    confidence: float,
    source: str,
    cache_hit: bool,
    goal: str | None,
    workflow_label: str | None,
    node_id: str | None = None,
    superstep: int = 0,
    trace_id: Any = None,
) -> None:
    """Append ``CONTROLLER_ROUTED`` for one routing decision."""
    await _append(
        event_store,
        session_id,
        superstep,
        EventType.CONTROLLER_ROUTED,
        node_id,
        {
            "target": target,
            "level": level,
            "label": label,
            "confidence": confidence,
            "source": source,
            "cache_hit": cache_hit,
            "goal": goal,
            "workflow_label": workflow_label,
        },
        trace_id=trace_id,
    )


__all__ = ["emit_controller_routed", "emit_intent_recognized"]
