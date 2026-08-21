"""Engine-internal guardrail middleware chain (1.3.5i E3).

The chain kernel lives in the engine layer — stage ordering, BLOCK short-circuit,
SANITIZE propagation, and monotonic tightening are fixed here and cannot be
overridden by individual stage implementations. Stages can be added and removed
by the assembly facade (`services/security/guardrail_assembly.py`), but the
chain semantics themselves are immutable.

Phases model the trust boundaries the chain guards:
    AGENT_PRE_STEP       — before any agent work in a superstep
    AGENT_REQUEST        — before an LLM call (analog of PreLLMHook)
    LLM_RESPONSE         — after an LLM call (analog of PostLLMHook)
    TOOL_PRE_EXECUTE     — before a tool call (analog of PreToolHook)
    TOOL_EXECUTE         — the actual tool execution (terminal handler)
    TOOL_POST_EXECUTE    — after tool execution, before result delivery
    TOOL_RESULT          — after the tool result has been produced

The terminal stage for tool execution is the actual ``tool_registry.execute``
(or sandboxed variant). Guards wrap execution by registering their stage in
TOOL_PRE_EXECUTE / TOOL_POST_EXECUTE and the executor registers itself as the
terminal handler for TOOL_EXECUTE.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from hecate.engine.guardrail import GuardrailAction, GuardrailResult


class Phase(StrEnum):
    """Chain phases — the seven trust boundaries the engine guards."""

    AGENT_PRE_STEP = "agent_pre_step"
    AGENT_REQUEST = "agent_request"
    LLM_RESPONSE = "llm_response"
    TOOL_PRE_EXECUTE = "tool_pre_execute"
    TOOL_EXECUTE = "tool_execute"
    TOOL_POST_EXECUTE = "tool_post_execute"
    TOOL_RESULT = "tool_result"


StageResult = GuardrailResult  # alias for chain-level clarity


@dataclass(frozen=True)
class StageDecision:
    """The decision a stage produces — extends GuardrailResult with stage identity.

    ``stage_id`` identifies the originating stage so audit consumers (event log,
    SIEM, decision sink) can attribute decisions back to a specific interceptor
    when a chain short-circuits or modifies data.
    """

    stage_id: str
    action: GuardrailAction
    reason: str = ""
    modified_data: dict[str, Any] | None = None

    @classmethod
    def allow(cls, stage_id: str = "") -> StageDecision:
        return cls(stage_id=stage_id, action=GuardrailAction.ALLOW)

    @classmethod
    def block(cls, stage_id: str, reason: str) -> StageDecision:
        return cls(stage_id=stage_id, action=GuardrailAction.BLOCK, reason=reason)

    @classmethod
    def sanitize(cls, stage_id: str, modified_data: dict[str, Any], reason: str = "") -> StageDecision:
        return cls(
            stage_id=stage_id,
            action=GuardrailAction.SANITIZE,
            reason=reason,
            modified_data=modified_data,
        )


# A stage receives the current data snapshot and a `next` callable. It must
# either return a StageDecision (short-circuit) or call `next` exactly once
# and return that call's result. The terminal handler for the phase is the
# default `next` registered on the chain.
#
# Contract: a stage that calls `next(data)` receives back either the
# terminal handler's return value (when downstream stages ALLOW) or a
# ``CallNextOutcome`` whose ``decision.action == BLOCK`` (when a downstream
# stage BLOCKed). The stage is then responsible for returning the
# downstream BLOCK decision (or, by spec monotonicity, its own BLOCK or
# ALLOW — never a "loosening" decision that would resurrect the call).
NextFn = Callable[[Any], Awaitable["CallNextOutcome"]]
StageHandler = Callable[[Any, NextFn], Awaitable[tuple[StageDecision, Any]]]


@dataclass
class CallNextOutcome:
    """The result a stage receives when it calls ``call_next(data)``.

    Carries both the (possibly-blocked) decision AND the data payload the
    chain would have produced, so the calling stage can read either side.
    """

    decision: StageDecision
    result: Any

    @property
    def blocked(self) -> bool:
        return self.decision.action == GuardrailAction.BLOCK


@dataclass
class _Stage:
    """Internal stage record. ``stage_id`` is required; ``handler`` runs the stage."""

    stage_id: str
    handler: StageHandler


@dataclass
class Chain:
    """An ordered middleware chain for one Phase.

    Composition is fixed: stages run in declaration order. A stage that returns
    BLOCK short-circuits the chain with its decision. A stage that returns
    SANITIZE without ``modified_data`` is treated as a contract violation and
    surfaced as BLOCK with the stage's identity (silent fall-through to ALLOW
    is forbidden).

    A terminal ``handler`` runs the real work for the phase (LLM call, tool
    execute). When all stages ALLOW, the terminal handler is called and its
    result is returned alongside the final ALLOW decision.

    An optional ``audit_hook`` is invoked once per non-ALLOW decision so the
    event log / decision sink can record the originating stage's identity.
    """

    phase: Phase
    stages: list[_Stage] = field(default_factory=list)
    handler: Callable[[Any], Awaitable[Any]] | None = None
    audit_hook: Callable[[StageDecision], Awaitable[None]] | None = None

    def add_stage(self, stage_id: str, handler: StageHandler) -> None:
        """Register a stage. Stages run in the order they are added."""
        self.stages.append(_Stage(stage_id=stage_id, handler=handler))

    def set_handler(self, handler: Callable[[Any], Awaitable[Any]]) -> None:
        """Register the terminal handler invoked when all stages ALLOW."""
        self.handler = handler

    def set_audit_hook(self, hook: Callable[[StageDecision], Awaitable[None]]) -> None:
        """Register an audit hook called on every non-ALLOW chain decision."""
        self.audit_hook = hook

    async def run(self, initial_data: Any) -> tuple[StageDecision, Any]:
        """Execute the chain against ``initial_data``.

        Returns a ``(decision, result)`` tuple. ``decision`` is the originating
        stage's StageDecision (or ALLOW with empty stage_id if the terminal
        handler ran unblocked). ``result`` is either the sanitized data
        flowing out of the chain or the terminal handler's return value.
        """
        if self.handler is None:
            raise ValueError(f"Chain {self.phase} has no terminal handler")
        decision, result = await self._run_stages(0, initial_data)
        if self.audit_hook is not None and decision.action != GuardrailAction.ALLOW:
            await self.audit_hook(decision)
        return decision, result

    async def _run_stages(self, index: int, data: Any) -> tuple[StageDecision, Any]:
        if index >= len(self.stages):
            result = await self.handler(data)
            return StageDecision.allow(stage_id=""), result

        stage = self.stages[index]

        async def call_next(sanitized: Any) -> CallNextOutcome:
            decision, next_result = await self._run_stages(index + 1, sanitized)
            return CallNextOutcome(decision=decision, result=next_result)

        decision, returned = await stage.handler(data, call_next)

        if decision.action == GuardrailAction.BLOCK:
            return decision, returned
        if decision.action == GuardrailAction.ALLOW:
            # Kernel monotonicity: downstream BLOCK MUST surface.
            if isinstance(returned, CallNextOutcome) and returned.blocked:
                return returned.decision, returned.result
            return decision, returned

        # SANITIZE — the modified data flows downstream.
        if decision.modified_data is None:
            return (
                StageDecision.block(
                    stage_id=stage.stage_id,
                    reason="sanitize contract violation: missing modified_data",
                ),
                returned,
            )
        downstream_decision, final_result = await self._run_stages(index + 1, decision.modified_data)
        if downstream_decision.action == GuardrailAction.BLOCK:
            return downstream_decision, final_result
        return decision, final_result


def chain_audit_record(decision: StageDecision) -> dict[str, Any]:
    """Render a chain decision as an audit dict for event log / decision sink.

    BLOCK decisions always carry stage identity so the audit trail can
    attribute the block to a specific interceptor.
    """

    return {
        "stage_id": decision.stage_id,
        "action": decision.action.value,
        "reason": decision.reason,
        "has_modified_data": decision.modified_data is not None,
    }


__all__ = [
    "Chain",
    "NextFn",
    "Phase",
    "StageDecision",
    "StageHandler",
    "chain_audit_record",
]
