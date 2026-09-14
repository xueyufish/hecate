"""Controller worker for the multi-agent central controller (2.6a).

The CONTROLLER node is the session-level router: on every user turn it
invokes the intent recognition engine (6.23) with evidence from its
referenced intent package, writes the routing decision to ``_route`` (the
same conditional-edge contract the CONDITION node uses), and persists the
L3 session-intent state in the reserved ``_intent_state`` channel so sticky
routing and goal tracking survive checkpoint/restore.

Routing priority:

1. ``start_workflow`` — the graph's first turn (empty prior state), when
   configured.
2. ``category_targets`` — the atomic label, then an active workflow label,
   mapped to a route target.
3. ``default_workflow`` — required fallback.

Isolation note: the controller only *routes*; executing a mapped target is
the target node's own contract (AGENT nodes dispatch sub-graphs with child
sessions and explicit channel mapping — the isolation semantics the spec
requires are inherited from the target nodes).
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from hecate.runtime.intent.engine import IntentRecognitionEngine, resolve_evidence
from hecate.runtime.intent.events import emit_controller_routed, emit_intent_recognized
from hecate.runtime.types import WorkerResult
from hecate.runtime.worker import Worker

logger = logging.getLogger(__name__)

# Reserved channel keys (underscore prefix bypasses declared-channel
# validation, mirroring ``_route`` / ``_has_tool_call``).
INTENT_STATE_KEY = "_intent_state"


class ControllerWorker(Worker):
    """Worker that executes CONTROLLER-type nodes.

    Args:
        port: RuntimePort-like object used for LLM classification (may be
            ``None`` — the engine then stops at the pattern path).
        evidence_port: Published-intent-evidence provider
            (``IntentEvidencePort``); ``None`` degrades every turn to the
            fallback-label path.
        event_store: EventStore for ``INTENT_RECOGNIZED`` and
            ``CONTROLLER_ROUTED`` events (execution_context overrides).
        engine: Pre-built recognition engine override (tests); a default
            engine is constructed when omitted.
    """

    def __init__(
        self,
        port: Any = None,
        evidence_port: Any = None,
        event_store: Any = None,
        engine: IntentRecognitionEngine | None = None,
    ) -> None:
        super().__init__(event_store=event_store)
        self._port = port
        self._evidence_port = evidence_port
        self._engine = engine

    def _get_engine(self) -> IntentRecognitionEngine:
        if self._engine is None:
            self._engine = IntentRecognitionEngine(port=self._port)
        return self._engine

    async def execute(
        self,
        node_id: str,
        node_config: dict,
        channel_snapshot: dict,
        execution_context: dict | None = None,
    ) -> WorkerResult:
        config = node_config
        category_targets: dict[str, str] = config.get("category_targets") or {}
        default_workflow = config.get("default_workflow")
        if not category_targets:
            return WorkerResult(
                node_id=node_id,
                error=ValueError(f"CONTROLLER node '{node_id}' requires non-empty category_targets"),
            )
        if not default_workflow:
            return WorkerResult(
                node_id=node_id,
                error=ValueError(f"CONTROLLER node '{node_id}' requires a default_workflow target"),
            )

        package_ref = config.get("intent_package") or {}
        try:
            package_id = uuid.UUID(str(package_ref.get("package_id")))
        except (ValueError, AttributeError) as e:
            return WorkerResult(
                node_id=node_id,
                error=ValueError(f"CONTROLLER node '{node_id}' has an invalid intent_package reference: {e}"),
            )
        version_pin = package_ref.get("version_id")
        version_id = uuid.UUID(str(version_pin)) if version_pin else None

        input_channel = config.get("input_channel", "messages")
        utterance = self._extract_utterance(channel_snapshot.get(input_channel))
        prior_state: dict = channel_snapshot.get(INTENT_STATE_KEY) or {}

        # Resolve published evidence (degrades to None on failure).
        evidence = None
        if self._evidence_port is not None:
            evidence = await resolve_evidence(self._evidence_port, package_id, version_id)

        engine = self._get_engine()
        result = await engine.recognize(_build_request(utterance, evidence, category_targets, prior_state, config))

        # Persist the L3 session-intent state via the reserved channel.
        new_state = dict(result.session_intent_update)
        updates: dict[str, Any] = {"messages": [], INTENT_STATE_KEY: new_state}

        target, level = self._decide_target(
            result=result,
            category_targets=category_targets,
            default_workflow=default_workflow,
            start_workflow=config.get("start_workflow"),
            first_turn=not prior_state.get("turn_labels"),
        )
        updates["_route"] = target

        # Observability: one recognition event + one routing event per turn.
        ctx = execution_context or {}
        event_store = ctx.get("event_store") or self._event_store
        session_id = ctx.get("session_id")
        await emit_intent_recognized(
            event_store,
            session_id,
            result,
            node_id=node_id,
            superstep=int(ctx.get("superstep", 0)),
            trace_id=ctx.get("trace_id"),
        )
        await emit_controller_routed(
            event_store,
            session_id,
            target=target,
            level=level,
            label=result.atomic.label,
            confidence=result.atomic.confidence,
            source=result.atomic.source.value,
            cache_hit=result.cache_hit,
            goal=new_state.get("goal"),
            workflow_label=new_state.get("workflow_label"),
            node_id=node_id,
            superstep=int(ctx.get("superstep", 0)),
            trace_id=ctx.get("trace_id"),
        )

        return WorkerResult(node_id=node_id, channel_updates=updates)

    # ------------------------------------------------------------------
    # Routing decision
    # ------------------------------------------------------------------

    @staticmethod
    def _decide_target(
        result: Any,
        category_targets: dict[str, str],
        default_workflow: str,
        start_workflow: str | None,
        first_turn: bool,
    ) -> tuple[str, str]:
        """Priority: start override → atomic → active workflow → default."""
        if first_turn and start_workflow:
            return start_workflow, "start"
        atomic_label = result.atomic.label
        if atomic_label and atomic_label in category_targets:
            return category_targets[atomic_label], "atomic"
        if result.workflow.active and result.workflow.label in category_targets:
            return category_targets[result.workflow.label], "workflow"
        return default_workflow, "default"

    @staticmethod
    def _extract_utterance(value: Any) -> str:
        """Pull the last user turn text out of the input channel value."""
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, list) and value:
            last = value[-1]
            if isinstance(last, str):
                return last
            if isinstance(last, dict):
                return str(last.get("content") or "")
        if isinstance(value, dict):
            return str(value.get("content") or "")
        return str(value)


def _build_request(
    utterance: str,
    evidence: Any,
    category_targets: dict[str, str],
    prior_state: dict,
    config: dict,
) -> Any:
    from hecate.runtime.intent.types import IntentRequest

    global_intent = config.get("global_intent") or {}
    goal_hint = global_intent.get("goal_hint") if global_intent.get("enabled") else None
    session_intent = dict(prior_state)
    if goal_hint and not session_intent.get("goal"):
        session_intent["goal"] = goal_hint
    return IntentRequest(
        utterance=utterance,
        evidence=evidence,
        session_intent=session_intent,
        fallback_labels=tuple(category_targets.keys()),
        recognition_model=config.get("recognition_model"),
    )


__all__ = ["ControllerWorker", "INTENT_STATE_KEY"]
