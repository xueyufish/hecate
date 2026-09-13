"""Routing evaluation for CONDITION nodes with advanced routing modes.

Provides routing dispatch for condition (expression), intent (pattern + LLM
legacy form, or intent-package delegation), and dynamic (LLM selects
speaker) routing modes. Used by condition node workers to determine the
``_route`` value written to channel state.

The INTENT mode supports two forms (6.23):

- **legacy** — ``intent_patterns`` regex list with an optional
  ``routing_prompt`` LLM fallback; behavior is byte-identical to the
  pre-6.23 contract.
- **package-backed** — ``intent_package`` (id + optional published-version
  pin) with ``category_targets``; classification delegates to the intent
  recognition engine (published evidence, decision cache, layered result),
  and evidence failure degrades to LLM classification over the
  ``category_targets`` keys.
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any

from hecate.runtime.types import IntentPattern, RoutingMode

logger = logging.getLogger(__name__)


async def _collect_llm(port: Any, prompt: str) -> str:
    """Collect one LLM response over the real RuntimePort contract.

    ``RuntimePort.llm_invoke(messages, config)`` is a token stream; the
    routing prompts travel as a single user message. (The pre-6.23 code
    called ``llm_invoke(prompt=...)``, which no RuntimePort implementation
    satisfies — surfaced when the package-backed path started sharing
    these tests' stubs with the real port contract.)
    """
    chunks: list[str] = []
    async for token in port.llm_invoke(
        messages=[{"role": "user", "content": prompt}],
        config={},
    ):
        chunks.append(token)
    return "".join(chunks)


# Shared recognition engine (its decision cache is process-local by
# design); created lazily so import never constructs state.
_shared_engine: Any = None


def _get_engine(engine_port: Any) -> Any:
    """The process-wide recognition engine, bound to the given LLM port."""
    global _shared_engine
    if _shared_engine is None:
        from hecate.runtime.intent.engine import IntentRecognitionEngine

        _shared_engine = IntentRecognitionEngine(port=engine_port)
    return _shared_engine


async def evaluate_routing(
    routing_mode: str,
    routing_config: dict[str, Any],
    input_value: Any,
    channel_snapshot: dict[str, Any],
    engine_port: Any | None = None,
    last_speaker: str | None = None,
    evidence_port: Any | None = None,
    event_store: Any | None = None,
    session_id: Any | None = None,
) -> str:
    """Dispatch routing evaluation based on routing_mode.

    Args:
        routing_mode: One of "condition", "intent", "dynamic".
        routing_config: Mode-specific configuration dict.
        input_value: The value to evaluate (typically from channel state).
        channel_snapshot: Current channel state for context.
        engine_port: Optional RuntimePort for LLM calls.
        last_speaker: Node ID of the last executing agent (for dynamic mode).
        evidence_port: Optional published-intent-evidence provider
            (6.23); required only for package-backed intent routing.
        event_store: Optional EventStore for recognition events (6.23).
        session_id: Optional session id for event correlation.

    Returns:
        A string route key matching an edge target dict key.
    """
    if not routing_mode or routing_mode == RoutingMode.CONDITION:
        return str(input_value) if input_value is not None else "true"
    if routing_mode == RoutingMode.INTENT:
        return await _evaluate_intent(routing_config, input_value, engine_port, evidence_port, event_store, session_id)
    if routing_mode == RoutingMode.DYNAMIC:
        return await _evaluate_dynamic(routing_config, input_value, channel_snapshot, engine_port, last_speaker)
    return str(input_value) if input_value is not None else "true"


async def _evaluate_intent(
    config: dict[str, Any],
    input_value: Any,
    engine_port: Any | None = None,
    evidence_port: Any | None = None,
    event_store: Any | None = None,
    session_id: Any | None = None,
) -> str:
    """Evaluate intent routing.

    Package-backed form first (``intent_package`` present); otherwise the
    legacy form: regex patterns first, LLM fallback second.
    """
    intent_package = config.get("intent_package")
    if intent_package:
        return await _evaluate_intent_package(config, input_value, engine_port, evidence_port, event_store, session_id)

    patterns = [IntentPattern(**p) for p in config.get("intent_patterns", [])]
    input_str = str(input_value)
    for pattern in patterns:
        if re.search(pattern.pattern, input_str, re.IGNORECASE):
            return pattern.target

    routing_prompt = config.get("routing_prompt")
    if routing_prompt and engine_port:
        response = await _collect_llm(engine_port, f"{routing_prompt}\n\nInput: {input_str}")
        return response.strip()

    return "default"


async def _evaluate_intent_package(
    config: dict[str, Any],
    input_value: Any,
    engine_port: Any | None = None,
    evidence_port: Any | None = None,
    event_store: Any | None = None,
    session_id: Any | None = None,
) -> str:
    """Package-backed intent routing: delegate to the recognition engine.

    The route key is the mapped target of the recognized category, or
    ``"default"`` when nothing matches. Evidence resolution failure
    degrades to LLM classification over the ``category_targets`` keys.
    """
    from hecate.runtime.intent.engine import resolve_evidence
    from hecate.runtime.intent.events import emit_intent_recognized
    from hecate.runtime.intent.types import IntentRequest

    input_str = str(input_value)
    category_targets: dict[str, str] = config.get("category_targets") or {}
    package_ref = config.get("intent_package") or {}

    try:
        package_id = uuid.UUID(str(package_ref.get("package_id")))
    except (ValueError, AttributeError):
        logger.warning("Intent routing has an invalid package reference; falling back to default")
        return "default"
    version_pin = package_ref.get("version_id")
    version_id = uuid.UUID(str(version_pin)) if version_pin else None

    evidence = None
    if evidence_port is not None:
        evidence = await resolve_evidence(evidence_port, package_id, version_id)

    engine = _get_engine(engine_port)
    result = await engine.recognize(
        IntentRequest(
            utterance=input_str,
            evidence=evidence,
            fallback_labels=tuple(category_targets.keys()),
        )
    )
    await emit_intent_recognized(
        event_store,
        session_id,
        result,
        superstep=0,
    )

    label = result.atomic.label
    if label and label in category_targets:
        return category_targets[label]
    return "default"


async def _evaluate_dynamic(
    config: dict[str, Any],
    input_value: Any,
    channel_snapshot: dict[str, Any],
    engine_port: Any | None = None,
    last_speaker: str | None = None,
) -> str:
    """Evaluate dynamic routing: LLM selects next speaker from candidates."""
    all_candidates = list(config.get("candidate_agents", []))
    allow_repeated = config.get("allow_repeated_speaker", False)

    candidates = all_candidates
    if not allow_repeated and last_speaker and last_speaker in candidates:
        candidates = [c for c in candidates if c != last_speaker]

    if not candidates:
        return "default"

    if engine_port:
        routing_prompt = config.get("routing_prompt", "Select the best agent to respond")
        response = await _collect_llm(
            engine_port,
            f"{routing_prompt}\n\nCandidates: {', '.join(candidates)}\n\nContext: {input_value}",
        )
        selected = response.strip()
        if selected in all_candidates:
            return selected
        logger.warning("Dynamic routing LLM returned invalid candidate '%s', falling back to default", selected)

    return "default"
