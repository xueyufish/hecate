"""Routing evaluation for CONDITION nodes with advanced routing modes.

Provides routing dispatch for condition (expression), intent (pattern + LLM),
and dynamic (LLM selects speaker) routing modes. Used by condition node workers
to determine the _route value written to channel state.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from hecate.engine.types import IntentPattern, RoutingMode

logger = logging.getLogger(__name__)


async def evaluate_routing(
    routing_mode: str,
    routing_config: dict[str, Any],
    input_value: Any,
    channel_snapshot: dict[str, Any],
    engine_port: Any | None = None,
    last_speaker: str | None = None,
) -> str:
    """Dispatch routing evaluation based on routing_mode.

    Args:
        routing_mode: One of "condition", "intent", "dynamic".
        routing_config: Mode-specific configuration dict.
        input_value: The value to evaluate (typically from channel state).
        channel_snapshot: Current channel state for context.
        engine_port: Optional EnginePort for LLM calls.
        last_speaker: Node ID of the last executing agent (for dynamic mode).

    Returns:
        A string route key matching an edge target dict key.
    """
    if not routing_mode or routing_mode == RoutingMode.CONDITION:
        return str(input_value) if input_value is not None else "true"
    if routing_mode == RoutingMode.INTENT:
        return await _evaluate_intent(routing_config, input_value, engine_port)
    if routing_mode == RoutingMode.DYNAMIC:
        return await _evaluate_dynamic(routing_config, input_value, channel_snapshot, engine_port, last_speaker)
    return str(input_value) if input_value is not None else "true"


async def _evaluate_intent(
    config: dict[str, Any],
    input_value: Any,
    engine_port: Any | None = None,
) -> str:
    """Evaluate intent routing: regex patterns first, LLM fallback second."""
    patterns = [IntentPattern(**p) for p in config.get("intent_patterns", [])]
    input_str = str(input_value)
    for pattern in patterns:
        if re.search(pattern.pattern, input_str, re.IGNORECASE):
            return pattern.target

    routing_prompt = config.get("routing_prompt")
    if routing_prompt and engine_port:
        response = await engine_port.llm_invoke(
            prompt=f"{routing_prompt}\n\nInput: {input_str}",
        )
        return response.strip()

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
        response = await engine_port.llm_invoke(
            prompt=f"{routing_prompt}\n\nCandidates: {', '.join(candidates)}\n\nContext: {input_value}",
        )
        selected = response.strip()
        if selected in all_candidates:
            return selected
        logger.warning("Dynamic routing LLM returned invalid candidate '%s', falling back to default", selected)

    return "default"
