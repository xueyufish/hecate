"""Engine-level tests for the guardrail middleware chain (1.3.5i E3).

The chain semantics (stage order, BLOCK short-circuit, SANITIZE propagation,
monotonic tightening, SANITIZE contract violation) are pinned here so the
engine kernel contract is verifiable independently of how stages are sourced.
"""

from __future__ import annotations

import pytest

from hecate.engine.guardrail import GuardrailAction
from hecate.engine.middleware import (
    Chain,
    Phase,
    StageDecision,
    chain_audit_record,
)


async def _allow(stage_id: str):
    """Build a passthrough stage. Inspects the CallNextOutcome to honor a
    downstream BLOCK with the kernel-monotonicity invariant.
    """

    async def handler(data, call_next):
        outcome = await call_next(data)
        if outcome.blocked:
            return outcome.decision, outcome
        return StageDecision.allow(stage_id=stage_id), outcome.result

    return handler


async def _block(stage_id: str, reason: str):
    """Build a short-circuit BLOCK stage."""

    async def handler(data, call_next):
        return StageDecision.block(stage_id=stage_id, reason=reason), None

    return handler


async def _sanitize(stage_id: str, mutator):
    """Build a SANITIZE stage that mutates data via ``mutator(data)``."""

    async def handler(data, call_next):
        modified = mutator(data)
        return (
            StageDecision.sanitize(stage_id=stage_id, modified_data=modified),
            modified,
        )

    return handler


@pytest.mark.asyncio
async def test_empty_chain_runs_terminal_handler():
    """No stages → terminal handler is invoked; result is returned alongside ALLOW."""
    chain = Chain(phase=Phase.TOOL_EXECUTE)
    chain.set_handler(lambda data: _async_return({"executed": True, "name": data}))
    decision, result = await chain.run("bash")
    assert decision.action == GuardrailAction.ALLOW
    assert result == {"executed": True, "name": "bash"}


@pytest.mark.asyncio
async def test_stages_run_in_declaration_order():
    chain = Chain(phase=Phase.TOOL_EXECUTE)
    chain.set_handler(lambda data: _async_return(data))
    calls: list[str] = []

    async def make_recorder(name: str):
        async def handler(data, call_next):
            calls.append(name)
            outcome = await call_next(data)
            if outcome.blocked:
                return outcome.decision, outcome
            return StageDecision.allow(stage_id=name), outcome.result

        return handler

    chain.add_stage("a", await make_recorder("a"))
    chain.add_stage("b", await make_recorder("b"))
    chain.add_stage("c", await make_recorder("c"))

    await chain.run("x")
    assert calls == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_block_short_circuits_remaining_stages():
    chain = Chain(phase=Phase.TOOL_PRE_EXECUTE)
    chain.set_handler(lambda data: _async_return("should-not-reach"))
    calls: list[str] = []

    async def recorder(name: str):
        async def handler(data, call_next):
            calls.append(name)
            outcome = await call_next(data)
            if outcome.blocked:
                return outcome.decision, outcome
            return StageDecision.allow(stage_id=name), outcome.result

        return handler

    chain.add_stage("a", await recorder("a"))
    chain.add_stage("blocker", await _block("blocker", "nope"))
    chain.add_stage("c", await recorder("c"))

    decision, _ = await chain.run("x")
    assert decision.action == GuardrailAction.BLOCK
    assert decision.stage_id == "blocker"
    assert decision.reason == "nope"
    assert calls == ["a"]  # c never ran


@pytest.mark.asyncio
async def test_sanitize_propagates_modified_data_to_subsequent_stages():
    chain = Chain(phase=Phase.TOOL_PRE_EXECUTE)
    chain.set_handler(lambda data: _async_return(data))

    seen: list[object] = []

    async def recorder(name: str):
        async def handler(data, call_next):
            seen.append(data)
            outcome = await call_next(data)
            if outcome.blocked:
                return outcome.decision, outcome
            return StageDecision.allow(stage_id=name), outcome.result

        return handler

    chain.add_stage("upper", await _sanitize("upper", str.upper))
    chain.add_stage("after", await recorder("after"))

    decision, result = await chain.run("hello")
    assert decision.action == GuardrailAction.SANITIZE
    assert decision.stage_id == "upper"
    assert seen == ["HELLO"]
    assert result == "HELLO"  # terminal saw the sanitized data


@pytest.mark.asyncio
async def test_sanitize_without_modified_data_is_blocked():
    """A SANITIZE stage that forgets ``modified_data`` MUST surface as BLOCK with the
    stage's identity, never silently fall through to ALLOW.
    """
    chain = Chain(phase=Phase.AGENT_REQUEST)
    chain.set_handler(lambda data: _async_return("ok"))

    async def broken(data, call_next):
        # Decision.action = SANITIZE but modified_data is None.
        return StageDecision(stage_id="broken", action=GuardrailAction.SANITIZE), None

    chain.add_stage("broken", broken)

    decision, _ = await chain.run("x")
    assert decision.action == GuardrailAction.BLOCK
    assert decision.stage_id == "broken"
    assert "missing modified_data" in decision.reason


@pytest.mark.asyncio
async def test_downstream_block_after_sanitize_carries_downstream_identity():
    """SANITIZE → next stage BLOCKs → block decision (with downstream identity)
    propagates back to the caller."""
    chain = Chain(phase=Phase.TOOL_PRE_EXECUTE)
    chain.set_handler(lambda data: _async_return("never-reached"))

    chain.add_stage("upper", await _sanitize("upper", str.upper))
    chain.add_stage("blocker", await _block("blocker", "no"))

    decision, _ = await chain.run("hi")
    assert decision.action == GuardrailAction.BLOCK
    assert decision.stage_id == "blocker"


@pytest.mark.asyncio
async def test_chain_audit_record_carries_block_identity():
    """Audit record for BLOCK MUST include the originating stage_id."""
    decision = StageDecision.block(stage_id="x", reason="nope")
    record = chain_audit_record(decision)
    assert record["stage_id"] == "x"
    assert record["action"] == "block"
    assert record["reason"] == "nope"


@pytest.mark.asyncio
async def test_chain_without_handler_raises():
    chain = Chain(phase=Phase.TOOL_EXECUTE)
    with pytest.raises(ValueError):
        await chain.run("x")


@pytest.mark.asyncio
async def test_audit_hook_fires_on_block_with_stage_identity():
    """T3.6 — chain audit: every non-ALLOW decision triggers the registered
    audit hook with the originating stage's identity, so the event log /
    decision sink can attribute the block to a specific interceptor.
    """
    chain = Chain(phase=Phase.TOOL_PRE_EXECUTE)
    chain.set_handler(lambda data: _async_return(data))

    seen: list[StageDecision] = []

    async def audit(decision):
        seen.append(decision)

    chain.set_audit_hook(audit)

    chain.add_stage("a", await _allow("a"))
    chain.add_stage("blocker", await _block("blocker", "nope"))

    await chain.run("x")
    assert len(seen) == 1
    assert seen[0].action == GuardrailAction.BLOCK
    assert seen[0].stage_id == "blocker"


@pytest.mark.asyncio
async def test_audit_hook_not_fired_on_allow():
    """T3.6 — ALLOW chain decisions do NOT trigger the audit hook; only BLOCK
    and contract-violation BLOCK paths are audited.
    """
    chain = Chain(phase=Phase.TOOL_PRE_EXECUTE)
    chain.set_handler(lambda data: _async_return(data))

    invocations = 0

    async def audit(decision):
        nonlocal invocations
        invocations += 1

    chain.set_audit_hook(audit)

    chain.add_stage("a", await _allow("a"))
    chain.add_stage("b", await _allow("b"))

    await chain.run("x")
    assert invocations == 0


@pytest.mark.asyncio
async def test_audit_hook_fires_on_sanitize_without_modified_data():
    """T3.6 — the SANITIZE-without-modified-data contract violation IS audited."""
    chain = Chain(phase=Phase.AGENT_REQUEST)
    chain.set_handler(lambda data: _async_return("ok"))

    seen: list[StageDecision] = []

    async def audit(decision):
        seen.append(decision)

    chain.set_audit_hook(audit)

    async def broken(data, call_next):
        return StageDecision(stage_id="broken", action=GuardrailAction.SANITIZE), None

    chain.add_stage("broken", broken)

    await chain.run("x")
    assert len(seen) == 1
    assert seen[0].action == GuardrailAction.BLOCK
    assert seen[0].stage_id == "broken"


async def _async_return(value):
    return value
